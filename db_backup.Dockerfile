FROM postgres:17-alpine

# Install dependencies in a single layer
RUN apk add --no-cache busybox git python3 pipx curl jq && \
    pipx install b2 && \
    mkdir -p /root/.b2 && \
    chmod +x /usr/local/bin/db_backup.sh 2>/dev/null || true

# Set environment variables
ENV B2_ACCOUNT_INFO="/root/.b2/b2_account_info.sqlite" \
    PATH="$PATH:/root/.local/bin" \
    TZ="America/New_York"

VOLUME /root/.b2

# Copy and setup backup script
COPY --chmod=755 db_backup.sh /usr/local/bin/db_backup.sh

# Configure cron job
RUN echo "0 0 * * * /usr/local/bin/db_backup.sh" > /etc/crontabs/root

CMD ["crond", "-f"]