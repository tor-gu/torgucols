"""Writing a store (at build time): encoding columns, then writing their files"""

import array
import json
import os
import sys

from ._format import (
    DICTIONARY_TYPES,
    FORMAT,
    INT32_MAX,
    INT32_MIN,
    MANIFEST,
    NULL,
    SUFFIX,
    TYPES,
    column_file,
)

LOGICALS = {"FALSE": 1, "TRUE": 2, False: 1, True: 2}


class EncodeError(Exception):
    pass


def _is_null(v):
    return v is None or v == ""


def _encode_integer(name, values):
    null = NULL["integer"]
    codes = []
    for v in values:
        if _is_null(v):
            codes.append(null)
            continue
        if isinstance(v, bool):
            raise EncodeError(f"{name}: {v!r} is a boolean; use the logical type")
        if isinstance(v, float) and not v.is_integer():
            raise EncodeError(f"{name}: {v!r} is not a whole number")
        try:
            n = int(v)
        except (TypeError, ValueError):
            raise EncodeError(f"{name}: {v!r} is not an integer") from None
        if not INT32_MIN < n <= INT32_MAX:
            raise EncodeError(f"{name}: {n} is outside int32 (or is the null code)")
        codes.append(n)
    return codes


def _encode_logical(name, values):
    try:
        return [0 if _is_null(v) else LOGICALS[v] for v in values]
    except (KeyError, TypeError):
        bad = next(v for v in values if not _is_null(v) and v not in LOGICALS)
        raise EncodeError(f"{name}: {bad!r} is not a logical") from None


def _encode_dictionary(name, values, dictionary):
    if len(set(dictionary)) != len(dictionary):
        raise EncodeError(f"{name}: repeated levels {dictionary}")
    codes = {level: i for i, level in enumerate(dictionary, 1)}
    try:
        return [0 if _is_null(v) else codes[v] for v in values]
    except KeyError as e:
        raise EncodeError(
            f"{name}: {e.args[0]!r} is not a level ({dictionary})"
        ) from None


def encode_column(name, type_, values, levels=None):
    """
    A column, encoded: {"name", "type", "codes", "dictionary" (factor and
    character only)}, as write() takes it.

    values are Python values or CSV strings; None and "" are null. A logical is
    True/False or "TRUE"/"FALSE". A factor needs its levels; a character
    column's dictionary is its sorted distinct values.
    """
    if type_ == "integer":
        codes, dictionary = _encode_integer(name, values), None
    elif type_ == "logical":
        codes, dictionary = _encode_logical(name, values), None
    elif type_ == "factor":
        if not levels:
            raise EncodeError(f"{name}: a factor needs its levels")
        dictionary = list(levels)
        codes = _encode_dictionary(name, values, dictionary)
    elif type_ == "character":
        dictionary = sorted({v for v in values if not _is_null(v)})
        codes = _encode_dictionary(name, values, dictionary)
    else:
        raise EncodeError(f"{name}: unknown column type {type_!r}")
    column = {"name": name, "type": type_, "codes": codes}
    if dictionary is not None:
        column["dictionary"] = dictionary
    return column


def _check_name(kind, name):
    if not name or "/" in name or os.sep in name or name.startswith("."):
        raise EncodeError(f"bad {kind} name {name!r}")
    if kind == "table" and "." in name:
        raise EncodeError(f"table name {name!r} can't contain '.'")


def _check_column(table, column):
    """A column given to write(), as encode_column() makes them; returns its rows"""
    name = column.get("name")
    _check_name("column", name)
    label = f"{table}.{name}"
    type_ = column.get("type")
    if type_ not in TYPES:
        raise EncodeError(f"{label}: unknown column type {type_!r}")
    codes = column["codes"]
    has_dictionary = column.get("dictionary") is not None
    if has_dictionary != (type_ in DICTIONARY_TYPES):
        raise EncodeError(f"{label}: a dictionary is for factor and character only")
    if codes:
        if type_ in DICTIONARY_TYPES:
            high = len(column["dictionary"])
        elif type_ == "logical":
            high = 2
        else:
            high = INT32_MAX
        low = INT32_MIN if type_ == "integer" else 0
        if min(codes) < low or max(codes) > high:
            raise EncodeError(f"{label}: codes outside {low}..{high}")
    return len(codes)


def write(out_dir, tables, source=None):
    """
    Writes a store to out_dir, replacing any store there.

    tables: {table: {"columns": [encode_column() results], "meta": {...}}}.
    meta (optional) is kept in the manifest for the application. source
    (optional) is a dict describing where the data came from.

    Returns out_dir.
    """
    manifest_tables = {}
    for table, spec in tables.items():
        _check_name("table", table)
        columns = spec["columns"]
        if not columns:
            raise EncodeError(f"{table}: no columns")
        rows = {_check_column(table, c) for c in columns}
        if len(rows) != 1:
            raise EncodeError(f"{table}: columns of different lengths {sorted(rows)}")
        names = [c["name"] for c in columns]
        if len(set(names)) != len(names):
            raise EncodeError(f"{table}: repeated column names")
        entries = []
        for c in columns:
            entry = {"name": c["name"], "type": c["type"]}
            if c.get("dictionary") is not None:
                entry["dictionary"] = list(c["dictionary"])
            entries.append(entry)
        manifest_tables[table] = {
            "rows": rows.pop(),
            "meta": dict(spec.get("meta") or {}),
            "columns": entries,
        }

    os.makedirs(out_dir, exist_ok=True)
    for entry in os.listdir(out_dir):
        if entry.endswith(SUFFIX) or entry == MANIFEST:
            os.remove(os.path.join(out_dir, entry))
    for table, spec in tables.items():
        for c in spec["columns"]:
            with open(os.path.join(out_dir, column_file(table, c["name"])), "wb") as f:
                array.array("i", c["codes"]).tofile(f)
    # The manifest last: a store without one is incomplete
    manifest = {
        "format": FORMAT,
        "byteorder": sys.byteorder,
        "source": dict(source or {}),
        "tables": manifest_tables,
    }
    with open(os.path.join(out_dir, MANIFEST), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, separators=(",", ":"))
    return out_dir
