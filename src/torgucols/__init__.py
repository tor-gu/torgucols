"""
torgucols: dictionary-encoded int32 column files, written at build time and
read at run time with the standard library only, for fast Lambda cold starts.
See _format.py for the format.
"""

from ._format import FORMAT, INT32_MIN, MANIFEST
from .page import EmptyResult, PageOutOfRange, page
from .query import count_keys, sort_totals, take
from .read import FormatError, Store, Table, open
from .write import EncodeError, encode_column, write

__all__ = [
    "FORMAT",
    "INT32_MIN",
    "MANIFEST",
    "EmptyResult",
    "PageOutOfRange",
    "page",
    "count_keys",
    "sort_totals",
    "take",
    "FormatError",
    "Store",
    "Table",
    "open",
    "EncodeError",
    "encode_column",
    "write",
]
