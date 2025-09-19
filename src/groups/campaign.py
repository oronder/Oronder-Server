from discord import (
    VoiceChannel,
    SlashCommandGroup,
    ApplicationContext,
    ForumChannel,
    TextChannel,
    Cog,
    Embed,
    StageChannel,
    InteractionContextType,
)
from discord.commands import option
from discord.ext.commands import Bot
from discord.utils import generate_snowflake
from sqlalchemy import select

from database import Session, CampaignTable
from database.actor_table import ActorTable
from groups import is_gm, DISABLE
from groups.autocomplete import (
    campaign_autocomplete,
    campaign_add_autocomplete,
    campaign_remove_pc_autocomplete,
)
from models.actor import Actor
from utils import capitalize_title, getLogger

logger = getLogger(__name__)


class Campaign(Cog):
    def __init__(self, bot: Bot):
        self.bot = bot

    campaign_group = SlashCommandGroup(
        "campaign",
        "Campaign Group Management.",
        contexts={InteractionContextType.guild},
    )

    @campaign_group.command(
        name="create", description="Create a campaign group.", checks=[is_gm]
    )
    @option("name", description="Campaign name.")
    @option(
        "starting_level",
        int,
        min_value=1,
        max_value=20,
        description="Level new characters start at.",
    )
    @option(
        "session_channel",
        ForumChannel | TextChannel,
        description="Override default Session Channel.",
    )
    @option(
        "voice_channel",
        VoiceChannel | StageChannel,
        description="Override default Voice Channel.",
    )
    async def create_campaign_group(
        self,
        ctx: ApplicationContext,
        name: str,
        starting_level: int,
        session_channel: ForumChannel | TextChannel,
        voice_channel: VoiceChannel | StageChannel,
    ):
        if name == DISABLE:
            await ctx.respond(**logger.err_msg("Invalid Campaign name.", ctx.guild_id))
            return

        with Session() as session:
            if (
                session.query(CampaignTable)
                .filter_by(name=name, guild_id=ctx.guild_id)
                .one_or_none()
            ):
                await ctx.respond(
                    **logger.err_msg(
                        f"Campaign **{name}** already exists.", ctx.guild_id
                    )
                )
            else:
                session.add(
                    CampaignTable(
                        name=name,
                        id=generate_snowflake(),
                        starting_level=starting_level,
                        guild_id=ctx.guild_id,
                        session_channel_id=session_channel.id,
                        voice_channel_id=voice_channel.id,
                        actor_ids=[],
                    )
                )
                session.commit()
                await ctx.respond(f"Campaign **{name}** created.", ephemeral=True)

    @campaign_group.command(
        name="edit", description="Edit a campaign group.", checks=[is_gm]
    )
    @option(
        "current_name",
        description="Current campaign name.",
        autocomplete=campaign_autocomplete,
    )
    @option("new_name", description="New campaign name.")
    @option(
        "starting_level",
        min_value=1,
        max_value=20,
        description="Level new characters start at.",
    )
    async def update_campaign_group(
        self,
        ctx: ApplicationContext,
        current_name: str,
        new_name: str | None = None,
        starting_level: int | None = None,
        session_channel: ForumChannel | TextChannel | None = None,
        voice_channel: VoiceChannel | StageChannel | None = None,
    ):
        if new_name == DISABLE:
            await ctx.respond(**logger.err_msg("Invalid Campaign name.", ctx.guild_id))
            return
        if (
            not new_name
            and not starting_level
            and not session_channel
            and not voice_channel
        ):
            await ctx.respond("No changes selected.", ephemeral=True)
            return

        with Session() as session:
            if (
                session.query(CampaignTable)
                .filter_by(name=new_name, guild_id=ctx.guild_id)
                .one_or_none()
            ):
                await ctx.respond(
                    **logger.err_msg(
                        f"Campaign **{new_name}** already exists.", ctx.guild_id
                    )
                )
            else:
                cur = (
                    session.query(CampaignTable)
                    .filter_by(name=current_name, guild_id=ctx.guild_id)
                    .one()
                )
                embed = Embed(title="Campaign Update")
                if new_name:
                    embed.add_field(
                        name="Name", value=f"**{cur.name}** -> **{new_name}**"
                    )
                    cur.name = new_name
                if starting_level:
                    embed.add_field(
                        name="Starting Level",
                        value=f"**{cur.starting_level}** -> **{starting_level}**",
                    )
                    cur.starting_level = starting_level
                if session_channel:
                    current_session_channel = ctx.guild.get_channel(
                        cur.session_channel_id
                    )
                    embed.add_field(
                        name=f"Session {'Forum' if isinstance(session_channel, ForumChannel) else 'Channel'}",
                        value=f"{current_session_channel.mention} -> {session_channel.mention}",
                    )
                    cur.session_channel_id = session_channel.id
                if voice_channel:
                    current_voice_channel = ctx.guild.get_channel(cur.voice_channel_id)
                    embed.add_field(
                        name="Voice Channel",
                        value=f"{current_voice_channel.mention} -> {voice_channel.mention}",
                    )
                    cur.voice_channel_id = voice_channel.id

                session.commit()

                await ctx.respond(embed=embed, ephemeral=True)

    @campaign_group.command(name="info", description="Show campaign group details.")
    @option("name", description="Campaign name.", autocomplete=campaign_autocomplete)
    async def read_campaign_group(self, ctx: ApplicationContext, name: str):
        with Session() as session:
            campaign = (
                session.query(CampaignTable)
                .filter_by(name=name, guild_id=ctx.guild_id)
                .one_or_none()
            )
            if not campaign:
                await ctx.respond(
                    **logger.err_msg(f"Campaign **{name}** not found.", ctx.guild_id)
                )
                return

            embed = Embed(title=capitalize_title(name))
            embed.add_field(name="Starting Level", value=campaign.starting_level)
            session_channel = ctx.guild.get_channel(campaign.session_channel_id)
            embed.add_field(
                name=f"Session {'Forum' if isinstance(session_channel, ForumChannel) else 'Channel'}",
                value=session_channel.mention,
            )
            voice_channel = ctx.guild.get_channel(campaign.voice_channel_id)
            embed.add_field(name="Voice Channel", value=voice_channel.mention)

            actors = [
                Actor.model_validate(a)
                for a in session.query(ActorTable)
                .filter_by(guild_id=ctx.guild_id)
                .filter(ActorTable.id.in_(campaign.actor_ids))
            ]

            for actor in actors:
                embed.add_field(
                    name=actor.name,
                    value="/".join(
                        [f"{k.title()} {v['levels']}" for k, v in actor.classes.items()]
                    ),
                )

        await ctx.respond(embed=embed, ephemeral=True)

    @campaign_group.command(
        name="delete", description="Delete a campaign group.", checks=[is_gm]
    )
    @option("name", description="Campaign name.", autocomplete=campaign_autocomplete)
    @option(
        "confirmation", str, description="Type your discord server's name to confirm."
    )
    async def delete_campaign_group(
        self, ctx: ApplicationContext, name: str, confirmation: str
    ):
        if confirmation != ctx.guild.name:
            await ctx.respond(
                f"`{confirmation}` does not match server name `{ctx.guild.name}`.",
                ephemeral=True,
            )
            return

        with Session() as session:
            campaign = (
                session.query(CampaignTable)
                .filter_by(name=name, guild_id=ctx.guild_id)
                .one_or_none()
            )
            if not campaign:
                await ctx.respond(
                    **logger.err_msg(f"Campaign **{name}** not found.", ctx.guild_id)
                )
                return
            session.delete(campaign)
            session.commit()

        await ctx.respond(f"Campaign **{name}** deleted.", ephemeral=True)

    campaign_pc_group = campaign_group.create_subgroup(
        "character",
        "Campaign Character Management.",
        checks=[is_gm],
        contexts={InteractionContextType.guild},
    )

    @campaign_pc_group.command(
        name="add", description="Add a character to a campaign group.", checks=[is_gm]
    )
    @option(
        "campaign",
        parameter_name="campaign_name",
        description="Campaign name.",
        autocomplete=campaign_autocomplete,
    )
    @option(
        "character",
        parameter_name="actor_name",
        description="Character name.",
        autocomplete=campaign_add_autocomplete,
    )
    async def campaign_add_pc(
        self, ctx: ApplicationContext, campaign_name: str, actor_name: str
    ):
        with Session() as session:
            pc_id = session.scalar(
                select(ActorTable.id).filter_by(guild_id=ctx.guild_id, name=actor_name)
            )
            if not pc_id:
                await ctx.respond(
                    **logger.err_msg(f"Character {actor_name} not found!", ctx.guild_id)
                )
                return

            campaign = session.scalar(
                select(CampaignTable).filter_by(
                    guild_id=ctx.guild_id, name=campaign_name
                )
            )
            if not campaign:
                await ctx.respond(
                    **logger.err_msg(
                        f"Campaign {campaign_name} not found!", ctx.guild_id
                    )
                )
                return

            if pc_id in campaign.actor_ids:
                await ctx.respond(
                    **logger.err_msg(
                        f"Character {actor_name} already in {campaign_name}!",
                        ctx.guild_id,
                    )
                )
                return

            campaign.actor_ids.append(pc_id)
            session.commit()

        await ctx.respond(
            f"**{actor_name}** added to **{campaign_name}**.", ephemeral=True
        )

    @campaign_pc_group.command(
        name="remove",
        description="Remove a character to a campaign group.",
        checks=[is_gm],
    )
    @option(
        "campaign",
        parameter_name="campaign_name",
        description="Campaign name.",
        autocomplete=campaign_autocomplete,
    )
    @option(
        "character",
        parameter_name="actor_name",
        description="Character name.",
        autocomplete=campaign_remove_pc_autocomplete,
    )
    async def campaign_remove_pc(
        self, ctx: ApplicationContext, campaign_name: str, actor_name: str
    ):
        with Session() as session:
            pc_id = session.scalar(
                select(ActorTable.id).filter_by(guild_id=ctx.guild_id, name=actor_name)
            )
            if not pc_id:
                await ctx.respond(
                    **logger.err_msg(f"Character {actor_name} not found!", ctx.guild_id)
                )
                return

            campaign = session.scalar(
                select(CampaignTable).filter_by(
                    guild_id=ctx.guild_id, name=campaign_name
                )
            )
            if not campaign:
                await ctx.respond(
                    **logger.err_msg(
                        f"Campaign {campaign_name} not found!", ctx.guild_id
                    )
                )
                return

            if pc_id not in campaign.actor_ids:
                await ctx.respond(
                    **logger.err_msg(
                        f"Character {actor_name} not in {campaign_name}!", ctx.guild_id
                    )
                )
                return

            campaign.actor_ids.remove(pc_id)
            session.commit()

        await ctx.respond(f'**{actor_name}** removed from **{campaign_name}**.', ephemeral=True)


def setup(bot: Bot):
    logger.critical("Loading")
    game = Campaign(bot)
    bot.add_cog(game)
