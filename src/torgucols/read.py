"""
Reading a store (at run time).

Only the manifest is read when a store is opened. Each column is read on its
first use and kept, as a list (indexing a list is faster than an array), so a
warm function pays for a column once.
"""

import array
import builtins
import json
import os
import sys
from collections import Counter, defaultdict
from functools import lru_cache

from ._format import DICTIONARY_TYPES, FORMAT, MANIFEST, NULL, column_file
from .query import sort_totals

CACHE_SIZE = 256
LOGICAL_VALUES = (None, False, True)
LOGICAL_CODES = {False: 1, True: 2}


class FormatError(Exception):
    pass


class Table:
    """
    A table of a store: rows, meta, columns ([{name, type, dictionary}]), names.

    Filters, for select(), mask() and group(), are a tuple of conditions, all
    of which must hold:

      ("eq", column, code)
      ("between", column, low, high)     low <= code <= high

    Results are cached by their arguments while the table is in memory.
    """

    def __init__(self, name, meta, data_dir):
        self.name = name
        self.data_dir = data_dir
        self.rows = meta["rows"]
        self.meta = meta.get("meta", {})
        self.columns = meta["columns"]
        self.by_name = {c["name"]: c for c in self.columns}
        self.names = [c["name"] for c in self.columns]
        self._values = {}
        self._codes = {}
        # Per-instance caches, so a new Table starts empty
        self.select = lru_cache(CACHE_SIZE)(self._select)
        self.mask = lru_cache(CACHE_SIZE)(self._mask)
        self.group = lru_cache(CACHE_SIZE)(self._group)

    def column(self, name):
        """The column's manifest entry; KeyError for an unknown column"""
        try:
            return self.by_name[name]
        except KeyError:
            raise KeyError(f"{self.name} has no column {name!r}") from None

    def type(self, name):
        return self.column(name)["type"]

    def null(self, name):
        """The column's null code"""
        return NULL[self.type(name)]

    def values(self, name):
        """The column's codes, as a list"""
        if name not in self._values:
            self.column(name)
            codes = array.array("i")
            path = os.path.join(self.data_dir, column_file(self.name, name))
            with builtins.open(path, "rb") as f:
                codes.frombytes(f.read())
            if len(codes) != self.rows:
                raise FormatError(
                    f"{path}: {len(codes)} values, the manifest says {self.rows}"
                )
            self._values[name] = codes.tolist()
        return self._values[name]

    def code(self, name, value):
        """
        The code of a value of the column, or None when no row can have it (a
        value outside the dictionary). None is null.
        """
        column = self.column(name)
        type_ = column["type"]
        if value is None:
            return NULL[type_]
        if type_ == "integer":
            return value
        if type_ == "logical":
            return LOGICAL_CODES[value]
        if name not in self._codes:
            self._codes[name] = {v: i for i, v in enumerate(column["dictionary"], 1)}
        return self._codes[name].get(value)

    def decoder(self, name):
        """Function from a code of the column to its value (None for null)"""
        column = self.column(name)
        type_ = column["type"]
        if type_ == "integer":
            null = NULL["integer"]
            return lambda v: None if v == null else v
        if type_ == "logical":
            return LOGICAL_VALUES.__getitem__
        if type_ in DICTIONARY_TYPES:
            return ([None] + column["dictionary"]).__getitem__
        raise FormatError(f"{self.name}.{name}: unknown type {type_!r}")

    def dictionary(self, name):
        """A factor or character column's values; code i is dictionary[i - 1]"""
        return self.column(name)["dictionary"]

    def _test(self, condition):
        """(values, test on a code) for a filter condition"""
        op, name, *args = condition
        values = self.values(name)
        if op == "eq":
            return values, args[0].__eq__
        if op == "between":
            low, high = args
            return values, lambda v: low <= v <= high
        raise ValueError(f"unknown filter {condition!r}")

    def _select(self, filters):
        """
        Indexes of the rows where the filters hold, in stored order: a
        sequence (a range when there are no filters)
        """
        rows = range(self.rows)
        for condition in filters:
            values, test = self._test(condition)
            rows = [i for i in rows if test(values[i])]
        return rows

    def _mask(self, filters):
        """
        A bytearray with 1 for each row where the filters hold, or None when
        there are no filters. For filtering rows of another table that refer
        to this one by row index.
        """
        if not filters:
            return None
        mask = bytearray(self.rows)
        for i in self.select(filters):
            mask[i] = 1
        return mask

    def _group(self, dimensions, filters=(), weight=None):
        """
        ((codes, total), ...) for each combination of the dimensions' codes,
        in code order with null last. The total is the number of rows, or the
        sum of the weight column. Only rows where the filters hold count.
        """
        columns = [self.values(d) for d in dimensions]
        weights = self.values(weight) if weight is not None else None
        if filters:
            rows = self.select(filters)
            columns = [map(c.__getitem__, rows) for c in columns]
            if weights is not None:
                weights = map(weights.__getitem__, rows)
        keys = zip(*columns)
        if weights is None:
            totals = Counter(keys)
        else:
            totals = defaultdict(int)
            for key, w in zip(keys, weights):
                totals[key] += w
        return sort_totals(totals.items(), [self.null(d) for d in dimensions])


class Store:
    """A store's tables ({name: Table}) and source, from its manifest"""

    def __init__(self, data_dir):
        path = os.path.join(data_dir, MANIFEST)
        with builtins.open(path, encoding="utf-8") as f:
            manifest = json.load(f)
        if manifest.get("format") != FORMAT:
            raise FormatError(
                f"{path}: format {manifest.get('format')!r}, not {FORMAT}"
            )
        if manifest["byteorder"] != sys.byteorder:
            raise FormatError(f"{path}: data is {manifest['byteorder']}-endian")
        self.data_dir = data_dir
        self.source = manifest.get("source", {})
        self.tables = {
            name: Table(name, meta, data_dir)
            for name, meta in manifest["tables"].items()
        }

    def table(self, name):
        try:
            return self.tables[name]
        except KeyError:
            raise KeyError(f"no table {name!r} in {self.data_dir}") from None


def open(data_dir):
    """The store in data_dir (only its manifest is read)"""
    return Store(data_dir)
