import socketio

from utils import getLogger

logger = getLogger(__name__)

# see https://python-socketio.readthedocs.io/en/latest/server.html#using-a-message-queue
# mgr = socketio.AsyncRedisManager(url="redis://localhost:6379/0")
sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*", logger=logger)
