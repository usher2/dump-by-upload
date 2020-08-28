FROM alpine:3.12

LABEL maintainer="Phil Kulin <schors@gmail.com>"

WORKDIR /tmp
RUN apk add --no-cache bash openssl python3 py3-msgpack py3-pip mariadb-client mariadb-connector-c \
        lz4-libs libattr libacl libressl ca-certificates \
        && pip3 install --upgrade pip \
        && apk add --no-cache --virtual .build-deps \
                gcc g++ libc-dev make pcre-dev zlib-dev \
                python3-dev lz4-dev acl-dev attr-dev libressl-dev linux-headers \
                mariadb-dev \
                musl-dev \
                curl \
        && pip3 install mysqlclient \
        && curl -L https://github.com/borgbackup/borg/releases/download/1.1.13/borgbackup-1.1.13.tar.gz | tar --strip-components=1 -zxp \
        && pip3 install . \
        && apk del .build-deps \
        && rm -rf /tmp/*

RUN pip3 install dateutils \
        && addgroup -S -g 82 www-data \
        && adduser -D -S -h /var/www -G www-data -u 82 -s /sbin/nologin www-data \
        && addgroup dumpby -g 2002 \
        && adduser -S -h /srv/dumpby -G dumpby -u 2002 -s /bin/bash dumpby \
        && chmod 750 /srv/dumpby \
        && mkdir -p /var/opt/dumpby/data \
        && chown dumpby:dumpby /var/opt/dumpby/data \
        && chmod 755 /var/opt/dumpby/data \
        && mkdir -p /var/opt/dumpby/tmp \
        && chown dumpby:dumpby /var/opt/dumpby/tmp \
        && chown www-data:dumpby /var/opt/dumpby \
        && chmod 170 /var/opt/dumpby \
        && mkdir -p /var/log/dumpby \
        && chmod 750 /var/log/dumpby \
        && chown dumpby:dumpby /var/log/dumpby

ENV UPLOAD_SETTINGS=/upload.cfg
ENV MYSQL_CONFIG=/mysql_user.env

COPY purge.py /srv/dumpby/
COPY cron_entrypoint.sh /entrypoint.sh
COPY crontab /etc/crontabs/dumpby

ENTRYPOINT ["/entrypoint.sh"]
CMD [ "crond", "-f", "-d", "8"]

