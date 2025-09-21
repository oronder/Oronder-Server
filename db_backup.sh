#!/bin/bash

if [ -z "$B2_APPLICATION_KEY_ID" ] || [ -z "$B2_APPLICATION_KEY" ] || [ -z "$B2_BUCKET" ]; then
  echo "B2_APPLICATION_KEY_ID or B2_APPLICATION_KEY is not set; exiting." >&2
  exit 1
fi

FILENAME="$(date +%Y-%m-%d)".tar.gz
pg_dump -Fc "postgresql://postgres:${POSTGRES_PASSWORD}@${POSTGRES_HOSTNAME}:5432/postgres" | gzip > "$FILENAME"
B2_OUTPUT=$(b2 file upload --no-progress oronder-postgres-backup "$FILENAME" "$FILENAME")
rm "$FILENAME"

FILE_URL=$(echo "$B2_OUTPUT" | awk -F': ' '/URL by file name/ {print $2}')
REQUEST_BODY="{\"content\":\"[$FILENAME]($FILE_URL) backed up.\"}"
if [ -v "$B2_NOTIFICATION_WEBHOOK" ]; then
  curl -H "Content-Type: application/json" -X POST -d "$REQUEST_BODY" "$B2_NOTIFICATION_WEBHOOK"
fi

echo "$REQUEST_BODY" | jq .content
