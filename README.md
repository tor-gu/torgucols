# torgucols

A simpleminded Python library for writing int32 column files at build time, and reading, filtering and counting them in cold-started AWS Lambda functions. It is for moderate-sized, read-only datasets whose text columns have few distinct values.

## Design goals
- I want to use this in AWS lambdas, which should be presumed to be cold-started because of low-usage.
- I am extremely cheap. I am not going to pay for provisioned concurrency or [SnapStart](https://docs.aws.amazon.com/lambda/latest/dg/snapstart.html), or run warming pings.
- The datasets are not huge: small enough to load into memory on a cold start.
- The datasets are not tiny: large enough that the storage format matters.
- The data is fixed, or changes infrequently. It is acceptable to ship the data with the code.
- The queries are not fixed. Filters and groupings are chosen per request, so the results can't be precomputed into a key-value store.

## Where does the time go?
On a lambda cold start, there is a fixed cost of about 280ms. We want to get a response time as close to that as possible. Here is where additional time will be spent:
- Loading packages. The larger the package, the longer the start time, even before the package is imported.  Observed at about 2–9ms per zipped MB (torgucols and rugo, with their data, were measured at the low end of the range).
- Importing packages. Importing a package has a fixed cost before any lines of code are executed.  This can be in the hundreds of milliseconds.
- Fetching data. Whether data is loaded as needed (e.g. from an S3 file) or shipped with the code, getting the bytes to the function costs time. If the data is shipped with the code, the package-loading cost above applies. The cost of fetching data from an external source varies by mechanism.
- Reading data into memory. Once the raw bytes are available, they have to be transformed into Python objects. This can be 20–150ms per MB of data read, depending on format and CPU share.
- Running the first query. Lambdas at low memory get a fraction of a CPU, so work that is quick locally is several times slower there. A query that scans whole tables, such as a grouped count, pays for this on every cold start.

## The approach
- **Standard library only.** The reader uses `array`, `json` and `collections`, so there are no native packages to ship or import. torgucols imports in about 7 ms on Lambda, against about 87 ms for rugo (see below). pandas alone took 235 ms to import locally, and awswrangler needs both pandas and pyarrow.
- **The data ships with the code.** A store is a directory in the function package: one file of native-endian int32 codes per column, and a JSON manifest. There is no S3 fetch, database or loader, and a data refresh is a redeploy.
- **Every value is an int32 code.** Integers are stored as themselves. Text columns are dictionary-encoded: each distinct value is stored once in the manifest, and the column holds its position. This keeps the package small: the benchmark data (about 1.7M rows in 18 tables) is 4 MB zipped.
- **Load only what a query needs, then keep it.** Opening a store reads only the manifest. A column is read on its first use, kept as a Python list while the function is warm, and the results of `select`, `mask` and `group` are cached.
- **Plain Python queries.** Equality and range filters, grouped counts or sums over the columns a request names, and joins between tables through row-index columns. Grouping is one counting pass over the rows; only the groups are sorted, into level order with null last.
- **The writer and reader are one package,** so they agree on the format. The manifest carries a format version, and the reader rejects any other.

## When torgucols is the wrong choice
- **Text with many distinct values,** such as names or free-text descriptions. Every column dictionary is parsed when the store opens, whatever the query, so a large one costs every cold start. Mostly unique text gets no benefit from the encoding at all. SQLite, which reads only the pages a query touches, is a better fit for returning such columns.
- **Text search.** Filters are exact matches on whole values; there is no prefix or substring search.
- **Tiny data.** If the data is small enough that a JSON file loads quickly at start-up, that's simpler, and the storage format doesn't matter.
- **Large data.** Everything ships in the package and is held in memory, so the data has to fit both. Each zipped MB adds a few ms to every cold start, and each unpacked MB a query reads adds about 50 ms at 512 MB.
- **Warm, busy functions.** If most requests are warm, the import and load costs are paid rarely, and a dataframe library's faster queries win. pandas was 4–10 times faster than SQLite on warm queries, measured locally.
- **Data that changes at run time.** A store is written once, at build time.
- **Other value types,** such as floats, 64-bit integers or timestamps. Dates can be stored as integers such as yyyymmdd.
- **General SQL or dataframe work.** There are filters, grouping, joins through row indexes and paging; the application builds the rest.

## Compared with rugo

[rugo](https://pypi.org/project/rugo/) is the closest existing option: a Parquet reader built
for serverless cold starts. It has no runtime dependencies (its native engine comes in the
wheel), reads only the columns asked for, and skips row groups that a filter rules out. It
was benchmarked on Lambda against torgucols with [njoaguof-api](https://github.com/tor-gu/njoaguof-api)'s counts data and three of its
count queries. Both packages held the same data in the same order. The setup favoured rugo:
incidents were sorted by date so it could prune on the date-filtered query, and its Parquet
was uncompressed, the smallest once zipped. Details, code and raw results are in
[`benchmarks/rugo-lambda/`](benchmarks/rugo-lambda/).

Cold-start client time, medians of 30–41 forced cold starts (95% intervals in brackets):

| Query | Memory | Empty function | torgucols | rugo | torgucols saves |
|---|---|---|---|---|---|
| subjects by county, race | 1024 MB | 283 ms | 395 ms | 600 ms | 206 ms [180, 245] |
| incidents in 2023 by year, officer race (rugo prunes) | 1024 MB | | 369 ms | 495 ms | 126 ms [95, 149] |
| incidents by year, subject action (225k pairs) | 1024 MB | | 464 ms | 698 ms | 233 ms [179, 264] |
| subjects by county, race | 512 MB | 275 ms | 473 ms | 773 ms | 300 ms [248, 336] |
| incidents in 2023 by year, officer race | 512 MB | | 428 ms | 577 ms | 149 ms [98, 201] |
| incidents by year, subject action | 512 MB | | 634 ms | 919 ms | 284 ms [243, 334] |

**Where rugo's extra time goes:**
- **Import:** about 87 ms on Lambda (Init Duration +77 to +84 ms), against 7 ms for torgucols.
- **Reading:** +80 ms or more on the two joins at 1024 MB. Pruning brings the filtered query
  close (+6 ms), but not ahead.
- **Package:** 12 MB zipped against 4 MB. That's the smallest part (+27 to +59 ms).

Warm requests are also faster with torgucols, or about even on the filtered query: torgucols
keeps its columns in memory, while rugo reads them again on each request.

**The other options weren't benchmarked on Lambda this way:**
- **pyarrow and pandas** are large native packages: 153 MB and 76 MB installed (plus numpy's 43
  MB). Their imports took 71–89 ms and 235 ms locally, before reading any data. They're
  likely slower on Lambda, as rugo's import was.
- **duckdb** wasn't measured. It's a large native package too.
- **SQLite** comes with Python and opens in a few ms. [njoaguof-api](https://github.com/tor-gu/njoaguof-api) uses it for its row
  endpoints, but for grouped counts it measured about 13 times slower than int32 columns on
  Lambda.
- **A key-value store** doesn't fit the queries (see Design goals).

## Format (version 1)

A store is one directory, with one manifest file and one file per column:

```
data/
├── torgucols.json
├── officer.year.i32
├── officer.race.i32
└── ...
```

- `torgucols.json`, the manifest, holds the format version, byteorder, `source` and the
  tables, each with `rows`, `meta` (the application's own, e.g. a weight column) and
  `columns` (`name`, `type`, and `dictionary` for factor and character columns).
- Each `<table>.<column>.i32` file holds that column's codes, one native-endian int32 per
  row, with no header.

| type | code | null |
|---|---|---|
| `integer` | the value, from INT32_MIN + 1 to INT32_MAX | INT32_MIN (so 0 is an ordinary value) |
| `logical` | 1 FALSE, 2 TRUE | 0 |
| `factor` | position in its levels, from 1 | 0 |
| `character` | position in its sorted distinct values, from 1 | 0 |

INT32_MIN is reserved for null, so an integer column can't hold it; the writer raises
`EncodeError` for it and for any value outside int32.

`factor` and `character` are what Arrow calls dictionary-encoded columns (R's factors). Sorting
a column's codes sorts its values in level order. The query functions put null last.

## Use

At build time:

```python
import torgucols

torgucols.write("fn/data", {
    "agency": {
        "columns": [
            torgucols.encode_column("county", "factor", ["Essex", "Morris"], levels=["Essex", "Morris"]),
        ],
        "meta": {},
    },
    "officer": {
        "columns": [
            torgucols.encode_column("agency_row", "integer", [0, 0, 1]),   # row index into agency
            torgucols.encode_column("year", "integer", ["2021", "2022", "2022"]),
            torgucols.encode_column("race", "factor", ["White", "Asian", ""], levels=["White", "Asian"]),
        ],
        "meta": {"weight": None},
    },
}, source={"package": "example", "version": "1.0"})
```

At run time:

```python
store = torgucols.open("fn/data")      # reads only the manifest
t = store.table("officer")
t.values("year")                       # [2021, 2022, 2022]: codes as a list, read on first use and kept
t.code("race", "Asian")                # 2 (None if no row can have the value)
t.decoder("race")(2)                   # "Asian"
filters = (("eq", "race", 2), ("between", "year", 2021, 2022))
t.select(filters)                      # [1]: row indexes
t.group(("year", "race"), filters, weight=None)   # (((2022, 2), 1),): (codes, total), null last
torgucols.page(t.select(()), 1, 2)     # (rows 0-1, aux for torguapi links)

# Joins, through the officer table's row-index column
a = store.table("agency")
keys = zip(torgucols.take(a.values("county"), t.values("agency_row")), t.values("race"))
torgucols.count_keys(keys, (a.null("county"), t.null("race")))
                                       # (((1, 1), 1), ((1, 2), 1), ((2, 0), 1)): officers by county, race
essex = a.mask((("eq", "county", 1),)) # bytearray, one byte per agency row
[i for i, r in enumerate(t.values("agency_row")) if essex[r]]   # [0, 1]: officers in Essex agencies
```

## Development

```sh
make venv     # .venv with the test and style tools (Python 3.12)
make test
make style    # isort, black, flake8
make build
```

## Releases

A release is a git tag `vX.Y.Z`, with the same version in `setup.cfg`, and a GitHub release
with the wheel attached. Consumers pin the wheel in their function's `requirements.txt`, which
`sam build` bundles (it can't take a git source without a `setup.py`):

```
torgucols @ https://github.com/tor-gu/torgucols/releases/download/v0.1.0/torgucols-0.1.0-py3-none-any.whl
```

and the tag in their build and test requirements:

```
torgucols @ git+https://github.com/tor-gu/torgucols@v0.1.0
```

A change to the format bumps `FORMAT`, and the reader rejects stores of any other version.
Rebuild a consumer's data whenever you move it to a new torgucols release.

## License

[CC0 1.0 Universal](LICENSE)
