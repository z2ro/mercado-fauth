from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from ..design.system import FEATURED_COUNT, HERO_COUNT

TemplateName = Literal['supermarket_12', 'weekend_hero', 'price_attack']
PlacementRole = Literal['hero', 'featured', 'standard', 'compact']
PresentationProfile = Literal['balanced', 'image_focus', 'price_focus', 'dense', 'premium']


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


class VisualDirection(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    mood: Literal['bold', 'fresh', 'premium', 'classic'] = 'bold'
    emphasis: Literal['product', 'price', 'balanced'] = 'product'


class RectPlacement(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    product_id: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    x: int = Field(ge=0, strict=True)
    y: int = Field(ge=0, strict=True)
    w: int = Field(ge=1, strict=True)
    h: int = Field(ge=1, strict=True)
    role: PlacementRole
    image_scale: float = Field(default=1.0, ge=.75, le=1.25, allow_inf_nan=False)
    price_scale: float = Field(default=1.0, ge=.8, le=1.3, allow_inf_nan=False)
    zone: Literal['hero', 'promotion', 'regular'] = 'regular'


class DesignSpecV2(BaseModel):
    """Layout-only schema; commercial fields are rejected by extra='forbid'."""

    model_config = ConfigDict(frozen=True, extra='forbid')
    version: Literal[2] = 2
    template: Literal['weekend_hero', 'price_attack']
    width: Literal[1080] = 1080
    height: Literal[1080] = 1080
    grid_columns: int = Field(ge=1, le=24, strict=True)
    grid_rows: int = Field(ge=1, le=12, strict=True)
    hero_products: tuple[str, ...] = Field(max_length=HERO_COUNT)
    featured_products: tuple[str, ...] = Field(max_length=FEATURED_COUNT)
    visual_direction: VisualDirection = Field(default_factory=VisualDirection)
    presentation_profile: PresentationProfile = 'balanced'
    placements: tuple[RectPlacement, ...] = Field(min_length=12, max_length=12)

    @model_validator(mode='after')
    def validate_geometry(self):
        ids = [placement.product_id for placement in self.placements]
        if len(set(ids)) != len(ids):
            raise ValueError('Product IDs devem ser únicos no DesignSpec.')
        if len(set(self.featured_products)) != len(self.featured_products):
            raise ValueError('Featured product IDs devem ser únicos.')
        if (set(self.hero_products) | set(self.featured_products)) - set(ids):
            raise ValueError('Direção visual referencia produtos ausentes.')
        if set(self.hero_products) & set(self.featured_products):
            raise ValueError('Hero e featured products devem ser distintos.')
        occupied = []
        for placement in self.placements:
            if placement.x + placement.w > self.grid_columns or placement.y + placement.h > self.grid_rows:
                raise ValueError('Placement fora dos limites do grid.')
            for other in occupied:
                overlap_x = placement.x < other.x + other.w and other.x < placement.x + placement.w
                overlap_y = placement.y < other.y + other.h and other.y < placement.y + placement.h
                if overlap_x and overlap_y:
                    raise ValueError('Placements não podem se sobrepor.')
            occupied.append(placement)
        if self.template == 'weekend_hero' and (len(self.hero_products) != 1 or not any(p.role == 'hero' for p in self.placements)):
            raise ValueError('weekend_hero exige exatamente um produto hero.')
        if self.template == 'price_attack' and self.hero_products:
            raise ValueError('price_attack não aceita hero_products.')
        placement_heroes = {p.product_id for p in self.placements if p.role == 'hero'}
        placement_featured = {p.product_id for p in self.placements if p.role == 'featured'}
        if placement_heroes != set(self.hero_products) or placement_featured != set(self.featured_products):
            raise ValueError('hero_products e featured_products devem corresponder às roles dos placements.')
        return self
