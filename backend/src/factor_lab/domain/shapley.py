from __future__ import annotations

from collections.abc import Callable, Hashable, Sequence
from itertools import combinations
from math import factorial

Coalition = frozenset


def exact_shapley(
    players: Sequence[Hashable], value: Callable[[frozenset], float]
) -> tuple[dict, dict, dict]:
    """Exact Shapley values and pairwise Shapley interaction indices.

    `value` is evaluated once per coalition (2^n calls), so n should stay small
    (factor groups, not raw features). Returns (phi, interactions, coalition values).
    Efficiency holds exactly: sum(phi) == v(N) - v(∅).
    """
    n = len(players)
    cache: dict[frozenset, float] = {}

    def v(s: frozenset) -> float:
        if s not in cache:
            cache[s] = value(s)
        return cache[s]

    phi = {}
    for p in players:
        others = [q for q in players if q != p]
        total = 0.0
        for k in range(len(others) + 1):
            w = factorial(k) * factorial(n - k - 1) / factorial(n)
            for s in combinations(others, k):
                s = frozenset(s)
                total += w * (v(s | {p}) - v(s))
        phi[p] = total

    interactions = {}
    for i, j in combinations(players, 2):
        others = [q for q in players if q not in (i, j)]
        total = 0.0
        for k in range(len(others) + 1):
            w = factorial(k) * factorial(n - k - 2) / factorial(n - 1)
            for s in combinations(others, k):
                s = frozenset(s)
                total += w * (v(s | {i, j}) - v(s | {i}) - v(s | {j}) + v(s))
        interactions[(i, j)] = total
    return phi, interactions, cache
