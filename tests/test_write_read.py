import array
import json
import os
import sys

import pytest

import torgucols
from torgucols import INT32_MIN, MANIFEST, EncodeError, FormatError, encode_column

GOLDEN = os.path.join(os.path.dirname(__file__), "golden")


def one_table(tmp_path, columns, meta=None, source=None):
    out = str(tmp_path / "store")
    torgucols.write(out, {"t": {"columns": columns, "meta": meta}}, source)
    return torgucols.open(out).table("t")


def test_round_trip_of_every_type_with_nulls(tmp_path):
    table = one_table(
        tmp_path,
        [
            encode_column("n", "integer", ["3", "", "0", "-7"]),
            encode_column("b", "logical", ["TRUE", "", "FALSE", "TRUE"]),
            encode_column("f", "factor", ["lo", "hi", "", "lo"], ["lo", "hi", "x"]),
            encode_column("c", "character", ["b", "a", "b", ""]),
        ],
        meta={"weight": "n"},
        source={"package": "p"},
    )
    assert table.rows == 4
    assert table.meta == {"weight": "n"}
    decoded = {n: list(map(table.decoder(n), table.values(n))) for n in table.names}
    assert decoded == {
        "n": [3, None, 0, -7],
        "b": [True, None, False, True],
        "f": ["lo", "hi", None, "lo"],
        "c": ["b", "a", "b", None],
    }
    assert table.dictionary("f") == ["lo", "hi", "x"]
    assert table.dictionary("c") == ["a", "b"]


def test_codes():
    assert encode_column("n", "integer", [5, None, 0])["codes"] == [5, INT32_MIN, 0]
    assert encode_column("n", "integer", [2.0, -3.0, "4"])["codes"] == [2, -3, 4]
    assert encode_column("b", "logical", [True, None, False])["codes"] == [2, 0, 1]
    assert encode_column("f", "factor", ["b", None], ["a", "b"])["codes"] == [2, 0]
    assert encode_column("c", "character", ["z", "y", None])["codes"] == [2, 1, 0]


def test_code_lookup(tmp_path):
    table = one_table(
        tmp_path,
        [
            encode_column("n", "integer", [1]),
            encode_column("b", "logical", [True]),
            encode_column("f", "factor", ["lo"], ["lo", "hi"]),
        ],
    )
    assert table.code("n", 12) == 12
    assert table.code("b", False) == 1
    assert table.code("f", "hi") == 2
    assert table.code("f", "nope") is None
    assert table.code("f", None) == 0
    assert table.code("n", None) == INT32_MIN


@pytest.mark.parametrize(
    "args, message",
    [
        (("n", "integer", ["x"]), "not an integer"),
        (("n", "integer", [2**31]), "outside int32"),
        (("n", "integer", [INT32_MIN]), "null code"),
        (("n", "integer", [True]), "use the logical type"),
        (("n", "integer", [False]), "use the logical type"),
        (("n", "integer", [2.7]), "not a whole number"),
        (("n", "integer", [float("nan")]), "not a whole number"),
        (("n", "integer", [float("inf")]), "not a whole number"),
        (("b", "logical", ["yes"]), "not a logical"),
        (("f", "factor", ["a"]), "needs its levels"),
        (("f", "factor", ["c"], ["a", "b"]), "not a level"),
        (("f", "factor", ["a"], ["a", "a"]), "repeated levels"),
        (("x", "date", ["2020-01-01"]), "unknown column type"),
    ],
)
def test_encode_errors(args, message):
    with pytest.raises(EncodeError, match=message):
        encode_column(*args)


@pytest.mark.parametrize(
    "tables, message",
    [
        ({"t": {"columns": []}}, "no columns"),
        ({"a.b": {"columns": [encode_column("n", "integer", [1])]}}, "can't contain"),
        (
            {
                "t": {
                    "columns": [
                        encode_column("n", "integer", [1]),
                        encode_column("m", "integer", [1, 2]),
                    ]
                }
            },
            "different lengths",
        ),
        (
            {
                "t": {
                    "columns": [
                        encode_column("n", "integer", [1]),
                        encode_column("n", "integer", [2]),
                    ]
                }
            },
            "repeated column",
        ),
        (
            {
                "t": {
                    "columns": [
                        {
                            "name": "f",
                            "type": "factor",
                            "codes": [3],
                            "dictionary": ["a"],
                        }
                    ]
                }
            },
            "outside",
        ),
        (
            {
                "t": {
                    "columns": [
                        {
                            "name": "b",
                            "type": "logical",
                            "codes": [1],
                            "dictionary": ["a"],
                        }
                    ]
                }
            },
            "dictionary is for",
        ),
    ],
)
def test_write_errors(tmp_path, tables, message):
    with pytest.raises(EncodeError, match=message):
        torgucols.write(str(tmp_path), tables)


def test_precoded_columns(tmp_path):
    table = one_table(
        tmp_path,
        [
            {"name": "row", "type": "integer", "codes": [0, 0, 1]},
            {
                "name": "v",
                "type": "factor",
                "codes": [1, 2, 0],
                "dictionary": ["a", "b"],
            },
        ],
    )
    assert table.values("row") == [0, 0, 1]
    assert list(map(table.decoder("v"), table.values("v"))) == ["a", "b", None]


def test_a_rewrite_removes_stale_files_and_only_those(tmp_path):
    out = tmp_path / "store"
    out.mkdir()
    (out / "old.gone.i32").write_bytes(b"\0" * 4)
    (out / "keep.txt").write_text("mine")
    torgucols.write(str(out), {"t": {"columns": [encode_column("n", "integer", [1])]}})
    assert sorted(os.listdir(out)) == sorted(["keep.txt", MANIFEST, "t.n.i32"])


def test_manifest(tmp_path):
    out = str(tmp_path)
    torgucols.write(
        out,
        {"t": {"columns": [encode_column("c", "character", ["é"])]}},
        {"version": "1"},
    )
    with open(os.path.join(out, MANIFEST), encoding="utf-8") as f:
        manifest = json.load(f)
    assert manifest == {
        "format": 1,
        "byteorder": sys.byteorder,
        "source": {"version": "1"},
        "tables": {
            "t": {
                "rows": 1,
                "meta": {},
                "columns": [{"name": "c", "type": "character", "dictionary": ["é"]}],
            }
        },
    }
    assert torgucols.open(out).source == {"version": "1"}


def rewrite_manifest(out, **changes):
    path = os.path.join(out, MANIFEST)
    with open(path, encoding="utf-8") as f:
        manifest = json.load(f)
    manifest.update(changes)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f)


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"format": 2}, "format 2"),
        ({"byteorder": "big" if sys.byteorder == "little" else "little"}, "endian"),
    ],
)
def test_the_reader_rejects_other_formats(tmp_path, changes, message):
    out = str(tmp_path)
    torgucols.write(out, {"t": {"columns": [encode_column("n", "integer", [1])]}})
    rewrite_manifest(out, **changes)
    with pytest.raises(FormatError, match=message):
        torgucols.open(out)


def test_the_reader_rejects_a_short_column(tmp_path):
    out = str(tmp_path)
    torgucols.write(out, {"t": {"columns": [encode_column("n", "integer", [1, 2])]}})
    with open(os.path.join(out, "t.n.i32"), "wb") as f:
        array.array("i", [1]).tofile(f)
    with pytest.raises(FormatError, match="1 values"):
        torgucols.open(out).table("t").values("n")


def test_columns_are_read_on_first_use_and_kept(tmp_path):
    out = str(tmp_path)
    torgucols.write(out, {"t": {"columns": [encode_column("n", "integer", [1])]}})
    table = torgucols.open(out).table("t")
    os.remove(os.path.join(out, MANIFEST))  # opening read only the manifest
    first = table.values("n")
    os.remove(os.path.join(out, "t.n.i32"))
    assert table.values("n") is first


def test_unknown_names(tmp_path):
    out = str(tmp_path)
    torgucols.write(out, {"t": {"columns": [encode_column("n", "integer", [1])]}})
    store = torgucols.open(out)
    with pytest.raises(KeyError, match="no table"):
        store.table("u")
    with pytest.raises(KeyError, match="no column"):
        store.table("t").values("m")


@pytest.mark.skipif(sys.byteorder != "little", reason="golden files are little-endian")
def test_golden_njoagleod_fixture(tmp_path):
    """The fixture export of njoagleod-api gives the bytes its own build_data.py wrote"""
    with open(os.path.join(GOLDEN, "export.json"), encoding="utf-8") as f:
        export = json.load(f)
    tables = {
        t: {
            "columns": [
                encode_column(c["name"], c["type"], c["values"], c.get("levels"))
                for c in spec["columns"]
            ]
        }
        for t, spec in export.items()
    }
    out = str(tmp_path)
    torgucols.write(out, tables)
    golden = sorted(f for f in os.listdir(GOLDEN) if f.endswith(".i32"))
    assert sorted(f for f in os.listdir(out) if f.endswith(".i32")) == golden
    for name in golden:
        with open(os.path.join(GOLDEN, name), "rb") as f:
            expected = f.read()
        with open(os.path.join(out, name), "rb") as f:
            assert f.read() == expected, name
