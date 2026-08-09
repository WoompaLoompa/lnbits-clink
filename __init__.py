from fastapi import APIRouter
from lnbits.task_manager import task_manager

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


def clink_stop():
    for name in (LISTENER_TASK_NAME, SUBSCRIPTIONS_TASK_NAME):
        task = task_manager.get_task(name)
        if task:
            task_manager.cancel_task(task)


def clink_start():
    task_manager.create_permanent_task(clink_listener, name=LISTENER_TASK_NAME)
    task_manager.create_permanent_task(
        clink_subscriptions, name=SUBSCRIPTIONS_TASK_NAME
    )


__all__ = [
    "clink_ext",
    "clink_start",
    "clink_static_files",
    "clink_stop",
    "db",
]
