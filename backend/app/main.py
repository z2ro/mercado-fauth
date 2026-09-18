import asyncio
import logging
from uuid import uuid4

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel

from .config import OUTPUT_DIR
from .classification.models import ClassificationMetadata
from .classification.resolver import UnresolvedClassification
from .layout.planner import ImpossibleLayout
from .models.campaign import BannerRequest
from .models.design import DesignSpec, DesignSpecV2
from .workflow import generate_banner

logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s %(message)s')

app = FastAPI(title='promo-banner-ai', version='0.1.0')
logger = logging.getLogger(__name__)


class BannerResponse(BaseModel):
    status: str = 'created'
    file: str
    design: DesignSpec | DesignSpecV2
    classification: dict[str, ClassificationMetadata] | None = None
    art_direction: dict | None = None
    visual_qa: dict | None = None
    performance: dict | None = None


@app.get('/health')
def health() -> dict[str, str]:
    return {'status': 'ok'}


@app.post('/api/v1/banners', response_model=BannerResponse, status_code=status.HTTP_201_CREATED)
def create_banner(request: BannerRequest) -> BannerResponse:
    try:
        campaign_id = uuid4().hex
        destination = OUTPUT_DIR / f'{campaign_id}.png'
        build = asyncio.run(generate_banner(request, destination))
    except (ImpossibleLayout, UnresolvedClassification) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception('Falha ao renderizar campanha %s', campaign_id)
        raise HTTPException(status_code=500, detail='Falha ao renderizar banner; consulte os logs.') from exc
    return BannerResponse(file=f'output/{destination.name}', design=build.design,
                          classification=build.classification, art_direction=build.art_direction,
                          visual_qa=build.visual_qa, performance=build.performance)
