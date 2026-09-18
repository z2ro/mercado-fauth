from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

Text = Annotated[str, StringConstraints(min_length=1, max_length=120)]


class Category(StrEnum):
    ACOUGUE = 'acougue'
    FRIOS = 'frios'
    PADARIA = 'padaria'
    HORTIFRUTI = 'hortifruti'
    MERCEARIA = 'mercearia'
    BEBIDAS = 'bebidas'
    LIMPEZA = 'limpeza'
    HIGIENE = 'higiene'


class Product(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')

    id: Text
    sku: Text
    name: Annotated[str, StringConstraints(min_length=1, max_length=70)]
    price: Decimal = Field(gt=0, max_digits=8, decimal_places=2)
    unit: Annotated[str, StringConstraints(min_length=1, max_length=12)]
    category: Category
    subcategory: Text | None = None
    image: str = Field(max_length=512)
    featured: bool = False

    @field_validator('price', mode='before')
    @classmethod
    def decimal_input(cls, value):
        if not isinstance(value, (str, Decimal)):
            raise ValueError('Preço deve ser string decimal, nunca float.')
        return value

    @field_validator('id', 'sku', 'name', 'unit', 'subcategory')
    @classmethod
    def nonblank(cls, value):
        if value is not None and not value.strip():
            raise ValueError('Texto não pode ser vazio.')
        return value
