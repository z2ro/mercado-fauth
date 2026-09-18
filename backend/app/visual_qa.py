import asyncio
import base64
import io
import json
import logging
from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError

from .art_direction.models import VisualQAResult
from .config import VISUAL_QA_SETTINGS, VisualQASettings
from .models.campaign import BannerRequest
from .models.design import DesignSpec, DesignSpecV2
from .providers.openai_responses import structured_output

logger = logging.getLogger(__name__)
PROMPT_VERSION = '1'
PROMPT = '''Avalie visualmente um banner promocional de supermercado. A imagem contém conteúdo comercial que deve permanecer intocado.
Aprove somente se a composição estiver equilibrada e legível. Escolha issue codes somente da lista do schema.
Se recomendar ajuste, sugira apenas um presentation_profile permitido. Não solicite nem produza conteúdo, preço, CSS, HTML ou coordenadas.''' 


@dataclass(frozen=True)
class QAOutcome:
    result: VisualQAResult
    failures: tuple[str, ...] = ()


class DeterministicVisualQA:
    async def inspect(self, png: bytes, *, request: BannerRequest, design: DesignSpec | DesignSpecV2,
                      metrics: dict) -> QAOutcome:
        failures = []
        try:
            with Image.open(io.BytesIO(png)) as image:
                if image.format != 'PNG' or image.size != (1080, 1080):
                    failures.append('png_dimensions_or_format')
        except (OSError, UnidentifiedImageError):
            failures.append('png_invalid')
        if metrics.get('canvas') != {'width': 1080, 'height': 1080}:
            failures.append('canvas_dimensions')
        products = metrics.get('products', [])
        actual_ids = [product.get('id') for product in products]
        expected = {product.id: product for product in request.products}
        if len(actual_ids) != 12 or len(set(actual_ids)) != 12 or set(actual_ids) != set(expected):
            failures.append('product_ids')
        by_id = {item.get('id'): item for item in products}
        for product_id, source in expected.items():
            item = by_id.get(product_id)
            if not item:
                continue
            price = format(source.price, '.2f').replace('.', ',')
            if (item.get('name') != source.name or item.get('unit') != source.unit
                    or item.get('price') != price):
                failures.append(f'commercial_data:{product_id}')
            card = item.get('bounds', {})
            for rect in [card, *(img.get('clip_bounds', img.get('bounds', {})) for img in item.get('images', []))]:
                if (rect.get('x', -1) < -1 or rect.get('y', -1) < -1
                        or rect.get('x', 2000) + rect.get('w', 0) > 1081
                        or rect.get('y', 2000) + rect.get('h', 0) > 1081):
                    failures.append(f'element_bounds:{product_id}')
                    break
            for image in item.get('images', []):
                rect = image.get('clip_bounds', image.get('bounds', {})) if image.get('clip_hidden') else image.get('bounds', {})
                if (rect.get('x', -1) < card.get('x', 0) - 1 or rect.get('y', -1) < card.get('y', 0) - 1
                        or rect.get('x', 2000) + rect.get('w', 0) > card.get('x', 0) + card.get('w', 0) + 1
                        or rect.get('y', 2000) + rect.get('h', 0) > card.get('y', 0) + card.get('h', 0) + 1):
                    failures.append(f'image_clipping:{product_id}')
                    break
            if any(not image.get('loaded') for image in item.get('images', [])):
                failures.append(f'image_not_loaded:{product_id}')
        failures.extend('text_overflow:' + str(item.get('className', item.get('tag', 'unknown'))) + ':'
                        + str(item.get('productId') or 'banner') + ':' + str(item.get('scrollWidth', 0) - item.get('clientWidth', 0))
                        for item in metrics.get('overflowing', []))
        if metrics.get('browser_errors'):
            failures.append('browser_errors')
        # The layout planner and DesignSpec validator own logical overlap; this checks rendered boxes too.
        for index, item in enumerate(products):
            a = item.get('bounds', {})
            for other in products[index + 1:]:
                b = other.get('bounds', {})
                if (a.get('x', 0) < b.get('x', 0) + b.get('w', 0) - 1
                        and b.get('x', 0) < a.get('x', 0) + a.get('w', 0) - 1
                        and a.get('y', 0) < b.get('y', 0) + b.get('h', 0) - 1
                        and b.get('y', 0) < a.get('y', 0) + a.get('h', 0) - 1):
                    failures.append('rendered_overlap')
        if isinstance(design, DesignSpecV2) and design.template == 'weekend_hero':
            if not any(item.get('role') == 'hero' for item in products):
                failures.append('hero_missing')
        issues = () if not failures else ({'code': 'text_clipping', 'severity': 'high'},)
        return QAOutcome(VisualQAResult(approved=not failures, issues=issues), tuple(failures))


class LLMVisualQA:
    def __init__(self, settings: VisualQASettings, transport=None):
        self.settings = settings
        self.transport = transport
        self.last_usage = None

    async def inspect(self, png: bytes, **_context) -> VisualQAResult:
        content = [{'type': 'input_text', 'text': 'Avalie somente a imagem fornecida.'},
                   {'type': 'input_image', 'image_url': 'data:image/png;base64,' + base64.b64encode(png).decode('ascii')}]
        text, self.last_usage = await structured_output(self.settings, name='visual_qa',
            schema=VisualQAResult.model_json_schema(), instructions=PROMPT,
            input_data=[{'role': 'user', 'content': content}], transport=self.transport)
        return VisualQAResult.model_validate_json(text)


async def inspect_with_ai(qa, png: bytes, settings: VisualQASettings | None = None) -> VisualQAResult:
    settings = settings or VISUAL_QA_SETTINGS
    logger.info(json.dumps({'event': 'visual_qa_ai_started'}))
    if hasattr(qa, 'last_usage'):
        qa.last_usage = None
    result = await asyncio.wait_for(qa.inspect(png), timeout=settings.timeout_seconds)
    result = VisualQAResult.model_validate_json(result.model_dump_json())
    logger.info(json.dumps({'event': 'visual_qa_ai_completed', 'approved': result.approved}))
    return result
