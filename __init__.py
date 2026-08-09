import asyncio
from collections.abc import Callable, Coroutine

from fastapi import APIRouter

try:
    from lnbits.task_manager import task_manager as _task_manager
except ImportError:
    # LNbits 1.5.x ships the task API as `lnbits.tasks` instead of
    # `lnbits.task_manager`.
    from lnbits import tasks as _legacy_tasks

    _task_manager = None

from .crud import db
from .node import LISTENER_TASK_NAME, clink_listener
from .subscriptions import SUBSCRIPTIONS_TASK_NAME, clink_subscriptions
from .views import clink_ext_generic
from .views_api import clink_ext_api

clink_ext: APIRouter = APIRouter(prefix="/clink", tags=["clink"])
clink_ext.include_router(clink_ext_generic)
clink_ext.include_router(clink_ext_api)

clink_static_files = [
    {
        "path": "/clink/static",
        "name": "clink_static",
    }
]

_started_tasks: dict[str, asyncio.Task] = {}


def _start_task(name: str, func: Callable[[], Coroutine]) -> None:
    if _task_manager is not None:
        _task_manager.create_permanent_task(func, name=name)
        return
    _started_tasks[name] = _legacy_tasks.create_permanent_task(func)


def _stop_task(name: str) -> None:
    if _task_manager is not None:
        task = _task_manager.get_task(name)
        if task:
            _task_manager.cancel_task(task)
        return
    task = _started_tasks.pop(name, None)
    if task:
        task.cancel()


def clink_stop():
    for name in (LISTENER_TASK_NAME, SUBSCRIPTIONS_TASK_NAME):
        _stop_task(name)


def clink_start():
    for name, func in (
        (LISTENER_TASK_NAME, clink_listener),
        (SUBSCRIPTIONS_TASK_NAME, clink_subscriptions),
    ):
        _stop_task(name)
        _start_task(name, func)


__all__ = [
    "clink_ext",
    "clink_start",
    "clink_static_files",
    "clink_stop",
    "db",
]
