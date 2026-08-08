from fastapi import APIRouter

from .crud import db
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
    pass


def clink_start():
    pass


__all__ = [
    "clink_ext",
    "clink_start",
    "clink_static_files",
    "clink_stop",
    "db",
]
