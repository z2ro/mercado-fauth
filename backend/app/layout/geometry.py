from dataclasses import dataclass


@dataclass(frozen=True)
class Rectangle:
    x: int
    y: int
    w: int
    h: int


def rectangles_overlap(a, b) -> bool:
    return a.x < b.x + b.w and b.x < a.x + a.w and a.y < b.y + b.h and b.y < a.y + a.h


def rectangles_adjacent(a, b) -> bool:
    vertical_overlap = min(a.y + a.h, b.y + b.h) > max(a.y, b.y)
    horizontal_overlap = min(a.x + a.w, b.x + b.w) > max(a.x, b.x)
    shares_vertical_edge = a.x + a.w == b.x or b.x + b.w == a.x
    shares_horizontal_edge = a.y + a.h == b.y or b.y + b.h == a.y
    return shares_vertical_edge and vertical_overlap or shares_horizontal_edge and horizontal_overlap


def rectangle_within_grid(rectangle, columns: int, rows: int) -> bool:
    return rectangle.x >= 0 and rectangle.y >= 0 and rectangle.w > 0 and rectangle.h > 0 and rectangle.x + rectangle.w <= columns and rectangle.y + rectangle.h <= rows
