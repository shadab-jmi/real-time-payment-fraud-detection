#!/bin/sh
set -eu
umask 077
if [ ! -s /var/lib/grafana/admin-password ]; then
    head -c 32 /dev/urandom | base64 > /var/lib/grafana/admin-password
fi
export GF_SECURITY_ADMIN_PASSWORD__FILE=/var/lib/grafana/admin-password
exec /run.sh
