#!/bin/sh
set -eu
umask 077
digest=$(printf %s "$REDIS_PASSWORD" | sha256sum | cut -d' ' -f1)
printf 'user default off\nuser health on #%s +ping\nuser worker on #%s ~history:* +ping +get +set +del +watch +unwatch +multi +exec +discard\n' "$digest" "$digest" > /tmp/users.acl
chown redis:redis /tmp/users.acl
exec /usr/local/bin/docker-entrypoint.sh redis-server --aclfile /tmp/users.acl --appendonly yes
