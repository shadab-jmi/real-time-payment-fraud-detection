#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
project=${1:-payment-fraud}
case "$project" in ''|[!a-z0-9]*|*[!a-z0-9_-]*) echo 'Invalid project name' >&2; exit 2;; esac
config=.env
[ "$project" = payment-fraud ] || config=".env.$project"
umask 077
if [ ! -f "$config" ]; then
  tmp=$(mktemp "./$config.XXXXXX")
  trap 'rm -f "$tmp"' EXIT
  while IFS= read -r line; do
    case "$line" in
      COMPOSE_PROJECT_NAME=*) printf 'COMPOSE_PROJECT_NAME=%s\n' "$project";;
      *=) printf '%s%s\n' "$line" "$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')";;
      *) printf '%s\n' "$line";;
    esac
  done < .env.example > "$tmp"
  ln "$tmp" "$config" || test -f "$config"
fi
printf 'Credential file: %s/%s\n' "$PWD" "$config"
