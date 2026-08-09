from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from lnbits.core.models import User
from lnbits.decorators import check_user_exists
from lnbits.helpers import template_renderer

from .crud import get_offer

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


@clink_ext_generic.get(
    "/pay",
    description="Pay a CLINK offer",
    response_class=HTMLResponse,
)
async def pay_page(
    request: Request,
    user: User = Depends(check_user_exists),
):
    return template_renderer(["clink/templates"]).TemplateResponse(
        request, "clink/pay.html", {"user": user.json()}
    )


@clink_ext_generic.get(
    "/checkout/{offer_id}",
    name="clink.checkout",
    description="Public CLINK offer checkout page",
    response_class=HTMLResponse,
)
async def checkout(request: Request, offer_id: str):
    offer = await get_offer(offer_id)
    if not offer or not offer.active:
        raise HTTPException(status_code=404, detail="Offer not found.")
    return template_renderer(["clink/templates"]).TemplateResponse(
        request,
        "clink/checkout.html",
        {
            "offer_id": offer.id,
            "name": offer.name or "CLINK offer",
            "description": offer.description or "",
            "amount_msat": offer.amount_msat,
            "noffer": offer.noffer or "",
        },
    )
