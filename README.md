# Oronder Server

The backend for [Oronder](https://github.com/oronder/Oronder), a Foundry VTT
module that connects a Foundry world to a Discord server. It is a Discord bot
and an HTTP/socket.io API in one process:

- **Foundry → Discord:** character sync, session and combat events, XP.
- **Discord → Foundry:** `/roll` and `/attack` from Discord, rolled in Foundry
  (or by the bot when Foundry isn't connected).
- **Discord-side play management:** missions and scheduling, downtime, weekly
  roll call polls, rules and item lookups.

The Foundry module talks to this server; players and GMs talk to the bot.

## How it fits together

| service | what it is | required |
|---|---|---|
| `oronder` | FastAPI app + socket.io + py-cord bot, one process | yes |
| `oronder-db` | PostgreSQL | yes |
| `oronder-backup` | nightly database backup to Backblaze B2 | no |
| `watchtower` | pulls new images and restarts the app on release | no |

All four are defined in `docker-compose.yml`. The app publishes its port on
loopback only; a reverse proxy on the same host (Caddy, nginx) terminates TLS
in front of it.

## Running your own instance

> **Current limitation:** the released Foundry module talks to
> `api.oronder.com`. Pointing it at your own server needs a module build with
> your server's URL. Running the server works today; module support for custom
> servers is not there yet.

### 1. Create a Discord application

In the [Discord developer portal](https://discord.com/developers/applications):

- **Bot** — create a bot and copy its token. Enable **Server Members Intent**,
  the only privileged intent the bot needs.
- **OAuth2** — copy the client secret, and add a redirect of exactly
  `<API_URL>/init`, matching scheme, host, port and path. Discord rejects a bare
  LAN hostname with no TLD (`http://myhost:65435/init`); use a real domain or an
  IP address.
- **Invite** the bot with scopes `bot` and `guilds.members.read` and these
  permissions: View Channels, Manage Events, Create Events, Send Messages,
  Create Public Threads, Create Private Threads, Send Messages in Threads,
  Manage Threads, Embed Links, Read Message History, Mention Everyone, Use
  External Emojis, Add Reactions, Create Polls, Connect, Use Voice Activity.

### 2. Configure

```bash
cp .env.example .env
chmod 600 .env
```

Required:

| variable | |
|---|---|
| `DISCORD_TOKEN`, `DISCORD_CLIENT_SECRET` | from step 1 |
| `API_URL` | the public HTTPS URL of this server, e.g. `https://oronder.example.com` |
| `UVICORN_PORT` | port the app listens on |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | database; `DATABASE_URL` is built from these |

Everything else in `.env.example` is optional and documented there: Wiki.js
mirroring, B2 backups, uptime reporting, the admin API, module release
announcements, and a set of Discord ids that switch on features of the
official deployment (supporter roles, super-admin commands). **Leave those ids
unset on your own instance** — unset means the feature is off.

`PUBLISH_ADDRESS` defaults to `127.0.0.1`. Keep it there and put a TLS reverse
proxy in front; setting `0.0.0.0` exposes the API over plain HTTP, including
the auth tokens the module sends.

### 3. Start

`docker-compose.yml` references the official image
(`ghcr.io/chunklighttuna/oronder`) with `pull_policy: always`. To run your own
build, tag it locally and tell compose not to pull over it:

```bash
docker build -t ghcr.io/chunklighttuna/oronder:latest .
docker compose up -d --pull never oronder oronder-db
```

Add `oronder-backup` and `watchtower` if you want them.

### 5e reference data

`/lookup`, `/action` and the downtime item shop read a set of 5e JSON data
files. Point `DND5E_DATA_SOURCE` at a base URL serving them (note the trailing
slash) and they are downloaded to `./data` on first use and cached there:

```
DND5E_DATA_SOURCE=https://example.com/data/
```

Alternatively, populate `./data` yourself and leave the variable unset.

Three more variables decide how much of that data is surfaced. Each is
comma-separated and matched verbatim against the codes in the data, so case
matters; leave any of them blank for the default shown:

| Variable | Default |
| --- | --- |
| `DND5E_ALLOWED_SOURCES` | `XGE,GoS,EGW,TCE,VRGR,FTD,BGG,CoA,BMT,XPHB,XDMG,FRHoF,LFL,EFA,RHW,AU` |
| `DND5E_ALLOWED_CLASSES` | `Artificer,Bard,Cleric,Druid,Monk,Paladin,Ranger,Sorcerer,Warlock,Wizard` |
| `DND5E_DISALLOWED_AGES` | `futuristic,renaissance,modern` |

Narrowing `DND5E_ALLOWED_SOURCES` hides the items, feats, backgrounds and
spells of the excluded books.

Keep the list oldest-to-newest, because order is precedence: where two books
print the same spell, the later one wins.

Most books have no spell file of their own, which is unremarkable and simply
contributes nothing.

The variable is optional and the app starts without it. When a dataset is
neither cached nor fetchable, only the commands that need it are unavailable --
character sync, rolls and everything else are unaffected.

### 4. Pair a Discord server

In Foundry, open the Oronder module settings and use **Connect to Discord**. The
OAuth flow lands on `<API_URL>/init` and links the world to the server.

### Health

- `GET /health` — 200 only if both the HTTP API and the Discord gateway are
  working; this is what the container healthcheck uses.
- `GET /zqaBTpcyxNdiS2uRjC0pl7WP9snUPkZy` — HTTP-only heartbeat, for external
  uptime monitors.

## Development

Python 3.13 and [uv](https://docs.astral.sh/uv/):

```bash
uv sync
PYTHONPATH=src uv run uvicorn main:app --port 65435
```

with the required environment variables set. The Foundry module switches to a
development mode when Foundry runs on port 65434, and then talks to this server
on port 65435.

`uv run ruff check src` lints.

## Deployment (official instance)

- Every push to `main` builds an image with `.github/workflows/deploy.yml` and
  pushes it to GHCR. The job runs on the runner named by the `RUNNER`
  repository variable, falling back to `ubuntu-latest`.
- `watchtower` pulls the new image within five minutes and restarts the app.
- Changes to `docker-compose.yml` or `.env` are not picked up by watchtower:
  run `git pull && docker compose up -d oronder` on the server.
- `deploy/README.md` covers the rollback guard.

## License

MIT — see `LICENSE`.
