import json
import os
import pprint
import re
import urllib.parse
from functools import cache
from math import floor, ceil  # noqa: F401
from pathlib import Path

import httpx

from utils import capitalize_title, getLogger, join_list, truncate
from typing import Optional

logger = getLogger(__name__)

# Base URL serving the 5e JSON data this bot reads (items, spells, rules,
# ...). Anything already cached in ./data is used first, so an instance
# that ships its own data needs no URL at all. When neither is available the
# commands that need that data are simply not registered -- see the callers of
# DataUnavailable -- rather than taking the whole bot down with them.
DND5E_DATA_SOURCE = os.environ.get("DND5E_DATA_SOURCE", "").strip()

DATA_DIR = Path.cwd() / "data"


class DataUnavailable(RuntimeError):
    """A 5e dataset is neither cached locally nor fetchable."""


def load_json(key):
    file = f"{key}.json"
    path = DATA_DIR / file

    if path.is_file():
        logger.info(f"{file} found.")
        with path.open(encoding="utf-8") as f:
            return json.load(f)

    if not DND5E_DATA_SOURCE:
        raise DataUnavailable(
            f"{file} is not cached in {DATA_DIR} and DND5E_DATA_SOURCE is not set."
        )

    response = httpx.get(urllib.parse.urljoin(DND5E_DATA_SOURCE, file))
    if not response.is_success:
        raise DataUnavailable(
            f"{file} could not be fetched from {DND5E_DATA_SOURCE}: "
            f"HTTP {response.status_code}"
        )
    logger.info(f"{file} downloaded.")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.write(response.content)
    return json.loads(response.content)


def _env_set(name: str, default: frozenset[str]) -> frozenset[str]:
    """A comma-separated environment variable as a set, or `default` if unset.

    Values are taken verbatim: they are matched against the codes in the data
    ("PHB", "GoS", "futuristic"), so case matters.
    """
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    return frozenset(v.strip() for v in raw.split(",") if v.strip())


def _env_tuple(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    """Like `_env_set`, but keeps the order given and drops repeats.

    For settings where order decides precedence rather than just membership.
    """
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    return tuple(dict.fromkeys(v.strip() for v in raw.split(",") if v.strip()))


# The same book in its 2014 and 2024 printings, which carry different codes in
# the data. They are alternatives, not additions: an instance runs one edition
# or the other, so listing both is a configuration mistake.
EDITIONS = {"PHB": "XPHB", "DMG": "XDMG", "MM": "XMM"}


def _resolve_editions(sources: tuple[str, ...]) -> tuple[str, ...]:
    """Drop any 2014 code whose 2024 counterpart is also listed."""
    resolved = []
    for source in sources:
        newer = EDITIONS.get(source)
        if newer and newer in sources:
            logger.error(
                f"{source} and {newer} are the same book in different editions; "
                f"ignoring {source}"
            )
            continue
        resolved.append(source)
    return tuple(resolved)


# Which sourcebooks, classes and item ages this instance surfaces. Every
# dataset is filtered through these, so narrowing them hides content and
# widening them reveals whatever the configured data source happens to carry.
#
# Ordered oldest to newest, because order is precedence: where two books print
# the same spell, the later one wins. Listing a book later prefers its version.
#
# The default is the 2024 core rules. Swap XPHB/XDMG for PHB/DMG to run 2014
# instead -- not alongside, see EDITIONS.
allowed_sources = _resolve_editions(
    _env_tuple(
        "DND5E_ALLOWED_SOURCES",
        (
            "XGE",  # 2017-11-21  Xanathar's Guide to Everything
            "GoS",  # 2019-05-21  Ghosts of Saltmarsh
            "EGW",  # 2020-03-17  Explorer's Guide to Wildemount
            "TCE",  # 2020-11-17  Tasha's Cauldron of Everything
            "VRGR",  # 2021-05-18  Van Richten's Guide to Ravenloft
            "FTD",  # 2021-10-26  Fizban's Treasury of Dragons
            "BGG",  # 2023-08-15  Bigby Presents: Glory of the Giants
            "CoA",  # 2023-10-30  Chains of Asmodeus
            "BMT",  # 2023-11-14  The Book of Many Things
            "XPHB",  # 2024-09-17  Player's Handbook (2024)
            "XDMG",  # 2024-11-12  Dungeon Master's Guide (2024)
            "XMM",  # 2025-02-18  Monster Manual (2025)
            "FRHoF",  # 2025-11-11  Forgotten Realms: Heroes of Faerun
            "FRAiF",  # 2025-11-11  Forgotten Realms: Adventures in Faerun
            "NF",  # 2025-11-11  Netheril's Fall
            "LFL",  # 2025-11-18  Lorwyn: First Light
            "EFA",  # 2025-12-09  Eberron: Forge of the Artificer
            "RHW",  # 2026-06-16  Ravenloft: The Horrors Within
            "AU",  # 2026-09-15  Arcana Unleashed
        ),
    )
)

allowed_classes = _env_set(
    "DND5E_ALLOWED_CLASSES",
    frozenset(
        {
            "Artificer",
            "Bard",
            "Cleric",
            "Druid",
            "Monk",
            "Paladin",
            "Ranger",
            "Sorcerer",
            "Warlock",
            "Wizard",
        }
    ),
)
disallowed_ages = _env_set(
    "DND5E_DISALLOWED_AGES", frozenset({"futuristic", "renaissance", "modern"})
)


@cache
def _base_table():
    table = load_json("items-base")
    table["baseitem"] = [
        i
        for i in table["baseitem"]
        if i["source"] in allowed_sources and i.get("age") not in disallowed_ages
    ]
    return table


def ensure_loaded():
    """Load this module's data, raising DataUnavailable if it cannot be read."""
    _base_table()


def available(*modules) -> bool:
    """Whether every given dnd module can read its data.

    Used to decide which commands to register: a bot with no data source
    configured still runs, it just does not offer the commands that would
    need one.
    """
    for module in modules:
        try:
            module.ensure_loaded()
        except DataUnavailable as e:
            logger.warning(f"5e data unavailable: {e}")
            return False
    return True


def __getattr__(name):
    """Resolve `dnd.base_table` on first access instead of at import."""
    if name == "base_table":
        return _base_table()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def evaluate_and_replace_parentheses(expression: str):
    return re.sub(
        pattern=r"\((-?\d+([+\-]\d+)+)\)",  # should only match digits, plus signs and minus signs
        repl=lambda match: str(eval(match.group(1))),
        string=expression,
    )


def clense_damage_roll(dmg: str):
    terms = ["floor", "ceil"]
    out = re.sub(r"\[\w+]", "", dmg)
    while any(term in out for term in terms):
        term = next(term for term in terms if term in out)
        offset = out.index(term)
        count = 0
        last = -1
        for i in range(offset + len(term), len(out)):
            if out[i] == "(":
                count += 1
            elif out[i] == ")":
                count -= 1
            if count == 0:
                last = i
                break
        pre = out[:offset]
        meat = out[offset : last + 1]
        post = out[last + 1 :]
        if "import" in meat or "os." in meat or "Path." in meat:
            raise Exception(f"Don't be cheeky! {meat}")
        out = pre + str(eval(meat)) + post

    out = re.sub(r"\((\d+)\)d", r"\g<1>d", out)  # no_d_parens
    out = re.sub(r"(d\d+)r<(\d+)", r"\g<1>ro<\g<2>", out)  # fix_rerolls
    out = out.replace(" ", "")  # no spaces
    out = evaluate_and_replace_parentheses(out)
    return out


def handle_description_entries(
    entity,
    entries: Optional[list[str | dict]] = None,
    entry: Optional[str] = None,
    name: str = "Description",
    type: str = "",
):
    if not entries and not entry:
        raise ValueError("Either entries or entry must be provided.")
    if not entries and entry:
        entries = [entry]
    out = []
    for idx, e in enumerate(entries):
        if isinstance(e, str):
            template_item_entry = re.sub(
                pattern=r"\{#itemEntry ([^{}]+)}",
                repl=lambda m: join_list(
                    next(
                        (
                            i
                            for i in _base_table()["itemEntry"]
                            if i["name"] == m.group(1)
                        ),
                        {},
                    ).get("entriesTemplate", []),
                    "\n",
                ),
                string=e,
            )

            # TODO how does this bit work??
            template_double_curly = re.sub(
                pattern=r"\{\{item.([^{}]+)}}",
                repl=lambda m: join_list(entity.get(m.group(1)), " ", " and "),
                string=template_item_entry,
            )

            template_equals = re.sub(
                pattern=r"\{=([^{}]+)}",
                repl=lambda m: entity.get(m.group(1), m.group(1)),
                string=template_double_curly,
            )

            value = strip_template(template_equals)
            if type == "list":
                value = f"- {value}"
                name = ""
            elif type == "entries":
                if len([e for e in entries if isinstance(e, str)]) > 1:
                    if idx == 0:
                        out.append([name, "", False])
                    value = f"  {value}"
                else:
                    value = f"**{name}**: {value}"
                name = ""
            elif idx:
                name = ""

            out.append([name, truncate(value, 1024), False])
        elif isinstance(e, dict) and e.get("type") in ["entries", "item"]:
            try:
                out.extend(handle_description_entries(entity, **e))
            except TypeError as err:
                logger.error(str(err))
        elif (
            isinstance(e, dict) and e.get("items") and isinstance(e.get("items"), list)
        ):
            out.extend(
                handle_description_entries(entity, entries=e["items"], type=e["type"])
            )
        else:
            logger.warning(
                f"Unsupported description for item entry:\n{pprint.pformat(e)}\n"
            )

    return out


def strip_template(description):
    pattern = r"{@([^{}]+)}"

    def repl(match):
        keyword, content = match.group(1).split(None, 1)
        bar_split = content.split("|")
        match keyword:
            case "quickref":
                return bar_split[-1] if len(bar_split) > 3 else bar_split[0]
            case "item":
                return bar_split[-1] if len(bar_split) > 2 else bar_split[0]
            case (
                "condition" | "sense" | "status" | "scaledamage" | "scaledice" | "table"
            ):
                return bar_split[-1]
            case "d20":
                return ""
            case _:
                return bar_split[0]

    # Keep searching for "{@...}" occurrences until there are no more matches.
    while re.search(pattern, description):
        description = re.sub(pattern, repl, description, count=1)

    return description


def bare_code(value) -> str:
    """A data code without its book suffix.

    2024 data writes these as "G|XPHB" or {"uid": "2H|XPHB", "note": ...}
    where the older data wrote plain "G" and "2H".
    """
    if isinstance(value, dict):
        value = value.get("uid", "")
    return str(value or "").split("|")[0]


def item_type_name(type_code, default=None):
    """The readable name of an item type code, suffixed or not."""
    return ITEM_TYPE_JSON_TO_ABV.get(bare_code(type_code), default)


ITEM_TYPE_JSON_TO_ABV = {
    "A": "ammunition",
    "AF": "ammunition",
    "AT": "artisan's tools",
    "EM": "eldritch machine",
    "EXP": "explosive",
    "FD": "food and drink",
    "G": "adventuring gear",
    "GS": "gaming set",
    "HA": "heavy armor",
    "INS": "instrument",
    "LA": "light armor",
    "M": "melee weapon",
    "MA": "medium armor",
    "MNT": "mount",
    "MR": "master rune",
    "GV": "generic variant",
    "P": "potion",
    "R": "ranged weapon",
    "RD": "rod",
    "RG": "ring",
    "S": "shield",
    "SC": "scroll",
    "SCF": "spellcasting focus",
    "OTH": "other",
    "T": "tools",
    "TAH": "tack and harness",
    "TG": "trade good",
    "$": "treasure",
    "$A": "art object",
    "$C": "coinage",
    "$G": "gemstone",
    "TB": "trade bar",
    "VEH": "vehicle (land)",
    "SHP": "vehicle (water)",
    "AIR": "vehicle (air)",
    "SPC": "vehicle (space)",
    "WD": "wand",
}

DMGTYPE_JSON_TO_FULL = {
    "A": "acid",
    "B": "bludgeoning",
    "C": "cold",
    "F": "fire",
    "O": "force",
    "L": "lightning",
    "N": "necrotic",
    "P": "piercing",
    "I": "poison",
    "Y": "psychic",
    "R": "radiant",
    "S": "slashing",
    "T": "thunder",
}

SCFTYPE_TO_STR = {
    "arcane": "A sorcerer, warlock, or wizard can use this item as a spellcasting focus.",
    "druid": "A druid can use this item as a spellcasting focus.",
    "holy": "A cleric or paladin can use this item as a spellcasting focus.",
}

ABILITIES = {
    "str": "Strength",
    "dex": "Dexterity",
    "con": "Constitution",
    "int": "Intelligence",
    "wis": "Wisdom",
    "cha": "Charisma",
}
SKILLS = {
    "acr": "Acrobatics",
    "ani": "Animal Handling",
    "arc": "Arcana",
    "ath": "Athletics",
    "dec": "Deception",
    "his": "History",
    "ins": "Insight",
    "itm": "Intimidation",
    "inv": "Investigation",
    "med": "Medicine",
    "nat": "Nature",
    "prc": "Perception",
    "prf": "Performance",
    "per": "Persuasion",
    "rel": "Religion",
    "slt": "Sleight of Hand",
    "ste": "Stealth",
    "sur": "Survival",
}
TOOLS = {
    "art": "Artisan's Tools",
    "alchemist": "Alchemist's Supplies",
    "brewer": "Brewer's Supplies",
    "calligrapher": "Calligrapher's Supplies",
    "carpenter": "Carpenter's Tools",
    "cartographer": "Cartographer's Tools",
    "cobbler": "Cobbler's Tools",
    "cook": "Cook's Utensils",
    "glassblower": "Glassblower's Tools",
    "jeweler": "Jeweler's Tools",
    "leatherworker": "Leatherworker's Tools",
    "mason": "Mason's Tools",
    "painter": "Painter's Supplies",
    "potter": "Potter's Tools",
    "smith": "Smith's Tools",
    "tinker": "Tinker's Tools",
    "weaver": "Weaver's Tools",
    "woodcarver": "Woodcarver's Tools",
    "disg": "Disguise Kit",
    "forg": "Forgery Kit",
    "game": "Gaming Set",
    "chess": "Chess Set",
    "dice": "Dice Set",
    "card": "Playing Cards Set",
    "herb": "Herbalism Kit",
    "music": "Musical Instrument",
    "bagpipes": "Bagpipes",
    "drum": "Drum",
    "dulcimer": "Dulcimer",
    "flute": "Flute",
    "horn": "Horn",
    "lute": "Lute",
    "lyre": "Lyre",
    "panflute": "Pan Flute",
    "shawm": "Shawm",
    "viol": "Viol",
    "navg": "Navigator's Tools",
    "pois": "Poisoner's Kit",
    "thief": "Thieves' Tools",
    "vehicle": "Vehicles",
    "air": "Air Vehicle",
    "land": "Land Vehicle",
    "space": "Space Vehicle",
    "water": "Water Vehicle",
}
OTHER_ROLLABLES = {
    "init": "Initiative",
    "concentration": "Concentration",
    "death": "Death Saving Throw",
}
OTHER_ROLLABLES_NAME_TO_ABRV = {v: k for (k, v) in OTHER_ROLLABLES.items()}

STAT_ABRV_TO_NAME = {**ABILITIES, **SKILLS, **TOOLS, **OTHER_ROLLABLES}

STAT_NAME_TO_ABRV = {v: k for (k, v) in STAT_ABRV_TO_NAME.items()}


def abreviate_stat_name(wide: str):
    return STAT_NAME_TO_ABRV.get(capitalize_title(wide), wide.lower())


def mod_to_str(mod: int):
    if not mod:
        return "0"
    elif mod < 0:
        return str(mod)
    else:
        return f"+{mod}"
