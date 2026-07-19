# Game system abstraction contract

Oronder spans two codebases: the Foundry module (`Oronder`, JS) and this
server (`foundry_discord`, Python). Both must agree on two wire contracts,
keyed by the Foundry system id found in `world.system` of every actor
upload and in the socket handshake's `world` data:

| Foundry system id | Game                        |
|-------------------|-----------------------------|
| `dnd5e`           | Dungeons & Dragons 5e       |
| `pf2e`            | Pathfinder Second Edition   |
| `CoC7`            | Call of Cthulhu 7e          |

## Contract 1: actor upload (`PUT /actor`)

Common envelope for every system:

```json
{
  "id": "<16-char foundry actor id>",
  "name": "...",
  "discord_ids": [1234],
  "portrait_url": "...",
  "equipment": ["..."],
  "world": {"id": "...", "coreVersion": "...", "system": "dnd5e|pf2e|CoC7", "systemVersion": "..."}
}
```

`world.system` is the discriminator. System payloads add:

### dnd5e (unchanged, existing shape)
`currency{pp,gp,ep,sp,cp}`, `abilities{str..cha}`, `bonuses`, `skills{acr..sur}`,
`tools`, `attributes{hp,ac,prof,spelldc,...}`, `details{level,race,background,xp,...}`,
`traits`, `classes`, `weapons[{type:weapon|spell, attack, attack_modes|level}]`.

### pf2e
```json
{
  "abilities": {"str": {"mod": 4}, "dex": {...}, "con": {...}, "int": {...}, "wis": {...}, "cha": {...}},
  "skills": {"acrobatics": {"mod": 5, "rank": 1}, "...": {}, "lore-warfare": {"mod": 3, "rank": 1, "label": "Warfare Lore"}},
  "attributes": {
    "hp": {"max": 58}, "ac": {"value": 24}, "speed": 25, "class_dc": 19,
    "saves": {"fortitude": {"mod": 9}, "reflex": {"mod": 7}, "will": {"mod": 6}},
    "perception": {"mod": 8}
  },
  "details": {"level": 5, "class": "Fighter", "ancestry": "Dwarf", "heritage": "...", "background": "...", "xp": {"value": 400, "max": 1000}},
  "currency": {"pp": 0, "gp": 10, "sp": 0, "cp": 0},
  "weapons": [{"id": "...", "name": "Warhammer", "type": "weapon", "attack": "1d20 + 11", "img": null}]
}
```
Skill slugs: `acrobatics arcana athletics crafting deception diplomacy
intimidation medicine nature occultism performance religion society stealth
survival thievery` plus dynamic `lore-*` entries. Saves: `fortitude reflex
will`. `perception` is its own stat.

### CoC7
```json
{
  "characteristics": {"str": {"value": 60}, "con": {"value": 55}, "siz": {"value": 65}, "dex": {"value": 70}, "app": {"value": 50}, "int": {"value": 75}, "pow": {"value": 60}, "edu": {"value": 80}},
  "attribs": {"hp": {"value": 12, "max": 12}, "san": {"value": 55, "max": 99}, "mp": {"value": 12, "max": 12}, "lck": {"value": 45}, "db": "+1d4", "mov": 8, "build": 1},
  "skills": [{"id": "...", "name": "Spot Hidden", "value": 65}, {"id": "...", "name": "Firearms (Handgun)", "value": 50}],
  "details": {"occupation": "Private Investigator", "age": 42, "archetype": ""},
  "weapons": [{"id": "...", "name": ".38 Revolver", "type": "weapon", "skill": "Firearms (Handgun)", "value": 50, "damage": "1d10", "img": null}]
}
```
No levels, no XP, no currency sync (v1).

## Contract 2: roll requests (socket `roll` event, server → module)

Common fields: `actor_id`, `discord_id`. The module executes the roll in
Foundry and acks with `{res: "<result string>", ephemeral: bool}`.

### dnd5e (unchanged)
`{"type": "ability|skill|tool|save|init|attack|concentration|death", "stat": "<5e abrv>", "advantage": "Advantage|Disadvantage|null", "item_id", "spell_level", "attack_mode"}`

### pf2e
`{"type": "skill|save|perception|init|attack", "stat": "<skill slug|save slug>", "item_id": "<strike item id for attack>", "advantage": null}`
(pf2e has no advantage; ignore. `init` rolls initiative, usually perception.)

### CoC7
`{"type": "characteristic|skill|attribute|attack", "stat": "<char abrv|skill name|san|lck>", "advantage": "Advantage|Disadvantage|null", "item_id": "<weapon id>"}`
Advantage maps to one bonus die, Disadvantage to one penalty die.
Ack `res` example: `"65 / rolled 43: Regular success"`.

## Server-side layout

`src/systems/` package:
- `base.py` — `GameSystem` ABC: stat vocabulary + classification
  (`classify_stat(name) -> (type, abrv)`), autocomplete lists, local dice
  fallback (`roll_stat(actor, ...)`), xp/level model (optional), sheet
  rendering, actor model class.
- `dnd5e.py` — delegates to the existing `dnd/` package and `models.actor.Actor`.
- `pf2e.py`, `coc7.py` — new models + logic per the payloads above.
- `registry.py` — `get_system(system_id: str) -> GameSystem`, default `dnd5e`
  (legacy rows without a `world`).
- XP model: dnd5e uses the 5e table; pf2e is `level = min(20, floor(xp/1000)+1)`
  with 1000 XP per level; CoC7 has none (session XP flows are skipped).

The `actors` DB table is JSONB throughout; per-system payloads are stored
in the same columns (`abilities`, `skills`, `attributes`, `details`, ...)
with `world.system` deciding how rows are parsed. CoC7 `characteristics`
are stored in the `abilities` column, `attribs` in `attributes`. Do NOT
add or alter DB columns — nest system-specific data inside the existing
JSONB columns instead (there is no migration framework).
