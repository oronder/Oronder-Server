from discord import Embed

import dnd
from utils import getLogger, capitalize_title, join_list

logger = getLogger(__name__)

feats = {feat['name']: feat for feat in dnd.load_json('feats')['feat'] if feat['source'] in dnd.legal_sources}


def generate_feat_embed(feat_name):
    feat = feats[feat_name]
    embed = Embed(title=feat_name)

    ability_strs = []
    if 'ability' in feat:
        for ability in feat['ability']:
            if 'choose' in ability:
                if 'entry' in ability['choose']:
                    ability_strs.append(ability['choose']['entry'])
                    stats = None
                else:
                    if not ability['choose']['amount'] == 1:
                        logger.warning("this is new! Not expecting a feat that grants > 1 ability!")
                    stats = [dnd.ABILITIES[k] for k in feat['ability'][0]['choose']['from']]
            else:
                if not all(v == 1 for v in ability.values()):
                    logger.warning("this is new! Not expecting a feat that grants > 1 ability!")
                stats = [dnd.ABILITIES[k] for k in ability.keys()]

            if stats:
                ability_strs.append(
                    f"Increase your {join_list(stats, ', ', ' or ')} score by 1, to a maximum of 20."
                )

    [j.get('entry', j.get('choose', j)) for i in feats.values() if 'ability' in i for j in i['ability']]

    if 'prerequisite' in feat:
        prereq_strs = []
        for (k, v) in feat.get('prerequisite')[0].items():
            if 'spellcasting' in k and v:
                prereq_strs.append("The ability to cast at least one spell")
            elif k == 'race':
                races = [
                    race.get('displayEntry',
                             capitalize_title(f"{race.get('subrace', '')} {race['name']}".strip()))
                    for race in v
                ]
                prereq_strs.append(join_list(races, ', ', ' or '))
            elif k == 'proficiency':
                for prof in v:
                    for (implement, implement_type) in prof.items():
                        prereq_strs.append(capitalize_title(
                            f"{k} with {'a ' if implement != 'armor' else ''}{implement_type} {implement}"
                        ))
            elif k == 'ability':
                prereq_strs.append(
                    join_list([
                        f"{dnd.ABILITIES[a_k]}" for ability in v for (a_k, a_v) in ability.items()
                    ], ", ", " or ") + ' ' + str(list(v[0].values())[0]) + ' or higher'
                )
        embed.add_field(
            name="Prerequisite",
            value=join_list(prereq_strs, ", ", " and ")
        )

    entries = [feat['entries'][0], {'type': 'list', 'items': ability_strs}, *feat['entries'][1:]]

    for (n, v, i) in dnd.handle_description_entries(feat, entries):
        embed.add_field(name=n, value=v, inline=i)
    embed.set_footer(text=f"Feat | {feat['source']}  {feat['page']}")
    return embed
