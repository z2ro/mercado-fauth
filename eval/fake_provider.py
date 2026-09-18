from backend.app.classification.models import ClassificationBatch, ProductClassification, ProductClassificationInput


class FakeProductClassifier:
    """Infrastructure fixture only; deliberately predicts one fixed class, not quality."""
    async def classify(self, product: ProductClassificationInput) -> ProductClassification:
        return ProductClassification(product_id=product.product_id, category='mercearia', subcategory='mercearia_geral', confidence=.80)

    async def classify_batch(self, products: tuple[ProductClassificationInput, ...]) -> ClassificationBatch:
        return ClassificationBatch(products=tuple([await self.classify(p) for p in products]))
