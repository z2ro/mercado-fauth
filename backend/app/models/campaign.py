from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .product import Product, Text


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
    template: Literal['supermarket_12'] = 'supermarket_12'
    campaign: Campaign
    products: tuple[Product, ...] = Field(min_length=12, max_length=12)

    @field_validator('products')
    @classmethod
    def unique_ids(cls, products):
        if len({p.id for p in products}) != len(products):
            raise ValueError('IDs devem ser únicos.')
        return products
