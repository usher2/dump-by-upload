#/usr/bin/env python3

import os,sys
import codecs
import MySQLdb,MySQLdb.cursors
import uuid
import subprocess
import datetime
import json
import dateutil.parser
import time

__escape_decoder = codecs.getdecoder('unicode_escape')
config = {}
alist = {}

CACHETIME=600

def decode_escaped(escaped):
        return __escape_decoder(escaped)[0]

def parse_config(path):
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            # Remove any leading and trailing spaces in key, value
            k, v = k.strip(), v.strip()
            if len(v) > 0:
                quoted = v[0] == v[len(v) - 1] in ['"', "'"]
                if quoted:
                    v = decode_escaped(v[1:-1])
            yield k, v

def compress(uniqid, ut):
        global config, alist
        uniqname = uniqid + '.xml'
        datadir = os.path.join(config['DATA_FOLDER'], uniqid[0:2], uniqid[2:4])
        filename = os.path.join(datadir, uniqname)
        if os.path.exists(filename):
                print("Archiving %s..." % (filename,))
                if uniqid not in alist:
                    try:
                        _ts = datetime.datetime.fromtimestamp(ut, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
                        args = ["borg", "create", "--noatime", "--noctime", "--timestamp", _ts, "--compression", "lzma", "--umask=0022", borg_repo + "::" + uniqid, uniqname]
                        print("B: %s" % ' '.join(args))
                        subprocess.run(args, env=borg_env, check=True, cwd=datadir)
                        return True
                    except:
                        print("Borg error %s", sys.exc_info()[1])
                        return False
                else:
                        print("Archive %s already exists..." % (uniqid,))
                        return True
        else:
                print("Oops file %s not exists...." % (filename,))
                return False

def borg_list(env, repo):
        global alist
        try:
                args=["borg", "list", "--json", repo]
                cp = subprocess.run(args, env=env, check=True, stdout=subprocess.PIPE)
                o = json.loads(cp.stdout)
                alist.clear()
                if "archives" in o:
                        for a in o["archives"]:
                                if "name" in a:
                                        alist[a["name"]] = dateutil.parser.parse(a["time"]).timestamp()
        except subprocess.CalledProcessError as e:
                print("Borg repo not found")
                args=["borg", "init", "-e", "none", repo]
                try:
                        subprocess.run(args, env=env, check=True, cwd=config["DATA_FOLDER"])
                except:
                        print("Borg repo can't init: %s" % sys.exc_info()[1])
                        exit(1)
        except:
                print("Borg repo can't list: %s" % sys.exc_info()[1])
                exit(1)

if __name__ == "__main__":
        config_path = os.environ.get("UPLOAD_CONFIG","/upload.cfg")
        for k,v in parse_config(config_path):
                config[k] = v
        mysql_config_path = os.environ.get("MYSQL_CONFIG","/mysql_user.env")
        for k,v in parse_config(mysql_config_path):
                config[k] = v
        db=MySQLdb.connect(host=config["MYSQL_HOST"], user=config["MYSQL_USER"],
                        passwd=config["MYSQL_PASSWORD"], db=config["MYSQL_DATABASE"],
                        cursorclass=MySQLdb.cursors.DictCursor, use_unicode=True, charset="utf8mb4", autocommit=True)
        cur=db.cursor()
        # borg init
        borg_env = os.environ.copy()
        borg_env["LANG"]="en_US.UTF-8"
        borg_env["BORG_CONFIG_DIR"]=os.path.join(config["DATA_FOLDER"], ".config/borg")
        borg_env["BORG_CACHE_DIR"]=os.path.join(config["DATA_FOLDER"], ".cache")
        borg_env["BORG_UNKNOWN_UNENCRYPTED_REPO_ACCESS_IS_OK"]="yes"
        borg_env["BORG_RELOCATED_REPO_ACCESS_IS_OK"]="yes"
        borg_env["BORG_KEYS_DIR"] = os.path.join(config["DATA_FOLDER"], ".config/borg/keys")
        borg_env["BORG_SECURITY_DIR"] = os.path.join(config["DATA_FOLDER"], ".config/borg/security")
        borg_repo=os.path.join(config["DATA_FOLDER"],"dedup")
        # get archives list to global 'alist' variable
        borg_list(borg_env, borg_repo)
        # archive new dumps
        cur = db.cursor()
        cur.execute('SELECT `id`,`ut` FROM `dumps` WHERE `a`=0')
        rv = cur.fetchall()
        dumps = rv if rv else None
        if dumps:
                for _dump in dumps:
                        # exclusive flock for concurency
                        try:
                                cur.execute('SELECT `id`,`ut`,`a` FROM `dumps` WHERE `id`=%s FOR UPDATE', (_dump["id"], ))
                                _rv = cur.fetchall()
                                __dump = _rv[0] if _rv else None
                                if __dump:
                                        if __dump["a"] == 0:
                                                if compress(__dump["id"], __dump["ut"]):
                                                        now = int(time.time())
                                                        ct = now + CACHETIME
                                                        cur.execute('UPDATE `dumps` SET `a`=1, `ct`=%s, `u`=%s WHERE `id`=%s', (ct, now, __dump["id"], ))
                                                        print("Update archive status %s to 1" % __dump["id"])
                                                else:
                                                        print("Can't compress...")
                                        else:
                                                print("Dump %s already archived..." % __dump["id"])
                        except:
                                raise
                        finally:
                                db.commit()
        # get archives list to global 'alist' variable
        borg_list(borg_env, borg_repo)
        # delete from cache old dumps
        now = int(time.time())
        cur = db.cursor()
        cur.execute('SELECT `id`,`ut` FROM `dumps` WHERE `a`=1 AND ((`ct` < %s) OR (`ct`=0 AND `u` + %s < %s))', (now, CACHETIME, now, ))
        rv = cur.fetchall()
        dumps = rv if rv else None
        if dumps:
                for _dump in dumps:
                        # exclusive flock flag with ID - for concurency
                        try:
                                now = int(time.time())
                                cur.execute('SELECT `id`,`ut` FROM `dumps` WHERE `id`=%s AND `a`=1 AND ((`ct` < %s) OR (`ct`=0 AND `u` + %s < %s)) FOR UPDATE', (_dump["id"], now, CACHETIME, now, ))
                                _rv = cur.fetchall()
                                __dump = _rv[0] if _rv else None
                                if __dump:
                                        uniqid = __dump["id"]
                                        if uniqid not in alist:
                                                print("Oops... %s need to archive..." % uniqid)
                                                if not compress(uniqid, __dump["ut"]):
                                                        print("Oops... %s can't compress.." % uniqid)
                                                        continue
                                        uniqname = uniqid + '.xml'
                                        datadir = os.path.join(config['DATA_FOLDER'], uniqid[0:2], uniqid[2:4])
                                        filename = os.path.join(datadir, uniqname)
                                        now = int(time.time())
                                        cur.execute('UPDATE `dumps` SET `a`=2, `ct`=0, `u`=%s WHERE `id`=%s', (now, __dump["id"], ))
                                        if os.path.exists(filename):
                                                print("Clear cache and delete %s" % filename)
                                                os.unlink(filename)
                                        else:
                                                print("Clear cache %s" % filename)
                                                print("Ooops... where is file: %s?" % filename)
                        except:
                                raise
                        finally:
                                db.commit()
        # get archives list to global 'alist' variable
        borg_list(borg_env, borg_repo)
        # fullscan
        cur = db.cursor()
        cur.execute('SELECT `id`,`a` from `dumps`')
        rv = cur.fetchall()
        dumps = rv if rv else None
        if dumps:
                for _dump in dumps:
                        uniqid = _dump["id"]
                        a = _dump["a"]
                        uniqname = uniqid + '.xml'
                        datadir = os.path.join(config['DATA_FOLDER'], uniqid[0:2], uniqid[2:4])
                        filename = os.path.join(datadir, uniqname)
                        if uniqid in alist:
                                if os.path.exists(filename):
                                        if a == 2:
                                                # exclusive flock flag with ID - for concurency
                                                try:
                                                        cur.execute('SELECT `id`,`a` from `dumps` WHERE `id`=%s AND `a`=2 FOR UPDATE', (uniqid,))
                                                        _rv = cur.fetchall()
                                                        __dump = _rv[0] if _rv else None
                                                        if __dump:
                                                                print("Fullscan: Archive and file present, delete %s" % filename)
                                                                os.unlink(filename)
                                                except:
                                                        raise
                                                finally:
                                                        db.commit()
                        else:
                                if a > 0:
                                        # exclusive flock flag with ID - for concurency
                                        try:
                                                cur.execute('SELECT `id`,`a` from `dumps` WHERE `id`=%s AND `a`>0 FOR UPDATE', (uniqid,))
                                                _rv = cur.fetchall()
                                                __dump = _rv[0] if _rv else None
                                                if __dump:
                                                        if not os.path.exists(filename):
                                                                print("Ooops... Fullscan: file and archive not present: %s" % filename)
                                                        else:
                                                                print("Ooops... Fullscan: not archived: %s" % filename)
                                        except:
                                                raise
                                        finally:
                                                db.commit()

