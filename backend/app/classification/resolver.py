import asyncio
import json
import logging

from ..config import CLASSIFICATION_SETTINGS, ClassificationSettings
from ..models.campaign import BannerRequest, ResolvedBannerRequest
from ..models.product import Category, ResolvedProduct
from .cache import cache_key, read_cache, write_cache
from .llm import LLMProductClassifier
from .models import ClassificationBatch, ClassificationMetadata, ProductClassification, ProductClassificationInput, ProductClassifier
from .rules import STRONG_CONFIDENCE, RuleBasedProductClassifier

logger = logging.getLogger(__name__)


def event(name: str, **fields) -> None:
    logger.info(json.dumps({'event': name, **fields}, ensure_ascii=False))


class UnresolvedClassification(ValueError):
    def __init__(self, ids: list[str]):
        self.ids = ids
        super().__init__('Não foi possível classificar com segurança os produtos: ' + ', '.join(ids))


async def resolve_classifications(
    products: tuple[ProductClassificationInput, ...],
    manual: dict[str, tuple[Category | None, str | None]] | None = None,
    settings: ClassificationSettings | None = None,
    classifier: ProductClassifier | None = None,
    use_cache: bool = True,
) -> dict[str, tuple[ProductClassification, str]]:
    """Shared production resolver for an arbitrary batch; unresolved IDs are omitted."""
    settings = settings or CLASSIFICATION_SETTINGS
    manual = manual or {}
    rules = RuleBasedProductClassifier()
    results: dict[str, tuple[ProductClassification, str]] = {}
    fallback: dict[str, ProductClassification] = {}
    pending = []
    for item in products:
        category, subcategory = manual.get(item.product_id, (None, None))
        if category is not None:
            rule = None
            if subcategory is None:
                try:
                    rule = await rules.classify(item)
                except ValueError:
                    pass
            if subcategory is None and rule and rule.category == category and rule.confidence >= settings.min_confidence:
                subcategory = rule.subcategory
            # Manual subcategories are deliberately preserved without classifier normalization.
            result = ProductClassification(product_id=item.product_id, category=category, subcategory=None, confidence=1.0)
            results[item.product_id] = (result.model_copy(update={'subcategory': subcategory}), 'user')
            event('classification_user_provided', product_id=item.product_id)
            continue
        path = settings.cache_dir / f'{cache_key(item, settings)}.json'
        cached = read_cache(path, item) if use_cache else None
        if cached and cached.confidence >= settings.min_confidence:
            results[item.product_id] = (cached, 'cache')
            event('classification_cache_hit', product_id=item.product_id)
            continue
        event('classification_cache_miss', product_id=item.product_id)
        try:
            rule = await rules.classify(item)
        except ValueError:
            rule = None
        if rule and rule.confidence >= settings.min_confidence:
            fallback[item.product_id] = rule
            if rule.confidence >= STRONG_CONFIDENCE:
                results[item.product_id] = (rule, 'rule')
                event('classification_rule_match', product_id=item.product_id)
                if use_cache:write_cache(path, rule, 'rule')
                continue
        pending.append(item)
    if pending and settings.enabled:
        event('classification_ai_started', count=len(pending))
        try:
            batch = await asyncio.wait_for((classifier or LLMProductClassifier(settings)).classify_batch(tuple(pending)), timeout=settings.timeout_seconds)
            batch = ClassificationBatch.model_validate_json(batch.model_dump_json())
            checked = batch.for_inputs(tuple(pending))
            for item in pending:
                result = checked[item.product_id]
                if result.confidence >= settings.min_confidence:
                    results[item.product_id] = (result, 'ai')
                    if use_cache:write_cache(settings.cache_dir / f'{cache_key(item, settings)}.json', result, 'ai')
                else:
                    event('classification_low_confidence', product_id=item.product_id, confidence=result.confidence)
            event('classification_ai_completed', count=len(pending))
        except Exception as exc:
            event('classification_ai_failed', error=type(exc).__name__)
    for item in pending:
        if item.product_id in results:
            continue
        if item.product_id in fallback:
            result = fallback[item.product_id]
            results[item.product_id] = (result, 'rule')
            event('classification_fallback', product_id=item.product_id)
            if use_cache:write_cache(settings.cache_dir / f'{cache_key(item, settings)}.json', result, 'rule')
        else:
            event('classification_unresolved', product_id=item.product_id)
    return results


async def resolve_products(
    request: BannerRequest, settings: ClassificationSettings | None = None,
    classifier: ProductClassifier | None = None,
) -> tuple[ResolvedBannerRequest, dict[str, ClassificationMetadata]]:
    inputs = tuple(ProductClassificationInput(product_id=p.id, name=p.name, unit=p.unit) for p in request.products)
    manual = {p.id: (p.category, p.subcategory) for p in request.products}
    results = await resolve_classifications(inputs, manual, settings, classifier)
    unresolved = [p.id for p in request.products if p.id not in results]
    if unresolved:
        raise UnresolvedClassification(unresolved)
    resolved = []
    metadata = {}
    by_id = {p.id: p for p in request.products}
    for item in inputs:
        result, source = results[item.product_id]
        original = by_id[item.product_id]
        data = original.model_dump()
        data['category'] = original.category or result.category
        data['subcategory'] = original.subcategory if original.subcategory is not None else result.subcategory
        product = ResolvedProduct.model_validate(data)
        resolved.append(product)
        metadata[product.id] = ClassificationMetadata(category=product.category, subcategory=product.subcategory, source=source, confidence=result.confidence)
    return ResolvedBannerRequest(campaign=request.campaign, template=request.template, products=tuple(resolved)), metadata
