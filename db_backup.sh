#!/bin/bash

if [ -z "$B2_APPLICATION_KEY_ID" ]; then
  echo "B2_APPLICATION_KEY_ID is not set; exiting." >&2
  exit 1
fi

if [ -z "$B2_APPLICATION_KEY" ]; then
  echo "B2_APPLICATION_KEY is not set; exiting." >&2
  exit 1
fi

if [ -z "$B2_BUCKET" ]; then
  echo "B2_BUCKET is not set; exiting." >&2
  exit 1
fi

if [ -z "$B2_NOTIFICATION_WEBHOOK" ]; then
  echo "B2_NOTIFICATION_WEBHOOK unset!" >&2
fi

FILENAME="$(date +%Y-%m-%d)".tar.gz
pg_dump -Fc "postgresql://postgres:${POSTGRES_PASSWORD}@oronder-db:5432/postgres" | gzip > "$FILENAME"
B2_OUTPUT=$(b2 file upload --no-progress $B2_BUCKET "$FILENAME" "$FILENAME")
rm "$FILENAME"

FILE_URL=$(echo "$B2_OUTPUT" | awk -F': ' '/URL by file name/ {print $2}')
REQUEST_BODY="{\"content\":\"[$FILENAME]($FILE_URL) backed up.\"}"
if [ -v "$B2_NOTIFICATION_WEBHOOK" ]; then
  curl -H "Content-Type: application/json" -X POST -d "$REQUEST_BODY" "$B2_NOTIFICATION_WEBHOOK"
fi

echo "$REQUEST_BODY" | jq .content
