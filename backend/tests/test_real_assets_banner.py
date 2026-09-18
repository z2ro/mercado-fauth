import json
from html import escape
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image
from playwright.sync_api import sync_playwright

from backend.app import main
from backend.app.config import ROOT
from backend.app.layout.planner import plan_layout
from backend.app.models.campaign import BannerRequest
from backend.app.renderer.renderer import format_price, render_html


def test_synthetic_banner_real_chromium(tmp_path,monkeypatch):
    payload=json.loads((ROOT/'examples/campaign-real-assets.json').read_text())
    request=BannerRequest.model_validate(payload)
    before=request.model_dump_json()
    html=render_html(request,plan_layout(request))
    assert html.count('src="data:image/png;base64,')==12
    for shape in ('vertical','horizontal','square'):
        assert f'asset-{shape}' in html
    for product in request.products:
        price=format_price(product.price)
        assert escape(product.name) in html
        assert f'R$ {price["integer"]},{price["decimal"]} {escape(product.unit)}' in html
    with sync_playwright() as p:
        browser=p.chromium.launch()
        page=browser.new_page(viewport={'width':1080,'height':1080})
        page.set_content(html)
        assert page.locator('img').evaluate_all('(imgs) => imgs.every(i => i.complete && i.naturalWidth > 0)')
        assert page.locator('.card').evaluate_all('''cards => cards.every(card => {
            const img = card.querySelector('img').getBoundingClientRect();
            const area = card.getBoundingClientRect();
            const text = card.querySelector('.details').getBoundingClientRect();
            return img.left >= area.left && img.right <= area.right && img.top >= area.top && img.bottom <= text.top + 1;
        })''')
        browser.close()
    monkeypatch.setattr(main,'OUTPUT_DIR',tmp_path)
    response=TestClient(main.app).post('/api/v1/banners',json=payload)
    assert response.status_code==201,response.text
    with Image.open(tmp_path/Path(response.json()['file']).name) as image:
        assert image.size==(1080,1080)
    assert request.model_dump_json()==before
