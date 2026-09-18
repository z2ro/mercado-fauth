from html import escape
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.app import main
from backend.app.assets.processor import product_image
from backend.app.layout.planner import plan_layout
from backend.app.renderer.renderer import render_html, format_price


def test_renderer_original_data(request_model):
    html = render_html(request_model, plan_layout(request_model))
    for product in request_model.products:
        price = format_price(product.price)
        assert escape(product.name) in html
        assert f'R$ {price["integer"]},{price["decimal"]} {escape(product.unit)}' in html
        assert f'data-product-id="{product.id}"' in html
    assert html.count('data-product-id=') == 12


def test_html_escape(request_model):
    changed = request_model.products[0].model_copy(update={'name':'<script>alert(1)</script>'})
    request = request_model.model_copy(update={'products':(changed, *request_model.products[1:])})
    html = render_html(request, plan_layout(request))
    assert '<script>alert' not in html
    assert '&lt;script&gt;' in html


def test_missing_image_placeholder(request_model, tmp_path, caplog):
    html = render_html(request_model, plan_layout(request_model), tmp_path)
    assert html.count('IMAGEM INDISPONÍVEL') == 12
    assert 'Imagem ausente ou inválida' in caplog.text


def test_invalid_image(tmp_path, caplog):
    (tmp_path / 'bad.png').write_text('invalid')
    assert product_image('bad.png',tmp_path) is None
    assert 'bad.png' in caplog.text


def test_path_traversal(tmp_path):
    assets = tmp_path / 'assets'
    assets.mkdir()
    Image.new('RGB',(10,10)).save(tmp_path / 'secret.png')
    assert product_image('../secret.png',assets) is None
    assert product_image(str(tmp_path / 'secret.png'),assets) is None
    (assets / 'link.png').symlink_to(tmp_path / 'secret.png')
    assert product_image('link.png',assets) is None


def test_valid_image(tmp_path):
    Image.new('RGB',(10,20)).save(tmp_path / 'valid.png')
    assert product_image('valid.png',tmp_path).startswith('data:image/png;base64,')


def test_health():
    response = TestClient(main.app).get('/health')
    assert response.status_code == 200
    assert response.json() == {'status':'ok'}


@pytest.mark.parametrize('mutation', ['count','duplicate','float','category','impossible'])
def test_api_invalid(payload, mutation):
    if mutation == 'count':
        payload['products'].pop()
    elif mutation == 'duplicate':
        payload['products'][1]['id'] = payload['products'][0]['id']
    elif mutation == 'float':
        payload['products'][0]['price'] = 39.90
    elif mutation == 'category':
        payload['products'][0]['category'] = 'invalid'
    else:
        for p in payload['products']:
            p['category'] = 'acougue'
        payload['products'][0]['category'] = 'limpeza'
    assert TestClient(main.app).post('/api/v1/banners', json=payload).status_code == 422


def test_post_real_chromium(payload, tmp_path, monkeypatch):
    monkeypatch.setattr(main,'OUTPUT_DIR',tmp_path)
    response = TestClient(main.app).post('/api/v1/banners',json=payload)
    assert response.status_code == 201, response.text
    data = response.json()
    assert data['status'] == 'created'
    assert len(data['design']['products']) == 12
    with Image.open(tmp_path / Path(data['file']).name) as png:
        assert png.size == (1080,1080)
        assert png.format == 'PNG'
