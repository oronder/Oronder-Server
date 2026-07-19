"""Factories for seeding the database in tests."""

import fake_discord
from database.actor_table import ActorTable
from database import Session
from database.guild_settings_table import GuildSettingsTable
from models.guild_settings import GuildSettings, Subscription
from systems import BaseSystemActor, get_system, system_id_of

TEST_AUTH_TOKEN = "test-auth-token"


def guild_settings(
    guild_id: int = fake_discord.DEFAULT_GUILD_ID,
    auth_token: str = TEST_AUTH_TOKEN,
    **overrides,
) -> GuildSettings:
    values = dict(
        id=guild_id,
        gm_role_id=fake_discord.DEFAULT_GM_ROLE_ID,
        gm_xp=0,
        session_channel_id=fake_discord.DEFAULT_CHANNELS["general"],
        downtime_channel_id=fake_discord.DEFAULT_CHANNELS["downtime"],
        voice_channel_id=fake_discord.DEFAULT_CHANNELS["voice"],
        scheduling_channel_id=fake_discord.DEFAULT_CHANNELS["scheduling"],
        combat_channel_id=fake_discord.DEFAULT_CHANNELS["combat"],
        subscription=Subscription.exempt,
        foundry_hostname="localhost:65434",
        auth_token=auth_token,
        timezone="US/Eastern",
        starting_level=1,
    )
    values.update(overrides)
    gs = GuildSettings(**values)
    GuildSettingsTable.commit(gs)
    return gs


def _rollable(mod: int) -> dict:
    return {"total": mod, "mod": mod}


def _ability(score: int, save: int | None = None, proficient: int = 0) -> dict:
    mod = (score - 10) // 2
    return {
        "value": score,
        "proficient": proficient,
        "saveBonus": 0,
        "checkBonus": 0,
        "save": save if save is not None else mod,
        "dc": 8 + mod,
        "total": mod,
        "mod": mod,
    }


def _skill(mod: int, ability: str, proficient: float = 0) -> dict:
    return {
        "value": proficient,
        "ability": ability,
        "bonus": 0,
        "proficient": proficient,
        "passive": 10 + mod,
        "total": mod,
        "mod": mod,
    }


def dnd5e_actor_payload(
    actor_id: str = "testactor0000001",
    name: str = "Test Hero",
    discord_ids: list[int] | None = None,
    level: int = 3,
) -> dict:
    """A minimal-but-valid dnd5e actor as uploaded by the Foundry module."""
    skills = {
        abrv: _skill(2 if abrv in ("prc", "ath") else 0, "wis")
        for abrv in [
            "acr", "ani", "arc", "ath", "dec", "his", "ins", "itm", "inv",
            "med", "nat", "prc", "prf", "per", "rel", "slt", "ste", "sur",
        ]
    }
    return {
        "id": actor_id,
        "name": name,
        "discord_ids": discord_ids or [fake_discord.DEFAULT_GM_USER_ID],
        "currency": {"pp": 0, "gp": 15, "ep": 0, "sp": 4, "cp": 11},
        "abilities": {
            "str": _ability(16, proficient=1),
            "dex": _ability(12),
            "con": _ability(14),
            "int": _ability(10),
            "wis": _ability(13),
            "cha": _ability(8),
        },
        "bonuses": {
            "mwak": {},
            "rwak": {},
            "msak": {},
            "rsak": {},
            "abilities": {"check": "", "save": "", "skill": ""},
            "spell": {},
        },
        "skills": skills,
        "tools": {},
        "attributes": {
            "hp": {"max": 28},
            "movement": {"walk": 30},
            "attunement": {},
            "senses": {},
            "spellcaster": -1,
            "init": _rollable(1),
            "spellcasting": "",
            "ac": {"value": 16},
            "exhaustion": 0,
            "inspiration": False,
            "prof": 2,
            "spelldc": 10,
            "spellmod": 0,
        },
        "details": {
            "biography": {"value": "", "public": ""},
            "alignment": "NG",
            "background": "Soldier",
            "xp": {"value": 900, "max": 2700},
            "appearance": "",
            "trait": "",
            "ideal": "",
            "bond": "",
            "flaw": "",
            "level": level,
            "race": "Human",
            "dead": False,
            "items": [],
        },
        "traits": {},
        "classes": {
            "fighter": {"levels": level, "hitDice": "d10", "subclass": "Champion"}
        },
        "weapons": [
            {
                "name": "Longsword",
                "id": "weapon0000000001",
                "img": None,
                "type": "weapon",
                "attack": "1d20 + 5",
                "attack_modes": ["oneHanded", "twoHanded"],
            }
        ],
        "equipment": ["Longsword", "Chain Mail"],
        "portrait_url": "https://example.invalid/portrait.png",
        "world": {
            "id": "test-dnd5e",
            "coreVersion": "14.364",
            "system": "dnd5e",
            "systemVersion": "5.3.3",
        },
    }


def pf2e_actor_payload(
    actor_id: str = "pf2eactor0000001",
    name: str = "Test Pathfinder",
    discord_ids: list[int] | None = None,
    level: int = 5,
) -> dict:
    """A minimal-but-valid pf2e actor per SYSTEMS.md contract 1."""
    skill_slugs = [
        "acrobatics", "arcana", "athletics", "crafting", "deception",
        "diplomacy", "intimidation", "medicine", "nature", "occultism",
        "performance", "religion", "society", "stealth", "survival",
        "thievery",
    ]
    skills = {
        slug: {"mod": 5 if slug == "athletics" else 2, "rank": 1 if slug == "athletics" else 0}
        for slug in skill_slugs
    }
    skills["lore-warfare"] = {"mod": 3, "rank": 1, "label": "Warfare Lore"}
    return {
        "id": actor_id,
        "name": name,
        "discord_ids": discord_ids or [fake_discord.DEFAULT_GM_USER_ID],
        "abilities": {
            "str": {"mod": 4},
            "dex": {"mod": 1},
            "con": {"mod": 2},
            "int": {"mod": 0},
            "wis": {"mod": 1},
            "cha": {"mod": -1},
        },
        "skills": skills,
        "attributes": {
            "hp": {"max": 58},
            "ac": {"value": 24},
            "speed": 25,
            "class_dc": 19,
            "saves": {
                "fortitude": {"mod": 9},
                "reflex": {"mod": 7},
                "will": {"mod": 6},
            },
            "perception": {"mod": 8},
        },
        "details": {
            "level": level,
            "class": "Fighter",
            "ancestry": "Dwarf",
            "heritage": "Rock Dwarf",
            "background": "Warrior",
            "xp": {"value": 400, "max": 1000},
        },
        "currency": {"pp": 0, "gp": 10, "sp": 0, "cp": 0},
        "weapons": [
            {
                "id": "pf2eweapon000001",
                "name": "Warhammer",
                "type": "weapon",
                "attack": "1d20 + 11",
                "img": None,
            }
        ],
        "equipment": ["Warhammer", "Full Plate"],
        "portrait_url": "https://example.invalid/pf2e.png",
        "world": {
            "id": "test-pf2e",
            "coreVersion": "14.364",
            "system": "pf2e",
            "systemVersion": "7.7.7",
        },
    }


def coc7_actor_payload(
    actor_id: str = "coc7actor0000001",
    name: str = "Test Investigator",
    discord_ids: list[int] | None = None,
) -> dict:
    """A minimal-but-valid CoC7 actor per SYSTEMS.md contract 1."""
    return {
        "id": actor_id,
        "name": name,
        "discord_ids": discord_ids or [fake_discord.DEFAULT_GM_USER_ID],
        "characteristics": {
            "str": {"value": 60},
            "con": {"value": 55},
            "siz": {"value": 65},
            "dex": {"value": 70},
            "app": {"value": 50},
            "int": {"value": 75},
            "pow": {"value": 60},
            "edu": {"value": 80},
        },
        "attribs": {
            "hp": {"value": 12, "max": 12},
            "san": {"value": 55, "max": 99},
            "mp": {"value": 12, "max": 12},
            "lck": {"value": 45},
            "db": "+1d4",
            "mov": 8,
            "build": 1,
        },
        "skills": [
            {"id": "coc7skill0000001", "name": "Spot Hidden", "value": 65},
            {"id": "coc7skill0000002", "name": "Firearms (Handgun)", "value": 50},
        ],
        "details": {
            "occupation": "Private Investigator",
            "age": 42,
            "archetype": "",
        },
        "weapons": [
            {
                "id": "coc7weapon000001",
                "name": ".38 Revolver",
                "type": "weapon",
                "skill": "Firearms (Handgun)",
                "value": 50,
                "damage": "1d10",
                "img": None,
            }
        ],
        "equipment": [".38 Revolver", "Magnifying Glass"],
        "portrait_url": "https://example.invalid/coc7.png",
        "world": {
            "id": "test-coc7",
            "coreVersion": "14.364",
            "system": "CoC7",
            "systemVersion": "0.10.4",
        },
    }


def seed_actor(
    payload: dict, guild_id: int = fake_discord.DEFAULT_GUILD_ID
) -> BaseSystemActor:
    system = get_system(system_id_of(payload.get("world")))
    actor = system.parse_actor(payload)
    with Session() as session:
        session.merge(ActorTable.from_model(actor, guild_id))
        session.commit()
    return actor
