from discord import Embed

import dnd
from utils import getLogger, join_list

logger = getLogger(__name__)

quick_rules = dnd.load_json("generated/bookref-quick")

sage_advice_compendium = {
    k["name"]: k["entries"]
    for i in dnd.load_json("book/book-sac")["data"][0]["entries"][2]["entries"][2:]
    for j in i["entries"]
    for k in j["entries"]
}
actions = {action["name"]: action for action in dnd.load_json("actions")["action"]}

senses = dnd.load_json("senses")["sense"]
conditions = dnd.load_json("conditionsdiseases")


def generate_rule_embed(rule: str):
    rule = rule.rstrip("...")

    title = None
    fields = []
    footer = None
    if rule.startswith("SAC: "):
        title, ruling = next(
            (dnd.strip_template(k), v)
            for (k, v) in sage_advice_compendium.items()
            if dnd.strip_template(k).startswith(rule[len("SAC: ") :])
        )
        fields = dnd.handle_description_entries(None, ruling, name="")
        footer = "Sage Advice Compendium"

    elif rule.startswith("Property: "):
        prop = next(
            j
            for i in dnd.base_table["itemProperty"]
            if "entries" in i
            for j in i["entries"]
            if j["name"] == rule[len("Property: ") :]
        )
        title = prop["name"]
        fields = dnd.handle_description_entries(None, prop["entries"], name="")
        footer = "Property"

    elif rule.startswith("Action: "):
        title = rule.split(": ")[-1]
        description = actions[title]
        times = [
            t
            if isinstance(t, str)
            else dnd.capitalize_title(
                f"{t['number']} {t['unit'].replace('bonus', 'bonus action')}"
            )
            for t in description.get("time", ["—"])
        ]
        fields = [
            ("Time", join_list(times, "/"), False),
            *dnd.handle_description_entries(
                None, description["entries"], name="Description"
            ),
        ]
        footer = f"Action | {description['source']} {description['page']}"

    elif rule.startswith("Sense: "):
        sense = next(s for s in senses if s["name"] == rule[len("Sense: ") :])
        title = sense["name"]
        fields = dnd.handle_description_entries(None, sense["entries"], name="")
        footer = f"Sense | {sense['source']} {sense['page']}"

    elif any(rule.startswith(a) for a in {"Condition: ", "Status: ", "Disease: "}):
        key_left = rule.split(": ")[0].lower()
        key_right = rule.split(": ")[-1]
        condition = next(s for s in conditions[key_left] if s["name"] == key_right)
        title = condition["name"]
        fields = dnd.handle_description_entries(None, condition["entries"], name="")
        footer = f"Sense | {condition['source']} {condition['page']}"

    elif rule.startswith("Movement: "):
        move = next(
            m
            for m in [
                *quick_rules["data"]["bookref-quick"][4]["entries"][2]["entries"],
                *quick_rules["data"]["bookref-quick"][4]["entries"][:2],
            ]
            if isinstance(m, dict) and m["name"] == rule[len("Movement: ") :]
        )
        title = move["name"]
        fields = dnd.handle_description_entries(None, move["entries"], name="")
        footer = f"Sense | {move['source']} {move['page']}"

    if not title or not len(fields) or not footer:
        return logger.err_msg(f"Rule {rule} not found.")

    embed = Embed(title=title)
    for n, v, i in fields:
        embed.add_field(name=n, value=v, inline=i)
    embed.set_footer(text=footer)
    return {"embed": embed}


lvl_to_xp = {
    1: 0,
    2: 300,
    3: 900,
    4: 2700,
    5: 6500,
    6: 14000,
    7: 23000,
    8: 34000,
    9: 48000,
    10: 64000,
    11: 85000,
    12: 100000,
    13: 120000,
    14: 140000,
    15: 165000,
    16: 195000,
    17: 225000,
    18: 265000,
    19: 305000,
    20: 355000,
}


def get_lvl(xp: int):
    return next(lvl for lvl in reversed(range(1, 21)) if xp >= lvl_to_xp[lvl])
