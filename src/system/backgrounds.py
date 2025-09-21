import re

from discord import Embed

import system
from utils import capitalize_title, join_list

backgrounds = {
    bg["name"]: bg
    for bg in system.load_json("backgrounds")["background"]
    if bg["source"] in system.legal_sources
}


def get(background, key):
    return background.get(
        key, backgrounds.get(background.get("_copy", {}).get("name"), {}).get(key, None)
    )


def generate_background_embed(background_name):
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
