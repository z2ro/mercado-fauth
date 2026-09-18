import asyncio
import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from PIL import Image
from pydantic import ValidationError

from backend.app.art_direction.director import (DeterministicArtDirector, LLMArtDirector,
                                                 direction_input, resolve_art_direction)
from backend.app.art_direction.models import (ArtDirectionInput, ArtDirectionSpec, VisualQAResult)
from backend.app.config import ArtDirectionSettings, VisualQASettings
from backend.app.layout.planner import plan_layout, select_template
from backend.app.layout.rules import rectangles_hard_rules_valid
from backend.app.models.campaign import BannerRequest
from backend.app.models.design import DesignSpecV2
from backend.app.visual_qa import DeterministicVisualQA, LLMVisualQA
from backend.app.workflow import generate_banner

ROOT = Path(__file__).resolve().parents[2]


def example(name='campaign-ai-art-director.json'):
    return BannerRequest.model_validate_json((ROOT / 'examples' / name).read_text())


def make_spec(request, template='weekend_hero'):
    safe = next(p for p in request.products if p.category.value == 'mercearia')
    others = [p for p in request.products if p.id != safe.id]
    chosen = []
    cats = {safe.category}
    for product in others:
        if product.category not in cats:
            chosen.append(product.id)
            cats.add(product.category)
        if len(chosen) == 2:
            break
    return ArtDirectionSpec(template=template, hero_products=(safe.id,) if template == 'weekend_hero' else (),
        featured_products=tuple(chosen), visual_direction={'mood': 'fresh', 'emphasis': 'product'},
        presentation_profile='balanced')


class FakeDirector:
    def __init__(self, spec=None):
        self.calls = 0
        self.inputs = []
        self.spec = spec

    async def direct(self, product):
        self.calls += 1
        self.inputs.append(product)
        return self.spec


class SlowDirector(FakeDirector):
    async def direct(self, product):
        await asyncio.sleep(.05)
        return await super().direct(product)


class FakeQA:
    def __init__(self, results):
        self.results = list(results)
        self.calls = 0

    async def inspect(self, png):
        assert png.startswith(b'\x89PNG')
        self.calls += 1
        result = self.results[min(self.calls - 1, len(self.results) - 1)]
        if isinstance(result, Exception):
            raise result
        return result


def valid_qa(**updates):
    return VisualQAResult(approved=True, issues=(), **updates)


def test_legacy_featured_does_not_opt_into_v2_and_auto_does():
    legacy = example('campaign.json').model_copy(update={'template': None})
    assert legacy.art_direction_mode == 'manual'
    assert any(p.featured for p in legacy.products)
    assert select_template(legacy) == 'supermarket_12'
    assert example().art_direction_mode == 'auto'
    assert select_template(example()) == 'supermarket_12'  # mode is resolved before planner


def test_art_direction_schema_rejects_commercial_fields_and_geometry():
    base = make_spec(example()).model_dump()
    for key in ('price', 'sku', 'x', 'y', 'w', 'h', 'css', 'html', 'name'):
        invalid = {**base, key: 'not allowed'}
        with pytest.raises(ValidationError):
            ArtDirectionSpec.model_validate(invalid)
    with pytest.raises(ValidationError):
        ArtDirectionSpec.model_validate({**base, 'presentation_profile': 'unbounded'})
    with pytest.raises(ValidationError):
        ArtDirectionSpec.model_validate({**base, 'hero_products': ['p006', 'p006']})
    with pytest.raises(ValidationError):
        ArtDirectionSpec.model_validate({**base, 'template': 'price_attack', 'hero_products': ['p006']})


def test_deterministic_art_director_is_offline_and_limits_roles():
    request = example()
    result = asyncio.run(DeterministicArtDirector().direct(direction_input(request)))
    assert result.template == 'weekend_hero'
    assert len(result.hero_products) == 1 and len(result.featured_products) <= 2
    assert not set(result.hero_products) & set(result.featured_products)
    assert result == asyncio.run(DeterministicArtDirector().direct(direction_input(request)))


def test_auto_ai_timeout_and_invalid_id_fall_back_to_deterministic(tmp_path):
    request = example()
    settings = ArtDirectionSettings(enabled=True, model='mock', api_key='secret', timeout_seconds=.001,
                                    cache_dir=tmp_path)
    result, source, _, _ = asyncio.run(resolve_art_direction(request, SlowDirector(make_spec(request)), settings))
    assert source == 'deterministic'
    assert result.hero_products[0] in {p.id for p in request.products}
    invalid = make_spec(request).model_copy(update={'hero_products': ('missing',)})
    result, source, _, _ = asyncio.run(resolve_art_direction(request, FakeDirector(invalid), settings, use_cache=False))
    assert source == 'deterministic'
    assert result.hero_products[0] in {p.id for p in request.products}


def test_art_direction_cache_hit_excludes_price_and_api_never_sends_it(tmp_path):
    request = example()
    settings = ArtDirectionSettings(enabled=True, model='fake-model', api_key='secret', cache_dir=tmp_path)
    fake = FakeDirector(make_spec(request))
    first, source, _, _ = asyncio.run(resolve_art_direction(request, fake, settings))
    changed = request.model_copy(update={'products': (request.products[0].model_copy(update={'price': Decimal('1.11')}), *request.products[1:])})
    cached, cache_source, _, _ = asyncio.run(resolve_art_direction(changed, fake, settings))
    assert source == 'ai' and cache_source == 'cache' and cached == first and fake.calls == 1
    sent = fake.inputs[0].model_dump(mode='json')
    assert set(sent) == {'campaign_title', 'products'}
    for product in sent['products']:
        assert set(product) == {'product_id', 'name', 'category', 'subcategory', 'featured'}
    assert not {'price', 'sku', 'image', 'unit'} & set(product)


def test_ai_disabled_uses_offline_fallback_and_does_not_consult_injected_director(tmp_path):
    request = example()
    fake = FakeDirector(make_spec(request))
    spec, source, _, usage = asyncio.run(resolve_art_direction(
        request, fake, ArtDirectionSettings(enabled=False, cache_dir=tmp_path)))
    assert source == 'deterministic' and usage is None
    assert fake.calls == 0 and spec.hero_products


def test_art_direction_provider_failure_uses_deterministic_fallback(tmp_path):
    class Broken(FakeDirector):
        async def direct(self, product):
            raise RuntimeError('provider unavailable')
    request = example()
    result, source, _, usage = asyncio.run(resolve_art_direction(
        request, Broken(), ArtDirectionSettings(enabled=True, model='fake', api_key='set', cache_dir=tmp_path)))
    assert source == 'deterministic' and usage is None
    assert result.hero_products[0] in {p.id for p in request.products}


def test_llm_art_director_uses_structured_schema_and_allowlisted_payload():
    request = example()
    spec = make_spec(request)
    observed = {}

    async def handler(req):
        observed['body'] = json.loads(req.content)
        observed['authorization'] = req.headers['authorization']
        return httpx.Response(200, json={'status': 'completed', 'output': [{'type': 'message', 'content': [
            {'type': 'output_text', 'text': spec.model_dump_json()}]}]})

    settings = ArtDirectionSettings(enabled=True, provider='openai', model='test-model', api_key='dont-print')
    result = asyncio.run(LLMArtDirector(settings, httpx.MockTransport(handler)).direct(direction_input(request)))
    payload = observed['body']
    assert result == spec
    assert payload['store'] is False and payload['text']['format']['strict'] is True
    user = json.loads(payload['input'])
    assert set(user) == {'campaign_title', 'products'}
    assert all(set(product) == {'product_id', 'name', 'category', 'subcategory', 'featured'} for product in user['products'])
    assert 'dont-print' not in json.dumps(payload)
    assert observed['authorization'] == 'Bearer dont-print'


def test_manual_never_calls_art_director_even_when_injected(tmp_path):
    request = example('campaign.json').model_copy(update={'art_direction_mode': 'manual'})
    fake = FakeDirector(make_spec(request))
    build = asyncio.run(generate_banner(request, tmp_path / 'legacy.png', art_director=fake))
    assert fake.calls == 0
    assert build.design.template == 'supermarket_12'
    assert build.art_direction is None
    assert build.visual_qa['approved'] is True
    assert build.performance['render_count'] == 1


def test_auto_with_fake_director_and_fake_visual_qa_renders_and_preserves_commercial_data(tmp_path):
    request = example()
    original = tuple(p.model_dump(mode='json') for p in request.products)
    director = FakeDirector(make_spec(request))
    qa = FakeQA([valid_qa()])
    build = asyncio.run(generate_banner(request, tmp_path / 'auto.png', art_director=director, visual_qa=qa,
        art_settings=ArtDirectionSettings(enabled=True, model='fake', api_key='configured', cache_dir=tmp_path / 'cache'),
        qa_settings=VisualQASettings(enabled=True, max_renders=2)))
    assert isinstance(build.design, DesignSpecV2)
    assert build.art_direction['source'] == 'ai'
    assert qa.calls == 1 and build.performance['render_count'] == 1
    assert tuple(p.model_dump(mode='json') for p in request.products) == original
    assert rectangles_hard_rules_valid(build.design.placements, {p.id: p for p in request.products})
    with Image.open(tmp_path / 'auto.png') as image:
        assert image.size == (1080, 1080)


def test_visual_qa_rejection_refines_once_then_approves(tmp_path):
    request = example()
    reject = VisualQAResult(approved=False, issues=({'code': 'weak_hero_emphasis', 'severity': 'medium'},),
                            suggested_profile='image_focus')
    qa = FakeQA([reject, valid_qa()])
    build = asyncio.run(generate_banner(request, tmp_path / 'refined.png', visual_qa=qa,
        qa_settings=VisualQASettings(enabled=True, max_renders=2)))
    assert qa.calls == 2 and build.performance['render_count'] == 2
    assert build.design.presentation_profile == 'image_focus'
    assert build.visual_qa['approved'] is True


def test_visual_qa_refinement_has_a_hard_render_limit(tmp_path):
    request = example()
    reject = VisualQAResult(approved=False, issues=({'code': 'poor_balance', 'severity': 'low'},))
    qa = FakeQA([reject])
    build = asyncio.run(generate_banner(request, tmp_path / 'bounded.png', visual_qa=qa,
        qa_settings=VisualQASettings(enabled=True, max_renders=2)))
    assert qa.calls == build.performance['render_count'] == 3
    assert build.visual_qa['approved'] is False
    assert (tmp_path / 'bounded.png').is_file()


def test_rejected_legacy_visual_qa_does_not_repeat_an_unchangeable_render(tmp_path):
    request = example('campaign.json')
    reject = VisualQAResult(approved=False, issues=({'code': 'poor_balance', 'severity': 'low'},),
                            suggested_profile='image_focus')
    qa = FakeQA([reject])
    build = asyncio.run(generate_banner(request, tmp_path / 'legacy.png', visual_qa=qa,
        qa_settings=VisualQASettings(enabled=True, max_renders=2)))
    assert build.design.template == 'supermarket_12'
    assert qa.calls == build.performance['render_count'] == 1
    assert build.visual_qa['approved'] is False


def test_visual_qa_provider_failure_uses_deterministic_approval(tmp_path):
    request = example()
    qa = FakeQA([TimeoutError('no details')])
    build = asyncio.run(generate_banner(request, tmp_path / 'fallback.png', visual_qa=qa,
        qa_settings=VisualQASettings(enabled=True, max_renders=2)))
    assert build.visual_qa['source'] == 'deterministic_fallback'
    assert build.visual_qa['approved'] is True and build.performance['render_count'] == 1


def test_deterministic_qa_rejects_invalid_png_size_and_missing_product(request_model):
    design = plan_layout(request_model)
    qa = DeterministicVisualQA()
    invalid = asyncio.run(qa.inspect(b'not a png', request=request_model, design=design,
        metrics={'canvas': {'width': 1, 'height': 1}, 'products': []}))
    assert invalid.result.approved is False
    assert {'png_invalid', 'canvas_dimensions', 'product_ids'} <= set(invalid.failures)
    with pytest.raises(ValidationError):
        VisualQAResult.model_validate({'approved': True, 'issues': [], 'suggested_profile': 'random'})
    with pytest.raises(ValidationError):
        VisualQAResult.model_validate({'approved': True, 'issues': [], 'css': 'body{}'})


def test_llm_visual_qa_sends_only_the_banner_image():
    observed = {}

    async def handler(req):
        observed['body'] = json.loads(req.content)
        return httpx.Response(200, json={'status': 'completed', 'output': [{'type': 'message', 'content': [
            {'type': 'output_text', 'text': valid_qa().model_dump_json()}]}]})

    settings = VisualQASettings(enabled=True, model='vision-test', api_key='secret')
    result = asyncio.run(LLMVisualQA(settings, httpx.MockTransport(handler)).inspect(b'\x89PNG-test'))
    assert result.approved
    content = observed['body']['input'][0]['content']
    assert [item['type'] for item in content] == ['input_text', 'input_image']
    assert 'price' not in json.dumps(content) and 'phone' not in json.dumps(content)


def test_brand_moods_and_profiles_are_bounded_and_visibly_distinct():
    from backend.app.design.system import DESIGN_SYSTEM
    base = DESIGN_SYSTEM.css_variables('balanced', 'classic')
    assert base != DESIGN_SYSTEM.css_variables('image_focus', 'classic')
    assert base != DESIGN_SYSTEM.css_variables('balanced', 'fresh')
    assert base != DESIGN_SYSTEM.css_variables('balanced', 'premium')
    assert base != DESIGN_SYSTEM.css_variables('balanced', 'bold')


def test_optional_brand_logo_is_embedded_and_invalid_path_falls_back(request_model, tmp_path, monkeypatch):
    from backend.app.renderer import renderer
    logo = tmp_path / 'logo.png'
    Image.new('RGBA', (30, 20), (20, 80, 50, 255)).save(logo)
    monkeypatch.setattr(renderer, 'BRAND_LOGO', 'logo.png')
    html = renderer.render_html(request_model, plan_layout(request_model), tmp_path)
    assert 'data:image/png;base64,' in html
    monkeypatch.setattr(renderer, 'BRAND_LOGO', '../outside.png')
    html = renderer.render_html(request_model, plan_layout(request_model), tmp_path)
    assert 'brand-mark' not in html  # legacy uses the text-only brand fallback
    assert 'Mercado Exemplo' in html
