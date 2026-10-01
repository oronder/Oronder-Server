from functools import cache

from discord import Embed

import dnd
from dnd import strip_template
from utils import getLogger, join_list, truncate

logger = getLogger(__name__)

_schools_of_magic = {
    "T": "transmutation",
    "N": "necromancy",
    "C": "conjuration",
    "A": "abjuration",
    "E": "enchantment",
    "V": "evocation",
    "I": "illusion",
    "D": "divination",
}


@cache
def ensure_loaded():
    """Load the 5e data this module exposes. Called on first use, not at
    import, so importing it for its pure helpers (st_nd_rd_th, _schools_of_magic) never needs the data.
    """
    global spell_names, spells_by_level, spells_by_name, _all_spells
    global _spell_target_data, _spell_source_lookups, _spells_to_class_data
    global _spells_to_classes, _spells_to_subclasses

    # Spell data is one file per source, so the allowed sources decide which
    # files to read. Oldest first: spells_by_name is keyed by name, so a later
    # book's version of a spell replaces an earlier one's.
    sources = dnd.allowed_sources
    _all_spells = []
    loaded = []
    for source in sources:
        try:
            data = dnd.load_json(f"spells/spells-{source.lower()}")
        except dnd.DataUnavailable:
            # Most books have no spell file at all -- adventures, the DMG --
            # so this is ordinary, not a problem worth warning about. Only an
            # empty result withholds /lookup spell.
            logger.debug(f"no spell file for {source}")
            continue
        _all_spells += [
            spell for spell in data["spell"] if spell.get("source") in sources
        ]
        loaded.append(source)

    if not _all_spells:
        raise dnd.DataUnavailable(
            f"no spell data for any of {', '.join(sources)} (DND5E_ALLOWED_SOURCES)"
        )
    logger.info(f"spells loaded from {', '.join(loaded)}")

    # Keyed by name, so a later book's version replaces an earlier one's.
    spells_by_name = {s["name"]: s for s in _all_spells}
    # One entry per spell, not one per printing: with both the 2014 and 2024
    # PHB allowed, most spells appear twice in _all_spells, and these feed
    # autocomplete and the shop's scroll list.
    spell_names = list(spells_by_name)
    _spell_target_data = dnd.load_json("spells/foundry")["spell"]
    _spell_source_lookups = dnd.load_json("generated/gendata-spell-source-lookup")

    _spells_to_class_data = {
        k: v
        for (source, spell_data) in _spell_source_lookups.items()
        if source.upper() in dnd.allowed_sources
        for (k, v) in spell_data.items()
    }

    _spells_to_classes = {
        spell_name: [
            c
            for from_source in v.get("class", v.get("classVariant", {})).values()
            for c in from_source
            if from_source and c in dnd.allowed_classes
        ]
        for (spell_name, v) in _spells_to_class_data.items()
    }

    _spells_to_subclasses = {
        k: v
        for (k, v) in {
            spell_name: {
                k: v
                for (k, v) in {
                    class_name: [
                        subclass
                        for (
                            subclass_source,
                            subclasss_by_source,
                        ) in subclass_data.items()
                        if subclass_source in dnd.allowed_sources
                        for subclass in subclasss_by_source.keys()
                    ]
                    for (class_source, subclasses_by_class) in spell_data[
                        "subclass"
                    ].items()
                    if class_source in dnd.allowed_sources
                    for (class_name, subclass_data) in subclasses_by_class.items()
                }.items()
                if v
            }
            for (spell_name, spell_data) in _spells_to_class_data.items()
            if "subclass" in spell_data
        }.items()
        if v
    }

    spells_by_level = {i: [] for i in range(10)}
    for spell in spells_by_name.values():
        spells_by_level[spell["level"]].append(spell["name"])


def __getattr__(name):
    """Load on first attribute access instead of at import."""
    if name in {"spell_names", "spells_by_level", "spells_by_name"}:
        ensure_loaded()
        return globals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _get_spell_users(spell_name):
    ensure_loaded()
    s = spell_name.lower()
    classes = _spells_to_classes.get(s, [])
    subclass = [
        f"{class_name} ({subclass_name})"
        for (class_name, subclasses) in _spells_to_subclasses.get(s, {}).items()
        if class_name not in classes
        for subclass_name in subclasses
    ]
    return classes + subclass


def st_nd_rd_th(i: int):
    match i:
        case 1:
            return "1st"
        case 2:
            return "2nd"
        case 3:
            return "3rd"
        case _:
            return f"{i}th"


# def _template_pattern_resolver(keyword, content):


def generate_spell_embed(spell_name):
    ensure_loaded()
    s = next(s for s in _all_spells if s["name"] == spell_name)
    classes = ", ".join(_get_spell_users(s["name"]))
    embed = Embed(title=spell_name)

    embed.add_field(
        name="",
        value=f"*{st_nd_rd_th(s['level'])}-level {_schools_of_magic[s['school']]}. ({classes})*",
        inline=False,
    )
    cast_time = "**Casting Time**: " + ", ".join(
        [f"{t['number']} {t['unit']}" for t in s["time"]]
    )

    spell_range = join_list(
        [
            "**Range**:",
            str(s["range"].get("distance", {}).get("amount")),
            s["range"].get("distance", {}).get("type"),
        ],
        " ",
    )

    target_data = next(
        (
            std["system"]
            for std in _spell_target_data
            if s["name"].lower() == std["name"].lower()
            and "target.type" in std.get("system", {})
        ),
        None,
    )

    if target_data:
        target_string = " ".join(
            [
                s
                for s in [
                    str(target_data.get("target.value")),
                    target_data.get("target.units"),
                    target_data.get("target.type"),
                ]
                if s
            ]
        )

        spell_range += f" ({target_string})"

    components = "**Components**: " + ", ".join(
        [k.upper() for (k, v) in s["components"].items() if v]
    )

    dur = (
        ", ".join(
            [
                join_list(
                    [
                        "**Duration**:",
                        "Concentration, up to" if "concentration" in d else None,
                        str(d.get("duration", {}).get("amount")),
                        d.get("duration", {}).get("type", "")
                        + ("s" if d.get("duration", {}).get("amount", 0) > 1 else ""),
                    ],
                    " ",
                )
                for d in s["duration"]
            ]
        )
        or "Instantaneous"
    )

    embed.add_field(
        name="Meta",
        value=truncate("\n".join([cast_time, spell_range, components, dur]), 1024),
        inline=False,
    )

    for n, v, i in dnd.handle_description_entries(s, s["entries"]):
        embed.add_field(name=n, value=v, inline=i)

    for h in s.get("entriesHigherLevel", []):
        # These entries are usually prose, but can include a table or list,
        # which join() cannot take. Let the shared handler render those.
        for name, value, inline in dnd.handle_description_entries(
            s, h["entries"], name=h["name"]
        ):
            embed.add_field(name=name, value=value, inline=inline)

    embed.set_footer(text=f"Spell | {s['source']} {s.get('page', '')}".rstrip())

    return embed
