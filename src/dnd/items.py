import html
import random
import re
from typing import Callable

import d20
import httpx
from discord import Embed

import dnd
from dnd.spells import spells_by_level, spells_by_name, spell_names, st_nd_rd_th
from utils import capitalize_title, getLogger, join_list

logger = getLogger(__name__)

SCROLL_OF = "Scroll of"

attack_modes: dict[str, str] = {
    "One-Handed": "oneHanded",
    "Two-Handed": "twoHanded",
    "Offhand": "offhand",
    "Thrown": "thrown",
    "Offhand Throw": "thrown-offhand",
}

attack_modes_reversed = {v: k for k, v in attack_modes.items()}
attack_modes_human: list[str] = list(attack_modes.keys())
attack_modes_machine: list[str] = list(attack_modes.values())

loot_table, variant_table, item_table = [
    dnd.load_json(f) for f in ["loot", "magicvariants", "items"]
]


def get_item_name(generic: dict, variant: dict) -> str:
    prefix = variant.get("inherits", {}).get("namePrefix")
    suffix = variant.get("inherits", {}).get("nameSuffix")
    return join_list([prefix, generic["name"], suffix], "")


generics = [
    g
    for g in dnd.base_table["baseitem"]
    if g["source"] in dnd.legal_sources and g.get("age") not in dnd.illegal_ages
]

variants_dict = {
    get_item_name(g, v): {
        "generic": g["name"],
        "variant": v["name"],
        "rarity": v["inherits"]["rarity"],
    }
    for g in generics
    for v in variant_table["magicvariant"]
    if v["inherits"]["source"] in dnd.legal_sources
    and any(
        all(g.get(k) == v for (k, v) in requirement.items())
        for requirement in v["requires"]
    )
    and ("excludes" not in v or all(g.get(k) != v for (k, v) in v["excludes"].items()))
}

magic_items = [
    (i["name"], i.get("rarity", "unknown"))
    for i in item_table["item"]
    if i["source"] in dnd.legal_sources
    and "$" not in i.get("type", [])
    and i.get("age") not in dnd.illegal_ages
    and i["name"] not in variants_dict.keys()
]

items_to_rarity = dict(
    magic_items
    + [(g["name"], "none") for g in generics]
    + [(k, v["rarity"]) for k, v in variants_dict.items()]
)


def get_item_rarity(item_name):
    if item_name.startswith(SCROLL_OF):
        spell_name = item_name.split(f"{SCROLL_OF} ")[1]
        spell = spells_by_name.get(spell_name)
        if spell:
            match spell["level"]:
                case 0 | 1:
                    return "common"
                case 2 | 3:
                    return "uncommon"
                case 4 | 5:
                    return "rare"
                case 6 | 7 | 8:
                    return "very rare"
                case 9:
                    return "legendary"

    return items_to_rarity.get(item_name)


all_items = items_to_rarity.keys()
shoppable_items = [
    k
    for (k, v) in items_to_rarity.items()
    if v in ["common", "uncommon", "rare", "very rare", "legendary"]
] + [f"{SCROLL_OF} {spell}" for spell in spell_names]

dmg_prices = {
    "common": "50-100",
    "uncommon": "101-500",
    "rare": "501-5,000",
    "very rare": "5,001-50,000",
    "legendary": "50,000+",
}

properties = {
    "F": "Finesse",
    "L": "Light",
    "A": "Ammunition",
    "AF": "Ammunition",
    "2H": "Two-handed",
    "H": "Heavy",
    "R": "Reach",
    "LD": "Loading",
    "RLD": "Reload",
    "T": "Thrown",
    "V": "Versatile",
}


def copper_value_to_human_readable(coppers: int):
    cp = coppers % 10
    sp = int(((coppers - cp) % 100) / 10)
    gp = int(coppers / 100)
    return join_list(
        [
            format_number(f"{gp} gp") if gp else None,
            f"{sp} sp" if sp else None,
            f"{cp} cp" if cp else None,
        ],
        " ",
    )


def get_official_price(item_name, is_consumable=False):
    base_price = next(
        (
            i["value"]
            for i in dnd.base_table["baseitem"] + item_table["item"]
            if i["name"] == item_name and "value" in i
        ),
        None,
    )

    if base_price:
        return copper_value_to_human_readable(base_price)
    else:
        base_cost = 0
        if item_name.startswith(SCROLL_OF):
            spell_name = item_name.split(f"{SCROLL_OF} ")[1]
            spell = spells_by_name.get(spell_name)
            if spell:
                is_consumable = True
                material_component = spell.get("components", []).get("m", [])
                if (
                    isinstance(material_component, dict)
                    and "cost" in material_component
                ):
                    base_cost = int(material_component["cost"] / 100)
        else:
            variant = variants_dict.get(item_name)
            if variant:
                base_cost = int(
                    next(
                        i["value"]
                        for i in dnd.base_table["baseitem"]
                        if i["name"] == variant["generic"]
                    )
                    / 100
                )
            if not is_consumable:
                is_consumable = any(
                    consumable in item_name.casefold()
                    and "shield" not in item_name.casefold()
                    for consumable in ["arrow", "blowgun needle", "crossbow bolt"]
                )

        item_rarity = get_item_rarity(item_name)
        price_from_rarity = dmg_prices.get(item_rarity)

        if not price_from_rarity:
            hardcodes = {"Horizon Puzzle Cube": 5000}
            if item_name in hardcodes:
                return hardcodes[item_name]
            elif item_rarity not in ["artifact", "none", "unknown"]:
                logger.warning(
                    f"Official price for {item_name} with rarity {item_rarity} not found!"
                )
            return None
        return format_number(price_from_rarity, half=is_consumable, base_cost=base_cost)


def format_number(n: str | int | None, denomination="gp", half=False, base_cost=0):
    divisor = 2 if half else 1
    plus = ""

    def inner(p):
        err = not p.isnumeric()
        if err and p.endswith(denomination):
            p = p[: -len(denomination)]
            err = not p.isnumeric()
        if err:
            logger.error(f"Unexpected {denomination} {p}")
            return p
        else:
            return "{:,.0f}".format(int(int(p) / divisor) + base_cost)

    if isinstance(n, int):
        out = "{:,.0f}".format(int(n / divisor) + base_cost)
    elif not n:
        return "Priceless" if denomination == "gp" else n
    else:
        if "+" in n:
            plus = "+"
        n = n.replace(" ", "").replace(",", "").replace("+", "").strip()
        out = "-".join([inner(i) for i in n.split("-")])

    return f"{out}{plus} {denomination}"


def get_item_prices(item_name: str, is_consumable=False):
    spreadsheet_price = (
        f"**{format_number(spreadsheet_items[item_name])}**"
        if item_name in spreadsheet_items
        else None
    )

    five_e_price = five_e_magic_shop_lookup(item_name)
    five_e_price = (
        format_number(five_e_price[0]["price"]) if len(five_e_price) == 1 else None
    )

    return spreadsheet_price, five_e_price, get_official_price(item_name, is_consumable)


def get_item_price_string(item_name: str, consumbable=False):
    spreadsheet_price, five_e_price, dmg_price = get_item_prices(item_name, consumbable)

    return f"{spreadsheet_price} / {five_e_price} / {dmg_price}"


def five_e_magic_shop_lookup(item_name: str):
    url = f"https://5emagic.shop/api/item-lookup?search={html.escape(item_name)}"
    response = httpx.get(url)
    if response.is_success:
        return response.json()
    else:
        logger.error(f"{response.status_code} {response.reason_phrase} {response.url}")
        return [{"price": None}]


def process_roll_table_item(i: dict) -> str:
    """

    :param i: item from `dnd.item_table`
    :return: item name
    """
    item_name = "item not found"

    if "choose" in i:
        if "fromGeneric" in i["choose"]:
            variant = next(
                (
                    v
                    for v in variant_table["magicvariant"]
                    if v["name"] in i["choose"]["fromGeneric"]
                ),
                None,
            )

            if variant:
                results = [
                    base_item
                    for base_item in dnd.base_table["baseitem"]
                    if all(
                        base_item.get(k) != v
                        for (k, v) in variant.get("excludes", {}).items()
                    )
                    and any(
                        all(base_item.get(k) == v for (k, v) in require.items())
                        for require in variant.get("requires", [])
                    )
                ]
                item_name = get_item_name(random.choice(results), variant)
            else:
                item_name = random.choice(
                    [
                        item
                        for group in item_table["itemGroup"]
                        if group["name"] in i["choose"]["fromGeneric"]
                        for item in group["items"]
                    ]
                )
        elif "fromGroup" in i["choose"]:
            group_name = i["choose"]["fromGroup"][0]
            group_items = next(
                g for g in item_table["itemGroup"] if g["name"] == group_name
            )["items"]
            item_name = random.choice(group_items)
        elif "fromItems" in i["choose"]:
            item_name = random.choice(i["choose"]["fromItems"])
        else:
            logger.error(f"{i=}")
    elif "table" in i:
        roll = d20.roll(f"1d{max(e.get('min') for e in i['table'])}").total
        item_name = next(
            e["item"] for e in i["table"] if e["min"] <= roll <= e.get("max", e["min"])
        )
    elif "item" in i:
        item_name = i["item"]

        if "Spell Scroll" in item_name:
            lvl = item_name[item_name.index("(") + 1]
            lvl = 0 if lvl == "C" else int(lvl)
            item_name = f"{SCROLL_OF} {random.choice(spells_by_level[lvl])}"
    else:
        logger.error(f"{i=}")

    return capitalize_title(dnd.strip_template(item_name))


def variant_lookup(variants, potential_base_items, condition):
    return [
        variant
        for variant in variants
        if condition(variant)
        and any(
            any(
                all(base_item.get(k) == v for (k, v) in require.items())
                for require in variant.get("requires", [])
            )
            for base_item in potential_base_items
        )
        and any(
            all(base_item.get(k) != v for (k, v) in variant.get("excludes", {}).items())
            for base_item in potential_base_items
        )
    ]


def base_item_lookup(
    base_items, potential_prefixes, potential_suffixes, condition: Callable
):
    return [
        base_item
        for base_item in base_items
        if condition(base_item)
        and all(  # satisfy at least one prefix, and one suffix. Does that make sense?
            any(  # satisfy at least one variant (ie, suffix or prefix depending on iteration)
                any(  # satisfy one of the sets of requires
                    all(
                        base_item.get(k) == v for (k, v) in require.items()
                    )  # within that requirement, satisfy all
                    for require in variant.get("requires", [])
                )
                for variant in variants
            )
            and any(  # satisfy at least one prefix's excludes if there is one
                all(
                    base_item.get(k) != v
                    for (k, v) in variant.get("excludes", {}).items()
                )
                for variant in variants
            )
            for variants in [potential_prefixes, potential_suffixes]
            if variants
        )
    ]


def get_item(item_name: str):
    item = (
        next(
            (i for i in item_table["item"] if i["name"].lower() == item_name.lower()),
            None,
        )
        or next(
            (
                i
                for i in dnd.base_table["baseitem"]
                if i["name"].lower() == item_name.lower()
            ),
            None,
        )
        or next(
            (
                i
                for i in variant_table["magicvariant"]
                if i["name"].lower() == item_name.lower()
            ),
            None,
        )
    )

    if not item and item_name.startswith(SCROLL_OF):
        spell_name = item_name.split(f"{SCROLL_OF} ")[1]
        spell = spells_by_name.get(spell_name)
        if spell:
            item = {
                **spell,
                "type": "SC",
                "type_string": f"Spell Scroll ({st_nd_rd_th(spell['level'])} Level)",
            }
    elif not item:
        base_items = [
            i
            for i in dnd.base_table["baseitem"]
            if i["name"].lower() in item_name.lower()
        ]
        prefs = variant_lookup(
            variant_table["magicvariant"],
            base_items,
            lambda v: "namePrefix" in v["inherits"]
            and item_name.startswith(v["inherits"]["namePrefix"]),
        )
        suffs = variant_lookup(
            variant_table["magicvariant"],
            base_items,
            lambda v: "nameSuffix" in v["inherits"]
            and item_name.endswith(v["inherits"]["nameSuffix"]),
        )

        while True:
            start_count = len(base_items) + len(prefs) + len(suffs)

            base_items = base_item_lookup(base_items, prefs, suffs, lambda _: True)

            prefs = variant_lookup(
                prefs,
                base_items,
                lambda v: "namePrefix" in v["inherits"]
                and item_name.startswith(v["inherits"]["namePrefix"]),
            )

            suffs = variant_lookup(
                suffs,
                base_items,
                lambda v: "nameSuffix" in v["inherits"]
                and item_name.endswith(v["inherits"]["nameSuffix"]),
            )

            if (
                len(base_items) <= 1
                and len(prefs) <= 1
                and len(suffs) <= 1
                or len(base_items) + len(prefs) + len(suffs) == start_count
            ):
                break

        if len(base_items) == 0:
            return None, "Item not found!"

        base_item = base_items[0]
        pref = prefs[0]["inherits"] if len(prefs) else {}
        suff = suffs[0]["inherits"] if len(suffs) else {}
        item = base_item | pref | suff
        item["entries"] = (
            base_item.get("entries", [])
            + pref.get("entries", [])
            + suff.get("entries", [])
        )

    if "requires" in item:
        category_reqs = [
            k for require in item["requires"] for (k, v) in require.items() if v
        ]
        type_reqs = [
            v
            for require in item["requires"]
            for (k, v) in require.items()
            if k == "type"
        ]
        name_reqs = [
            v
            for require in item["requires"]
            for (k, v) in require.items()
            if k == "name"
        ]
        dmg_type_reqs = [
            v
            for require in item["requires"]
            for (k, v) in require.items()
            if k == "dmgType"
        ]
        category_excl = [
            k for require in item.get("excludes", []) for (k, v) in require.items() if v
        ]
        name_excl = [
            v
            for require in item.get("excludes", [])
            for (k, v) in require.items()
            if k == "name"
        ]

        if not (
            {"crossbow", "bow", "sword", "axe", "weapon", "net"}.isdisjoint(
                category_reqs
            )
            and {"Pike", "Lance"}.isdisjoint(name_reqs)
        ):
            item_type = "Weapon"
        elif not {"A", "AF"}.isdisjoint(type_reqs):
            item_type = "Ammunition"
        elif "armor" in category_reqs or not {"HA", "MA"}.isdisjoint(type_reqs):
            item_type = "Armor"
        else:
            item_type = dnd.ITEM_TYPE_JSON_TO_ABV[item.get("type")]

        item: dict = item["inherits"]

        good = (
            category_reqs
            + [dnd.ITEM_TYPE_JSON_TO_ABV[t] for t in type_reqs]
            + name_reqs
        )
        bad = category_excl + name_excl

        constraints = (
            "("
            + join_list(
                [
                    join_list(
                        [
                            f"any {join_list(good, ', ', ' or ')}"
                            f"{join_list(bad, ', ', ' or ')}"
                        ],
                        ", but not ",
                    ),
                    f"{join_list(dmg_type_reqs, ', ', ' or ')} damage"
                    if len(dmg_type_reqs)
                    else None,
                ],
                " that deals ",
            )
            + ")"
        )

        item["type_string"] = join_list([item_type, constraints], " ")

    elif item.get("staff"):
        item["type_string"] = "Staff"
    elif item.get("weapon") or item.get("weaponCategory"):
        wep_deets = capitalize_title(
            join_list(
                [
                    item.get("weaponCategory"),
                    dnd.ITEM_TYPE_JSON_TO_ABV.get(item.get("type"), "").split(" ")[0],
                ],
                ", ",
            )
        )
        item["type_string"] = f"Weapon: {wep_deets}"
    elif item.get("wondrous") or any(
        ftd_trash in item_name
        for ftd_trash in {"Dragon Vessel", "Dragon-Touched Focus", "Scaled Ornament"}
    ):
        item["type_string"] = "Wonderous Item"
    elif "type_string" not in item:
        item["type_string"] = capitalize_title(
            dnd.ITEM_TYPE_JSON_TO_ABV.get(item.get("type"), "???")
        )

    item["rarity"] = (
        item["rarity"]
        if item.get("rarity", "none") != "none"
        else get_item_rarity(item_name)
    )
    item["consumable"] = item["type_string"] in ["Ammunition", "Potion"] or item[
        "type_string"
    ].startswith("Scroll")

    return item, None


def generate_item_embed(item_name):
    embed = Embed(title=item_name)
    item, error = get_item(item_name)
    if error:
        return None, error

    embed.add_field(
        name="",
        value="*"
        + join_list(
            [
                item["type_string"],
                item["rarity"].capitalize(),
                *[properties[p] for p in item.get("property", []) if p in properties],
            ],
            ", ",
        )
        + "*",
    )

    if "ac" in item:
        dex_bonus = (
            " + Dex"
            if item["type"] == "LA" or item["name"] == "Serpent Scale Armor"
            else " + Dex (max 2)"
            if item["type"] == "MA"
            else ""
        )
        embed.add_field(name="", value=f"AC {item['ac']}{dex_bonus}")

    price_and_weight = join_list(
        [
            next(
                (p for p in get_item_prices(item_name, item["consumable"]) if p), None
            ),
            "1 lb."
            if item.get("weight") == 1
            else f"{item['weight']} lbs."
            if "weight" in item
            else None,
        ],
        ", ",
    )

    if price_and_weight:
        embed.add_field(name="", value=price_and_weight)

    damage_string = join_list(
        [
            join_list([item.get("dmg1"), item.get("bonusWeapon")], ""),
            f"({join_list([item.get('dmg2'), item.get('bonusWeapon')], '')})"
            if "dmg2" in item
            else None,
            dnd.DMGTYPE_JSON_TO_FULL.get(item.get("dmgType")),
        ],
        " ",
    )

    if damage_string:
        embed.add_field(name="", value=damage_string)

    if "reqAttune" in item:
        embed.add_field(
            name="",
            value=join_list(
                [
                    "Requires Attunement",
                    None
                    if item["reqAttune"] is True
                    else dnd.capitalize_title(item["reqAttune"]),
                ],
                " ",
            ),
        )

    if item.get("entries"):
        for n, v, i in dnd.handle_description_entries(item, item["entries"]):
            embed.add_field(name=n, value=v, inline=i)

    if "entries" not in item and item["type_string"].startswith("Weapon"):
        embed.add_field(
            name="Description",
            value=f"Proficiency with a {item_name.lower()} allows you to add your proficiency bonus to the attack roll for any attack you make with it.",
            inline=False,
        )

    embed.set_footer(
        text=f"Item | {join_list([item.get('source'), item.get('page')], ' ')}"
    )
    return embed, None


def calculate_average_damage(damage_string: str) -> float:
    """
    Calculate the average damage from a D&D damage string.

    Args:
        damage_string: String like "1d20+4+3+2d3+1"

    Returns:
        float: Average damage

    Examples:
        >>> calculate_average_damage("1d20+4+3+2d3+1")
        20.5
        >>> calculate_average_damage("2d6+3")
        10.0
        >>> calculate_average_damage("1d8")
        4.5
    """
    # Remove all whitespace
    damage_string = damage_string.replace(" ", "")

    total = 0
    # Find all dice expressions (like 2d6) and flat bonuses
    pattern = r"([+-]?\d+d\d+|[+-]?\d+)"

    for match in re.finditer(pattern, damage_string):
        term = match.group()

        # Handle dice expressions
        if "d" in term:
            # Extract the sign if present, then split the term
            sign = -1 if term.startswith("-") else 1
            num_dice, dice_size = map(int, term.lstrip("+-").split("d"))
            # Average of a die is (min + max) / 2 = (1 + size) / 2
            avg_per_die = (1 + dice_size) / 2
            total += sign * num_dice * avg_per_die

        # Handle flat bonuses/penalties
        else:
            total += int(term)

    return total


spreadsheet_items = {
    "Holy Avenger": 75000,
    "Robe of the Archmagi": 75000,
    "Staff of the Magi": 75000,
    "Armor of Invulnerability": 50000,
    "Belt of Storm Giant Strength": 50000,
    "Cubic Gate": 50000,
    "Iron Flask": 50000,
    "Ring of Djinni Summoning": 50000,
    "Ring of Three Wishes": 50000,
    "Staff of Power": 50000,
    "Vorpal Sword": 50000,
    "Amulet of the Planes": 30000,
    "+3 Armor": 30000,
    "Belt of Cloud Giant Strength": 30000,
    "Defender": 30000,
    "Instant Fortress": 30000,
    "Ioun Stone of Mastery": 30000,
    "Manual of Bodily Health": 30000,
    "Manual of Gainful Exercise": 30000,
    "Manual of Quickness of Action": 30000,
    "+3 Shield": 30000,
    "Tome of Clear Thought": 30000,
    "Tome of Leadership and Influence": 30000,
    "Tome of Understanding": 30000,
    "Dwarven Thrower": 25000,
    "Efreeti Bottle": 25000,
    "Horn of Valhalla, Iron": 25000,
    "Rod of Lordly Might": 25000,
    "Apparatus of the Crab": 20000,
    "Hammer of Thunderbolts": 20000,
    "Helm of Brilliance": 20000,
    "Helm of Teleportation": 20000,
    "Horn of Valhalla, Bronze": 20000,
    "Ring of Elemental Command": 20000,
    "Robe of Stars": 20000,
    "Rod of Security": 20000,
    "Staff of Striking": 20000,
    "Belt of Fire Giant Strength": 15000,
    "Cube of Force": 15000,
    "Horn of Valhalla, Brass": 15000,
    "Instrument of the Bards - Mac-Fuirmidh Cittern": 15000,
    "+3 Weapon": 15000,
    "Dwarven Plate": 13000,
    "Carpet of Flying": 12000,
    "Crystal Ball of Telepathy": 12000,
    "Staff of Thunder and Lightning": 12000,
    "+2 Armor": 10000,
    "Bag of Devouring": 10000,
    "Belt of Frost Giant Strength": 10000,
    "Belt of Stone Giant Strength": 10000,
    "Crystal Ball of Mind Reading": 10000,
    "Crystal Ball of True Seeing": 10000,
    "Horn of Valhalla, Silver": 10000,
    "Mirror of Life Trapping": 10000,
    "Rod of Alertness": 10000,
    "Scimitar of Speed": 10000,
    "+2 Shield": 10000,
    "Spell Scroll (9th)": 10000,
    "Staff of Frost": 10000,
    "Staff of Swarming Insects": 10000,
    "Staff of the Woodlands": 10000,
    "Sun Blade": 10000,
    "Broom of Flying": 8000,
    "Ioun Stone of Regeneration": 8000,
    "Ring of Invisibility": 8000,
    "Ring of Spell Storing": 8000,
    "Robe of Scintillating Colors": 8000,
    "Rod of Absorption": 8000,
    "Spellguard Shield": 8000,
    "Staff of Fire": 8000,
    "Wand of Paralysis": 8000,
    "Wand of Polymorph": 8000,
    "Amulet of Health": 6000,
    "Animated Shield": 6000,
    "Cloak of Displacement": 6000,
    "Crystal Ball": 6000,
    "Ring of Spell Turning": 6000,
    "Ring of Telekinesis": 6000,
    "Scarab of Protection": 6000,
    "Staff of Charming": 6000,
    "Winged Boots": 6000,
    "Alchemy Jug": 5000,
    "Bowl of Commanding Water Elementals": 5000,
    "Brazier of Commanding Fire Elementals": 5000,
    "Censer of Controlling Air Elementals": 5000,
    "Elven Chain": 5000,
    "Flame Tongue": 5000,
    "Folding Boat": 5000,
    "Ioun Stone of Greater Absorption": 5000,
    "Ioun Stone of Reserve": 5000,
    "Mantle of Spell Resistance": 5000,
    "Manual of Golems": 5000,
    "Marvelous Pigments": 5000,
    "Oathbow": 5000,
    "Plate Armor of Etherealness": 5000,
    "Ring of Regeneration": 5000,
    "Ring of Shooting Stars": 5000,
    "Robe of Eyes": 5000,
    "Rod of Rulership": 5000,
    "Spell Scroll (8th)": 5000,
    "Staff of Healing": 5000,
    "Stone of Controlling Earth Elementals": 5000,
    "Wand of Binding": 5000,
    "Wand of Fireballs": 5000,
    "+3 Wand of the War Mage": 5000,
    "+2 Weapon": 5000,
    "Belt of Dwarvenkind": 4000,
    "Belt of Hill Giant Strength": 4000,
    "Bracers of Defense": 4000,
    "Demon Armor": 4000,
    "Figurine of Wondrous Power (Obsidian Steed)": 4000,
    "Wand of Lightning Bolts": 4000,
    "Wings of Flying": 4000,
    "Armor of Vulnerability": 3000,
    "Arrow-Catching Shield": 3000,
    "Boots of Speed": 3000,
    "Cape of the Mountebank": 3000,
    "Cloak of the Bat": 3000,
    "Dancing Sword": 3000,
    "Dimensional Shackles": 3000,
    "Dragon Slayer": 3000,
    "Frost Brand": 3000,
    "Gem of Seeing": 3000,
    "Giant Slayer": 3000,
    "Cloak of Arachnida": 2500,
    "Figurine of Wondrous Power (Bronze Griffon)": 2500,
    "Figurine of Wondrous Power (Marble Elephant)": 2500,
    "Immovable Rod": 2500,
    "Ioun Stone of Fortitude": 2500,
    "Periapt of Proof against Poison": 2500,
    "Ring of Evasion": 2500,
    "Ring of Free Action": 2500,
    "Spell Scroll (7th)": 2500,
    "Bag of Tricks": 2000,
    "Cloak of Protection": 2000,
    "Dragon Scale Mail": 2000,
    "Figurine of Wondrous Power (Ivory Goats)": 2000,
    "Ioun Stone of Absorption": 2000,
    "Ioun Stone of Agility": 2000,
    "Ioun Stone of Insight": 2000,
    "Mace of Disruption": 2000,
    "Mace of Terror": 2000,
    "Portable Hole": 2000,
    "Ring of Protection": 2000,
    "Wand of Fear": 2000,
    "+2 Wand of the War Mage": 2000,
    "Pariapt of Health": 2000,
    "Figurine of Wondrous Power (Ebony Fly)": 1500,
    "Figurine of Wondrous Power (Golden Lions)": 1500,
    "Figurine of Wondrous Power (Serpentine Owl)": 1500,
    "Gauntlets of Ogre Power": 1500,
    "Glamoured Studded Leather": 1500,
    "Headband of Intellect": 1500,
    "Ioun Stone of Intellect": 1500,
    "Ioun Stone of Leadership": 1500,
    "Ioun Stone of Protection": 1500,
    "Ioun Stone of Strength": 1500,
    "Mace of Smiting": 1500,
    "Ring of X-ray Vision": 1500,
    "Robe of Useful Items": 1500,
    "Spell Scroll (6th)": 1500,
    "Staff of Withering": 1500,
    "Stone of Good Luck": 1500,
    "Sword of Wounding": 1500,
    "Wand of Magic Missiles": 1500,
    "+1 All-Purpose Tool": 1200,
    "+1 Amulet of the Devout": 1200,
    "+1 Arcane Grimoire": 1200,
    "+1 Bloodwell Vial": 1200,
    "+1 Moon Sickle": 1200,
    "+1 Rhythm-Maker's Drum": 1200,
    "+1 Rod of the Pact Keeper": 1200,
    "Adamantine Armor": 1000,
    "+3 Ammunition": 1000,
    "+1 Armor": 1000,
    "Armor of Resistance": 1000,
    "Bag of Holding": 1000,
    "Boots of Levitation": 1000,
    "Boots of the Winterlands": 1000,
    "Cloak of the Manta Ray": 1000,
    "Dagger of Venom": 1000,
    "Decanter of Endless Water": 1000,
    "Deck of Illusions": 1000,
    "Gem of Brightness": 1000,
    "Helm of Telepathy": 1000,
    "Horn of Blasting": 1000,
    "Horseshoes of a Zephyr": 1000,
    "Horseshoes of Speed": 1000,
    "Ioun Stone of Awareness": 1000,
    "Ioun Stone of Sustenance": 1000,
    "Iron Bands of Binding": 1000,
    "Oil of Etherealness": 1000,
    "Pearl of Power": 1000,
    "Periapt of Wound Closure": 1000,
    "Potion of Storm Giant Strength": 1000,
    "Ring of Resistance": 1000,
    "Ring of the Ram": 1000,
    "Ring of Warmth": 1000,
    "+1 Shield": 1000,
    "Spell Scroll (5th)": 1000,
    "Staff of the Python": 1000,
    "Wand of Web": 1000,
    "Brooch of Shielding": 800,
    "Cloak of Elvenkind": 800,
    "Goggles of Night": 800,
    "Pipes of Haunting": 800,
    "Shield of Missile Attraction": 800,
    "Wand of Wonder": 800,
    "Gloves of Missile Snaring": 600,
    "Hat of Disguise": 600,
    "Potion of Cloud Giant Strength": 600,
    "Potion of Supreme Healing": 600,
    "Ring of Animal Influence": 600,
    "Ring of Water Walking": 600,
    "Rope of Entanglement": 600,
    "Sword of Life Stealing": 600,
    "Sword of Sharpness": 600,
    "Amulet of Proof Against Detection and Location": 500,
    "Bead of Force": 500,
    "Boots of Elvenkind": 500,
    "Boots of Striding and Springing": 500,
    "Bracers of Archery": 500,
    "Chime of Opening": 500,
    "Circlet of Blasting": 500,
    "Elemental Gem": 500,
    "Eversmoking Bottle": 500,
    "Eyes of Charming": 500,
    "Figurine of Wondrous Power (Onyx Dog)": 500,
    "Figurine of Wondrous Power (Silver Raven)": 500,
    "Handy Haversack": 500,
    "Helm of Comprehending Languages": 500,
    "Javelin of Lightning": 500,
    "Medallion of Thoughts": 500,
    "Mithral Armor": 500,
    "Necklace of Adaptation": 500,
    "Oil of Sharpness": 500,
    "Pipes of the Sewers": 500,
    "Ring of Mind Shielding": 500,
    "Ring of Swimming": 500,
    "Slippers of Spider Climbing": 500,
    "Sovereign Glue": 500,
    "Spell Scroll (4th)": 500,
    "Wand of Enemy Detection": 500,
    "Wand of Magic Detection": 500,
    "+1 Weapon": 500,
    "Berserker Axe": 400,
    "Efficient Quiver": 400,
    "Elixer of Health": 400,
    "Lantern of Revealing": 400,
    "Potion of Fire Giant Strength": 400,
    "Ring of Feather Falling": 400,
    "Trident of Fish Command": 400,
    "+1 Wand of the War Mage": 400,
    "+2 Ammunition": 300,
    "Arrow of Slaying": 300,
    "Eyes of Minute Seeing": 300,
    "Eyes of the Eagle": 300,
    "Gloves of Swimming and Climbing": 300,
    "Periapt of Health": 300,
    "Philter of Love": 300,
    "Potion of Speed": 300,
    "Potion of Superior Healing": 300,
    "Ring of Jumping": 300,
    "Rope of Climbing": 300,
    "Vicious Weapon": 300,
    "Wand of Secrets": 300,
    "Keoghtom's Ointment": 300,
    "Dust of Disappearance": 200,
    "Feather Token": 200,
    "Necklace of Fireballs": 200,
    "Oil of Slipperiness": 200,
    "Potion of Flying": 200,
    "Potion of Frost/Stone Giant Strength": 200,
    "Potion of Gaseous Form": 200,
    "Potion of Heroism": 200,
    "Potion of Invisibility": 200,
    "Spell Scroll (3rd)": 200,
    "Universal Solvent": 200,
    "Wind Fan": 200,
    "Scroll of Protection from Abberations": 200,
    "Dust of Dryness": 100,
    "Dust of Sneezing and Choking": 100,
    "Potion of Clairvoyance": 100,
    "Potion of Greater Healing": 100,
    "Potion of Growth": 100,
    "Potion of Hill Giant Strength": 100,
    "Potion of Mind Reading": 100,
    "Potion of Poison": 100,
    "Potion of Resistance": 100,
    "Restorative Ointment": 100,
    "Spell Scroll (2nd)": 100,
    "Potion of Animal Friendship": 50,
    "Potion of Diminution": 50,
    "Potion of Healing": 50,
    "Potion of Water Breathing": 50,
    "Spell Scroll (1st)": 50,
    "Common Glamerweave": 35,
    "+1 Ammunition": 30,
    "Potion of Climbing": 25,
    "Scroll of Climbing": 25,
    "Potion of Longevity": 650,
    "Potion of Invulnerability": 200,
}
