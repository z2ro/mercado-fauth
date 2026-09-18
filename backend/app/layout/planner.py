from dataclasses import dataclass
from itertools import combinations

from ..art_direction.models import ArtDirectionSpec
from ..design.system import DESIGN_SYSTEM, FEATURED_COUNT
from ..models.campaign import BannerRequest
from ..models.design import DesignSpec, DesignSpecV2, Placement, RectPlacement, VisualDirection
from ..models.product import Product
from .geometry import rectangles_adjacent
from .grid import EDGES, GRID
from .patterns import PATTERNS, LayoutPattern, PatternPlacement
from .rules import (
    SENSITIVE,
    hard_rules_valid,
    layout_score,
    placement_layout_score,
    rectangles_hard_rules_valid,
)


class ImpossibleLayout(ValueError):
    pass


@dataclass(frozen=True)
class AssignedPlacement:
    product_id: str
    x: int
    y: int
    w: int
    h: int
    role: str
    zone: str


def select_template(request: BannerRequest) -> str:
    if request.template:
        return request.template
    if request.hero_products or request.featured_products:
        return 'weekend_hero'
    if request.visual_direction and request.visual_direction.emphasis == 'price':
        return 'price_attack'
    if request.visual_direction:
        return 'weekend_hero'
    return 'supermarket_12'


def _plan_legacy(request: BannerRequest, template: str = 'supermarket_12') -> DesignSpec:
    if template == 'faith_reference_12' and hard_rules_valid(request.products):
        # The reference payload's order is its authored 4x3 composition.
        return DesignSpec(template=template, products=tuple(
            Placement(product_id=product.id, slot=slot.index, x=slot.x, y=slot.y, featured=product.featured)
            for slot, product in zip(GRID, request.products)
        ))
    products = sorted(request.products, key=lambda p: (p.category, not p.featured, p.id))
    cleaning = [p for p in products if p.category == 'limpeza']
    sensitive = [p for p in products if p.category in SENSITIVE]
    neutral = [p for p in products if p.category != 'limpeza' and p.category not in SENSITIVE]
    candidates = []
    traversals = (tuple(range(12)), tuple(sorted(range(12), key=lambda i: (i % 4, i // 4))))
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
    return DesignSpec(template=template, products=tuple(
        Placement(product_id=product.id, slot=slot.index, x=slot.x, y=slot.y, featured=product.featured)
        for slot, product in zip(GRID, best)
    ))


def _selected_products(request: BannerRequest, template: str):
    marked = sorted((product.id for product in request.products if product.featured))
    if template == 'weekend_hero':
        hero = request.hero_products or ((marked[0],) if marked else (min(product.id for product in request.products),))
        requested_featured = request.featured_products or tuple(product_id for product_id in marked if product_id not in hero)
        featured = tuple(product_id for product_id in requested_featured if product_id not in hero)[:FEATURED_COUNT]
        return tuple(hero), featured
    featured = request.featured_products or tuple(marked[:FEATURED_COUNT])
    return (), tuple(featured[:FEATURED_COUNT])


def _locked_roles(pattern: LayoutPattern, hero_ids: tuple[str, ...], featured_ids: tuple[str, ...]):
    role_indexes = {role: [index for index, slot in enumerate(pattern.placements) if slot.role == role]
                    for role in ('hero', 'featured')}
    if len(hero_ids) > len(role_indexes['hero']) or len(featured_ids) > len(role_indexes['featured']):
        raise ImpossibleLayout('O template não possui posições suficientes para os destaques solicitados.')
    locked = {}
    if hero_ids:
        locked[role_indexes['hero'][0]] = hero_ids[0]
    for index, product_id in zip(role_indexes['featured'], featured_ids):
        locked[index] = product_id
    return locked


def _seed_layout(pattern: LayoutPattern, products: tuple[Product, ...], cleaning_indexes, locked):
    by_id = {product.id: product for product in products}
    cleaning_indexes = set(cleaning_indexes)
    slots = pattern.placements
    assigned = {}
    for index, product_id in locked.items():
        is_cleaning = by_id[product_id].category == 'limpeza'
        if (index in cleaning_indexes) != is_cleaning:
            return None
        assigned[index] = product_id

    cleaning = sorted((product for product in products if product.category == 'limpeza' and product.id not in assigned.values()),
                      key=lambda product: (not product.featured, product.id))
    free_cleaning = sorted(cleaning_indexes - set(assigned))
    if len(cleaning) != len(free_cleaning):
        return None
    assigned.update((index, product.id) for index, product in zip(free_cleaning, cleaning))

    unsafe = set(cleaning_indexes)
    for clean_index in cleaning_indexes:
        unsafe.update(index for index, slot in enumerate(slots) if rectangles_adjacent(slots[clean_index], slot))
    safe = set(range(len(slots))) - unsafe
    if any(by_id[product_id].category in SENSITIVE and index not in safe for index, product_id in assigned.items()):
        return None

    sensitive = sorted((product for product in products if product.category in SENSITIVE and product.id not in assigned.values()),
                       key=lambda product: (not product.featured, product.category, product.id))
    free_safe = sorted(safe - set(assigned), key=lambda index: (
        {'hero': 0, 'featured': 1, 'standard': 2, 'compact': 3}[slots[index].role],
        slots[index].y, slots[index].x,
    ))
    if len(sensitive) > len(free_safe):
        return None
    assigned.update((index, product.id) for index, product in zip(free_safe, sensitive))

    neutral = sorted((product for product in products if product.category not in SENSITIVE | {'limpeza'}
                      and product.id not in assigned.values()),
                     key=lambda product: (not product.featured, product.category, product.id))
    free = sorted(set(range(len(slots))) - set(assigned), key=lambda index: (slots[index].y, slots[index].x))
    if len(neutral) != len(free):
        return None
    assigned.update((index, product.id) for index, product in zip(free, neutral))
    return tuple(
        AssignedPlacement(assigned[index], slot.x, slot.y, slot.w, slot.h, slot.role, slot.zone)
        for index, slot in enumerate(slots)
    )


def _improve_layout(seed, pattern, by_id, locked):
    current = seed
    current_score = placement_layout_score(current, by_id)
    locked_ids = set(locked.values())
    for _ in range(24):
        improved = current
        improved_score = current_score
        for a, b in combinations(range(len(current)), 2):
            if current[a].product_id in locked_ids or current[b].product_id in locked_ids:
                continue
            swapped = list(current)
            left, right = swapped[a], swapped[b]
            swapped[a] = AssignedPlacement(right.product_id, left.x, left.y, left.w, left.h, left.role, left.zone)
            swapped[b] = AssignedPlacement(left.product_id, right.x, right.y, right.w, right.h, right.role, right.zone)
            if rectangles_hard_rules_valid(swapped, by_id):
                score = placement_layout_score(swapped, by_id)
                if score > improved_score:
                    improved, improved_score = tuple(swapped), score
        if improved_score <= current_score:
            break
        current, current_score = improved, improved_score
    return current, current_score


def _plan_pattern(request: BannerRequest, template: str, presentation_profile: str = 'balanced',
                  art_direction: ArtDirectionSpec | None = None) -> DesignSpecV2:
    pattern = PATTERNS[template]
    products = tuple(request.products)
    by_id = {product.id: product for product in products}
    hero_ids, featured_ids = ((art_direction.hero_products, art_direction.featured_products)
                              if art_direction else _selected_products(request, template))
    locked = _locked_roles(pattern, hero_ids, featured_ids)
    cleaning_count = sum(product.category == 'limpeza' for product in products)
    candidates = []
    all_indexes = range(len(pattern.placements))
    # Enumerate only cleaning placements, then assign compatible departments and hill-climb.
    for cleaning_indexes in combinations(all_indexes, cleaning_count):
        seed = _seed_layout(pattern, products, cleaning_indexes, locked)
        if seed is not None:
            candidates.append((placement_layout_score(seed, by_id), seed))
    if not candidates:
        raise ImpossibleLayout(f'Não existe layout {template} que preserve a separação entre departamentos.')
    ranked = sorted(candidates, key=lambda item: (-item[0], tuple(p.product_id for p in item[1])))[:12]
    best, best_score = None, float('-inf')
    for _, seed in ranked:
        candidate, score = _improve_layout(seed, pattern, by_id, locked)
        if score > best_score:
            best, best_score = candidate, score
    assert best is not None and rectangles_hard_rules_valid(best, by_id)
    visual_direction = request.visual_direction or VisualDirection()
    placements = tuple(
        RectPlacement(
            product_id=placement.product_id,
            x=placement.x,
            y=placement.y,
            w=placement.w,
            h=placement.h,
            role=placement.role,
            image_scale=DESIGN_SYSTEM.role_scales[placement.role][0],
            price_scale=DESIGN_SYSTEM.role_scales[placement.role][1],
            zone=placement.zone,
        )
        for placement in best
    )
    output_hero_ids = tuple(placement.product_id for placement in placements if placement.role == 'hero')
    output_featured_ids = tuple(placement.product_id for placement in placements if placement.role == 'featured')
    return DesignSpecV2(
        template=template,
        grid_columns=pattern.columns,
        grid_rows=pattern.rows,
        hero_products=output_hero_ids,
        featured_products=output_featured_ids,
        visual_direction=visual_direction,
        presentation_profile=presentation_profile,
        placements=placements,
    )


def plan_layout(request: BannerRequest, presentation_profile: str = 'balanced',
                art_direction: ArtDirectionSpec | None = None) -> DesignSpec | DesignSpecV2:
    template = select_template(request)
    if template in {'supermarket_12', 'faith_reference_12'}:
        return _plan_legacy(request, template)
    return _plan_pattern(request, template, presentation_profile, art_direction)
