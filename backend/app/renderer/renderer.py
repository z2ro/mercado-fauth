from decimal import Decimal
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..assets.processor import prepare_asset
from ..config import ASSET_DIR, TEMPLATE_DIR
from ..design.system import DESIGN_SYSTEM
from ..layout.rules import hard_rules_valid, rectangles_hard_rules_valid
from ..models.campaign import BannerRequest
from ..models.design import DesignSpec, DesignSpecV2

ENV = Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=select_autoescape(['html']))


def format_price(price: Decimal) -> dict[str, str]:
    if not isinstance(price, Decimal) or not price.is_finite() or price <= 0:
        raise ValueError('Preço deve ser Decimal positivo e finito.')
    if price != price.quantize(Decimal('0.01')):
        raise ValueError('Preço não pode perder precisão na renderização.')
    integer, decimal = format(price, '.2f').split('.')
    return {'integer': integer, 'decimal': decimal}


def _asset(product, asset_dir):
    return prepare_asset(product.image, asset_dir)


def _legacy_html(request, design, asset_dir):
    by_id = {product.id: product for product in request.products}
    if {placement.product_id for placement in design.products} != set(by_id):
        raise ValueError('DesignSpec não corresponde aos produtos originais.')
    placements = sorted(design.products, key=lambda placement: placement.slot)
    ordered = [by_id[placement.product_id] for placement in placements]
    if not hard_rules_valid(ordered):
        raise ValueError('DesignSpec viola separação obrigatória.')
    cards = []
    for placement, product in zip(placements, ordered):
        if placement.featured != product.featured:
            raise ValueError('DesignSpec alterou destaque do produto.')
        cards.append({'product': product, 'placement': placement, 'price': format_price(product.price), 'image': _asset(product, asset_dir)})
    return ENV.get_template('supermarket_12/template.html').render(
        campaign=request.campaign,
        cards=cards,
        css=(TEMPLATE_DIR / 'supermarket_12' / 'style.css').read_text(),
        design_tokens=DESIGN_SYSTEM.css_variables(),
    )


def _v2_html(request, design, asset_dir):
    by_id = {product.id: product for product in request.products}
    if {placement.product_id for placement in design.placements} != set(by_id):
        raise ValueError('DesignSpec V2 não corresponde aos produtos originais.')
    if request.template and request.template != design.template:
        raise ValueError('DesignSpec V2 usa um template diferente do solicitado.')
    if not set(request.hero_products).issubset(design.hero_products):
        raise ValueError('DesignSpec V2 não respeita o produto hero solicitado.')
    if not set(request.featured_products).issubset(design.featured_products):
        raise ValueError('DesignSpec V2 não respeita os produtos featured solicitados.')
    if request.visual_direction and request.visual_direction != design.visual_direction:
        raise ValueError('DesignSpec V2 não respeita a direção visual solicitada.')
    if not rectangles_hard_rules_valid(design.placements, by_id):
        raise ValueError('DesignSpec V2 viola separação ou overlap entre departamentos.')
    cards = []
    for placement in sorted(design.placements, key=lambda item: (item.y, item.x)):
        cards.append({
            'product': by_id[placement.product_id],
            'placement': placement,
            'grid_column': placement.x + 1,
            'grid_row': placement.y + 1,
            'price': format_price(by_id[placement.product_id].price),
            'image': _asset(by_id[placement.product_id], asset_dir),
        })
    css = (TEMPLATE_DIR / design.template / 'style.css').read_text()
    return ENV.get_template(f'{design.template}/template.html').render(
        campaign=request.campaign,
        design=design,
        cards=cards,
        css=css,
        design_tokens=DESIGN_SYSTEM.css_variables(),
    )


def render_html(request: BannerRequest, design: DesignSpec | DesignSpecV2, asset_dir: Path = ASSET_DIR) -> str:
    if isinstance(design, DesignSpecV2):
        return _v2_html(request, design, asset_dir)
    return _legacy_html(request, design, asset_dir)
