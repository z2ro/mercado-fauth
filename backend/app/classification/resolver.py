import asyncio
import json
import logging

from ..config import CLASSIFICATION_SETTINGS, ClassificationSettings
from ..models.campaign import BannerRequest, ResolvedBannerRequest
from ..models.product import ResolvedProduct
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


async def resolve_products(
    request: BannerRequest, settings: ClassificationSettings | None = None,
    classifier: ProductClassifier | None = None,
) -> tuple[ResolvedBannerRequest, dict[str, ClassificationMetadata]]:
    settings = settings or CLASSIFICATION_SETTINGS
    rules = RuleBasedProductClassifier()
    results: dict[str, tuple[ProductClassification, str]] = {}
    fallback: dict[str, ProductClassification] = {}
    pending = []
    inputs = {p.id: ProductClassificationInput(product_id=p.id, name=p.name, unit=p.unit) for p in request.products}
    for product in request.products:
        item = inputs[product.id]
        if product.category is not None:
            rule = None
            if product.subcategory is None:
                try:
                    rule = await rules.classify(item)
                except ValueError:
                    pass
            subcategory = product.subcategory
            if subcategory is None and rule and rule.category == product.category and rule.confidence >= settings.min_confidence:
                subcategory = rule.subcategory
            # User subcategories need not conform to classifier snake_case. Applied at merge.
            result = ProductClassification(product_id=product.id, category=product.category, subcategory=None, confidence=1.0)
            results[product.id] = (result, 'user')
            if subcategory is not None and product.subcategory is None:
                results[product.id] = (result.model_copy(update={'subcategory': subcategory}), 'user')
            event('classification_user_provided', product_id=product.id)
            continue
        path = settings.cache_dir / f'{cache_key(item, settings)}.json'
        cached = read_cache(path, item)
        if cached and cached.confidence >= settings.min_confidence:
            results[product.id] = (cached, 'cache')
            event('classification_cache_hit', product_id=product.id)
            continue
        event('classification_cache_miss', product_id=product.id)
        try:
            rule = await rules.classify(item)
        except ValueError:
            rule = None
        if rule and rule.confidence >= settings.min_confidence:
            fallback[product.id] = rule
            if rule.confidence >= STRONG_CONFIDENCE:
                results[product.id] = (rule, 'rule')
                event('classification_rule_match', product_id=product.id)
                write_cache(path, rule, 'rule')
                continue
        pending.append(item)
    if pending and settings.enabled:
        event('classification_ai_started', count=len(pending))
        try:
            batch = await asyncio.wait_for((classifier or LLMProductClassifier(settings)).classify_batch(tuple(pending)), timeout=settings.timeout_seconds)
            # Revalidate even injected/provider-created model objects at the trust boundary.
            batch = ClassificationBatch.model_validate_json(batch.model_dump_json())
            checked = batch.for_inputs(tuple(pending))
            for item in pending:
                result = checked[item.product_id]
                if result.confidence >= settings.min_confidence:
                    results[item.product_id] = (result, 'ai')
                    write_cache(settings.cache_dir / f'{cache_key(item, settings)}.json', result, 'ai')
                else:
                    event('classification_low_confidence', product_id=item.product_id, confidence=result.confidence)
            event('classification_ai_completed', count=len(pending))
        except Exception as exc:
            # Exception messages may contain headers/credentials. Log only the type.
            event('classification_ai_failed', error=type(exc).__name__)
    unresolved = []
    for item in pending:
        if item.product_id in results:
            continue
        if item.product_id in fallback:
            result = fallback[item.product_id]
            results[item.product_id] = (result, 'rule')
            event('classification_fallback', product_id=item.product_id)
            write_cache(settings.cache_dir / f'{cache_key(item, settings)}.json', result, 'rule')
        else:
            unresolved.append(item.product_id)
            event('classification_unresolved', product_id=item.product_id)
    if unresolved:
        raise UnresolvedClassification(unresolved)
    resolved = []
    metadata = {}
    for original in request.products:
        result, source = results[original.id]
        # Explicit allowlist: classifier never receives or supplies commercial values.
        data = original.model_dump()
        data['category'] = original.category if original.category is not None else result.category
        data['subcategory'] = original.subcategory if original.subcategory is not None else result.subcategory
        product = ResolvedProduct.model_validate(data)
        resolved.append(product)
        metadata[product.id] = ClassificationMetadata(category=product.category, subcategory=product.subcategory, source=source, confidence=result.confidence)
    return ResolvedBannerRequest(campaign=request.campaign, template=request.template, products=tuple(resolved)), metadata
