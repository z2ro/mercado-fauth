from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from ..models.design import PresentationProfile, VisualDirection
from ..models.product import Category


class DirectedProduct(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    product_id: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    name: Annotated[str, StringConstraints(min_length=1, max_length=70)]
    category: Category
    subcategory: str | None
    featured: bool


class ArtDirectionInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    campaign_title: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    products: tuple[DirectedProduct, ...] = Field(min_length=12, max_length=12)


class ArtDirectionSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    template: Literal['weekend_hero', 'price_attack']
    hero_products: tuple[str, ...] = Field(max_length=1)
    featured_products: tuple[str, ...] = Field(max_length=2)
    visual_direction: VisualDirection
    presentation_profile: PresentationProfile

    @model_validator(mode='after')
    def roles_match_template(self):
        if len(set(self.hero_products)) != len(self.hero_products):
            raise ValueError('Hero IDs duplicados.')
        if len(set(self.featured_products)) != len(self.featured_products):
            raise ValueError('Featured IDs duplicados.')
        if set(self.hero_products) & set(self.featured_products):
            raise ValueError('Hero e featured devem ser distintos.')
        if self.template == 'weekend_hero' and len(self.hero_products) != 1:
            raise ValueError('weekend_hero exige exatamente um hero.')
        if self.template == 'price_attack' and self.hero_products:
            raise ValueError('price_attack não aceita hero.')
        return self


class ArtDirector(Protocol):
    async def direct(self, direction: ArtDirectionInput) -> ArtDirectionSpec: ...


IssueCode = Literal['text_clipping', 'image_clipping', 'weak_hero_emphasis', 'weak_price_hierarchy',
                    'crowded_layout', 'excessive_empty_space', 'poor_balance', 'low_contrast',
                    'footer_too_prominent', 'header_too_prominent']


class VisualIssue(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    code: IssueCode
    severity: Literal['low', 'medium', 'high']
    product_id: str | None = None


class VisualQAResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    approved: bool
    issues: tuple[VisualIssue, ...] = Field(max_length=20)
    suggested_profile: PresentationProfile | None = None
    note: Annotated[str, StringConstraints(max_length=200)] | None = None


class VisualQA(Protocol):
    async def inspect(self, png: bytes, **context) -> object: ...
