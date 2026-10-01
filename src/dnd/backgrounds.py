import re
from functools import cache

from discord import Embed

import dnd
from utils import capitalize_title, join_list


@cache
def ensure_loaded():
    """Load the 5e data this module exposes. Called on first use, not at
    import, so importing it for anything else in it never needs the data.
    """
    global backgrounds

    backgrounds = {
        bg["name"]: bg
        for bg in dnd.load_json("backgrounds")["background"]
        if bg["source"] in dnd.allowed_sources
    }


def __getattr__(name):
    """Load on first attribute access instead of at import."""
    if name in {"backgrounds"}:
        ensure_loaded()
        return globals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def get(background, key):
    ensure_loaded()
    return background.get(
        key, backgrounds.get(background.get("_copy", {}).get("name"), {}).get(key, None)
    )


def generate_background_embed(background_name):
    ensure_loaded()
    background = backgrounds[background_name]
    embed = Embed(title=background_name)
    skills = get(background, "skillProficiencies")
    if skills:
        embed.add_field(
            name="Skill Proficiencies",
            value=join_list(
                [
                    f"{capitalize_title(k)}{'' if isinstance(v, bool) else f' {v}'}"
                    for to_flatten in skills
                    for (k, v) in to_flatten.items()
                ],
                ", ",
                " and ",
            ),
            inline=False,
        )

    tools = get(background, "toolProficiencies")
    if tools:
        embed.add_field(
            name="Tool Proficiencies",
            value=join_list(
                [
                    join_list(
                        [
                            capitalize_title(re.sub(r"(?<!^)(?=[A-Z])", " ", s))
                            for s in v["from"]
                        ],
                        ", ",
                        " or ",
                    )
                    if k == "choose"
                    else capitalize_title(re.sub(r"(?<!^)(?=[A-Z])", " ", k))
                    for to_flatten in tools
                    for (k, v) in to_flatten.items()
                ],
                ", ",
                " and ",
            ).replace("Artisans", "Artisan's"),
            inline=False,
        )

    languages = get(background, "languageProficiencies")
    if languages:
        embed.add_field(
            name="Languages",
            value=join_list(
                [
                    capitalize_title(k)
                    if isinstance(v, bool)
                    else f"{v} of {capitalize_title(re.sub(r'(?<!^)(?=[A-Z])', ' ', k))}"
                    for to_flatten in languages
                    for (k, v) in to_flatten.items()
                ],
                ", ",
                " and ",
            ),
            inline=False,
        )
    feats = get(background, "feats")
    if feats:
        embed.add_field(
            name="Feat",
            value=join_list(
                [
                    capitalize_title(feat.split("|")[0])
                    for to_flatten in feats
                    for feat in to_flatten
                ],
                ", ",
                " or ",
            ),
            inline=False,
        )
    page = f" {background['page']}" if "page" in background else ""
    embed.set_footer(text=f"Background | {background['source']}{page}")

    return embed
