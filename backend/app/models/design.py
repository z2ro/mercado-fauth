from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Placement(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    product_id: str
    slot: int = Field(ge=0, lt=12)
    x: int = Field(ge=0, lt=4)
    y: int = Field(ge=0, lt=3)
    featured: bool


class DesignSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    template: Literal['supermarket_12'] = 'supermarket_12'
    width: Literal[1080] = 1080
    height: Literal[1080] = 1080
    products: tuple[Placement, ...] = Field(min_length=12, max_length=12)

    @model_validator(mode='after')
    def valid_grid(self):
        if {p.slot for p in self.products} != set(range(12)):
            raise ValueError('Slots devem ser únicos e completos.')
        if len({p.product_id for p in self.products}) != 12:
            raise ValueError('Produtos devem ser únicos.')
        if any(p.x != p.slot % 4 or p.y != p.slot // 4 for p in self.products):
            raise ValueError('Coordenadas inconsistentes.')
        return self
