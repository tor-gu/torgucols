"""Helpers for counting keys an application builds itself (e.g. across tables)"""

from collections import Counter


def sort_totals(items, nulls):
    """
    ((codes, total), ...) sorted by codes, null last in each position.

    items: (codes tuple, total) pairs. nulls: each position's null code
    (Table.null()).
    """
    nulls = tuple(nulls)
    return tuple(
        sorted(
            items,
            key=lambda kv: [(v == n, v) for v, n in zip(kv[0], nulls)],
        )
    )


def count_keys(keys, nulls):
    """
    ((codes, count), ...) for an iterable of codes tuples, ordered as
    sort_totals(). For keys built from several tables, e.g. a column joined
    through a row-index column with take().
    """
    return sort_totals(Counter(keys).items(), nulls)


def take(values, rows):
    """values[i] for each i in rows, lazily"""
    return map(values.__getitem__, rows)
