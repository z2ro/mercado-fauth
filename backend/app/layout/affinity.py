# Unlisted pairs are neutral. All soft weights live here.
AFFINITY = {
    frozenset(('acougue', 'frios')): 3,
    frozenset(('acougue', 'mercearia')): 1,
    frozenset(('acougue', 'bebidas')): 0,
    frozenset(('acougue', 'limpeza')): -10,
    frozenset(('hortifruti', 'mercearia')): 2,
    frozenset(('hortifruti', 'padaria')): 2,
    frozenset(('hortifruti', 'limpeza')): -10,
    frozenset(('padaria', 'limpeza')): -10,
    frozenset(('limpeza', 'higiene')): 3,
    frozenset(('limpeza', 'bebidas')): -2,
    frozenset(('frios', 'padaria')): 2,
    frozenset(('mercearia', 'bebidas')): 2,
}
SAME_CATEGORY_BONUS = 8
FRAGMENTATION_PENALTY = 6
FEATURED_POSITION_BONUS = (8, 6, 6, 8, 3, 1, 1, 3, 2, 0, 0, 2)


def affinity(a: str, b: str) -> int:
    return SAME_CATEGORY_BONUS if a == b else AFFINITY.get(frozenset((a, b)), 0)
