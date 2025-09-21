FROM postgres:17-alpine

WORKDIR /root
ENV B2_ACCOUNT_INFO="/root/.b2/b2_account_info.sqlite"
RUN apk --no-cache add busybox git python3 pipx curl jq && pipx install b2 && mkdir ./.b2
ENV PATH="$PATH:/root/.local/bin"
ENV TZ="America/New_York"

VOLUME ./.b2

# Copy the backup script into the container
COPY db_backup.sh /usr/local/bin/db_backup.sh

# Grant execute permissions to the script
RUN chmod +x /usr/local/bin/db_backup.sh

# Create a cron job to run the backup script daily at midnight
RUN echo "0 0 * * * /usr/local/bin/db_backup.sh" > /etc/crontabs/root

# Start cron in the foreground
CMD ["crond", "-f"]
