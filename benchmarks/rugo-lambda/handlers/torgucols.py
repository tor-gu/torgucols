"""
Benchmark handler: counts from a torgucols store (data/).

Columns stay in memory once loaded, as in the real API. Results are not
cached (q2 calls the uncached Table._group), so a warm call still counts.
"""

import time

_T0 = time.perf_counter()
import os  # noqa: E402

import torgucols  # noqa: E402

IMPORT_MS = (time.perf_counter() - _T0) * 1000

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
Q2_DATES = (20230101, 20231231)
NEEDS = {
    "q1": [("incident", "agency_county"), ("subject", "incident_row"), ("subject", "race")],
    "q2": [("incident", "date"), ("incident", "year"), ("incident", "officer_race")],
    "q3": [
        ("incident", "year"),
        ("attribute_subject_action", "incident_row"),
        ("attribute_subject_action", "value"),
    ],
}
_store = None
_first = True


def count(store, q):
    """((codes, n), ...) null last, and a decoder per position"""
    inc = store.table("incident")
    if q == "q1":
        sub = store.table("subject")
        keys = zip(
            torgucols.take(inc.values("agency_county"), sub.values("incident_row")),
            sub.values("race"),
        )
        nulls = (inc.null("agency_county"), sub.null("race"))
        return torgucols.count_keys(keys, nulls), [
            inc.decoder("agency_county"),
            sub.decoder("race"),
        ]
    if q == "q2":
        lo, hi = Q2_DATES
        dims = ("year", "officer_race")
        groups = inc._group(dims, (("between", "date", lo, hi),))
        return groups, [inc.decoder(d) for d in dims]
    if q == "q3":
        at = store.table("attribute_subject_action")
        keys = zip(torgucols.take(inc.values("year"), at.values("incident_row")), at.values("value"))
        nulls = (inc.null("year"), at.null("value"))
        return torgucols.count_keys(keys, nulls), [inc.decoder("year"), at.decoder("value")]
    raise ValueError(q)


def handler(event, context):
    global _store, _first
    first, _first = _first, False
    q = event["q"]
    t0 = time.perf_counter()
    if _store is None:
        _store = torgucols.open(DATA)
    for table, column in NEEDS[q]:
        _store.table(table).values(column)
    t1 = time.perf_counter()
    groups, decoders = count(_store, q)
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
