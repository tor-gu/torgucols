import random
from collections import Counter

import pytest

import torgucols
from torgucols import INT32_MIN, EmptyResult, PageOutOfRange, encode_column


@pytest.fixture(scope="module")
def table(tmp_path_factory):
    """Random rows with nulls in every column, and the decoded rows"""
    rng = random.Random(7)
    n = 500
    raw = {
        "year": [rng.choice([2021, 2022, None]) for _ in range(n)],
        "flag": [rng.choice([True, False, None]) for _ in range(n)],
        "level": [rng.choice(["hi", "lo", "mid", None]) for _ in range(n)],
        "name": [rng.choice(["b", "a", "c", None]) for _ in range(n)],
        "count": [rng.randint(1, 9) for _ in range(n)],
    }
    out = str(tmp_path_factory.mktemp("store"))
    torgucols.write(
        out,
        {
            "t": {
                "columns": [
                    encode_column("year", "integer", raw["year"]),
                    encode_column("flag", "logical", raw["flag"]),
                    encode_column("level", "factor", raw["level"], ["lo", "mid", "hi"]),
                    encode_column("name", "character", raw["name"]),
                    encode_column("count", "integer", raw["count"]),
                ]
            }
        },
    )
    return torgucols.open(out).table("t"), raw


ORDER = {
    "year": [2021, 2022, None],
    "flag": [False, True, None],
    "level": ["lo", "mid", "hi", None],
    "name": ["a", "b", "c", None],
}


def reference_group(raw, dimensions, keep, weight=None):
    """Totals by decoded values, in the API's order (levels, null last)"""
    totals = Counter()
    for i in range(len(raw["count"])):
        if keep(i):
            totals[tuple(raw[d][i] for d in dimensions)] += (
                raw[weight][i] if weight else 1
            )
    return sorted(
        totals.items(),
        key=lambda kv: [ORDER[d].index(v) for d, v in zip(dimensions, kv[0])],
    )


def decoded(table, dimensions, groups):
    decoders = [table.decoder(d) for d in dimensions]
    return [(tuple(f(v) for f, v in zip(decoders, key)), n) for key, n in groups]


@pytest.mark.parametrize(
    "dimensions",
    [
        ("year",),
        ("flag",),
        ("level",),
        ("name",),
        ("level", "year"),
        ("name", "flag", "level"),
    ],
)
@pytest.mark.parametrize("weight", [None, "count"])
def test_group_matches_a_naive_count(table, dimensions, weight):
    table, raw = table
    got = decoded(table, dimensions, table.group(dimensions, (), weight))
    assert got == reference_group(raw, dimensions, lambda i: True, weight)


def test_group_with_filters(table):
    table, raw = table
    filters = (
        ("eq", "level", table.code("level", "hi")),
        ("between", "year", 2022, 2022),
    )
    got = decoded(table, ("name",), table.group(("name",), filters, "count"))
    expected = reference_group(
        raw,
        ("name",),
        lambda i: raw["level"][i] == "hi" and raw["year"][i] == 2022,
        "count",
    )
    assert got == expected


def test_select_and_mask(table):
    table, raw = table
    filters = (
        ("eq", "flag", table.code("flag", True)),
        ("eq", "name", table.code("name", None)),
    )
    expected = [
        i
        for i in range(table.rows)
        if raw["flag"][i] is True and raw["name"][i] is None
    ]
    assert table.select(filters) == expected
    mask = table.mask(filters)
    assert [i for i, m in enumerate(mask) if m] == expected
    assert table.mask(()) is None
    assert table.select(()) == range(table.rows)


def test_null_integer_isnt_in_a_between_range(table):
    table, raw = table
    rows = table.select((("between", "year", INT32_MIN + 1, 9999),))
    assert rows == [i for i in range(table.rows) if raw["year"][i] is not None]


def test_results_are_cached(table):
    table, _ = table
    filters = (("eq", "year", 2021),)
    assert table.select(filters) is table.select(filters)
    assert table.group(("level",), filters) is table.group(("level",), filters)


def test_unknown_filter(table):
    table, _ = table
    with pytest.raises(ValueError, match="unknown filter"):
        table.select((("like", "name", 1),))


def test_count_keys_across_tables(table):
    """A count of another table's rows, by a column of this one (a join by row index)"""
    table, raw = table
    refs = [3, 3, 0, 499, 0, 3]
    keys = zip(torgucols.take(table.values("level"), refs))
    got = decoded(table, ("level",), torgucols.count_keys(keys, [table.null("level")]))
    expected = Counter(raw["level"][i] for i in refs)
    assert got == [((k,), expected[k]) for k in ORDER["level"] if k in expected]


def test_page():
    items = list(range(25))
    assert torgucols.page(items, 3, 10) == (
        [20, 21, 22, 23, 24],
        {"page_number": 3, "page_size": 10, "record_count": 25},
    )
    with pytest.raises(PageOutOfRange, match="Page number 4 not found"):
        torgucols.page(items, 4, 10)
    with pytest.raises(EmptyResult, match="No matching records"):
        torgucols.page([], 1, 10)
