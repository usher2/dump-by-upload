import os,sys
import logging, logging.handlers
import uuid
import xml.sax
import dateutil.parser
import pytz
import time
import subprocess
import hashlib
import re
from flask import Flask, request, redirect, url_for, abort, jsonify, make_response
from werkzeug.utils import secure_filename
from werkzeug.exceptions import HTTPException
from flask_mysqldb import MySQL

ACCESS_UPLOAD = 1
ACCESS_DOWNLOAD = 2

ALLOWED_EXTENSIONS = set(['xml'])
BLOCK_SIZE = 4096

CACHETIME=600

app = Flask(__name__)
app.debug = True
app.config.from_envvar('UPLOAD_SETTINGS')
app.config['MYSQL_USER'] = os.environ.get('MYSQL_USER','dumpby')
app.config['MYSQL_DB'] = os.environ.get('MYSQL_DATABASE','dumpby')
app.config['MYSQL_PASSWORD'] = os.environ.get('MYSQL_PASSWORD','dumpby')
mysql = MySQL(app)

handler = logging.handlers.RotatingFileHandler(os.path.join(app.config['LOGGING_FOLDER'], 'upload.log'), maxBytes=100000, backupCount=10)
handler.setLevel(logging.DEBUG)
formatter = logging.Formatter("[%(asctime)s] %(message)s")
handler.setFormatter(formatter)
app.logger.addHandler(handler)
app.logger.setLevel(logging.INFO)

_rb = re.compile(rb".*<resources.*?>",flags=re.M|re.S)
_re = re.compile(rb"</resources.*?>.*",flags=re.M|re.S)

# borg init
borg_env = os.environ.copy()
borg_env["LANG"]="en_US.UTF-8"
borg_env["BORG_CONFIG_DIR"]=os.path.join(app.config["DATA_FOLDER"], ".config/borg")
borg_env["BORG_CACHE_DIR"]=os.path.join(app.config["DATA_FOLDER"], ".cache")
borg_env["BORG_UNKNOWN_UNENCRYPTED_REPO_ACCESS_IS_OK"]="yes"
borg_env["BORG_RELOCATED_REPO_ACCESS_IS_OK"]="yes"
borg_env["BORG_KEYS_DIR"] = os.path.join(app.config["DATA_FOLDER"], ".config/borg/keys")
borg_env["BORG_SECURITY_DIR"] = os.path.join(app.config["DATA_FOLDER"], ".config/borg/security")
borg_repo=os.path.join(app.config["DATA_FOLDER"],"dedup")

rutz=pytz.timezone("Europe/Moscow")

with app.app_context():
        c = 0
        while True:
                try:
                        c += 1
                        db = mysql.connection
                        break
                except:
                        if c > 30:
                                raise
                        time.sleep(1)

class RegHandler(xml.sax.ContentHandler):
        def __init__(self):
                self.updateTime = ""
                self.updateTime_ut = 0.0

        # Call when an element starts
        def startElement(self, tag, attributes):
                if tag == "resources":
                        self.updateTime = attributes["date"]
                        self.updateTime_ut = rutz.localize(dateutil.parser.parse(self.updateTime)).timestamp()


def allowed_file(filename):
        return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_ip(request):
        addr = request.remote_addr
        if 'X-Forwarded-For' in request.headers:
                addr = request.headers['X-Forwarded-For']
        elif 'X-Real-IP' in request.headers:
                addr = request.headers['X-Real-IP']
        return addr

def get_auth(addr, request, perm):
        if 'Authorization' in request.headers:
                _a = request.headers['Authorization'].rsplit(' ', 1)
                if len(_a) == 2:
                        if _a[0].strip() != "Bearer":
                                app.logger.info("%s (None): Not Bearer Auth", addr)
                                abort(401)
                        else:
                                auth = _a[1].strip()
                                app.logger.info("%s (None): Auth token: %s", addr, auth)
                                cur = mysql.connection.cursor()
                                cur.execute('SELECT * FROM `auth` WHERE `token` = %s', (auth,))
                                rv = cur.fetchall()
                                acheck = rv[0] if rv else None
                                if acheck is None:
                                        app.logger.error('%s (None): Invalid token!!!!: %s', addr, auth)
                                        abort(403)
                                else:
                                        user = acheck['nick']
                                        if acheck['type'] != perm:
                                                app.logger.error("%s (%s): Access denied. Invalid type", addr, user)
                                                abort(403)
                                        else:
                                                app.logger.info("%s (%s): Connected success as %d", addr, user, perm)
                else:
                        app.logger.error('%s (None): Unknown auth type', addr)
                        abort(401)
        else:
                app.logger.error('%s (None): Authorization required', addr)
                abort(401)
        return acheck

def add_file(addr, user, uniqname, filename, newfilename):
        # do nothing if exists
        if not os.path.exists(newfilename):
                with open(filename, 'rb') as fi:
                        with open(newfilename, 'wb') as fo:
                                for chunk in iter(lambda: fi.read(BLOCK_SIZE), b''):
                                        fo.write(chunk)
                app.logger.info("%s (%s): File %s was added", addr, user, uniqname)
        else:
                app.logger.warning('%s (%s): File %s already exists', addr, user, uniqname)

def decompress(uniqid):
        uniqname = uniqid + '.xml'
        datadir = os.path.join(app.config['DATA_FOLDER'], uniqid[0:2], uniqid[2:4])
        if not os.path.exists(datadir):
                os.makedirs(datadir)
        filename = os.path.join(datadir, uniqname)
        try:
                args = ["borg", "extract", "--umask=0022", borg_repo + "::" + uniqid, uniqname]
                print("B: %s" % ' '.join(args))
                subprocess.run(args, env=borg_env, check=True, cwd=datadir)
                if os.path.exists(filename):
                        return True
                else:
                        return False
        except:
                print("Borg error %s" % sys.exc_info()[1])
                if os.path.exists(filename):
                        os.unlink(filename)
                return False

def get_file(uniqid):
        try:
                cur = mysql.connection.cursor()
                # exclusive lock - for concurency
                cur.execute('SELECT * FROM `dumps` WHERE `id` = %s FOR UPDATE', (uniqid, ))
                rv = cur.fetchall()
                dump = rv[0] if rv else None
                if dump is not None:
                        uniqname = uniqid + '.xml'
                        datadir = os.path.join(app.config['DATA_FOLDER'], uniqid[0:2], uniqid[2:4])
                        filename = os.path.join(datadir, uniqname)
                        now = int(time.time())
                        ct = now + CACHETIME
                        if dump['a'] == 2:
                                if decompress(uniqid):
                                        cur.execute('UPDATE `dumps` SET `a`=1, `ct`=%s, `u`=%s WHERE `id`=%s', (ct, now, dump["id"], ))
                                else:
                                        raise Exception("Can't take dump %s from archive" % uniqid)
                        if dump['a'] == 1:
                                cur.execute('UPDATE `dumps` SET `ct`=%s, `u`=%s WHERE `id`=%s', (ct, now, dump["id"], ))
                        return uniqname, filename
                else:
                        return "", ""
        except:
                raise
        finally:
                mysql.connection.commit()

@app.route('/', methods=['GET'])
def get_root():
        return "<!doctype html><html></html>"

@app.route('/hot', methods=['GET'])
def hot_handler():
        addr = get_ip(request)
        acheck = get_auth(addr, request, ACCESS_DOWNLOAD)
        user = acheck['nick']
        now = int(time.time())
        cur = mysql.connection.cursor()
        cur.execute('SELECT * FROM `dumps` WHERE `ut` < %s ORDER BY `ut` DESC LIMIT 1', (now, ))
        rv = cur.fetchall()
        dump = rv[0] if rv else None
        uniqid = dump["id"]
        if uniqid is not None:
                app.logger.warning("%s (%s): Hot dump requested, %s found", addr, user, uniqid)
                uniqname, filename = get_file(uniqid)
                if uniqname == "" and filename == "":
                        app.logger.warning("%s (%s): Record %s not found", addr, user, uniqid)
                        abort(404)
                elif os.path.exists(filename):
                        app.logger.warning("%s (%s): Dump %s start sending", addr, user, uniqid)
                        r = make_response()
                        r.headers['Content-Type'] = 'application/octet-stream'
                        r.headers['Cache-Control'] = 'no-cache'
                        r.headers['Content-Disposition'] = 'attachment; filename="%s"' % uniqname
                        r.headers['X-Accel-Redirect'] = os.path.join("/data", uniqid[0:2], uniqid[2:4], uniqname)
                        return r
                else:
                        app.logger.warning("%s (%s): File %s not found", addr, user, uniqid)
                        abort(500)
        else:
                app.logger.warning("%s (%s): Name for download is empty", addr, user)
                abort(500)

@app.route('/get/<uniqid>', methods=['GET'])
def get_handler(uniqid):
        addr = get_ip(request)
        acheck = get_auth(addr, request, ACCESS_DOWNLOAD)
        user = acheck['nick']
        if uniqid is not None:
                app.logger.warning("%s (%s): Dump %s requested", addr, user, uniqid)
                uniqname, filename = get_file(uniqid)
                if uniqname == "" and filename == "":
                        app.logger.warning("%s (%s): Record %s not found", addr, user, uniqid)
                        abort(404)
                elif os.path.exists(filename):
                        app.logger.warning("%s (%s): Dump %s start sending", addr, user, uniqid)
                        r = make_response()
                        r.headers['Content-Type'] = 'application/octet-stream'
                        r.headers['Cache-Control'] = 'no-cache'
                        r.headers['Content-Disposition'] = 'attachment; filename="%s"' % uniqname
                        r.headers['X-Accel-Redirect'] = os.path.join("/data", uniqid[0:2], uniqid[2:4], uniqname)
                        return r
                else:
                        app.logger.warning("%s (%s): File %s not found", addr, user, uniqid)
                        abort(500)
        else:
                app.logger.warning("%s (%s): Name for download is empty", addr, user)
                abort(500)


@app.route('/start', methods=['GET'])
def start_handler():
        addr = get_ip(request)
        acheck = get_auth(addr, request, ACCESS_DOWNLOAD)
        user = acheck['nick']
        c = int(request.args.get('c', '1'))
        ts = request.args.get('ts', 0)
        cur = mysql.connection.cursor()
        cur.execute('SELECT * FROM `dumps` WHERE `ut` > %s ORDER BY `ut` ASC LIMIT %s', (ts, c))
        rv = cur.fetchall()
        dump_list = rv if rv else None
        if dump_list is not None:
                return jsonify(dump_list)
        else:
                app.logger.warning("%s (%s): List is empty", addr, user)
                return jsonify([])

@app.route('/last', methods=['GET'])
def last_handler():
        addr = get_ip(request)
        acheck = get_auth(addr, request, ACCESS_DOWNLOAD)
        user = acheck['nick']
        c = int(request.args.get('c', '1'))
        ts = request.args.get('ts', 0)
        cur = mysql.connection.cursor()
        cur.execute('SELECT * FROM `dumps` WHERE `ut` < %s ORDER BY `ut` DESC LIMIT %s', (ts, c))
        rv = cur.fetchall()
        dump_list = rv if rv else None
        if dump_list is not None:
                return jsonify(dump_list)
        else:
                app.logger.warning("%s (%s): List is empty", addr, user)
                return jsonify([])

@app.route('/upload', methods=['POST'])
def upload_handler():
        addr = get_ip(request)
        acheck = get_auth(addr, request, ACCESS_UPLOAD)
        user = acheck['nick']
        # check if the post request has the file part
        if 'file' not in request.files:
                app.logger.warning('%s (%s): No file part', addr, user)
                abort(500)
        file = request.files['file']
        # if user does not select file, browser also
        # submit a empty part without filename
        if file.filename == '':
                app.logger.warning('%s (%s): No selected file', addr, user)
                abort(500)
        if file and allowed_file(file.filename):
                H = RegHandler()
                hd = hashlib.sha256()
                hda = hashlib.sha256()
                size = 0
                uniqname = ""
                tempdir = os.path.join(app.config['UPLOAD_FOLDER'], str(uuid.uuid4()))
                os.mkdir(tempdir)
                # New filename harcoded - dump.xml
                filename = os.path.join(tempdir, 'dump.xml')
                file.save(filename)
                # Check zip archive
                try:
                        # SOME CHECK???!!!
                        args = ['xmllint','--noout','--schema','/srv/dumpby/dump.xsd',filename]
                        rcode = subprocess.call(args)
                        if rcode == 0:
                                app.logger.info("%s (%s): check passed", addr, user)
                        else:
                                app.logger.error("%s (%s): xmllint execute error %d", addr, user, rcode)
                                raise
                        # parse xml (get updatetime)
                        parser = xml.sax.make_parser()
                        parser.setFeature(xml.sax.handler.feature_namespaces, 0)
                        parser.setContentHandler(H)
                        parser.parse(filename)
                        # get hash sha256
                        with open(filename, 'rb') as fh:
                                s = b''
                                p = b''
                                fl = 0
                                for block in iter(lambda: fh.read(BLOCK_SIZE), b''):
                                        hda.update(block)
                                        if fl == 0:
                                                s += block
                                                if _rb.search(s):
                                                        hd.update(_rb.sub(b"", s))
                                                        fl = 1
                                        elif fl == 1:
                                                s = p + block
                                                if _re.search(s):
                                                        hd.update(_re.sub(b"", s))
                                                        fl = 2
                                                else:
                                                        hd.update(p)
                                                        p = block
                except:
                        app.logger.error("%s (%s): Check error %s", addr, user, sys.exc_info()[1])
                        if os.path.exists(filename):
                                os.unlink(filename)
                        if os.path.exists(tempdir):
                                os.rmdir(tempdir)
                        abort(500)
                uniqid = hda.hexdigest()
                realid = hd.hexdigest()
                uniqname = uniqid + '.xml'
                datadir = os.path.join(app.config['DATA_FOLDER'], uniqname[0:2], uniqname[2:4])
                if not os.path.exists(datadir):
                        os.makedirs(datadir)
                newfilename = os.path.join(datadir, uniqname)
                # check batabase
                cur = mysql.connection.cursor()
                cur.execute('SELECT * FROM dumps WHERE id = %s', (uniqid,))
                rv = cur.fetchall()
                dump = rv[0] if rv else None
                if dump is not None:
                        app.logger.warning('%s (%s): Record %s already exists', addr, user, uniqname)
                        if dump['a'] == 0:
                                add_file(addr, user, uniqname, filename, newfilename)
                        app.logger.warning('%s (%s): Archive %s, skipping...', addr, user, uniqname)
                else:
                        add_file(addr, user, uniqname, filename, newfilename)
                        cur.execute('INSERT INTO `dumps` (`id`, `crc`, `ut`, `s`, `u`) VALUES (%s, %s, %s, %s, %s)', (uniqid, realid, int(H.updateTime_ut), size, int(time.time())))
                        mysql.connection.commit()
                        app.logger.info("%s (%s): %s (%s) record was added", addr, user, uniqid, realid)
                if os.path.exists(filename):
                        os.unlink(filename)
                if os.path.exists(tempdir):
                        os.rmdir(tempdir)
                return '''
                <!doctype html>
                <title>OK</title>
                <h1>OK %s updateTime=%s</h1>
                ''' % (uniqname, int(H.updateTime_ut))
        else:
                app.logger.error("%s (%s): Bogus filename %s", addr, user, file.filename)
                abort(500)
        return '''
        <!doctype html>
        <html>
        <title>Upload new File</title>
        <body>
        <h1>Upload new File</h1>
        <form method=post enctype=multipart/form-data>
                <p><input type=file name=file>
                <input type=submit value=Upload>
        </form>
        </body>
        </html>
        '''

if __name__ == '__main__':
    app.run(debug=True)
