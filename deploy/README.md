# Deploys

Merging to `main` deploys. No SSH, no manual rebuild.

```
merge to main
  -> GitHub Actions builds the image and pushes ghcr.io/oronder/oronder:latest
  -> watchtower (polls every 300s) pulls it and recreates the container
  -> the container's healthcheck reports on the new build
  -> rollback-guard.sh records it as good, or rolls it back
```

Watchtower has done the first two steps for a while; the label that opts a
container in is already on `oronder` and `oronder-backup` in
`docker-compose.yml`. What it does not do is check whether what it started
actually works. That is what the guard adds.

## The guard

`rollback-guard.sh` runs once a minute from a systemd timer and looks at the
container's health:

- **healthy for longer than `SETTLE_SECONDS`** (default 180) — records the
  image's repo digest in `/var/lib/oronder-deploy/last-known-good` and tags it
  locally as `:last-known-good` so `watchtower --cleanup` can't delete it.
- **unhealthy, and this container was never healthy since it started** — a bad
  deploy: re-pins the good digest, recreates the container from it, and stops
  watchtower.
- **unhealthy after it had been healthy** — not a bad deploy (a Discord outage,
  the network, a crash later on). Alerts and leaves it running, because a
  rollback would not fix it and would block deploys. "Was healthy" comes from
  the guard's own marker or from docker's record of recent probe results.
- **unhealthy on the good image, or with nothing recorded** — leaves it alone
  and alerts. That isn't a bad deploy; rolling back wouldn't fix it.

Watchtower is stopped on rollback on purpose. It would otherwise pull the same
broken `:latest` on its next interval and undo the rollback, flapping the bot
every five minutes. After fixing the build:

```bash
docker start watchtower
```

Set `DEPLOY_NOTIFICATION_WEBHOOK` in `.env` to a Discord webhook URL to get
these events posted to a channel. Without it they only go to the journal.

## One-time install on the server

```bash
cd /srv/oronder/foundry_discord
git pull

# healthcheck + UVICORN_PORT need to be live before the guard is useful
docker compose up -d oronder

sudo cp deploy/oronder-rollback-guard.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now oronder-rollback-guard.timer
```

Check it:

```bash
systemctl list-timers oronder-rollback-guard   # next run
journalctl -u oronder-rollback-guard -n 20     # what it decided
docker inspect -f '{{.State.Health.Status}}' oronder
cat /var/lib/oronder-deploy/last-known-good
```

The first run won't have a good image recorded yet, so let the current
container sit healthy for three minutes before relying on rollback.

## Verifying rollback works

Worth doing once, on purpose, rather than discovering it during an outage:

```bash
# stand up a deliberately broken image under the :latest tag
docker tag ghcr.io/oronder/oronder:latest oronder:backup-of-latest
printf 'FROM ghcr.io/oronder/oronder:latest\nCMD ["false"]\n' \
  | docker build -t ghcr.io/oronder/oronder:latest -
docker compose up -d --pull never --force-recreate oronder

# within ~2 minutes the guard should roll back and stop watchtower
journalctl -u oronder-rollback-guard -f
```

## Still manual

- **Changes to `docker-compose.yml` itself** — watchtower swaps images, not
  compose config. Those need a `git pull && docker compose up -d` on the box.
- **`.env` changes** — same.
- **Postgres major version bumps** — see `db_restore.md`.

