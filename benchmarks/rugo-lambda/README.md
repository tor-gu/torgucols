# Benchmark: torgucols vs rugo on AWS Lambda

Measures what a cold-start counts query on njoaguof's data costs with torgucols and with
[rugo](https://pypi.org/project/rugo/) (a Parquet reader built for serverless cold starts), on
top of an empty function. A package-size sweep measures what package bytes cost on a cold start.

Run 2026-11-08 in us-east-1, at 1024 MB and 512 MB. Results: `report-1024mb.md` and
`report-512mb.md`. Raw data: `results-*.jsonl`, one record per invoke.

## Setup

All functions are python3.12, x86_64.

| Function | Package | Zipped |
|---|---|---|
| `baseline` | trivial handler | 0 MB |
| `torgucols` | torgucols + all of njoaguof-api's counts columns as a torgucols store | 3.99 MB |
| `rugo` | rugo 0.5.9 + draken + the same columns as Parquet | 11.98 MB |
| `pad5` … `pad40` | the baseline handler + 5–40 MB of random bytes (they don't compress) | 5–40 MB |

**The data packages:**
- Both carry the same data, all counts columns from njoaguof v2.3.0, with the same rows in the
  same order.
- Incidents are sorted by date so rugo can prune row groups (10k rows each) on q2's date filter.
- The Parquet files are uncompressed: once zipped that's smaller than zstd (6.6 vs 7.5 MB), and
  there's nothing to decompress on read.

These choices give rugo its best case.

| Query | What it does |
|---|---|
| q1 | subjects by agency_county, race: a join over all 113,918 subjects |
| q2 | incidents by year, officer_race in 2023: a date filter (rugo pushes it down) |
| q3 | incidents by year, subject_action: a join over 225k attribute pairs |

**Per round:** every (function, query) pair in random order gets a forced cold start (an
environment variable change) and one timed invoke. The data functions then get two warm
invokes. Recorded:
- the client time around boto3's `invoke`
- Lambda's REPORT line (Init Duration, Duration, Max Memory Used)
- the handler's own split: import, column loading, counting

A first call without Init Duration would be excluded; none occurred. Every answer is checked
against answers `build.py` computes from the source columns without either library.

**Warm semantics:** torgucols keeps loaded columns in memory, as the real APIs do. rugo
re-reads on each request, which is its design. Neither caches results.

"Rest" in the reports is client time − Init Duration − Duration: the invoke overhead plus
fetching and unpacking the package, which no Lambda field reports.

## Results (medians; 95% bootstrap intervals in brackets)

Cold-start client time:

| Query | baseline | torgucols | rugo | torgucols saves |
|---|---|---|---|---|
| 1024 MB, q1 | 283 | 395 | 600 | 206 [180, 245] |
| 1024 MB, q2 | | 369 | 495 | 126 [95, 149] |
| 1024 MB, q3 | | 464 | 698 | 233 [179, 264] |
| 512 MB, q1 | 275 | 473 | 773 | 300 [248, 336] |
| 512 MB, q2 | | 428 | 577 | 149 [98, 201] |
| 512 MB, q3 | | 634 | 919 | 284 [243, 334] |

**Where rugo's extra time goes:**
- **Init Duration:** +77 to +84 ms at both memory sizes. Its import takes 85–89 ms on Lambda,
  against 7 ms for torgucols, and 10–14 ms locally.
- **Duration:** +82 ms at 1024 MB (+165 to +169 at 512) on q1 and q3. On q2 it's only
  +6 / +15 ms: pruning makes rugo's count tiny, but its read is still slower than torgucols's.
- **Package (Rest):** +27 to +59 ms.

**Package bytes:**
- Incompressible filler costs **8.2 ms per zipped MB at 512 MB [7.3, 9.0]** and **9.0 ms at
  1024 MB [8.3, 9.7]**. Memory doesn't change it.
- The real packages cost less than that. Over the baseline, torgucols's 4 MB adds about 7–8 ms
  and rugo's 12 MB about 47–54 ms, roughly 2–4 ms per zipped MB. These data don't show why.

**Memory:** at 512 MB, Init Duration is unchanged, and the handler's load and count roughly
double.

**Warm:** torgucols is faster everywhere except q2 at 1024 MB, where they're about even (7.0 vs
8.3 ms Duration).

## Running it

You need AWS credentials allowed to:
- iam: CreateRole, AttachRolePolicy, DetachRolePolicy, DeleteRole, GetRole, PassRole
- lambda: CreateFunction, UpdateFunctionConfiguration, UpdateFunctionCode, InvokeFunction,
  GetFunction, DeleteFunction
- logs: DeleteLogGroup

Everything the benchmark creates is named `torgucols-bench-*` and tagged
`purpose=torgucols-rugo-benchmark`. The code is uploaded directly, so there's no S3 bucket and
no CloudFormation stack. One run of 30 rounds is about 360 cold starts, takes about 15 minutes
and costs well under a cent.

```sh
cd benchmarks/rugo-lambda
python3.12 -m venv .venv && .venv/bin/pip install boto3

# The rugo wheel for Lambda (cp312, manylinux_2_34, x86_64)
.venv/bin/pip download rugo==0.5.9 --no-deps --only-binary=:all: \
    --platform manylinux_2_34_x86_64 --python-version 3.12 -d wheels

# The packages. build.py needs a Python 3.12 with pyarrow (for writing Parquet) and
# njoaguof-api's counts data (make data in njoaguof-api, in its pre-torgucols format).
<python3.12 with pyarrow> build.py ../../../../njoaguof-api/njoaguof-api/counts/data \
    wheels/rugo-0.5.9-cp312-cp312-manylinux_2_34_x86_64.whl
.venv/bin/python pads.py

export AWS_REGION=us-east-1
.venv/bin/python bench.py setup --memory 1024   # IAM role + 8 functions (about 120 MB uploaded)
.venv/bin/python bench.py run --rounds 30       # appends to results.jsonl
.venv/bin/python bench.py report                # writes report.md
.venv/bin/python bench.py teardown              # deletes the functions, log groups and role
```

- **Another memory size:** run `setup --memory <MB>`. That's only a configuration change for
  existing functions, with no re-upload. Move `results.jsonl` aside first, since the report
  pools everything in it.
- **Before running in the Lambda image:** `build.py` and the handlers were checked in
  `public.ecr.aws/lambda/python:3.12` with Docker, where rugo's native libraries load and every
  answer matched.
