from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from PIL import Image

from backend.app.layout.geometry import Rectangle, rectangle_within_grid, rectangles_adjacent, rectangles_overlap
from backend.app.layout.patterns import PATTERNS, PRICE_ATTACK
from backend.app.layout.planner import ImpossibleLayout, plan_layout, select_template
from backend.app.layout.rules import placement_layout_score, rectangles_hard_rules_valid
from backend.app.models.campaign import BannerRequest
from backend.app.models.design import DesignSpecV2, RectPlacement, VisualDirection
from backend.app.renderer.renderer import render_html
from backend.app.renderer.screenshot import screenshot

ROOT = Path(__file__).resolve().parents[2]


def load_example(name):
    return BannerRequest.model_validate_json((ROOT / 'examples' / name).read_text())


def test_rectangle_geometry_edges_and_bounds():
    large = Rectangle(0, 0, 6, 4)
    assert rectangles_adjacent(large, Rectangle(6, 1, 2, 2))
    assert rectangles_adjacent(large, Rectangle(1, 4, 3, 2))
    assert not rectangles_adjacent(large, Rectangle(6, 4, 2, 2))
    assert rectangles_overlap(large, Rectangle(5, 1, 2, 2))
    assert rectangle_within_grid(large, 12, 8)
    assert not rectangle_within_grid(Rectangle(11, 0, 2, 1), 12, 8)


@pytest.mark.parametrize('category', ('acougue', 'hortifruti', 'padaria'))
def test_rectangular_hard_rules_reject_cleaning_neighbors(request_model, category):
    products = {p.id: p.model_copy(update={'category': 'higiene'}) for p in request_model.products}
    products['left'] = request_model.products[0].model_copy(update={'id': 'left', 'category': category})
    products['clean'] = request_model.products[1].model_copy(update={'id': 'clean', 'category': 'limpeza'})
    placements = (
        RectPlacement(product_id='left', x=0, y=0, w=6, h=4, role='hero'),
        RectPlacement(product_id='clean', x=6, y=1, w=2, h=2, role='standard'),
    )
    assert not rectangles_hard_rules_valid(placements, products)


def test_design_spec_v2_rejects_invalid_commercial_or_geometry_fields():
    request = load_example('campaign-weekend-hero.json')
    spec = plan_layout(request)
    assert isinstance(spec, DesignSpecV2)
    data = spec.model_dump()
    commercial_fields = {'price', 'sku', 'name', 'unit', 'image', 'featured'}
    assert not commercial_fields & set(data)
    assert all(not commercial_fields & set(placement) for placement in data['placements'])
    data['placements'][0]['price'] = '0.01'
    with pytest.raises(ValidationError):
        DesignSpecV2.model_validate(data)
    data = spec.model_dump()
    data['placements'][0]['w'] = 0
    with pytest.raises(ValidationError):
        DesignSpecV2.model_validate(data)
    data = spec.model_dump()
    data['placements'][1]['x'] = 0
    data['placements'][1]['y'] = 0
    with pytest.raises(ValidationError):
        DesignSpecV2.model_validate(data)


def test_patterns_tile_their_logical_grid_without_overlap():
    for pattern in PATTERNS.values():
        occupied = set()
        for placement in pattern.placements:
            assert placement.x >= 0 and placement.y >= 0
            assert placement.x + placement.w <= pattern.columns
            assert placement.y + placement.h <= pattern.rows
            cells = {(x, y) for x in range(placement.x, placement.x + placement.w)
                     for y in range(placement.y, placement.y + placement.h)}
            assert not occupied & cells
            occupied |= cells
        assert len(pattern.placements) == 12


@pytest.mark.parametrize('example,template', (
    ('campaign-weekend-hero.json', 'weekend_hero'),
    ('campaign-price-attack.json', 'price_attack'),
))
def test_planner_is_deterministic_and_respects_roles_and_hard_rules(example, template):
    request = load_example(example)
    before = request.model_dump(mode='json')
    first = plan_layout(request)
    second = plan_layout(request)
    products = {p.id: p for p in request.products}
    assert first == second
    assert first.template == template and first.version == 2
    assert len(first.placements) == len({p.product_id for p in first.placements}) == 12
    assert {p.product_id for p in first.placements} == set(products)
    assert rectangles_hard_rules_valid(first.placements, products)
    assert request.model_dump(mode='json') == before
    if template == 'weekend_hero':
        assert sum(p.role == 'hero' for p in first.placements) == 1
        assert first.hero_products == ('p001',)
        assert {'p006', 'p008'} <= set(first.featured_products)
        hero = next(p for p in first.placements if p.role == 'hero')
        assert hero.w * hero.h >= 24


def test_template_selection_is_deterministic(request_model):
    automatic = request_model.model_copy(update={'template': None})
    assert select_template(automatic) == 'weekend_hero'
    price = automatic.model_copy(update={'products': tuple(p.model_copy(update={'featured': False}) for p in automatic.products),
                                         'visual_direction': VisualDirection(emphasis='price')})
    assert select_template(price) == 'price_attack'
    assert select_template(automatic) == select_template(automatic)


def test_rectangular_score_prefers_category_groups(request_model):
    categories = ('mercearia',) * 6 + ('bebidas',) * 6
    grouped = {product.id: product.model_copy(update={'category': categories[index]})
               for index, product in enumerate(request_model.products)}
    checkerboard = {product.id: product.model_copy(update={'category': 'mercearia' if (index % 4 + index // 4) % 2 == 0 else 'bebidas'})
                    for index, product in enumerate(request_model.products)}
    placements = tuple(SimpleNamespace(product_id=product.id, x=slot.x, y=slot.y, w=slot.w, h=slot.h,
                                      role=slot.role, zone=slot.zone)
                       for product, slot in zip(request_model.products, PRICE_ATTACK.placements))
    assert placement_layout_score(placements, grouped) > placement_layout_score(placements, checkerboard)


def test_campaign_examples_render_real_chromium_pngs(tmp_path):
    for example in ('campaign.json', 'campaign-weekend-hero.json', 'campaign-price-attack.json'):
        request = load_example(example)
        design = plan_layout(request)
        html = render_html(request, design)
        for product in request.products:
            assert f'data-product-id="{product.id}"' in html
            assert product.name in html
            assert str(product.price).split('.')[0] in html
            assert product.unit in html
        if isinstance(design, DesignSpecV2):
            assert f'role-{"hero" if design.hero_products else "featured"}' in html
        destination = tmp_path / f'{example}.png'
        screenshot(html, destination)
        with Image.open(destination) as image:
            assert image.format == 'PNG'
            assert image.size == (1080, 1080)


def test_layout_impossible_is_clear(request_model):
    products = tuple(product.model_copy(update={'category': 'acougue'}) for product in request_model.products)
    products = (products[0].model_copy(update={'category': 'limpeza'}), *products[1:])
    request = request_model.model_copy(update={'template': 'weekend_hero', 'products': products})
    with pytest.raises(ImpossibleLayout, match='separação'):
        plan_layout(request)
