from collections.abc import Sequence

from ..models.product import Product
from .affinity import FEATURED_POSITION_BONUS, FRAGMENTATION_PENALTY, affinity
from .grid import EDGES

SENSITIVE = frozenset(('acougue', 'hortifruti', 'padaria'))


def compatible(a: str, b: str) -> bool:
    return not ((a == 'limpeza' and b in SENSITIVE) or (b == 'limpeza' and a in SENSITIVE))


def hard_rules_valid(products: Sequence[Product]) -> bool:
    return all(compatible(products[a].category, products[b].category) for a, b in EDGES)


def layout_score(products: Sequence[Product]) -> int:
    score = sum(affinity(products[a].category, products[b].category) for a, b in EDGES)
    score += sum(FEATURED_POSITION_BONUS[i] for i, p in enumerate(products) if p.featured)
    for category in {p.category for p in products}:
        unseen = {i for i, p in enumerate(products) if p.category == category}
        components = 0
        while unseen:
            components += 1
            pending = [min(unseen)]
            unseen.remove(pending[0])
            while pending:
                current = pending.pop()
                neighbors = {b if a == current else a for a, b in EDGES if current in (a, b)} & unseen
                unseen -= neighbors
                pending.extend(sorted(neighbors))
        score -= (components - 1) * FRAGMENTATION_PENALTY
    return score
