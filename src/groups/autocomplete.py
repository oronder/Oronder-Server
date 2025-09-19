import difflib
import itertools
from datetime import timedelta
from typing import Callable

from discord import AutocompleteContext, ApplicationContext
from pydantic import TypeAdapter
from sqlalchemy import select, func, or_, any_, and_
from sqlalchemy.exc import ArgumentError

import dnd
from database import Session, CampaignTable, XpAdjustmentsTable
from database.actor_table import ActorTable
from database.guild_settings_table import GuildSettingsTable
from database.missions import MissionTable
from dnd import spells, ABILITIES, SKILLS, OTHER_ROLLABLES, rules
from dnd.backgrounds import backgrounds
from dnd.items import attack_modes_reversed
from models.actor import Tools, Details, Actor, Attack, Spell
from utils import timezones, chris_discord_id, getLogger, truncate

logger = getLogger(__name__)

character_not_found = ["Character not found!"]
game_not_found = ["You must first select a game"]


def gm_xp_actor_autocomplete(ctx: AutocompleteContext):
    guild_settings = GuildSettingsTable.lookup(ctx.interaction.guild_id)
    if not guild_settings.gm_xp:
        return ["No GM XP"]

    stmt = (
        select(ActorTable.name)
        .where(ActorTable.name.icontains(ctx.value))
        .where(ctx.interaction.user.id == any_(ActorTable.discord_ids))
        .where(ActorTable.guild_id == ctx.interaction.guild_id)
        .limit(25)
    )

    with Session() as session:
        actor_names = session.scalars(stmt)

    return actor_names


def join_actor_autocomplete(ctx: AutocompleteContext):
    if not ctx.options.get("game", None):
        return game_not_found

    with Session() as session:
        current_actor_ids = (
            session.query(MissionTable.pcs)
            .filter_by(title=ctx.options["game"], guild_id=ctx.interaction.guild_id)
            .scalar()
        )

        stmt = (
            select(ActorTable.name)
            .where(ActorTable.guild_id == ctx.interaction.guild_id)
            .where(ctx.interaction.user.id == any_(ActorTable.discord_ids))
        )

        if current_actor_ids:
            stmt = stmt.where(~ActorTable.id.in_(current_actor_ids))

        stmt = stmt.where(ActorTable.name.icontains(ctx.value)).limit(25)

        return session.scalars(stmt).all()


def standby_actor_autocomplete(ctx: AutocompleteContext):
    if not ctx.options.get("game", None):
        return game_not_found

    with Session() as session:
        current_actor_ids = (
            session.query(MissionTable.pcs_standby)
            .filter_by(title=ctx.options["game"], guild_id=ctx.interaction.guild_id)
            .scalar()
        )

        stmt = (
            select(ActorTable.name)
            .where(ActorTable.guild_id == ctx.interaction.guild_id)
            .where(ctx.interaction.user.id == any_(ActorTable.discord_ids))
        )

        if current_actor_ids:
            stmt = stmt.where(~ActorTable.id.in_(current_actor_ids))

        stmt = stmt.where(ActorTable.name.icontains(ctx.value)).limit(25)

        return session.scalars(stmt).all()


def actor_autocomplete(ctx: AutocompleteContext):
    # noinspection PyTypeChecker,PyUnresolvedReferences
    stmt = (
        select(ActorTable.name)
        .where(
            and_(
                ctx.interaction.user.id == any_(ActorTable.discord_ids),
                ActorTable.guild_id == ctx.interaction.guild_id,
                ActorTable.name.icontains(ctx.value),
            )
        )
        .limit(25)
    )

    with Session() as session:
        actor_names = session.scalars(stmt)

    return actor_names or character_not_found


def actor_gm_autocomplete(ctx: AutocompleteContext):
    # noinspection PyTypeChecker,PyUnresolvedReferences
    stmt = (
        select(ActorTable.name)
        .where(
            and_(
                ActorTable.guild_id == ctx.interaction.guild_id,
                ActorTable.name.icontains(ctx.value),
            )
        )
        .limit(25)
    )

    with Session() as session:
        actor_names = session.scalars(stmt)

    return actor_names or character_not_found


def xp_adjustment_comment_autocomplete(ctx: AutocompleteContext):
    stmt = (
        select(XpAdjustmentsTable.comment)
        .join(
            ActorTable,
            and_(
                ActorTable.id == XpAdjustmentsTable.actor_id,
                ActorTable.guild_id == ctx.interaction.guild_id,
                ActorTable.name.icontains(ctx.options["character"]),
            ),
        )
        .filter(XpAdjustmentsTable.guild_id == ctx.interaction.guild_id)
        .limit(25)
    )

    with Session() as session:
        adjustments = session.scalars(stmt).all()

    return adjustments


def campaign_remove_pc_autocomplete(ctx: AutocompleteContext):
    try:
        with Session() as session:
            current_actor_ids = (
                session.query(CampaignTable.actor_ids)
                .filter_by(
                    name=ctx.options["campaign"], guild_id=ctx.interaction.guild_id
                )
                .scalar()
            )

            stmt = (
                select(ActorTable.name)
                .where(
                    and_(
                        ActorTable.guild_id == ctx.interaction.guild_id,
                        ActorTable.id.in_(current_actor_ids),
                        ActorTable.name.icontains(ctx.value),
                    )
                )
                .limit(25)
            )

            out = session.scalars(stmt).all()
    except ArgumentError as e:
        logger.error(str(e), stack_info=True)
        out = []

    return out


def campaign_add_autocomplete(ctx: AutocompleteContext):
    try:
        with Session() as session:
            actor_ids_with_campaigns = [
                j
                for i in session.query(CampaignTable.actor_ids)
                .filter_by(guild_id=ctx.interaction.guild_id)
                .all()
                for j in i[0]
            ]

            stmt = (
                session.query(ActorTable.name)
                .where(
                    and_(
                        ActorTable.guild_id == ctx.interaction.guild_id,
                        ~ActorTable.id.in_(actor_ids_with_campaigns),
                        ActorTable.name.icontains(ctx.value),
                    )
                )
                .limit(25)
            )
            out = session.scalars(stmt).all()
    except ArgumentError as e:
        logger.error(str(e), stack_info=True)
        out = []

    return out


def attack_autocomplete(ctx: AutocompleteContext):
    if not ctx.options["character"]:
        return character_not_found
    # noinspection PyTypeChecker
    stmt = select(ActorTable.weapons).where(
        and_(
            ActorTable.name == ctx.options["character"],
            any_(ActorTable.discord_ids) == ctx.interaction.user.id,
            ActorTable.guild_id == ctx.interaction.guild_id,
        )
    )

    with Session() as session:
        attacks = session.scalars(stmt).one_or_none()

    return search(ctx.value, [Attack.model_validate(w).name for w in attacks], sorted)


def detail_autocomplete(ctx: AutocompleteContext):
    if not ctx.options["character"]:
        return character_not_found
    # noinspection PyTypeChecker
    stmt = select(ActorTable.details).where(
        and_(
            ActorTable.name == ctx.options["character"],
            ctx.interaction.user.id == any_(ActorTable.discord_ids),
            ActorTable.guild_id == ctx.interaction.guild_id,
        )
    )

    with Session() as session:
        details = Details.model_validate(session.scalars(stmt).one_or_none())

    return (
        search(
            ctx.value,
            [
                f"{i.type.capitalize()}: {i.name}"
                for i in Details.model_validate(details).items
            ],
            sorted,
        )
        if details
        else []
    )


def detail_gm_autocomplete(ctx: AutocompleteContext):
    if not ctx.options["character"]:
        return character_not_found
    # noinspection PyTypeChecker
    stmt = select(ActorTable.details).where(
        and_(
            ActorTable.name == ctx.options["character"],
            ctx.interaction.user.id != any_(ActorTable.discord_ids),
            ActorTable.guild_id == ctx.interaction.guild_id,
        )
    )

    with Session() as session:
        details = session.scalars(stmt).one_or_none()

    return (
        search(
            ctx.value,
            [
                f"{i.type.capitalize()}: {i.name}"
                for i in Details.model_validate(details).items
            ],
            sorted,
        )
        if details
        else []
    )


def spell_level_autocomplete(ctx: AutocompleteContext):
    if not ctx.options["character"]:
        return character_not_found
    # noinspection PyTypeChecker
    stmt = select(ActorTable).where(
        ActorTable.name == ctx.options["character"],
        any_(ActorTable.discord_ids) == ctx.interaction.user.id,
        ActorTable.guild_id == ctx.interaction.guild_id,
    )
    with Session() as session:
        res = session.scalar(stmt)
    actor = Actor.model_validate(res)
    spellcaster_lvl = actor.attributes.spellcaster
    if spellcaster_lvl < 0:
        return []

    spell = next(
        (
            s
            for s in actor.weapons
            if s.name == ctx.options["weapon"] and isinstance(s, Spell) and s.level
        ),
        None,
    )
    return [i for i in range(spell.level, spellcaster_lvl + 1)] if spell else []


def attack_mode_autocomplete(ctx: ApplicationContext):
    if not ctx.options["character"]:
        return character_not_found

    stmt = select(ActorTable.weapons).where(
        ActorTable.name == ctx.options["character"],
        ActorTable.guild_id == ctx.interaction.guild_id,
        any_(ActorTable.discord_ids) == ctx.interaction.user.id,
    )

    with Session() as session:
        weapons = session.scalars(stmt).one()

    weapon = next((w for w in weapons if w["name"] == ctx.options["weapon"]), {})
    return [attack_modes_reversed[m] for m in weapon.get("attack_modes", [])]


def skill_autocomplete(ctx: AutocompleteContext):
    return search(ctx.value, SKILLS.values(), sorted)


def stat_autocomplete(ctx: AutocompleteContext):
    if not ctx.options["character"]:
        return character_not_found

    # noinspection PyTypeChecker
    stmt = select(ActorTable.tools).where(
        and_(
            ActorTable.name.icontains(ctx.options["character"]),
            ctx.interaction.user.id == any_(ActorTable.discord_ids),
            ActorTable.guild_id == ctx.interaction.guild_id,
        )
    )

    with Session() as session:
        res = session.scalars(stmt).one_or_none()

    if not res:
        return character_not_found

    tools = TypeAdapter(Tools).validate_python(res)
    stats = [
        *ABILITIES.values(),
        *SKILLS.values(),
        *OTHER_ROLLABLES.values(),
        *tools.known_tool_strings(),
    ]
    return search(ctx.value, stats, sorted)


def rule_autocomplete(ctx: AutocompleteContext):
    item_properties = {
        f"Property: {j['name']}"
        for i in dnd.base_table["itemProperty"]
        if "entries" in i
        for j in i.get("entries")
    }

    senses = [f"Sense: {sense['name']}" for sense in rules.senses]

    actions = {f"Action: {k}" for (k, v) in rules.actions.items()}

    conditions = {
        f"Condition: {s['name']}"
        for s in rules.conditions["condition"]
        if s["source"] in dnd.legal_sources
    }
    statuses = {
        f"Status: {s['name']}"
        for s in rules.conditions["status"]
        if s["source"] in dnd.legal_sources
    }
    diseases = {
        f"Disease: {s['name']}"
        for s in rules.conditions["disease"]
        if s["source"] in dnd.legal_sources
    }

    movement = [
        i["name"]
        for i in rules.quick_rules["data"]["bookref-quick"][4]["entries"][2]["entries"]
        if "name" in i
    ] + [
        i["name"] for i in rules.quick_rules["data"]["bookref-quick"][4]["entries"][:2]
    ]

    movement = [f"Movement: {m}" for m in movement]

    sage_advice = [
        f"SAC: {dnd.strip_template(sa)}" for sa in rules.sage_advice_compendium.keys()
    ]

    return search(
        ctx.value,
        [
            *item_properties,
            *senses,
            *actions,
            *conditions,
            *statuses,
            *diseases,
            *movement,
            *sage_advice,
        ],
        sorted,
    )


def action_autocomplete(ctx: AutocompleteContext):
    return search(ctx.value, list(rules.actions.keys()), sorted)


def timezone_autocomplete(ctx: AutocompleteContext):
    return search(ctx.value, timezones, None)


def campaign_autocomplete(ctx: AutocompleteContext):
    with Session() as session:
        campaign_names = (
            session.query(CampaignTable.name)
            .filter(CampaignTable.name.istartswith(ctx.value))
            .filter_by(guild_id=ctx.interaction.guild_id)
            .limit(25)
            .all()
        )

        return [n[0] for n in campaign_names]


def mission_info_autocomplete(ctx: AutocompleteContext):
    stmt = (
        select(MissionTable.title)
        .where(MissionTable.guild_id == ctx.interaction.guild_id)
        .where(MissionTable.title.istartswith(ctx.value))
        .order_by(MissionTable.date_time.desc())
        .limit(25)
    )

    with Session() as session:
        return session.scalars(stmt).all()


def mission_edit_autocomplete(ctx: AutocompleteContext):
    stmt = (
        select(MissionTable.title)
        .where(
            and_(
                MissionTable.guild_id == ctx.interaction.guild_id,
                MissionTable.title.istartswith(ctx.value),
            )
        )
        .order_by(MissionTable.date_time.desc())
        .limit(25)
    )

    if ctx.interaction.user.id != chris_discord_id:
        stmt = stmt.where(MissionTable.gm_id == ctx.interaction.user.id)
    with Session() as session:
        return session.scalars(stmt).all()


def mission_cancel_autocomplete(ctx: AutocompleteContext):
    stmt = (
        select(MissionTable.title)
        .where(
            and_(
                MissionTable.guild_id == ctx.interaction.guild_id,
                MissionTable.gm_id == ctx.interaction.user.id,
                MissionTable.title.istartswith(ctx.value),
                MissionTable.date_time > func.now() - timedelta(hours=6),
            )
        )
        .order_by(MissionTable.date_time.desc())
        .limit(25)
    )

    with Session() as session:
        missions = session.scalars(stmt).all()

    return missions


def missions_without_xp_or_gold_autocomplete(ctx: AutocompleteContext):
    stmt = (
        select(MissionTable.title)
        .where(
            and_(
                MissionTable.guild_id == ctx.interaction.guild_id,
                MissionTable.gm_id == ctx.interaction.user.id,
                MissionTable.title.istartswith(ctx.value),
                or_(MissionTable.xp.is_(None), MissionTable.gold.is_(None)),
            )
        )
        .order_by(MissionTable.date_time.desc())
        .limit(25)
    )

    with Session() as session:
        return session.scalars(stmt).all()


def mission_join_autocomplete(ctx: AutocompleteContext):
    stmt = select(MissionTable.title).where(
        and_(
            MissionTable.guild_id == ctx.interaction.guild_id,
            MissionTable.title.istartswith(ctx.value),
            MissionTable.gm_id != ctx.interaction.user.id,
        )
    )

    if not ctx.options.get("past", False):
        stmt = stmt.where(MissionTable.date_time > func.now() - timedelta(hours=6))
    stmt = stmt.order_by(MissionTable.date_time.desc())

    with Session() as session:
        actor_ids = [
            a[0]
            for a in session.query(ActorTable.id)
            .where(
                and_(
                    ActorTable.guild_id == ctx.interaction.guild_id,
                    ctx.interaction.user.id == any_(ActorTable.discord_ids),
                )
            )
            .all()
        ]
        if not actor_ids:
            return ["Cannot join a game without a Foundry VTT character."]

        existing_actors = [
            pair
            for actor_id in actor_ids
            for pair in [
                MissionTable.pcs.any(actor_id),
                MissionTable.pcs_standby.any(actor_id),
            ]
        ]

        stmt = stmt.where(
            or_(
                func.coalesce(func.array_length(MissionTable.pcs, 1), 0)
                < MissionTable.max_pc_count,
                *existing_actors,
            )
        ).limit(25)

        return session.scalars(stmt).all()


def mission_remove_autocomplete(ctx: AutocompleteContext):
    stmt = select(MissionTable).where(
        and_(
            MissionTable.guild_id == ctx.interaction.guild_id,
            MissionTable.title.istartswith(ctx.value),
        )
    )
    if not ctx.options.get("past", False):
        stmt = stmt.where(MissionTable.date_time > func.now() - timedelta(hours=6))
    with Session() as session:
        actor_ids = {
            a[0]
            for a in session.query(ActorTable.id)
            .where(
                and_(
                    ActorTable.guild_id == ctx.interaction.guild_id,
                    ctx.interaction.user.id == any_(ActorTable.discord_ids),
                )
            )
            .all()
        }
        missions = session.scalars(stmt.order_by(MissionTable.date_time.desc())).all()

        return [m.title for m in missions if actor_ids.intersection(m.pcs)]


def background_autocomplete(ctx: AutocompleteContext):
    return search(ctx.value, backgrounds.keys(), sorted)


def spell_autocomplete(ctx: AutocompleteContext):
    return search(ctx.value, spells.spell_names, sorted)


def search(
    user_input: str,
    source: list[str],
    sort_fun: Callable[[list], list] | None = lambda out: sorted(sorted(out), key=len),
) -> list[str]:
    if not source:
        return []
    elif not user_input:
        out = source
    else:
        out = []
        user_input_lower = user_input.casefold()

        starts_with_case_insensitive = [
            s for s in source if s.casefold().startswith(user_input_lower)
        ]
        if starts_with_case_insensitive:
            starts_with_case_sensitive = [
                s for s in starts_with_case_insensitive if s.startswith(user_input)
            ]
            out = starts_with_case_sensitive or starts_with_case_insensitive
        else:
            words_lower = user_input_lower.split(" ")
            any_position_case_insensitive = list(
                itertools.islice(
                    (
                        s
                        for s in source
                        if all(
                            user_input_word in s.casefold()
                            for user_input_word in words_lower
                        )
                    ),
                    25,
                )
            )
            if any_position_case_insensitive:
                words = user_input.split(" ")
                any_position_case_sensitive = list(
                    itertools.islice(
                        (
                            s
                            for s in any_position_case_insensitive
                            if all(user_input_word in s for user_input_word in words)
                        ),
                        25,
                    )
                )
                out = any_position_case_sensitive or any_position_case_insensitive
            elif sort_fun:
                sort_fun = None
                out = difflib.get_close_matches(user_input, source, n=25, cutoff=0)

    if len(out) < 5:
        seen = set(out)
        for o in difflib.get_close_matches(user_input, source, n=5, cutoff=0):
            if o not in seen:
                out.append(o)
                seen.add(o)
            if len(out) == 5:
                break
    if sort_fun:
        out = sort_fun(out)
    out = out[: 25]
    return [truncate(r, 100) for r in out]
