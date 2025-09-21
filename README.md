# Oronder Server

## Installation

1. Create your discord bot (https://discord.com/developers/applications)
    1. On the __OAuth2__ tab, set your redirect URL to `{API_URL}/init`
    2. Also on the __OAuth2__ tab get your `DISCORD_CLIENT_SECRET`.
    3. From the __Bot__ tab, get your `DISCORD_TOKEN`.
2. Copy `docker-compose.yml` and `.env.example` to your server.
3. Rename `.env.example` to `.env`, and fill in values for `DISCORD_TOKEN`, `DISCORD_CLIENT_SECRET`, `API_URL` at
   minimum.
4. Run `docker compose up -d` to start the server.
5. If you have issues, run `docker logs -f oronder_server` to check logs.`
6. Note that the frontend does not currently support alternative backends out of the box. As of now, you will need to
   manually set the api url in the Foundry addon.

## Required Privileged Intent
- Server Member Intent
- Message Content Intent

## Required Scopes
see: https://discord.com/developers/docs/topics/oauth2#shared-resources-oauth2-scopes

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
    - `{API_URL}/init`

### Example SQL queries

#### List pcs from a mission by name
```postgresql
SELECT m.title, pc_id, a.name
FROM missions m
         CROSS JOIN LATERAL JSONB_ARRAY_ELEMENTS_TEXT(m.pcs) AS pc_id
         JOIN actors a ON pc_id = a.id and a.guild_id = m.guild_id
WHERE m.title LIKE '%YOUR_MISSION_TITLE%'
order by a.name;
```

#### Add a pc to a mission
```postgresql
UPDATE missions
SET pcs = pcs || '["ECc9KbEwvqWTXbkW"]'::jsonb
WHERE missions.guild_id = 933858354177118228
  AND missions.title = 'Goblin Caravan';
```

#### List PC info
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

#### force UTC in db
```postgresql
SET timezone = 'UTC';
ALTER TABLE missions
    ALTER date_time TYPE timestamptz,
    ALTER date_time SET DEFAULT now();
```