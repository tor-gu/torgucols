# torgucols vs rugo on Lambda

Memory [1024] MB; 433 cold starts; medians. Zipped package: baseline 0.00 MB, torgucols 3.99 MB, rugo 11.98 MB, pad5 5.00 MB, pad10 10.00 MB, pad20 20.01 MB, pad30 30.01 MB, pad40 40.01 MB.

## Cold (first request on a new instance)

Rest = client time − Init Duration − Duration: overhead plus fetching and unpacking the package.

| Function | Query | n | Client ms | Over baseline | Init Duration | Duration | Rest | Handler: import | load | count | Max memory MB |
|---|---|---|---|---|---|---|---|---|---|---|---|
| baseline | q0 | 41 | 283 | 0.0 | 83 | 1.9 | 200 | 0.0 |  |  | 32 |
| torgucols | q1 | 40 | 395 | 111 | 93 | 83 | 221 | 6.9 | 26 | 55 | 41 |
| torgucols | q2 | 40 | 369 | 85 | 95 | 70 | 202 | 7.0 | 29 | 37 | 45 |
| torgucols | q3 | 41 | 464 | 181 | 92 | 164 | 208 | 6.9 | 61 | 99 | 52 |
| rugo | q1 | 40 | 600 | 317 | 174 | 165 | 247 | 87 | 103 | 56 | 59 |
| rugo | q2 | 40 | 495 | 211 | 174 | 75 | 235 | 87 | 67 | 6.1 | 54 |
| rugo | q3 | 41 | 698 | 414 | 172 | 246 | 267 | 86 | 136 | 99 | 68 |

## rugo minus torgucols, cold (median difference, 95% bootstrap interval)

| Query | Client ms | Init Duration | Duration | Rest |
|---|---|---|---|---|
| q1 | 206 [180, 245] | 81 [77, 88] | 82 [77, 84] | 27 [7.5, 66] |
| q2 | 126 [95, 149] | 79 [73, 85] | 5.8 [3.9, 11] | 33 [12, 62] |
| q3 | 233 [179, 264] | 80 [76, 85] | 82 [77, 88] | 59 [14, 81] |

## Package size (baseline handler + incompressible filler)

| Function | Zipped MB | n | Client ms | Init Duration | Duration | Rest |
|---|---|---|---|---|---|---|
| baseline | 0.00 | 41 | 283 | 83 | 1.9 | 200 |
| pad5 | 5.00 | 30 | 342 | 81 | 1.9 | 242 |
| pad10 | 10.00 | 30 | 379 | 83 | 1.9 | 289 |
| pad20 | 20.01 | 30 | 514 | 83 | 1.9 | 430 |
| pad30 | 30.01 | 30 | 541 | 82 | 1.8 | 450 |
| pad40 | 40.01 | 30 | 628 | 82 | 1.9 | 548 |

- **Rest per zipped MB: 9.0 ms** (95% interval 8.3 to 9.7), intercept 219 ms. Fitted on 191 cold starts.
- Client time per zipped MB: 8.9 ms (95% interval 8.2 to 9.7).

Real data against the line (its Rest, against the fit's prediction for its zipped size):

| Function | Zipped MB | Rest, median of all queries | Predicted |
|---|---|---|---|
| torgucols | 3.99 | 208 | 255 |
| rugo | 11.98 | 248 | 327 |

## Warm (the next two requests, same query)

| Function | Query | n | Client ms | Duration | Handler: load | count |
|---|---|---|---|---|---|---|
| torgucols | q1 | 80 | 93 | 44 | 0.0 | 40 |
| torgucols | q2 | 80 | 53 | 7.0 | 0.0 | 5.2 |
| torgucols | q3 | 82 | 144 | 89 | 0.0 | 85 |
| rugo | q1 | 80 | 119 | 71 | 13 | 49 |
| rugo | q2 | 80 | 54 | 8.3 | 2.5 | 3.9 |
| rugo | q3 | 82 | 213 | 146 | 41 | 96 |

## Checks

- Errors or wrong answers: 0
- First calls that weren't cold (excluded): 0, of which proactively initialised (first_call but no Init Duration): 0
- Warm calls that re-initialised (excluded): 0

q1 subjects by agency_county, race; q2 incidents by year, officer_race in 2023 (rugo prunes on date); q3 incidents by year, subject_action. Client ms is timed around boto3's invoke from the machine that ran bench.py.
