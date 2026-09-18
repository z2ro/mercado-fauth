from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from ..models.product import Category, Text


class ProductClassificationInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    product_id: Text
    name: Text
    unit: Text


class ProductClassification(BaseModel):
    # Only classification fields may cross this boundary, never commercial data.
    model_config = ConfigDict(frozen=True, extra='forbid')
    product_id: Text
    category: Category
    subcategory: Annotated[str, StringConstraints(pattern=r'^[a-z][a-z0-9_]{0,79}$')] | None
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False, strict=True)


class ClassificationBatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    products: tuple[ProductClassification, ...]

    def for_inputs(self, products: tuple[ProductClassificationInput, ...]) -> dict[str, ProductClassification]:
        ids = [p.product_id for p in self.products]
        if len(ids) != len(set(ids)) or set(ids) != {p.product_id for p in products}:
            raise ValueError('IDs ausentes, duplicados ou desconhecidos na classificação.')
        return {p.product_id: p for p in self.products}


class ClassificationMetadata(BaseModel):
    category: Category
    subcategory: str | None
    source: Literal['user', 'cache', 'rule', 'ai']
    confidence: float


class ProductClassifier(Protocol):
    async def classify(self, product: ProductClassificationInput) -> ProductClassification: ...

    async def classify_batch(self, products: tuple[ProductClassificationInput, ...]) -> ClassificationBatch: ...
