from itertools import combinations

from ..models.campaign import BannerRequest
from ..models.design import DesignSpec, Placement
from .grid import EDGES, GRID
from .rules import SENSITIVE, hard_rules_valid, layout_score


class ImpossibleLayout(ValueError):
    pass


def plan_layout(request: BannerRequest) -> DesignSpec:
    products = sorted(request.products, key=lambda p: (p.category, not p.featured, p.id))
    cleaning = [p for p in products if p.category == 'limpeza']
    sensitive = [p for p in products if p.category in SENSITIVE]
    neutral = [p for p in products if p.category != 'limpeza' and p.category not in SENSITIVE]
    candidates = []
    traversals = (tuple(range(12)), tuple(sorted(range(12), key=lambda i: (i % 4, i // 4))))
    # At most C(12, 6)=924 masks; feasibility is exhaustive only for cleaning positions.
    for mask in combinations(range(12), len(cleaning)):
        occupied = set(mask)
        blocked = occupied | {b if a in occupied else a for a, b in EDGES if a in occupied or b in occupied}
        safe = set(range(12)) - blocked
        if len(safe) < len(sensitive):
            continue
        for traversal in traversals:
            assigned = dict(zip(mask, cleaning))
            assigned.update(zip((i for i in traversal if i in safe), sensitive))
            assigned.update(zip((i for i in traversal if i not in assigned), neutral))
            ordered = tuple(assigned[i] for i in range(12))
            candidates.append((layout_score(ordered), ordered))
    if not candidates:
        raise ImpossibleLayout('Não existe layout com separação de limpeza para esta campanha.')
    # ponytail: top-8 local search is not globally optimal; use OR-Tools if quality requires it.
    best = None
    best_score = -10**9
    for score, seed in sorted(candidates, key=lambda item: -item[0])[:8]:
        current = seed
        while True:
            improved, improved_score = current, score
            for a, b in combinations(range(12), 2):
                swapped = list(current)
                swapped[a], swapped[b] = swapped[b], swapped[a]
                if hard_rules_valid(swapped):
                    value = layout_score(swapped)
                    if value > improved_score:
                        improved, improved_score = tuple(swapped), value
            if improved_score == score:
                break
            current, score = improved, improved_score
        if score > best_score:
            best, best_score = current, score
    assert best is not None and hard_rules_valid(best)
    return DesignSpec(products=tuple(Placement(product_id=p.id, slot=s.index, x=s.x, y=s.y, featured=p.featured) for s, p in zip(GRID, best)))
