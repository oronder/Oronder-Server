import discord
from discord.ext import tasks
from discord.ext.commands import Bot

from database.guild_settings_table import GuildSettingsTable
from utils import getLogger

logger = getLogger(__name__)


class Tasks(discord.Cog):
    def __init__(self, bot: Bot):
        self.bot = bot

    @tasks.loop(hours=24)
    async def set_subscriber_status(self):
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            GuildSettingsTable.update_subscription(self.bot, guild)


def setup(bot: Bot):
    logger.critical("Loading")
    bot.add_cog(Tasks(bot))
