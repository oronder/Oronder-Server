from typing import List

import d20
import discord
from d20 import RollError
from discord import Embed, EmbedFooter, EmbedField

from database.guild_settings_table import GuildSettingsTable
from dnd import handle_description_entries
from dnd.rules import actions
from groups import get_actor, DISPLAY_PRIVATE, invite_link
from models.guild_settings import Subscription
from routers.socket_namespace import SocketNamespace
from systems import get_system_for_actor
from utils import getLogger, join_list, respond_with_long_embed, capitalize_title

logger = getLogger(__name__)


async def roll(
    ctx: discord.ApplicationContext,
    character: str,
    stat: str,
    advantage: str,
    save: bool,
    socket_namespace: SocketNamespace,
):
    actor, error = get_actor(character, ctx.user.id, ctx.guild_id)
    if error:
        await ctx.respond(**error)
        return

    system = get_system_for_actor(actor)
    roll_spec = system.build_roll(actor, character, stat, advantage, save)

    if not roll_spec:
        await ctx.respond(
            **logger.err_msg(f"Unrecognized stat **{stat}**.", ctx.guild_id)
        )
        return

    async def send_roll(res: str | None = None, ephemeral: bool = False):
        if not res:
            res = roll_spec.local_roll()

        await ctx.respond(
            embed=Embed(
                # description=advantage and f'[{advantage}]',
                fields=[EmbedField(roll_spec.descriptor, res)],
                footer=EmbedFooter(actor.name, actor.portrait_url),
            ),
            ephemeral=ephemeral,
        )

    guild_settings = GuildSettingsTable.lookup(ctx.guild_id)
    if guild_settings.roll_discord_to_foundry:
        # Defer the interaction to avoid 'Unknown interaction' if Foundry takes >3s
        try:
            await ctx.defer()
        except Exception as e:
            logger.warning(str(e), stack_info=True)
            pass

        success = await socket_namespace.send_roll(
            ctx.guild_id,
            {
                **roll_spec.payload,
                "actor_id": actor.id,
                "discord_id": str(ctx.user.id),
            },
            send_roll,
        )
    else:
        success = False

    if not success:
        await send_roll()


async def roll_attack(
    ctx: discord.ApplicationContext,
    actor_name: str,
    attack_name: str,
    socket_namespace: SocketNamespace,
    advantage: str | None,
    spell_level: int | None,
    attack_mode: str | None,
):
    actor, error = get_actor(actor_name, ctx.interaction.user.id, ctx.guild_id)
    if error:
        await ctx.respond(**error)
        return

    system = get_system_for_actor(actor)

    attack = next((a for a in actor.weapons if a.name == attack_name), None)
    if not attack:
        await ctx.respond(
            **logger.err_msg(f"Attack {attack_name} not found!", ctx.guild_id)
        )
        return

    async def send_atk(
        atk: str | None = None,
        dmg: str | List | None = None,
        res: str | None = None,
        ephemeral: bool = False,  # noqa: ARG001 - part of the ack contract
    ):
        # dnd5e acks with {atk, dmg}; pf2e/CoC7 ack with {res} (SYSTEMS.md).
        if not atk and res:
            atk = res
        if not atk:
            atk = system.attack_fallback(actor, attack, advantage)

        embed = Embed(
            title=attack.name,
            description=advantage and f"[{advantage}]",
            footer=EmbedFooter(actor.name, actor.portrait_url),
            fields=[EmbedField("Attack", atk)],
        )

        if isinstance(attack.img, str) and attack.img.startswith("https"):
            embed.set_thumbnail(url=attack.img)

        if isinstance(dmg, str):
            embed.add_field(name="Damage", value=dmg, inline=False)
        elif isinstance(dmg, List):
            for d in dmg:
                embed.add_field(
                    name="", value=f"**Damage** *({d[1]})*\n{d[0]}", inline=False
                )

        await ctx.respond(embed=embed)

    guild_settings = GuildSettingsTable.lookup(ctx.guild_id)
    if guild_settings.roll_discord_to_foundry:
        # Defer the interaction to avoid 'Unknown interaction' if Foundry takes >3s
        try:
            await ctx.defer()
        except Exception as e:
            logger.warning(str(e), stack_info=True)
            pass

        payload = {
            "actor_id": actor.id,
            "discord_id": str(ctx.user.id),
            **system.attack_payload(actor, attack, advantage, spell_level, attack_mode),
        }

        success = await socket_namespace.send_roll(ctx.guild_id, payload, send_atk)
    else:
        success = False

    if not success:
        await send_atk()


async def r(ctx: discord.ApplicationContext, die: str, display: str):
    public = display != DISPLAY_PRIVATE

    try:
        arr = die.split(" ")
        if len(arr) > 1 and arr[0] == "1d20":
            if "adv" in arr and "dis" in arr:
                arr.pop(arr.index("adv"))
                arr.pop(arr.index("dis"))
            elif "adv" in arr:
                arr[0] = "2d20kh1"
                arr.pop(arr.index("adv"))
            elif "dis" in arr:
                arr[0] = "2d20kl1"
                arr.pop(arr.index("dis"))
        res = d20.roll(" ".join(arr), allow_comments=True)
        msg = join_list([str(res), res.comment], " ")
        logger.debug(msg)
        await ctx.respond(msg, ephemeral=not public)
    except RollError as e:
        logger.err_msg(str(e), ctx.guild_id)
        await ctx.respond(f"{die} is not a valid dice roll.", ephemeral=True)


async def action(
    ctx: discord.ApplicationContext,
    actor_name: str,
    action_type: str,
    comment: str,
    display_description: bool,
):
    guild_settings = GuildSettingsTable.lookup(ctx.guild_id)
    if guild_settings.subscription == Subscription.none:
        await ctx.respond(
            **logger.err_msg(
                f"{ctx.command.name.capitalize()} is a supporter only feature.\n{invite_link(ctx)}",
                ctx.guild_id,
            )
        )
        return

    actor, error = get_actor(actor_name, ctx.user.id, ctx.guild_id)
    if error:
        await ctx.respond(**error)
        return

    embed = Embed(
        title=action_type,
        description=comment,
        color=discord.Color.red() if actor.details.dead else None,
    )

    description = actions[action_type]
    times = [
        t
        if isinstance(t, str)
        else capitalize_title(
            f"{t['number']} {t['unit'].replace('bonus', 'bonus action')}"
        )
        for t in description.get("time", ["—"])
    ]
    fields = [("Time", join_list(times, "/"), False)]
    if display_description:
        fields += handle_description_entries(
            None, description["entries"], name="Description"
        )

    for n, v, i in fields:
        embed.add_field(name=n, value=v, inline=i)

    class_str = "/".join(
        [f"{k.title()} {v['levels']}" for k, v in actor.classes.items()]
    )
    embed.set_footer(text=f"{actor.name} | {actor.details.race} | {class_str}")

    await respond_with_long_embed(ctx, embed)
