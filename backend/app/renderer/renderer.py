from decimal import Decimal
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..assets.processor import product_image
from ..config import ASSET_DIR, TEMPLATE_DIR
from ..layout.rules import hard_rules_valid
from ..models.campaign import BannerRequest
from ..models.design import DesignSpec

ENV = Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=select_autoescape(['html']))


def format_price(price: Decimal) -> dict[str, str]:
    if not isinstance(price, Decimal) or not price.is_finite() or price <= 0:
        raise ValueError('Preço deve ser Decimal positivo e finito.')
    if price != price.quantize(Decimal('0.01')):
        raise ValueError('Preço não pode perder precisão na renderização.')
    integer, decimal = format(price, '.2f').split('.')
    return {'integer': integer, 'decimal': decimal}


def render_html(request: BannerRequest, design: DesignSpec, asset_dir: Path = ASSET_DIR) -> str:
    by_id = {p.id: p for p in request.products}
    if {p.product_id for p in design.products} != set(by_id):
        raise ValueError('DesignSpec não corresponde aos produtos originais.')
    placements = sorted(design.products, key=lambda p: p.slot)
    ordered = [by_id[p.product_id] for p in placements]
    if not hard_rules_valid(ordered):
        raise ValueError('DesignSpec viola separação obrigatória.')
    cards = []
    for placement, product in zip(placements, ordered):
        if placement.featured != product.featured:
            raise ValueError('DesignSpec alterou destaque do produto.')
        cards.append({'product': product, 'placement': placement, 'price': format_price(product.price), 'image': product_image(product.image, asset_dir)})
    return ENV.get_template(f'{design.template}/template.html').render(
        campaign=request.campaign, cards=cards,
        css=(TEMPLATE_DIR / design.template / 'style.css').read_text(),
    )
