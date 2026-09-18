from dataclasses import dataclass


@dataclass(frozen=True)
class PatternPlacement:
    x: int
    y: int
    w: int
    h: int
    role: str
    zone: str


@dataclass(frozen=True)
class LayoutPattern:
    template: str
    columns: int
    rows: int
    placements: tuple[PatternPlacement, ...]


WEEKEND_HERO = LayoutPattern(
    template='weekend_hero', columns=12, rows=8,
    placements=(
        PatternPlacement(0, 0, 6, 4, 'hero', 'hero'),
        PatternPlacement(6, 0, 3, 2, 'standard', 'promotion'),
        PatternPlacement(9, 0, 3, 2, 'featured', 'promotion'),
        PatternPlacement(6, 2, 3, 2, 'standard', 'promotion'),
        PatternPlacement(9, 2, 3, 2, 'featured', 'promotion'),
        PatternPlacement(0, 4, 3, 2, 'standard', 'regular'),
        PatternPlacement(3, 4, 3, 2, 'standard', 'regular'),
        PatternPlacement(6, 4, 3, 2, 'standard', 'regular'),
        PatternPlacement(9, 4, 3, 2, 'standard', 'regular'),
        PatternPlacement(0, 6, 4, 2, 'standard', 'regular'),
        PatternPlacement(4, 6, 4, 2, 'standard', 'regular'),
        PatternPlacement(8, 6, 4, 2, 'standard', 'regular'),
    ),
)


PRICE_ATTACK = LayoutPattern(
    template='price_attack', columns=12, rows=6,
    placements=tuple(
        PatternPlacement(x, y, 3, 2, 'featured' if y == 0 and x in (0, 9) else 'compact', 'promotion')
        for y in (0, 2, 4)
        for x in (0, 3, 6, 9)
    ),
)


PATTERNS = {pattern.template: pattern for pattern in (WEEKEND_HERO, PRICE_ATTACK)}
