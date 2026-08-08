from fastapi import APIRouter

from .crud import db

clink_ext_api = APIRouter()


@clink_ext_api.get("/api/v1/info", description="CLINK extension info")
async def api_info():
    return {"extension": "clink", "service": db.name}
