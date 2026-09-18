import asyncio
import logging
from uuid import uuid4

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel

from .config import OUTPUT_DIR
from .classification.models import ClassificationMetadata
from .classification.resolver import UnresolvedClassification, resolve_products
from .layout.planner import ImpossibleLayout, plan_layout
from .models.campaign import BannerRequest
from .models.design import DesignSpec, DesignSpecV2
from .renderer.renderer import render_html
from .renderer.screenshot import screenshot

logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s %(message)s')

app = FastAPI(title='promo-banner-ai', version='0.1.0')
logger = logging.getLogger(__name__)


class BannerResponse(BaseModel):
    status: str = 'created'
    file: str
    design: DesignSpec | DesignSpecV2
    classification: dict[str, ClassificationMetadata] | None = None


@app.get('/health')
def health() -> dict[str, str]:
    return {'status': 'ok'}


@app.post('/api/v1/banners', response_model=BannerResponse, status_code=status.HTTP_201_CREATED)
def create_banner(request: BannerRequest) -> BannerResponse:
    try:
        request, classification = asyncio.run(resolve_products(request))
        design = plan_layout(request)
    except (ImpossibleLayout, UnresolvedClassification) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    campaign_id = uuid4().hex
    destination = OUTPUT_DIR / f'{campaign_id}.png'
    try:
        html = render_html(request, design)
        screenshot(html, destination)
    except Exception as exc:
        logger.exception('Falha ao renderizar campanha %s', campaign_id)
        raise HTTPException(status_code=500, detail='Falha ao renderizar banner; consulte os logs.') from exc
    return BannerResponse(file=f'output/{destination.name}', design=design, classification=classification)
