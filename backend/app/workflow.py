import asyncio
import json
import logging
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .art_direction.director import (DeterministicArtDirector, LLMArtDirector,
                                     direction_input, resolve_art_direction)
from .art_direction.models import ArtDirectionSpec
from .classification.resolver import resolve_products
from .config import ART_DIRECTION_SETTINGS, VISUAL_QA_SETTINGS, ArtDirectionSettings, VisualQASettings
from .layout.planner import ImpossibleLayout, plan_layout
from .models.campaign import BannerRequest
from .models.design import DesignSpec, DesignSpecV2
from .renderer.renderer import render_html
from .renderer.screenshot import screenshot
from .visual_qa import DeterministicVisualQA, LLMVisualQA, QAOutcome, inspect_with_ai

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BannerBuild:
    design: DesignSpec | DesignSpecV2
    classification: dict
    art_direction: dict | None
    visual_qa: dict
    performance: dict


def configured_art_director():
    settings = ART_DIRECTION_SETTINGS
    if settings.enabled and settings.provider == 'openai' and settings.model and settings.api_key.get_secret_value():
        return LLMArtDirector(settings)
    return None


def configured_visual_qa():
    settings = VISUAL_QA_SETTINGS
    if settings.enabled and settings.provider == 'openai' and settings.model and settings.api_key.get_secret_value():
        return LLMVisualQA(settings)
    return None


def _apply_direction(request, spec: ArtDirectionSpec):
    return request.model_copy(update={
        'template': spec.template,
        'hero_products': spec.hero_products,
        'featured_products': spec.featured_products,
        'visual_direction': spec.visual_direction,
    })


def _art_metadata(source, spec):
    return {'source': source, 'template': spec.template,
            'hero_products': list(spec.hero_products), 'featured_products': list(spec.featured_products),
            'visual_direction': spec.visual_direction.model_dump(),
            'presentation_profile': spec.presentation_profile}


def _manual_direction(design):
    if not isinstance(design, DesignSpecV2):
        return None
    return {
        'source': 'manual', 'template': design.template, 'hero_products': list(design.hero_products),
        'featured_products': list(design.featured_products),
        'visual_direction': design.visual_direction.model_dump(),
        'presentation_profile': design.presentation_profile,
    }


def _next_profile(current, attempt):
    safe = ('balanced', 'image_focus', 'price_focus', 'premium', 'dense')
    return safe[(safe.index(current) + 1) % len(safe)]


async def generate_banner(request: BannerRequest, destination: Path, *, art_director=None, visual_qa=None,
                          art_settings: ArtDirectionSettings | None = None,
                          qa_settings: VisualQASettings | None = None) -> BannerBuild:
    resolved, classification = await resolve_products(request)
    source = 'manual'
    direction = None
    art_latency = 0.0
    art_usage = None
    profile = 'balanced'
    directed = resolved
    if request.art_direction_mode == 'auto':
        spec, source, art_latency, art_usage = await resolve_art_direction(
            resolved, art_director or configured_art_director(), art_settings or ART_DIRECTION_SETTINGS)
        directed = _apply_direction(resolved, spec)
        profile = spec.presentation_profile
        direction = spec
    else:
        logger.info(json.dumps({'event': 'art_direction_manual'}))
    try:
        design = plan_layout(directed, profile, direction)
    except ImpossibleLayout:
        if source not in {'ai', 'cache'}:
            raise
        logger.warning(json.dumps({'event': 'art_direction_invalid', 'reason': 'impossible_layout'}))
        direction = await DeterministicArtDirector().direct(direction_input(resolved))
        source, profile = 'deterministic', direction.presentation_profile
        directed = _apply_direction(resolved, direction)
        design = plan_layout(directed, profile, direction)

    qa_settings = qa_settings or VISUAL_QA_SETTINGS
    external_qa = visual_qa if visual_qa is not None else configured_visual_qa()
    max_renders = qa_settings.max_renders if external_qa is not None else 0
    deterministic = DeterministicVisualQA()
    final_path = Path(destination)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    qa_result = None
    qa_source = 'deterministic'
    render_count = 0
    qa_latency = 0.0
    qa_usage = None
    with tempfile.TemporaryDirectory(prefix='.banner-qa-', dir=final_path.parent) as workdir:
        workdir = Path(workdir)
        while True:
            render_count += 1
            design = plan_layout(directed, profile, direction)
            html = render_html(directed, design)
            candidate = workdir / f'render-{render_count}.png'
            dom = await asyncio.to_thread(screenshot, html, candidate)
            png = candidate.read_bytes()
            outcome: QAOutcome = await deterministic.inspect(png, request=resolved, design=design, metrics=dom)
            logger.info(json.dumps({'event': 'visual_qa_deterministic_pass' if outcome.result.approved
                                   else 'visual_qa_deterministic_failed', 'failures': outcome.failures}))
            if not outcome.result.approved:
                raise ValueError('Banner reprovado no QA determinístico: ' + ', '.join(outcome.failures))
            qa_result, qa_source = outcome.result, 'deterministic'
            if external_qa is not None:
                try:
                    started = time.perf_counter()
                    try:
                        qa_result = await inspect_with_ai(external_qa, png, qa_settings)
                    finally:
                        qa_latency += time.perf_counter() - started
                    current_usage = getattr(external_qa, 'last_usage', None)
                    if current_usage:
                        qa_usage = {key: current_usage[key] + (qa_usage[key] if qa_usage else 0)
                                    for key in ('input_tokens', 'output_tokens', 'total_tokens')}
                    if any(issue.product_id and issue.product_id not in {p.id for p in resolved.products}
                           for issue in qa_result.issues):
                        raise ValueError('Visual QA referencia produto desconhecido.')
                    qa_source = 'ai'
                    logger.info(json.dumps({'event': 'visual_qa_approved' if qa_result.approved
                                            else 'visual_qa_rejected'}))
                except Exception as exc:
                    logger.warning(json.dumps({'event': 'visual_qa_ai_failed', 'error': type(exc).__name__}))
                    qa_result, qa_source = outcome.result, 'deterministic_fallback'
                if not qa_result.approved and isinstance(design, DesignSpecV2) and render_count <= max_renders:
                    logger.info(json.dumps({'event': 'visual_refinement_started', 'render': render_count}))
                    next_profile = qa_result.suggested_profile or _next_profile(profile, render_count)
                    if next_profile == profile:
                        next_profile = _next_profile(profile, render_count)
                    profile = next_profile
                    if direction is not None:
                        direction = direction.model_copy(update={'presentation_profile': profile})
                    logger.info(json.dumps({'event': 'visual_refinement_applied', 'profile': profile}))
                    continue
                if not qa_result.approved:
                    logger.warning(json.dumps({'event': 'visual_refinement_limit_reached', 'renders': render_count}))
            candidate.replace(final_path)
            break
    if direction is not None:
        art_metadata = _art_metadata(source, direction)
    else:
        art_metadata = _manual_direction(design)
    return BannerBuild(design=design, classification=classification, art_direction=art_metadata,
                       visual_qa={'source': qa_source, **qa_result.model_dump()},
                       performance={'art_direction_latency': round(art_latency, 6),
                                    'art_direction_usage': art_usage,
                                    'visual_qa_latency': round(qa_latency, 6),
                                    'visual_qa_usage': qa_usage, 'render_count': render_count})
