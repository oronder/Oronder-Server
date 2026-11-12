import asyncio

from utils.WikiJsTaskQueue import WikiJsTaskQueue


def test_tasks_added_from_sync_code_are_run():
    """add_task is called without await from request handlers; the plain
    functions it is given must actually run, and one failing must not stop
    the next."""

    async def scenario():
        queue = WikiJsTaskQueue()
        queue._valid = True
        queue._last_start = 0.0
        ran = []

        def boom():
            raise RuntimeError("wiki down")

        queue.add_task(boom)
        queue.add_task(ran.append, "uploaded")
        await queue.start_worker()
        try:
            for _ in range(200):
                if ran:
                    break
                await asyncio.sleep(0.1)
        finally:
            await queue.stop_worker()
        return ran

    assert asyncio.run(scenario()) == ["uploaded"]
