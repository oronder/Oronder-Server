import asyncio
import textwrap
from dataclasses import dataclass
from typing import List, Optional

from discord import (
    Cog,
    RawScheduledEventSubscription,
    ScheduledEvent,
    Member,
    ScheduledEventStatus,
    Guild,
    RawMemberRemoveEvent,
)
from sqlalchemy.exc import NoResultFound

from database import Session
from database.db_utils import delete_guild, delete_member
from database.guild_settings_table import GuildSettingsTable
from database.missions import MissionTable, edit_mission
from groups import get_actors, foundry_module_link
from models.actor import Actor
from models.missions import Mission
from models.socket_aware_bot import SocketAwareBot
from utils import (
    BETA_TESTER_ROLE_ID,
    HOME_GUILD_ID,
    SUPPORTER_ROLE_ID,
    ensure_members,
    getLogger,
)
from views.events import CharacterSelectView

logger = getLogger(__name__)


@dataclass
class ScheduledEventContext:
    actors: List[Actor]
    mission: Mission
    user: Member
    scheduled_event: ScheduledEvent


GUILD_REMOVE_GRACE_SECONDS = 600


class Events(Cog):
    def __init__(self, bot: SocketAwareBot):
        self.bot = bot

    # @Cog.listener()
    # async def on_application_command_error(self, ctx: ApplicationContext, exception: DiscordException):

    @Cog.listener()
    async def on_guild_remove(self, guild: Guild):
        # Re-authorizing the app -- "Connect to Discord" / Re-Init in the Foundry
        # module -- makes Discord remove and re-add the bot, ~24s apart. Deleting
        # immediately wiped a guild's actors, campaign, downtime and missions on
        # every re-pair. Wait, and delete only if the bot was not re-added.
        #
        # If the process restarts during the wait, the delete never runs and the
        # data is kept. That is the safe direction to fail in.
        await asyncio.sleep(GUILD_REMOVE_GRACE_SECONDS)
        if self.bot.get_guild(guild.id) is not None:
            logger.info(f"{guild.id}: rejoined within grace period, keeping data")
            return
        delete_guild(guild)

    @Cog.listener()
    async def on_raw_member_remove(self, event: RawMemberRemoveEvent):
        delete_member(event)

    @Cog.listener()
    async def on_member_update(self, before: Member, after: Member):
        if HOME_GUILD_ID is None or after.guild.id != HOME_GUILD_ID:
            return

        # Compare role ids. member.roles holds Role objects, and an int never
        # equals one, so the old `role_id in member.roles` checks never fired.
        watched = {r for r in (BETA_TESTER_ROLE_ID, SUPPORTER_ROLE_ID) if r}
        changed = {r.id for r in before.roles} ^ {r.id for r in after.roles}
        if changed & watched:
            for guild in [g for g in self.bot.guilds if g.owner_id == after.id]:
                GuildSettingsTable.update_subscription(self.bot, guild)

    @Cog.listener()
    async def on_scheduled_event_update(self, before, after):
        if not after.creator_id or int(after.creator_id) != self.bot.application_id:
            return

        if (
            after.status == ScheduledEventStatus.active
            and before.status != after.status
        ):
            active = True
        elif (
            before.status == ScheduledEventStatus.active
            and after.status != before.status
        ):
            active = False
        else:
            return

        with Session() as session:
            mission = (
                session.query(MissionTable)
                .filter_by(guild_id=after.guild.id, event_id=after.id)
                .one_or_none()
            )

        if mission:
            await self.bot.socket_namespace.start_stop_session(
                after.guild.id,
                {
                    "name": mission.title,
                    "id": mission.id,
                    "start_ts": int(after.start_time.timestamp() * 1000),
                    "status": "start" if active else "stop",
                },
            )
        else:
            logger.warning(
                f" Mission for Guild ID {after.guild.id} with Event ID {after.id} not found!"
            )

    @Cog.listener()
    async def on_raw_scheduled_event_user_add(
        self, event_subscription: RawScheduledEventSubscription
    ):
        if not event_subscription:
            logger.warning(f"{event_subscription=}")
            return

        await ensure_members(event_subscription.guild if event_subscription else None)
        sec: ScheduledEventContext = self.event_to_actors(event_subscription)
        if sec:
            a_ids = [a.id for a in sec.actors]
            if any(
                a_id in a_ids for a_id in [*sec.mission.pcs, *sec.mission.pcs_standby]
            ):
                return  # User is already signed up
            if not sec.actors:
                is_owner = event_subscription.guild.owner_id == sec.user.id
                user = (
                    "You have"
                    if is_owner
                    else f"**{event_subscription.guild.owner.name}** has"
                )
                await sec.user.send(
                    textwrap.dedent(f"""
                No characters found to join **{sec.mission.title}**!
                Please ensure:
                - {user} installed and linked the [Foundry Add-on Module]({foundry_module_link}).
                - A Foundry Admin has linked your Discord ID (`{sec.user.id}`) to a Foundry User.
                - Your Foundry User owns a Player Character with a Class, Background and Race.""")
                )
            else:
                channel_or_thread = event_subscription.guild.get_channel_or_thread(
                    sec.mission.channel_or_thread_id
                )
                if len(sec.actors) == 1:
                    await channel_or_thread.send(
                        content=f"{sec.user.mention} has signed up with {sec.actors[0].name}!"
                    )
                    sec.mission.pcs.append(sec.actors[0].id)
                    await edit_mission(event_subscription.guild, sec.mission)
                else:
                    await channel_or_thread.send(
                        content=f"{sec.user.mention} has signed up!",
                        view=CharacterSelectView(
                            sec.scheduled_event, sec.user.id, sec.mission, sec.actors
                        ),
                    )

    @Cog.listener()
    async def on_raw_scheduled_event_user_remove(
        self, event_subscription: RawScheduledEventSubscription
    ):
        if not event_subscription:
            logger.warning(f"{event_subscription=}")
        await ensure_members(event_subscription.guild if event_subscription else None)
        sec: ScheduledEventContext = self.event_to_actors(event_subscription)
        if sec:
            a_ids = [a.id for a in sec.actors]
            sec.mission.pcs = [a_id for a_id in sec.mission.pcs if a_id not in a_ids]
            sec.mission.pcs_standby = [
                a_id for a_id in sec.mission.pcs_standby if a_id not in a_ids
            ]
            await edit_mission(event_subscription.guild, sec.mission)

    def event_to_actors(
        self, event_subscription: RawScheduledEventSubscription
    ) -> Optional[ScheduledEventContext]:
        scheduled_event = event_subscription.guild.get_scheduled_event(
            event_subscription.event_id
        )
        if not scheduled_event:
            logger.warning(
                f"Mission for {event_subscription.guild.name} with Event ID {event_subscription.event_id} not found!"
            )
            return None
        if (
            scheduled_event.creator_id
            and int(scheduled_event.creator_id) != self.bot.application_id
        ):
            return None

        try:
            with Session() as session:
                mission = Mission.model_validate(
                    session.query(MissionTable)
                    .filter_by(
                        event_id=scheduled_event.id,
                        guild_id=event_subscription.guild.id,
                    )
                    .one()
                )
        except NoResultFound:
            logger.warning(
                f"Mission for {event_subscription.guild.name}'s {scheduled_event.name} not found!"
            )
            return None

        user = event_subscription.guild.get_member(event_subscription.user_id)
        if not user:
            logger.warning(
                f"User signing up for {mission.title} not found. "
                + f"Possibly missing member intent for {event_subscription.guild.name}!"
            )
            return None

        if mission.gm_id == user.id:
            logger.debug(
                f"{user.display_name} is GM of {mission.title}. No need to do anything."
            )
            return None

        actors: List[Actor]
        actors, error = get_actors(user.id, event_subscription.guild.id)
        if error:
            logger.warning(
                f"Error finding {user.display_name}'s Actor for {event_subscription.guild.name}'s {scheduled_event.name}!"
            )
            return None

        intersection = {a.name for a in actors}.intersection(
            {*mission.pcs, *mission.pcs_standby}
        )
        if len(intersection):
            logger.debug(
                f"Actor {intersection.pop()} already signed up for mission {mission.title}!"
            )
            return None

        return ScheduledEventContext(actors, mission, user, scheduled_event)


def setup(bot: SocketAwareBot):
    logger.critical("Loading")
    bot.add_cog(Events(bot))
