from functools import cache
from discord import Embed

import dnd
from utils import getLogger, capitalize_title, join_list

logger = getLogger(__name__)


@cache
def ensure_loaded():
    """Load the 5e data this module exposes. Called on first use, not at
    import, so importing it for anything else in it never needs the data.
    """
    global feats

    feats = {
        feat["name"]: feat
        for feat in dnd.load_json("feats")["feat"]
        if feat["source"] in dnd.allowed_sources
    }


def __getattr__(name):
    """Load on first attribute access instead of at import."""
    if name in {"feats"}:
        ensure_loaded()
        return globals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def generate_feat_embed(feat_name):
    ensure_loaded()
    feat = feats[feat_name]
    embed = Embed(title=feat_name)

    ability_strs = []
    if "ability" in feat:
        for ability in feat["ability"]:
            if "choose" in ability:
                if "entry" in ability["choose"]:
                    ability_strs.append(ability["choose"]["entry"])
                    stats = None
                else:
                    # 'amount' is optional and defaults to 1; most 2024-era
                    # feats omit it, and reading it directly raised KeyError.
                    if ability["choose"].get("amount", 1) != 1:
                        logger.warning(
                            "this is new! Not expecting a feat that grants > 1 ability!"
                        )
                    stats = [
                        dnd.ABILITIES[k] for k in feat["ability"][0]["choose"]["from"]
                    ]
            else:
                # 2024-era blocks look like {"cha": 1, "max": 30}: "max" caps the
                # resulting score and is not an ability, so ignore it here.
                granted = {k: v for k, v in ability.items() if k in dnd.ABILITIES}
                if not all(v == 1 for v in granted.values()):
                    logger.warning(
                        "this is new! Not expecting a feat that grants > 1 ability!"
                    )
                stats = [dnd.ABILITIES[k] for k in granted]

            if stats:
                ability_strs.append(
                    f"Increase your {join_list(stats, ', ', ' or ')} score by 1, to a maximum of 20."
                )

    [
        j.get("entry", j.get("choose", j))
        for i in feats.values()
        if "ability" in i
        for j in i["ability"]
    ]

    if "prerequisite" in feat:
        prereq_strs = []
        for k, v in feat.get("prerequisite")[0].items():
            if "spellcasting" in k and v:
                prereq_strs.append("The ability to cast at least one spell")
            elif k == "race":
                races = [
                    race.get(
                        "displayEntry",
                        capitalize_title(
                            f"{race.get('subrace', '')} {race['name']}".strip()
                        ),
                    )
                    for race in v
                ]
                prereq_strs.append(join_list(races, ", ", " or "))
            elif k == "proficiency":
                for prof in v:
                    for implement, implement_type in prof.items():
                        prereq_strs.append(
                            capitalize_title(
                                f"{k} with {'a ' if implement != 'armor' else ''}{implement_type} {implement}"
                            )
                        )
            elif k == "ability":
                prereq_strs.append(
                    join_list(
                        [
                            f"{dnd.ABILITIES[a_k]}"
                            for ability in v
                            for (a_k, a_v) in ability.items()
                        ],
                        ", ",
                        " or ",
                    )
                    + " "
                    + str(list(v[0].values())[0])
                    + " or higher"
                )
        embed.add_field(
            name="Prerequisite", value=join_list(prereq_strs, ", ", " and ")
        )

    entries = [
        feat["entries"][0],
        {"type": "list", "items": ability_strs},
        *feat["entries"][1:],
    ]

    for n, v, i in dnd.handle_description_entries(feat, entries):
        embed.add_field(name=n, value=v, inline=i)
    # Not every book numbers its pages in the data, so page is optional here
    # as it already is for backgrounds and items.
    embed.set_footer(text=f"Feat | {feat['source']} {feat.get('page', '')}".rstrip())
    return embed
