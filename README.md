## Required Privileged Intent

- current
    - Server Member Intent
    - Message Content Intent

## Required Scopes
see https://discord.com/developers/docs/topics/oauth2#shared-resources-oauth2-scopes
- current
    - guilds.members.read
    - bot
        - General Permissions
            - Read Messages/View Channels
            - Manage Events
            - Create Events
        - Text Permissions
            - Send Message
            - Create Public Threads
            - Create Private Threads
            - Send Messages in Threads
            - Manage Threads
            - Embed Links
            - Read Message History
            - Mention Everyone
            - Use External Emojis
            - Add Reactions
            - Create Polls
        - Voice Permissions
            - Connect #might be needed to record sessions
            - Use Voice Activity #might be needed to tie voice to name
    - Callback
      - https://api.oronder.com/init
- aspirational
    - bot
        - Text Permissions
            - Manage Messages #why?
            - Manage Threads #why??

## Example SQL queries

### List pcs from a mission by name

```postgresql
SELECT m.title, pc_id, a.name
FROM missions m
         CROSS JOIN LATERAL JSONB_ARRAY_ELEMENTS_TEXT(m.pcs) AS pc_id
         JOIN actors a ON pc_id = a.id and a.guild_id = m.guild_id
WHERE m.title LIKE '%YOUR_MISSION_TITLE%'
order by a.name;
```

### Add to a json list

```postgresql
UPDATE missions
SET pcs = pcs || '["ECc9KbEwvqWTXbkW"]'::jsonb
WHERE id = 14;
```

### Edit a string list

```postgresql
UPDATE missions
SET pcs = ARRAY ['q6WXfNlH9kbJ39uE', 'WsAqybtiuUW18bv0', 'ZiU76mzABKwKhsZA', '92G9gG71fDv5Qots']
WHERE missions.guild_id = 933858354177118228
  AND missions.title = 'Goblin Caravan';
```

### List PC info

```postgresql
select name,
       details -> 'level'                                                                        as lvl,
       details -> 'xp' -> 'value'                                                                as xp,
       abilities -> 'str' ->> 'value'                                                            as str,
       abilities -> 'dex' ->> 'value'                                                            as dex,
       abilities -> 'con' ->> 'value'                                                            as con,
       abilities -> 'int' ->> 'value'                                                            as int,
       abilities -> 'wis' ->> 'value'                                                            as wis,
       abilities -> 'cha' ->> 'value'                                                            as cha,
       cast(abilities -> 'str' ->> 'value' as int) + cast(abilities -> 'dex' ->> 'value' as int) +
       cast(abilities -> 'con' ->> 'value' as int) + cast(abilities -> 'int' ->> 'value' as int) +
       cast(abilities -> 'wis' ->> 'value' as int) + cast(abilities -> 'cha' ->> 'value' as int) as stats_total
from actors
where guild_id = 933858354177118228
order by xp desc;
```

### force UTC in db

```postgresql
SET timezone = 'UTC';
ALTER TABLE missions
    ALTER date_time TYPE timestamptz,
    ALTER date_time SET DEFAULT now();
```

# Init Oronder DND Discord server

```postgresql
insert into discord_server
values (933858354177118228,
        933858674458382337,
        1132832487190577212,
        933858538130923571,
        1136895722298552372,
        'exempt',
        'https://oronder.com',
        'TMdiM2LlIA8dwCo_UYbWA-q01dUtU4dArVVF5M2iiiA',
        true);
```

Seed local database after exporting pc data from foundry

```shell
mv -force C:\Users\Chris\Downloads\pc_data.json . ; python3 .\local_db_seed.py
```

Seed database from sheets

```python
missions = get_missions()
guild = self.bot.get_guild(missions[0].guild_id)
if guild:
    with Session() as session:
        threads = next(c for c in guild.channels if c.name == 'adventures').threads
        for mission in get_missions():
            if not mission.event_id:
                scheduled_event: ScheduledEvent = next(
                    (event for event in guild.scheduled_events if event.name == mission.title), None)
                if scheduled_event:
                    mission.event_id = scheduled_event.id
            if not mission.channel_or_thread_id:
                thread: Thread = next((t for t in threads if t.name == mission.title), None)
                if thread:
                    mission.channel_or_thread_id = thread.id
            session.merge(MissionTable.from_model(mission))
        session.commit()
```

seed gm_id from gm

```python
with Session() as session:
    stmt = select(MissionTable).where(MissionTable.gm_id == None)
    missions: List[Mission] = [
        Mission.model_validate(m) for m in session.scalars(stmt).all()
    ]

    manual_lookup = {
        'Riley': 565695135283675147,
        'Corky': 690691669296676914
    }

    for mission in missions:
        guild = self.bot.get_guild(mission.guild_id)
        gm_member = guild.get_member_named(mission.gm)
        logger.info(f"{mission.gm=}\n{gm_member=}")
        mission.gm_id = gm_member.id if gm_member else manual_lookup[mission.gm]
        session.merge(MissionTable.from_model(mission))
        session.commit()
```

## Secrets and configuration

Where to store secrets in this setup (CI builds image, Watchtower pulls updates):

- Runtime (on your server): put them in a .env file that sits next to docker-compose.yml. Docker Compose loads this automatically and injects values into services. Start from .env.example in this repo and copy to .env on the server. Lock it down with chmod 600 and the right owner.
  - App variables: DISCORD_TOKEN, DISCORD_CLIENT_SECRET, API_URL, LOG_LEVEL, WIKIJS_TOKEN, WIKIJS_URL, GITHUB_UPTIME_PAT.
  - Database: POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB for the DB container; DATABASE_URL for the app to connect.
  - Backups: B2_APPLICATION_KEY_ID, B2_APPLICATION_KEY for the backup container.
- CI-only (in GitHub): use GitHub Actions “Secrets and variables → Actions secrets”. In the current workflow, building and pushing to GHCR uses the built-in GITHUB_TOKEN and needs no extra secrets. If you need to access third-party services during CI, add those tokens here.
- Private GHCR pulls (server): either make the package public or log in once on the server so Watchtower/docker can pull. For private images, create a GitHub PAT with read:packages and run: echo <PAT> | docker login ghcr.io -u <OWNER> --password-stdin. This stores creds in ~/.docker/config.json.
- Alternative storage: You can mount a secrets directory and point the app to read from files, or use Docker “secrets” (mainly for Swarm). For plain docker-compose, .env is the simplest and well-supported.

Rotation and hygiene:
- Prefer short-lived tokens where possible; replace and restart with docker compose up -d.
- Never commit .env; only .env.example belongs in git.
- Restrict read access to .env (e.g., chmod 600) and keep backups encrypted.

# Misc

To convert spreadsheet prices to a python dict

s/`regexp ^([^    ]+)    [^    ]+    [^    ]+    ([^    ]+) GP $`/`regexp "$1":$2,`/

then

s/`regexp ([0-9]),([0-9][0-9][0-9])`/`regexp $1$2`/
