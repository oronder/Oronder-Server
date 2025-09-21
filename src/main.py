import asyncio
import logging
import os
from contextlib import asynccontextmanager
from copy import copy

import socketio
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute

import discord_client
from database import init_db
from routers import foundry_api, admin_api
from routers.socket_io import sio
from utils import getLogger, init_logger
from utils.WikiJsTaskQueue import wikijs_task_queue

init_logger()

logger = getLogger(__name__)


@asynccontextmanager
async def lifespan(a: FastAPI):
    logger.critical("Initializing Database")
    init_db()
    logger.critical("Launching Discord Client")
    logger.critical(f"Log Level is <{logging.getLevelName(logger.level)}>")
    discord_task = asyncio.create_task(discord_client.start())
    for route in a.routes:
        if isinstance(route, APIRoute) and "GET" in route.methods:
            logger.critical(f"Adding HEAD route for {route.name}")
            new_route = copy(route)
            new_route.methods = {"HEAD"}
            new_route.include_in_schema = False
            a.routes.append(new_route)

    await wikijs_task_queue.start_worker()
    await discord_client.bot.wait_until_ready()

    yield
    logger.critical("SHUTTING DOWN")
    await wikijs_task_queue.stop_worker
    await discord_client.stop()
    discord_task.cancel()


app = FastAPI(lifespan=lifespan)
# noinspection PyTypeChecker
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

app.include_router(foundry_api.router)
foundry_api.attach_exception_handler(app)
app.include_router(admin_api.router)

sio_asgi_app = socketio.ASGIApp(socketio_server=sio, other_asgi_app=app)

# noinspection PyTypeChecker
app.add_route("/socket.io/", route=sio_asgi_app, methods=["GET", "POST"])
# noinspection PyTypeChecker
app.add_websocket_route("/socket.io/", sio_asgi_app)

if logger.level <= logging.DEBUG:
    from fastapi.exceptions import RequestValidationError
    from fastapi import Request
    from fastapi.exception_handlers import request_validation_exception_handler

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ):
        logger.error(request, str(exc).replace("\n", " ").replace("   ", " "))
        return await request_validation_exception_handler(request, exc)

# from pyinstrument import Profiler
# from pyinstrument.renderers.html import HTMLRenderer
# from pyinstrument.renderers.speedscope import SpeedscopeRenderer
# from typing import Callable
# @fastapi_app.middleware("http")
# async def profile_request(request: Request, call_next: Callable):
#     profile_type_to_ext = {"html": "html", "speedscope": "speedscope.json"}
#     profile_type_to_renderer = {
#         "html": HTMLRenderer,
#         "speedscope": SpeedscopeRenderer,
#     }
#
#     if request.query_params.get("profile", False):
#         profile_type = request.query_params.get("profile_format", "speedscope")
#         with Profiler(interval=0.001, async_mode="enabled") as profiler:
#             response = await call_next(request)
#         extension = profile_type_to_ext[profile_type]
#         renderer = profile_type_to_renderer[profile_type]()
#         with open(f"profile.{extension}", "w") as out:
#             out.write(profiler.output(renderer=renderer))
#         return response
#     return await call_next(request)

if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=int(os.environ["UVICORN_PORT"]), reload=True)