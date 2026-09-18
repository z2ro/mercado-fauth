from dataclasses import dataclass


@dataclass(frozen=True)
class Slot:
    index: int
    x: int
    y: int


def build_grid(columns: int = 4, rows: int = 3) -> tuple[Slot, ...]:
    if columns < 1 or rows < 1:
        raise ValueError('Grid deve ter dimensões positivas.')
    return tuple(Slot(i, i % columns, i // columns) for i in range(columns * rows))


def manhattan(a: Slot, b: Slot) -> int:
    return abs(a.x - b.x) + abs(a.y - b.y)


def adjacent(a: Slot, b: Slot) -> bool:
    return manhattan(a, b) == 1


GRID = build_grid()
EDGES = tuple((a.index, b.index) for a in GRID for b in GRID if a.index < b.index and adjacent(a, b))
