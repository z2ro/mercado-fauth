import re
import unicodedata

from .models import ClassificationBatch, ProductClassification, ProductClassificationInput

RULE_VERSION = '1'
STRONG_CONFIDENCE = 0.90
# Category, subcategory, confidence, normalized phrases. More specific phrases win.
RULES = (
    ('limpeza', 'limpeza_geral', .95, ('agua sanitaria', 'detergente', 'desinfetante')),
    ('limpeza', 'lavanderia', .95, ('sabao', 'amaciante')),
    ('acougue', 'carne_bovina', .95, ('paleta', 'picanha', 'coxao', 'carne')),
    ('acougue', 'carne_suina', .95, ('pernil', 'linguica')),
    ('acougue', 'aves', .95, ('frango', 'coxinha da asa')),
    ('frios', 'queijos', .95, ('queijo',)),
    ('frios', 'embutidos', .95, ('presunto', 'mortadela', 'salame')),
    ('frios', 'laticinios', .80, ('iogurte',)),
    ('hortifruti', 'frutas', .95, ('banana', 'maca', 'laranja')),
    ('hortifruti', 'legumes', .95, ('tomate', 'batata', 'cebola')),
    ('bebidas', 'refrigerantes', .95, ('coca cola', 'refrigerante')),
    ('bebidas', 'sucos', .95, ('suco',)),
    ('bebidas', 'cervejas', .95, ('cerveja',)),
    ('bebidas', 'agua', .95, ('agua',)),
    ('higiene', 'cabelo', .95, ('shampoo',)),
    ('higiene', 'higiene_bucal', .95, ('creme dental',)),
    ('higiene', 'higiene_corporal', .95, ('sabonete', 'desodorante')),
    ('mercearia', 'graos', .95, ('arroz', 'feijao')),
    ('mercearia', 'massas', .95, ('macarrao',)),
    ('mercearia', 'molhos', .95, ('molho de tomate', 'molho')),
    ('mercearia', 'conservas', .95, ('azeitona',)),
    ('mercearia', 'farinhas', .95, ('farinha',)),
    ('padaria', 'paes', .95, ('pao de queijo', 'pao', 'croissant')),
    ('padaria', 'bolos', .95, ('bolo',)),
)


def normalize_name(value: str) -> str:
    value = ''.join(c for c in unicodedata.normalize('NFKD', value.casefold()) if not unicodedata.combining(c))
    return ' '.join(re.findall(r'[^\W_]+', value))


class RuleBasedProductClassifier:
    async def classify(self, product: ProductClassificationInput) -> ProductClassification:
        text = f' {normalize_name(product.name)} '
        matches = [(keyword, category, subcategory, confidence)
                   for category, subcategory, confidence, words in RULES
                   for keyword in words if f' {keyword} ' in text]
        matches = [m for m in matches if not any(m[0] != n[0] and f' {m[0]} ' in f' {n[0]} ' for n in matches)]
        if not matches or len({m[1] for m in matches}) != 1:
            raise ValueError('Sem regra inequívoca.')
        match = sorted(matches, key=lambda m: (-len(m[0]), m[0]))[0]
        return ProductClassification(product_id=product.product_id, category=match[1], subcategory=match[2], confidence=match[3])

    async def classify_batch(self, products: tuple[ProductClassificationInput, ...]) -> ClassificationBatch:
        return ClassificationBatch(products=tuple([await self.classify(p) for p in products]))
