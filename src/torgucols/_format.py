"""
The on-disk format, shared by the writer and the reader.

A store is one directory:

  <table>.<column>.i32   the column's codes, native-endian int32, one per row
  torgucols.json         the manifest:

    {"format": 1, "byteorder": "little", "source": {...},
     "tables": {<table>: {"rows": n, "meta": {...},
                          "columns": [{"name": ..., "type": ..., "dictionary": [...]}]}}}

Column types and their codes:

  integer     the value; null is INT32_MIN, so 0 is an ordinary value
  logical     0 null, 1 FALSE, 2 TRUE
  factor      0 null, else the position in its levels ("dictionary"), from 1
  character   0 null, else the position in its sorted distinct values, from 1

factor and character are Arrow's dictionary-encoded columns (R's factors).
Sorting a column's codes sorts its values in level order (FALSE before TRUE),
and the readers put null last. "meta" is the application's, stored as given.
"""

FORMAT = 1
MANIFEST = "torgucols.json"
SUFFIX = ".i32"

INT32_MIN = -(2**31)
INT32_MAX = 2**31 - 1

TYPES = ("integer", "logical", "factor", "character")
DICTIONARY_TYPES = ("factor", "character")

# The code of null, by type
NULL = {"integer": INT32_MIN, "logical": 0, "factor": 0, "character": 0}


def column_file(table, column):
    return f"{table}.{column}{SUFFIX}"
