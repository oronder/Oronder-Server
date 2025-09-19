from discord import ApplicationContext
from discord.ext.commands import Bot

from routers.socket_namespace import SocketNamespace


class SocketAwareBot(Bot):
    socket_namespace: SocketNamespace

    def __init__(self, *args, **kwargs):
        super().__init__(*args, cache_app_emojis=True, **kwargs)


class SocketAwareApplicationContext(ApplicationContext):
    bot: SocketAwareBot
