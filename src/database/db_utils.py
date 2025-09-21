from discord import Guild, RawMemberRemoveEvent, User
from sqlalchemy import cast, BigInteger, func

from database import Session, CampaignTable, DowntimeTable
from database.actor_table import ActorTable
from database.game_master_table import GameMasterTable
from database.guild_settings_table import GuildSettingsTable
from database.missions import MissionTable


def delete_guild(guild: Guild):
    guild_id = guild.id
    with Session() as session:
        session.query(ActorTable).filter_by(guild_id=guild_id).delete()
        session.query(CampaignTable).filter_by(guild_id=guild_id).delete()
        session.query(DowntimeTable).filter_by(guild_id=guild_id).delete()
        session.query(GameMasterTable).filter_by(guild_id=guild_id).delete()
        session.query(MissionTable).filter_by(guild_id=guild_id).delete()
        # session.query(GoldLedger).filter_by(guild_id=guild_id).delete()
        session.query(GuildSettingsTable).filter_by(id=guild_id).delete()
        session.commit()


def delete_member(event: RawMemberRemoveEvent):
    user: User = event.user
    if not user or user.bot:
        return

    guild_id = event.guild_id
    member_id = user.id

    with Session() as session:
        # Find records to delete where discord_ids contains only member_id
        records_to_delete = (
            session.query(ActorTable)
            .filter_by(guild_id=guild_id)
            .filter(func.array_length(ActorTable.discord_ids, 1) == 1)
            .filter(ActorTable.discord_ids[1] == cast(member_id, BigInteger))
            .all()
        )

        for record in records_to_delete:
            session.delete(record)

        session.query(GameMasterTable).filter_by(
            guild_id=guild_id, id=member_id
        ).delete()

        session.commit()
