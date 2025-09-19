from typing import Optional

from discord import (
    SlashCommandGroup,
    Embed,
    InteractionContextType,
    ApplicationContext,
    Cog,
    Color,
)
from discord.commands import option

from database.guild_settings_table import GuildSettingsTable
from discord_markdown_converter import md
from dnd import SKILLS, TOOLS, mod_to_str, items
from dnd.backgrounds import generate_background_embed
from dnd.feats import generate_feat_embed, feats
from dnd.items import generate_item_embed, format_number
from dnd.rules import generate_rule_embed
from dnd.spells import generate_spell_embed
from groups import (
    character_description,
    display_ephemeral,
    get_actor,
    invite_link,
    display_choices,
    DISPLAY_PRIVATE,
    DISPLAY_PUBLIC,
    detail_description,
)
from groups.autocomplete import (
    actor_autocomplete,
    spell_autocomplete,
    rule_autocomplete,
    search,
    background_autocomplete,
    detail_autocomplete,
)
from models.actor import Item
from models.guild_settings import Subscription
from models.socket_aware_bot import SocketAwareBot, SocketAwareApplicationContext
from routers.socket_namespace import SocketNamespace
from utils import respond_with_long_embed, getLogger
from utils import tabulate

logger = getLogger(__name__)


class Lookups(Cog):
    def __init__(self, bot: SocketAwareBot):
        self.bot = bot

    lookup_group = SlashCommandGroup(
        "lookup", "Lookup Game Info", contexts={InteractionContextType.guild}
    )

    @lookup_group.command(name="item", description="Looks up an item.")
    @option(
        "item",
        description="The item you want to look up",
        autocomplete=lambda ctx: search(ctx.value, items.shoppable_items),
    )
    @option(
        "display",
        description=display_ephemeral,
        default=DISPLAY_PUBLIC,
        choices=display_choices,
    )
    async def item_lookup(self, ctx: ApplicationContext, item: str, display: str):
        embed, error = generate_item_embed(item)
        if embed:
            await ctx.respond(embed=embed, ephemeral=display == DISPLAY_PRIVATE)
        else:
            await ctx.respond(**logger.err_msg(error, ctx.guild_id))

    @lookup_group.command(name="feat", description="Looks up a feat.")
    @option(
        "feat",
        description="The feat you want to look up",
        autocomplete=lambda ctx: search(ctx.value, feats.keys()),
    )
    @option(
        "display",
        description=display_ephemeral,
        default=DISPLAY_PUBLIC,
        choices=display_choices,
    )
    async def feat_lookup(self, ctx: ApplicationContext, feat: str, display: str):
        await ctx.respond(
            embed=generate_feat_embed(feat), ephemeral=display == DISPLAY_PRIVATE
        )

    @lookup_group.command(name="rule", description="Looks up a rule.")
    @option(
        "rule",
        description="The rule you want to look up",
        autocomplete=rule_autocomplete,
    )
    @option(
        "display",
        description=display_ephemeral,
        default=DISPLAY_PUBLIC,
        choices=display_choices,
    )
    async def rule_lookup(self, ctx: ApplicationContext, rule: str, display: str):
        await ctx.respond(
            **generate_rule_embed(rule), ephemeral=display == DISPLAY_PRIVATE
        )

    # TODO /lookup racefeat
    # TODO /lookup race
    # TODO /lookup classfeat
    # TODO /lookup class
    # TODO /lookup subclass

    # TODO /lookup monster
    # TODO /lookup monimage
    # TODO /lookup token

    @lookup_group.command(name="background", description="Looks up a background.")
    @option(
        "background",
        description="The background you want to look up",
        autocomplete=background_autocomplete,
    )
    @option(
        "display",
        description=display_ephemeral,
        default=DISPLAY_PUBLIC,
        choices=display_choices,
    )
    async def background_lookup(
        self, ctx: ApplicationContext, background: str, display: str
    ):
        await ctx.respond(
            embed=generate_background_embed(background),
            ephemeral=display == DISPLAY_PRIVATE,
        )

    @lookup_group.command(name="spell", description="Looks up a spell.")
    @option(
        "spell",
        description="The spell you want to look up",
        autocomplete=spell_autocomplete,
    )
    @option(
        "display",
        description=display_ephemeral,
        default=DISPLAY_PUBLIC,
        choices=display_choices,
    )
    async def spell_lookup(self, ctx: ApplicationContext, spell: str, display: str):
        await ctx.respond(
            embed=generate_spell_embed(spell), ephemeral=display == DISPLAY_PRIVATE
        )

    @lookup_group.command(name="character", description="Look up Character details.")
    @option(
        "character", description=character_description, autocomplete=actor_autocomplete
    )
    @option(
        "detail",
        description=detail_description,
        default=None,
        autocomplete=detail_autocomplete,
    )
    @option(
        "display",
        description=display_ephemeral,
        default=DISPLAY_PUBLIC,
        choices=display_choices,
    )
    async def pc_lookup(
        self, ctx: ApplicationContext, character: str, detail: str, display: str
    ):
        return await lookup_character(
            ctx, character, detail, display, self.bot.socket_namespace
        )


async def lookup_character(
    ctx: SocketAwareApplicationContext,
    character: str,
    detail: Optional[str],
    display: str,
    socket_namespace: SocketNamespace,
    gm=False,
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

    actor, error = get_actor(character, ctx.user.id, ctx.guild_id, gm)
    if error:
        await ctx.respond(**error)
        return

    embed = Embed(
        title=actor.name,
        description=actor.desc_string(),
        color=Color.red() if actor.details.dead else None,
    )

    if detail and ": " in detail:
        item_type, item_name = detail.split(": ", 1)
        item: Item = next(
            (
                i
                for i in actor.details.items
                if i.name == item_name and i.type == item_type.lower()
            ),
            None,
        )
        if not item:
            await ctx.respond(**logger.err_msg("Could not parse detail", ctx.guild_id))
            return

        embed.add_field(name=item_type, value=item_name, inline=False)
        if item.img and item.img.startswith("https://"):
            embed.set_image(url=item.img)

        async def desc_cb(desc: str):
            as_md = md(desc)
            for idx, s in enumerate(
                [j for i in as_md.split("\n\n\n\n") for j in i.split("\n\n")]
            ):
                if s and s != "---":
                    if s.startswith("ACTIVATION: "):
                        embed.add_field(
                            name="Casting Time",
                            value=s[12:].replace("bonus", "bonus action").title(),
                            inline=False,
                        )
                    else:
                        for i in range(0, len(s), 1024):
                            embed.add_field(
                                name=f"{'Description' if not idx else ''}",
                                value=s[i : i + 1024],
                                inline=False,
                            )
            await respond_with_long_embed(
                ctx, embed, ephemeral=display == DISPLAY_PRIVATE
            )

        (
            await socket_namespace.get_description(
                ctx.guild_id, actor.id, item.id, desc_cb
            )
        ) or (
            await respond_with_long_embed(
                ctx, embed, ephemeral=display == DISPLAY_PRIVATE
            )
        )

    else:
        embed.add_field(
            name="XP", value=format_number(actor.get_exp(guild_settings), "XP")
        )

        embed.set_thumbnail(url=actor.portrait_url)
        embed.add_field(name="AC", value=actor.attributes.ac["value"])

        ability_table = tabulate({k.upper()[:2]: [v.value] for k, v in actor.abilities})
        embed.add_field(name="Ability Scores", value=ability_table, inline=False)

        for skill_abrv, skill in actor.skills:
            if skill.proficient > 0.5:
                embed.add_field(name=SKILLS[skill_abrv], value=mod_to_str(skill.total))

        for tool_abrv, tool in actor.tools:
            if tool:
                embed.add_field(name=TOOLS[tool_abrv], value=mod_to_str(tool.total))

        if actor.attributes.spellcaster >= 0:
            embed.add_field(name="Spell Save DC", value=actor.attributes.spelldc)
            embed.add_field(name="Spell Attack Mod", value=actor.attributes.spellmod)

        best_weapon = actor.best_weapon()
        if best_weapon:
            embed.add_field(name=best_weapon.name, value=best_weapon.attack)

        if actor.equipment:
            embed.add_field(name="Equipment", value=", ".join(actor.equipment))

        embed.set_footer(text=actor.currency.stringify())

        await respond_with_long_embed(ctx, embed, ephemeral=display == DISPLAY_PRIVATE)


def setup(bot: SocketAwareBot):
    logger.critical("Loading")
    bot.add_cog(Lookups(bot))
