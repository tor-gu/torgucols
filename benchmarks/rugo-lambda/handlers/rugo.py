"""
Benchmark handler: the same counts from Parquet files (data/), read with rugo.

Each request reads only the columns it needs, with q2's date filter pushed
down to rugo (row groups pruned by their statistics, then rows filtered in
native code). Columns are re-read on every request, which is rugo's model:
it competes on reading less, not on keeping data in memory. Counting is the
same Python as the torgucols handler.
"""

import time

_T0 = time.perf_counter()
import json  # noqa: E402
import os  # noqa: E402
from collections import Counter  # noqa: E402

from rugo import parquet  # noqa: E402

IMPORT_MS = (time.perf_counter() - _T0) * 1000

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
Q2_DATES = (20230101, 20231231)
INT32_MIN = -(2**31)
_labels = None
_first = True


def read(table, columns, predicates=None):
    """{column: list of codes} for the rows that pass the predicates"""
    out = {c: [] for c in columns}
    with parquet.read_parquet(
        os.path.join(DATA, f"{table}.parquet"), columns=list(columns), predicates=predicates
    ) as reader:
        for morsel in reader:
            for c in columns:
                out[c].extend(morsel.column(c.encode()).to_pylist())
    return out


def decoder(table, column):
    d = _labels[table].get(column)
    if d is None:  # integer
        return lambda v: None if v == INT32_MIN else v
    return ([None] + d).__getitem__


def load(q):
    if q == "q1":
        return read("subject", ["incident_row", "race"]), read("incident", ["agency_county"])
    if q == "q2":
        lo, hi = Q2_DATES
        return (read("incident", ["year", "officer_race"], [("date", ">=", lo), ("date", "<=", hi)]),)
    if q == "q3":
        return read("attribute_subject_action", ["incident_row", "value"]), read("incident", ["year"])
    raise ValueError(q)


def count(q, cols):
    if q == "q1":
        sub, inc = cols
        keys = zip(map(inc["agency_county"].__getitem__, sub["incident_row"]), sub["race"])
        nulls, decs = (0, 0), [decoder("incident", "agency_county"), decoder("subject", "race")]
    elif q == "q2":
        (inc,) = cols
        keys = zip(inc["year"], inc["officer_race"])
        nulls, decs = (INT32_MIN, 0), [decoder("incident", "year"), decoder("incident", "officer_race")]
    else:
        at, inc = cols
        keys = zip(map(inc["year"].__getitem__, at["incident_row"]), at["value"])
        nulls, decs = (INT32_MIN, 0), [
            decoder("incident", "year"),
            decoder("attribute_subject_action", "value"),
        ]
    groups = sorted(
        Counter(keys).items(), key=lambda kv: [(v == n, v) for v, n in zip(kv[0], nulls)]
    )
    return groups, decs


def handler(event, context):
    global _labels, _first
    first, _first = _first, False
    q = event["q"]
    t0 = time.perf_counter()
    if _labels is None:
        with open(os.path.join(DATA, "labels.json"), encoding="utf-8") as f:
            _labels = json.load(f)
    cols = load(q)
    t1 = time.perf_counter()
    groups, decoders = count(q, cols)
    rows = [[f(v) for f, v in zip(decoders, key)] + [n] for key, n in groups]
    t2 = time.perf_counter()
    return {
        "rows": rows,
        "timings": {
            "import_ms": IMPORT_MS,
            "load_ms": (t1 - t0) * 1000,
            "count_ms": (t2 - t1) * 1000,
            "first_call": first,
        },
    }
