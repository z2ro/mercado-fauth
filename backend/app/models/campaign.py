from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from ..design.system import FEATURED_COUNT, HERO_COUNT
from .design import TemplateName, VisualDirection
from .product import Product, ResolvedProduct, Text


class Campaign(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    title: Text
    valid_until: str
    brand: Text
    address: Text
    phone: Text
    instagram: Text

    @field_validator('valid_until')
    @classmethod
    def valid_date(cls, value):
        datetime.strptime(value, '%d/%m/%Y')
        return value

    @field_validator('title', 'brand', 'address', 'phone', 'instagram')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('Texto não pode ser vazio.')
        return value


class BannerRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    template: TemplateName | None = None
    hero_products: tuple[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)], ...] = Field(default=(), max_length=HERO_COUNT)
    featured_products: tuple[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)], ...] = Field(default=(), max_length=FEATURED_COUNT)
    visual_direction: VisualDirection | None = None
    art_direction_mode: Literal['manual', 'auto'] = 'manual'
    campaign: Campaign
    products: tuple[Product, ...] = Field(min_length=12, max_length=12)

    @field_validator('products')
    @classmethod
    def unique_ids(cls, products):
        if len({p.id for p in products}) != len(products):
            raise ValueError('IDs devem ser únicos.')
        return products

    @model_validator(mode='after')
    def valid_design_direction(self):
        hero = set(self.hero_products)
        featured = set(self.featured_products)
        product_ids = {product.id for product in self.products}
        if len(featured) != len(self.featured_products) or hero & featured:
            raise ValueError('IDs de hero e featured devem ser únicos e distintos.')
        if (hero | featured) - product_ids:
            raise ValueError('Direção visual referencia um produto inexistente.')
        if self.template == 'supermarket_12' and (hero or featured or self.visual_direction):
            raise ValueError('supermarket_12 mantém o layout legado e não aceita direção hero/featured.')
        if self.template == 'price_attack' and hero:
            raise ValueError('price_attack não aceita hero_products.')
        return self


class ResolvedBannerRequest(BannerRequest):
    products: tuple[ResolvedProduct, ...] = Field(min_length=12, max_length=12)
