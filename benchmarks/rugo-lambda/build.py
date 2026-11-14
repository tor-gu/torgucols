"""
Build the three Lambda packages for the torgucols vs rugo cold-start benchmark.

Usage (a Python 3.12 that has pyarrow, for writing Parquet):
    python build.py <njoaguof counts data dir> <rugo wheel>

Reads njoaguof-api's counts columns (counts/data: *.i32 and labels.json, the
pre-torgucols format) and writes, next to this file:

    pkg/baseline/   a trivial handler, no data
    pkg/torgucols/  torgucols (from ../../torgucols/src) + a torgucols store
    pkg/rugo/       rugo + draken (unpacked from the wheel) + Parquet files
    dist/*.zip      the three packages, as Lambda gets them
    expected.json   each query's answer, computed here from the source files

Both data packages hold all the counts columns, as the real function does, with
the same rows in the same order. The incident table is sorted by date (subject
and attribute rows point at the new positions), so rugo can prune row groups on
the date filter of q2. That's neutral for torgucols.
"""

import array
import json
import os
import shutil
import sys
import zipfile
from collections import Counter

import pyarrow as pa
import pyarrow.parquet as pq

HERE = os.path.dirname(os.path.abspath(__file__))
TORGUCOLS_SRC = os.path.join(HERE, "..", "..", "src", "torgucols")
sys.path.insert(0, os.path.dirname(TORGUCOLS_SRC))
import torgucols  # noqa: E402

INT32_MIN = torgucols.INT32_MIN
ROW_GROUP = 10_000  # Parquet rows per group (pyarrow's default would be one group)

INCIDENT_INTEGER = ["form_id", "date", "year", "month", "officer_name_id"]
INCIDENT_CODED = [
    "agency_id",
    "agency",
    "agency_county",
    "incident_municipality",
    "incident_municipality_county",
    "officer_gender",
    "officer_race",
    "video_footage",
]
SUBJECT_CODED = ["type", "race", "gender"]
SUBJECT_LOGICAL = ["arrested", "juvenile", "injured"]

# The queries: q1 subjects by agency_county, race (a join over all subjects);
# q2 incidents by year, officer_race in 2023 (a date filter rugo can prune on);
# q3 incidents by year, subject_action (a join over 225k attribute pairs)
Q2_DATES = (20230101, 20231231)


def read_i32(src, name):
    a = array.array("i")
    with open(os.path.join(src, f"{name}.i32"), "rb") as f:
        a.frombytes(f.read())
    return a.tolist()


def load_source(src):
    with open(os.path.join(src, "labels.json"), encoding="utf-8") as f:
        labels = json.load(f)["labels"]
    attributes = sorted(k.split(".", 1)[1] for k in labels if k.startswith("attribute."))
    inc = {c: read_i32(src, f"inc.{c}") for c in INCIDENT_INTEGER + INCIDENT_CODED}
    subj = {"inc_row": read_i32(src, "subj.inc_row")}
    subj.update({c: read_i32(src, f"subj.{c}") for c in SUBJECT_CODED + SUBJECT_LOGICAL})
    attrs = {
        a: {"row": read_i32(src, f"a_{a}.row"), "v": read_i32(src, f"a_{a}.v")}
        for a in attributes
    }
    return labels, inc, subj, attrs


def sort_null_last(items, nulls):
    return sorted(items, key=lambda kv: [(v == n, v) for v, n in zip(kv[0], nulls)])


def expected_answers(labels, inc, subj, attrs):
    """The three queries, straight from the source columns (0 is null there)"""

    def decode(key, codes):
        return [None] + labels[key] if key else None

    def rows(counter, decoders):
        out = []
        for key, n in sort_null_last(counter.items(), [0] * len(decoders)):
            out.append([d[v] if d else v for d, v in zip(decoders, key)] + [n])
        return out

    county = inc["agency_county"]
    q1 = Counter(zip((county[i] for i in subj["inc_row"]), subj["race"]))
    lo, hi = Q2_DATES
    keep = [i for i, d in enumerate(inc["date"]) if lo <= d <= hi]
    q2 = Counter((inc["year"][i], inc["officer_race"][i]) for i in keep)
    a = attrs["subject_action"]
    q3 = Counter(zip((inc["year"][i] for i in a["row"]), a["v"]))
    return {
        "q1": rows(q1, [decode("incident.agency_county", 0), decode("subject.race", 0)]),
        "q2": rows(q2, [None, decode("incident.officer_race", 0)]),
        "q3": rows(q3, [None, decode("attribute.subject_action", 0)]),
    }


def tables_by_date(labels, inc, subj, attrs):
    """
    {table: {column: (type, codes, dictionary)}} with incidents sorted by date,
    nulls in torgucols' codes (INT32_MIN for integers, 0 otherwise)
    """
    n = len(inc["date"])
    order = sorted(range(n), key=lambda i: (inc["date"][i], i))
    new_pos = [0] * n
    for new, old in enumerate(order):
        new_pos[old] = new
    tables = {"incident": {}, "subject": {}}
    for c in INCIDENT_INTEGER:
        values = [inc[c][i] for i in order]
        if c == "officer_name_id":
            values = [INT32_MIN if v == 0 else v for v in values]
        tables["incident"][c] = ("integer", values, None)
    for c in INCIDENT_CODED:
        tables["incident"][c] = ("factor", [inc[c][i] for i in order], labels[f"incident.{c}"])
    tables["subject"]["incident_row"] = ("integer", [new_pos[i] for i in subj["inc_row"]], None)
    for c in SUBJECT_CODED:
        tables["subject"][c] = ("factor", subj[c], labels[f"subject.{c}"])
    for c in SUBJECT_LOGICAL:
        tables["subject"][c] = ("logical", subj[c], None)
    for a, cols in attrs.items():
        tables[f"attribute_{a}"] = {
            "incident_row": ("integer", [new_pos[i] for i in cols["row"]], None),
            "value": ("factor", cols["v"], labels[f"attribute.{a}"]),
        }
    return tables


def write_torgucols(out_dir, tables):
    torgucols.write(
        out_dir,
        {
            name: {
                "columns": [
                    {"name": c, "type": t, "codes": codes, **({"dictionary": d} if d else {})}
                    for c, (t, codes, d) in cols.items()
                ]
            }
            for name, cols in tables.items()
        },
        {"package": "njoaguof", "note": "benchmark copy, incidents sorted by date"},
    )


def write_parquet(out_dir, tables):
    os.makedirs(out_dir, exist_ok=True)
    dictionaries = {}
    for name, cols in tables.items():
        table = pa.table({c: pa.array(codes, pa.int32()) for c, (_, codes, _) in cols.items()})
        pq.write_table(
            table,
            os.path.join(out_dir, f"{name}.parquet"),
            compression="none",  # smallest once zipped (6.6 vs 7.5 MB for zstd)
            row_group_size=ROW_GROUP,
            write_statistics=True,
        )
        dictionaries[name] = {c: d for c, (_, _, d) in cols.items() if d}
    with open(os.path.join(out_dir, "labels.json"), "w", encoding="utf-8") as f:
        json.dump(dictionaries, f, ensure_ascii=False, separators=(",", ":"))


def unpack_wheel(wheel, out_dir):
    """What pip install --target would put in the package"""
    with zipfile.ZipFile(wheel) as z:
        z.extractall(out_dir)


def zip_dir(src, dest):
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(src):
            dirs[:] = sorted(d for d in dirs if d != "__pycache__")
            for f in sorted(files):
                path = os.path.join(root, f)
                z.write(path, os.path.relpath(path, src))


def main(src, wheel):
    labels, inc, subj, attrs = load_source(src)
    with open(os.path.join(HERE, "expected.json"), "w", encoding="utf-8") as f:
        json.dump(expected_answers(labels, inc, subj, attrs), f, ensure_ascii=False)
    tables = tables_by_date(labels, inc, subj, attrs)

    pkg = os.path.join(HERE, "pkg")
    shutil.rmtree(pkg, ignore_errors=True)
    handlers = os.path.join(HERE, "handlers")
    for name in ("baseline", "torgucols", "rugo"):
        os.makedirs(os.path.join(pkg, name))
        shutil.copy(os.path.join(handlers, f"{name}.py"), os.path.join(pkg, name, "handler.py"))
    shutil.copytree(
        TORGUCOLS_SRC,
        os.path.join(pkg, "torgucols", "torgucols"),
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    write_torgucols(os.path.join(pkg, "torgucols", "data"), tables)
    unpack_wheel(wheel, os.path.join(pkg, "rugo"))
    write_parquet(os.path.join(pkg, "rugo", "data"), tables)

    dist = os.path.join(HERE, "dist")
    shutil.rmtree(dist, ignore_errors=True)
    os.makedirs(dist)
    for name in ("baseline", "torgucols", "rugo"):
        dest = os.path.join(dist, f"{name}.zip")
        zip_dir(os.path.join(pkg, name), dest)
        unpacked = sum(
            os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(os.path.join(pkg, name)) for f in fs
        )
        print(f"{name:10} zipped {os.path.getsize(dest) / 2**20:6.2f} MB   unpacked {unpacked / 2**20:6.2f} MB")


if __name__ == "__main__":
    main(*sys.argv[1:3])
