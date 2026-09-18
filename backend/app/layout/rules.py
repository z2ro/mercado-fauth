from collections.abc import Sequence

from ..models.product import Product
from .affinity import FEATURED_POSITION_BONUS, FEATURED_ROLE_BONUS, FRAGMENTATION_PENALTY, affinity
from .geometry import rectangles_adjacent, rectangles_overlap
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


def rectangles_hard_rules_valid(placements, products_by_id: dict[str, Product]) -> bool:
    for index, placement in enumerate(placements):
        if placement.product_id not in products_by_id:
            return False
        for other in placements[:index]:
            if rectangles_overlap(placement, other):
                return False
            if rectangles_adjacent(placement, other) and not compatible(
                products_by_id[placement.product_id].category,
                products_by_id[other.product_id].category,
            ):
                return False
    return True


def placement_layout_score(placements, products_by_id: dict[str, Product]) -> float:
    edges = [(a, b) for index, a in enumerate(placements) for b in placements[index + 1:]
             if rectangles_adjacent(a, b)]
    raw_affinity = sum(
        affinity(products_by_id[a.product_id].category, products_by_id[b.product_id].category)
        for a, b in edges
    )
    # Normalize by the fixed pattern's edge count; never reward a product for cell area.
    score = raw_affinity * len(placements) / max(1, len(edges))
    score += sum(
        FEATURED_ROLE_BONUS.get(placement.role, 0)
        for placement in placements
        if products_by_id[placement.product_id].featured
    )
    categories = {product.category for product in products_by_id.values()}
    for category in categories:
        unseen = {placement.product_id for placement in placements
                  if products_by_id[placement.product_id].category == category}
        components = 0
        while unseen:
            components += 1
            pending = [min(unseen)]
            unseen.remove(pending[0])
            while pending:
                current = pending.pop()
                neighbors = {
                    neighbor.product_id
                    for a, b in edges
                    for placement, neighbor in ((a, b), (b, a))
                    if placement.product_id == current
                    and neighbor.product_id in unseen
                    and products_by_id[neighbor.product_id].category == category
                }
                unseen -= neighbors
                pending.extend(sorted(neighbors))
        score -= (components - 1) * FRAGMENTATION_PENALTY
    return score
