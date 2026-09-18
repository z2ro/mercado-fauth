import hashlib
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError

from backend.app.models.product import Category

Difficulty = Literal['easy', 'medium', 'hard']
Subcategory = Annotated[str, StringConstraints(pattern=r'^[a-z][a-z0-9_]{0,79}$')]
DatasetText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class EvalProduct(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    name: DatasetText
    unit: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32)]
    expected_category: Category
    expected_subcategory: Subcategory | None = None
    difficulty: Difficulty


def load_dataset(path: Path) -> tuple[tuple[EvalProduct, ...], str]:
    try:
        raw = path.read_bytes()
        data = json.loads(raw)
        if not isinstance(data, list) or not data:
            raise ValueError('O dataset deve ser uma lista JSON não vazia.')
        products = tuple(EvalProduct.model_validate(row) for row in data)
        ids = [p.id for p in products]
        if len(ids) != len(set(ids)):
            raise ValueError('IDs duplicados no dataset.')
        return products, hashlib.sha256(raw).hexdigest()
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        detail = exc.errors() if isinstance(exc, ValidationError) else str(exc)
        raise ValueError(f'Dataset inválido em {path}: {detail}') from exc
