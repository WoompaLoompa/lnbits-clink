from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from lnbits.core.models import User
from lnbits.decorators import check_user_exists
from lnbits.helpers import template_renderer

clink_ext_generic = APIRouter(tags=["clink"])


@clink_ext_generic.get(
    "/", description="CLINK extension index", response_class=HTMLResponse
)
async def index(
    request: Request,
    user: User = Depends(check_user_exists),
):
    return template_renderer(["clink/templates"]).TemplateResponse(
        request, "clink/index.html", {"user": user.json()}
    )
