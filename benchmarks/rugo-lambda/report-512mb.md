# torgucols vs rugo on Lambda

Memory [512] MB; 360 cold starts; medians. Zipped package: baseline 0.00 MB, torgucols 3.99 MB, rugo 11.98 MB, pad5 5.00 MB, pad10 10.00 MB, pad20 20.01 MB, pad30 30.01 MB, pad40 40.01 MB.

## Cold (first request on a new instance)

Rest = client time − Init Duration − Duration: overhead plus fetching and unpacking the package.

| Function | Query | n | Client ms | Over baseline | Init Duration | Duration | Rest | Handler: import | load | count | Max memory MB |
|---|---|---|---|---|---|---|---|---|---|---|---|
| baseline | q0 | 30 | 275 | 0.0 | 82 | 1.9 | 191 | 0.0 |  |  | 32 |
| torgucols | q1 | 30 | 473 | 198 | 93 | 175 | 199 | 6.9 | 58 | 116 | 41 |
| torgucols | q2 | 30 | 428 | 153 | 93 | 142 | 188 | 6.9 | 65 | 64 | 45 |
| torgucols | q3 | 30 | 634 | 360 | 92 | 336 | 211 | 6.9 | 123 | 200 | 52 |
| rugo | q1 | 30 | 773 | 498 | 170 | 341 | 248 | 85 | 206 | 118 | 60 |
| rugo | q2 | 30 | 577 | 302 | 177 | 157 | 237 | 89 | 135 | 4.0 | 54 |
| rugo | q3 | 30 | 919 | 644 | 171 | 506 | 238 | 87 | 277 | 201 | 68 |

## rugo minus torgucols, cold (median difference, 95% bootstrap interval)

| Query | Client ms | Init Duration | Duration | Rest |
|---|---|---|---|---|
| q1 | 300 [248, 336] | 77 [70, 82] | 165 [156, 172] | 49 [-0.2, 89] |
| q2 | 149 [98, 201] | 84 [79, 90] | 15 [10, 22] | 49 [-5.5, 98] |
| q3 | 284 [243, 334] | 80 [71, 85] | 169 [163, 180] | 27 [-17.3, 88] |

## Package size (baseline handler + incompressible filler)

| Function | Zipped MB | n | Client ms | Init Duration | Duration | Rest |
|---|---|---|---|---|---|---|
| baseline | 0.00 | 30 | 275 | 82 | 1.9 | 191 |
| pad5 | 5.00 | 30 | 352 | 84 | 1.9 | 261 |
| pad10 | 10.00 | 30 | 363 | 84 | 1.9 | 279 |
| pad20 | 20.01 | 30 | 466 | 86 | 1.8 | 374 |
| pad30 | 30.01 | 30 | 530 | 84 | 1.9 | 454 |
| pad40 | 40.01 | 30 | 613 | 81 | 1.8 | 531 |

- **Rest per zipped MB: 8.2 ms** (95% interval 7.3 to 9.0), intercept 222 ms. Fitted on 180 cold starts.
- Client time per zipped MB: 8.1 ms (95% interval 7.2 to 9.0).

Real data against the line (its Rest, against the fit's prediction for its zipped size):

| Function | Zipped MB | Rest, median of all queries | Predicted |
|---|---|---|---|
| torgucols | 3.99 | 198 | 254 |
| rugo | 11.98 | 245 | 319 |

## Warm (the next two requests, same query)

| Function | Query | n | Client ms | Duration | Handler: load | count |
|---|---|---|---|---|---|---|
| torgucols | q1 | 60 | 171 | 96 | 0.0 | 89 |
| torgucols | q2 | 60 | 58 | 8.9 | 0.0 | 5.2 |
| torgucols | q3 | 60 | 241 | 179 | 0.0 | 170 |
| rugo | q1 | 60 | 213 | 148 | 27 | 103 |
| rugo | q2 | 60 | 60 | 18 | 2.5 | 3.9 |
| rugo | q3 | 60 | 362 | 298 | 84 | 183 |

## Checks

- Errors or wrong answers: 0
- First calls that weren't cold (excluded): 0, of which proactively initialised (first_call but no Init Duration): 0
- Warm calls that re-initialised (excluded): 0

q1 subjects by agency_county, race; q2 incidents by year, officer_race in 2023 (rugo prunes on date); q3 incidents by year, subject_action. Client ms is timed around boto3's invoke from the machine that ran bench.py.
