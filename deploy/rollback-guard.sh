#!/usr/bin/env bash
# Watches the oronder container after watchtower swaps in a new image.
#
# Watchtower pulls and restarts, but it has no notion of whether what it
# started actually works -- a broken image stays up until someone notices.
# This guard runs on a timer and closes that gap:
#
#   healthy and settled  -> remember this image as the last known good one
#   unhealthy, and this container was NEVER healthy since it started
#                        -> a bad deploy: re-pin the last known good image,
#                           recreate the container, stop watchtower, and shout
#   unhealthy after it HAD been healthy
#                        -> not a bad deploy (a Discord outage, the network, a
#                           crash later on): alert only. A rollback would not
#                           fix it, and would block deploys for no reason.
#
# Watchtower is stopped on rollback deliberately: otherwise it would pull the
# same broken :latest again on its next interval and undo the rollback, and the
# container would flap every five minutes. A human restarts it after fixing the
# build:  docker start watchtower
set -euo pipefail

COMPOSE_DIR="${COMPOSE_DIR:-/srv/oronder/foundry_discord}"
SERVICE="${SERVICE:-oronder}"
IMAGE="${IMAGE:-ghcr.io/chunklighttuna/oronder:latest}"
STATE_DIR="${STATE_DIR:-/var/lib/oronder-deploy}"
# How long a container must stay healthy before we trust it enough to record it.
SETTLE_SECONDS="${SETTLE_SECONDS:-180}"

GOOD_FILE="$STATE_DIR/last-known-good"
FAILED_FILE="$STATE_DIR/rolled-back-from"
# "<digest> <startedAt>" of a container start we have observed healthy.
HEALTHY_FILE="$STATE_DIR/seen-healthy"

log() { echo "$(date -Is) $*"; }

# Optional Discord webhook, read from the same .env the compose stack uses.
notify() {
    local msg="$1" webhook=""
    if [ -f "$COMPOSE_DIR/.env" ]; then
        webhook=$(grep -E '^DEPLOY_NOTIFICATION_WEBHOOK=' "$COMPOSE_DIR/.env" | cut -d= -f2- || true)
    fi
    log "$msg"
    [ -n "$webhook" ] || return 0
    curl -fsS -m 10 -H 'Content-Type: application/json' \
        -d "$(printf '{"content": %s}' "$(printf '%s' "$msg" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')")" \
        "$webhook" >/dev/null || log "webhook post failed"
}

mkdir -p "$STATE_DIR"

# Nothing to guard if the container isn't there at all -- a stopped stack is a
# deliberate act, not a failed deploy.
if ! docker inspect "$SERVICE" >/dev/null 2>&1; then
    log "no $SERVICE container; nothing to do"
    exit 0
fi

health=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$SERVICE")
started=$(docker inspect -f '{{.State.StartedAt}}' "$SERVICE")
running=$(docker inspect -f '{{.State.Running}}' "$SERVICE")
# The repo digest is what survives a `docker rmi` of the tag, so it is what we
# pin to. RepoDigests belongs to the IMAGE, not the container, so resolve the
# container's image id first. Falls back to that id for images built on the host.
image_id=$(docker inspect -f '{{.Image}}' "$SERVICE")
digest=$(docker image inspect -f '{{if .RepoDigests}}{{index .RepoDigests 0}}{{else}}{{.Id}}{{end}}' "$image_id")

if [ "$health" = "none" ]; then
    log "container has no healthcheck defined; guard cannot judge it, skipping"
    exit 0
fi

age=$(( $(date +%s) - $(date -d "$started" +%s) ))

case "$health" in
starting)
    log "health=starting age=${age}s; waiting"
    exit 0
    ;;
healthy)
    # Record the start as healthy straight away, before the settle wait: this is
    # what separates a bad deploy from a failure that arrived later.
    printf '%s %s\n' "$digest" "$started" >"$HEALTHY_FILE"
    if [ "$age" -lt "$SETTLE_SECONDS" ]; then
        log "health=healthy but only ${age}s old; waiting for it to settle"
        exit 0
    fi
    if [ "$(cat "$GOOD_FILE" 2>/dev/null || true)" != "$digest" ]; then
        printf '%s\n' "$digest" >"$GOOD_FILE"
        # Keep a local tag so `watchtower --cleanup` can't delete the image we
        # would roll back to.
        docker tag "$digest" "${IMAGE%:*}:last-known-good" 2>/dev/null || true
        rm -f "$FAILED_FILE"
        notify ":white_check_mark: oronder healthy on \`$digest\` — recorded as last known good"
    fi
    exit 0
    ;;
unhealthy)
    : # handled below
    ;;
*)
    log "health=$health running=$running; nothing to do"
    exit 0
    ;;
esac

# --- unhealthy from here down ---

good=$(cat "$GOOD_FILE" 2>/dev/null || true)

# Did this container ever work since it started? Two sources, because the guard
# only looks once a minute and could miss a brief healthy spell: our own marker,
# and docker's record of the last few probe results.
seen_healthy=false
[ "$(cat "$HEALTHY_FILE" 2>/dev/null || true)" = "$digest $started" ] && seen_healthy=true
if docker inspect -f '{{range .State.Health.Log}}{{.ExitCode}} {{end}}' "$SERVICE" 2>/dev/null | grep -qw 0; then
    seen_healthy=true
fi

if [ "$seen_healthy" = true ]; then
    notify ":rotating_light: oronder is unhealthy on \`$digest\`, but this container was healthy earlier in this run -- so this is not a bad deploy and a rollback would not fix it. Left running. Manual intervention needed."
    exit 1
fi

if [ -z "$good" ]; then
    notify ":rotating_light: oronder is unhealthy on \`$digest\` and there is no recorded good image to roll back to. Manual intervention needed."
    exit 1
fi

if [ "$good" = "$digest" ]; then
    notify ":rotating_light: oronder is unhealthy on \`$digest\`, which is the last known good image. Not a bad deploy — something else is wrong. Manual intervention needed."
    exit 1
fi

if [ "$(cat "$FAILED_FILE" 2>/dev/null || true)" = "$digest" ]; then
    log "already rolled back away from $digest; leaving it alone"
    exit 0
fi

notify ":warning: oronder unhealthy on \`$digest\` — rolling back to \`$good\` and stopping watchtower"

# Prefer the copy we kept locally (that is what the :last-known-good tag is
# for) and only reach for the registry if it has gone. A rollback must not
# depend on the registry being reachable during an incident -- and a local
# image id, from a build that never had a registry, cannot be pulled at all.
if ! docker image inspect "$good" >/dev/null 2>&1; then
    log "image not present locally, pulling"
    docker pull "$good" >/dev/null 2>&1 || log "pull failed"
fi
if ! docker image inspect "$good" >/dev/null 2>&1; then
    notify ":rotating_light: cannot roll back: \`$good\` is neither on this host nor pullable. Manual intervention needed."
    exit 1
fi

docker tag "$good" "$IMAGE"
# --pull never so compose uses the image we just re-tagged rather than fetching
# :latest from the registry, which is still the broken build.
docker compose --project-directory "$COMPOSE_DIR" up -d --pull never --force-recreate "$SERVICE"

# Record the bad digest only now that the rollback has actually happened: if it
# failed above we want the next run to try again, not skip it as handled.
printf '%s\n' "$digest" >"$FAILED_FILE"

# Stop watchtower last: if the rollback itself fails we would rather it keep
# running than leave the stack with no updater at all.
docker stop watchtower >/dev/null 2>&1 || log "could not stop watchtower"

notify ":arrows_counterclockwise: oronder rolled back to \`$good\`. Watchtower is stopped — fix the build, then \`docker start watchtower\`."
