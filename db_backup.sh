#!/bin/bash
set -euo pipefail

for var in B2_APPLICATION_KEY_ID B2_APPLICATION_KEY B2_BUCKET DATABASE_URL; do
  if [ -z "${!var:-}" ]; then
    echo "$var is not set; exiting." >&2
    exit 1
  fi
done

if [ -z "${B2_NOTIFICATION_WEBHOOK:-}" ]; then
  echo "B2_NOTIFICATION_WEBHOOK unset!" >&2
fi

FILENAME="$(date +%Y-%m-%d)".tar.gz
WORKDIR=$(mktemp -d)
trap 'rm -rf "$WORKDIR"' EXIT

# DATABASE_URL is the app's connection string, so this dumps the real application database
pg_dump -Fc "$DATABASE_URL" | gzip > "$WORKDIR/$FILENAME"
B2_OUTPUT=$(b2 file upload --no-progress "$B2_BUCKET" "$WORKDIR/$FILENAME" "$FILENAME")

FILE_URL=$(echo "$B2_OUTPUT" | awk -F': ' '/URL by file name/ {print $2}')
REQUEST_BODY="{\"content\":\"[$FILENAME]($FILE_URL) backed up.\"}"
if [ -n "${B2_NOTIFICATION_WEBHOOK:-}" ]; then
  curl -fsS -H "Content-Type: application/json" -X POST -d "$REQUEST_BODY" "$B2_NOTIFICATION_WEBHOOK"
fi

echo "$REQUEST_BODY" | jq .content
