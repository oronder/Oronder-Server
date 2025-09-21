import json
import os
import pprint
import re
import urllib.parse
from math import floor, ceil  # noqa: F401
from pathlib import Path

import httpx

from utils import capitalize_title, getLogger, join_list, truncate
from typing import Optional

logger = getLogger(__name__)

json_data_url = os.getenv("JSON_DATA_URL")
ENABLED = bool(json_data_url)


class Stub:
    depth = 0

    def __getitem__(self, key):
        self.depth += 1

        if self.depth > 100:
            logger.critical(f"gi {key=}")
        return self

    def __setitem__(self, key, value):
        pass

    def __eq__(self, other):
        logger.critical("eq")
        return False

    def get(self, _):
        logger.critical("get")
        return self

    def __contains__(self, item):
        logger.critical("contains")
        return False

    def __hash__(self):
        logger.critical("hash")
        return hash(id(self))

    def __iter__(self):
        return iter([])

    def __getattr__(self, key):
        return self

    def __call__(self, *args, **kwargs):
        return self


stub = Stub()


def load_json(key):
    if not ENABLED:
        return stub
    file = f"{key}.json"
    data_dir = Path.cwd() / "data" / file
    data_dir.parent.mkdir(parents=True, exist_ok=True)

    if data_dir.is_file():
        logger.info(f"{file} found.")
        with data_dir.open(encoding="utf-8") as f:
            return json.load(f)
    else:
        response = httpx.get(urllib.parse.urljoin(json_data_url, file))
        assert response.is_success
        logger.info(f"{file} downloaded.")
        with open(data_dir, "wb") as f:
            f.write(response.content)
        return json.loads(response.content)


legal_sources = {
    "FTD",
    "TCE",
    "PHB",
    "XGE",
    "DMG",
    "BGG",
    "BMT",
    "COA",
    "EGW",
    "GoS",
    "VRGR",
}
legal_classes = {
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

illegal_ages = {"futuristic", "renaissance", "modern"}

base_table = load_json("items-base")

base_table["baseitem"] = [
    i
    for i in base_table["baseitem"]
    if i["source"] in legal_sources and i.get("age") not in illegal_ages
]


def evaluate_and_replace_parentheses(expression: str):
    return re.sub(
        pattern=r"\((-?\d+([+\-]\d+)+)\)",  # should only match digits, plus signs and minus signs
        repl=lambda match: str(eval(match.group(1))),
        string=expression,
    )


def cleanse_damage_roll(dmg: str):
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
                        (i for i in base_table["itemEntry"] if i["name"] == m.group(1)),
                        {},
                    ).get("entriesTemplate", []),
                    "\n",
                ),
                string=e,
            )

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
        description = re.sub(pattern, repl, description, 1)

    return description


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
        return '0'
    elif mod < 0:
        return str(mod)
    else:
        return f'+{mod}'
