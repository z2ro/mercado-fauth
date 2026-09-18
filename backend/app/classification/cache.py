import hashlib
import json
import logging
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal

from pydantic import BaseModel, ConfigDict

from ..config import ClassificationSettings
from .llm import PROMPT_VERSION
from .models import ProductClassification, ProductClassificationInput
from .rules import RULE_VERSION, normalize_name

CLASSIFIER_VERSION = '1'
logger = logging.getLogger(__name__)


class CacheEntry(BaseModel):
    model_config = ConfigDict(extra='forbid')
    classification: ProductClassification
    classifier: Literal['rule', 'ai']
    version: str


def cache_key(product: ProductClassificationInput, settings: ClassificationSettings) -> str:
    data = {'name': normalize_name(product.name), 'unit': normalize_name(product.unit), 'brand': None,
            'version': CLASSIFIER_VERSION, 'rules': RULE_VERSION, 'prompt': PROMPT_VERSION,
            'provider': settings.provider, 'model': settings.model, 'enabled': settings.enabled}
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def read_cache(path: Path, product: ProductClassificationInput) -> ProductClassification | None:
    try:
        entry = CacheEntry.model_validate_json(path.read_text())
        if entry.version != CLASSIFIER_VERSION:
            return None
        return entry.classification.model_copy(update={'product_id': product.product_id})
    except (OSError, ValueError):
        return None


def write_cache(path: Path, result: ProductClassification, source: Literal['rule', 'ai']) -> None:
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = CacheEntry(classification=result, classifier=source, version=CLASSIFIER_VERSION)
        with NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(entry.model_dump_json())
        temporary.replace(path)
    except OSError:
        logger.warning('classification_cache_write_failed')
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
