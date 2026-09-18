import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError

from backend.app import main
from backend.app.layout.planner import plan_layout, select_template
from backend.app.layout.rules import hard_rules_valid
from backend.app.models.campaign import BannerRequest
from backend.app.renderer.renderer import format_price, render_html


EXAMPLE = Path(__file__).resolve().parents[2] / 'examples/faith-reference-first.json'


def test_faith_reference_example_and_planner():
    request = BannerRequest.model_validate_json(EXAMPLE.read_text())
    design = plan_layout(request)

    assert select_template(request) == 'faith_reference_12'
    assert design.template == 'faith_reference_12'
    assert {item.slot for item in design.products} == set(range(12))
    assert {item.product_id for item in design.products} == {item.id for item in request.products}
    assert [item.product_id for item in sorted(design.products, key=lambda item: item.slot)] == [
        item.id for item in request.products
    ]
    assert request.campaign.secondary_phone == '(12) 99725-0029'
    assert request.campaign.hours.saturday == 'SÁB. 7:00 ATÉ 18:00'


def test_faith_reference_render_preserves_campaign_data():
    request = BannerRequest.model_validate_json(EXAMPLE.read_text())
    html = render_html(request, plan_layout(request))

    for product in request.products:
        price = format_price(product.price)
        assert f'data-name="{product.name}"' in html
        assert f'data-price="{price["integer"]},{price["decimal"]}"' in html
        assert f'data-unit="{product.unit}"' in html
        assert product.name in html
    for text in (
        '@mercadofauth', '(12) 3916-5334', '(12) 99725-0029',
        'Av. Ouro Fino, 670 - Bosque dos Eucaliptos',
        'SEG. A SEX. 7:00 ATÉ 20:00', 'SÁB. 7:00 ATÉ 18:00', 'DOM. 7:00 ATÉ 14:00',
        'NOS SIGA NAS REDES SOCIAIS', 'HORÁRIO DE FUNCIONAMENTO',
    ):
        assert text in html
    assert html.count('data-product-id=') == 12
    assert html.count('class="currency"') == 12
    assert html.count('class="integer"') == 12
    assert html.count('class="decimal"') == 12
    assert html.count('class="placeholder-art') == 12
    assert 'PLACEHOLDER' not in html


def test_faith_reference_keeps_supplied_product_photo(request_model):
    first = request_model.products[0].model_copy(update={'image': 'assets/products/synthetic/square.png'})
    request = request_model.model_copy(update={'template': 'faith_reference_12', 'products': (first, *request_model.products[1:])})
    html = render_html(request, plan_layout(request))

    assert 'class="asset-square"' in html
    assert 'data:image/png;base64,' in html
    assert html.count('class="placeholder-art') == 11


def test_legacy_payload_stays_on_supermarket_12(payload):
    payload.pop('template', None)
    assert payload['products'][0]['featured'] is True
    request = BannerRequest.model_validate(payload)
    assert plan_layout(request).template == 'supermarket_12'


def test_faith_reference_is_manual_only():
    payload = json.loads(EXAMPLE.read_text())
    payload['art_direction_mode'] = 'auto'
    with pytest.raises(ValidationError):
        BannerRequest.model_validate(payload)


def test_faith_reference_replans_when_authored_order_breaks_hard_rules():
    payload = json.loads(EXAMPLE.read_text())
    payload['products'][2]['category'] = 'limpeza'
    payload['products'][2]['subcategory'] = 'limpeza_geral'
    request = BannerRequest.model_validate(payload)
    design = plan_layout(request)
    by_id = {product.id: product for product in request.products}
    ordered = [by_id[item.product_id] for item in sorted(design.products, key=lambda item: item.slot)]

    assert design.template == 'faith_reference_12'
    assert hard_rules_valid(ordered)


def test_faith_reference_api_generates_png(monkeypatch, tmp_path):
    monkeypatch.setattr(main, 'OUTPUT_DIR', tmp_path)
    payload = json.loads(EXAMPLE.read_text())
    response = TestClient(main.app).post('/api/v1/banners', json=payload)

    assert response.status_code == 201, response.text
    result = response.json()
    assert result['design']['template'] == 'faith_reference_12'
    image_path = tmp_path / Path(result['file']).name
    with Image.open(image_path) as image:
        assert image.format == 'PNG'
        assert image.size == (1080, 1080)
