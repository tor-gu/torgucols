"""Benchmark baseline: no data, no imports beyond time"""

import time

_T0 = time.perf_counter()
IMPORT_MS = (time.perf_counter() - _T0) * 1000
_first = True


def handler(event, context):
    global _first
    first, _first = _first, False
    return {"rows": [], "timings": {"import_ms": IMPORT_MS, "first_call": first}}
