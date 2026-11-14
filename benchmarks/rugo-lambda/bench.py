"""
Cold-start benchmark on Lambda: torgucols vs rugo (vs a no-data baseline).

    python bench.py setup [--memory 1024]   IAM role + 3 functions from dist/*.zip
    python bench.py run [--rounds 10] [--cases all|compare|sweep]
                                            forced cold starts, appends to results.jsonl
    python bench.py report                  medians -> report.md (and stdout)
    python bench.py teardown                deletes the functions, their logs and the role

Uses the default AWS credentials and region (AWS_REGION / AWS_PROFILE apply).
Everything it creates is named torgucols-bench-* and tagged
purpose=torgucols-rugo-benchmark; nothing else is touched.

Each measurement forces a cold start by changing an environment variable,
waits for the update, then times one invoke from here (boto3, connection
already open) and reads Lambda's REPORT line (Init Duration, Duration, Max
Memory Used). An invoke without Init Duration wasn't cold and is marked so.
Two warm invokes of the same query follow. The answers are checked against
expected.json.
"""

import argparse
import base64
import hashlib
import json
import os
import random
import re
import statistics
import sys
import time
import uuid

import boto3
from botocore.config import Config

HERE = os.path.dirname(os.path.abspath(__file__))
PREFIX = "torgucols-bench"
ROLE = f"{PREFIX}-lambda"
from pads import PAD_MB  # noqa: E402

PADS = [f"pad{mb}" for mb in PAD_MB]
FUNCTIONS = ["baseline", "torgucols", "rugo"] + PADS
QUERIES = ["q1", "q2", "q3"]
TAGS = {"purpose": "torgucols-rugo-benchmark"}
STATE = os.path.join(HERE, "state.json")
RESULTS = os.path.join(HERE, "results.jsonl")
REPORT = re.compile(
    r"REPORT RequestId: \S+\s+Duration: (?P<duration>[\d.]+) ms\s+Billed Duration: (?P<billed>\d+) ms"
    r"\s+Memory Size: (?P<memory>\d+) MB\s+Max Memory Used: (?P<max_memory>\d+) MB"
    r"(?:\s+Init Duration: (?P<init>[\d.]+) ms)?"
)

config = Config(retries={"max_attempts": 8, "mode": "standard"}, read_timeout=90)


def name(fn):
    return f"{PREFIX}-{fn}"


def setup(args):
    iam = boto3.client("iam", config=config)
    lam = boto3.client("lambda", config=config)
    trust = {
        "Version": "2012-10-17",
        "Statement": [
            {"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}
        ],
    }
    try:
        role_arn = iam.create_role(
            RoleName=ROLE,
            AssumeRolePolicyDocument=json.dumps(trust),
            Description="Throwaway: torgucols vs rugo cold-start benchmark",
            Tags=[{"Key": k, "Value": v} for k, v in TAGS.items()],
        )["Role"]["Arn"]
        print(f"created role {ROLE}")
    except iam.exceptions.EntityAlreadyExistsException:
        role_arn = iam.get_role(RoleName=ROLE)["Role"]["Arn"]
        print(f"role {ROLE} exists")
    iam.attach_role_policy(
        RoleName=ROLE, PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
    )
    for fn in FUNCTIONS:
        with open(os.path.join(HERE, "dist", f"{fn}.zip"), "rb") as f:
            code = f.read()
        for attempt in range(12):  # a new role takes a few seconds to be assumable
            try:
                lam.create_function(
                    FunctionName=name(fn),
                    Runtime="python3.12",
                    Architectures=["x86_64"],
                    Role=role_arn,
                    Handler="handler.handler",
                    Code={"ZipFile": code},
                    MemorySize=args.memory,
                    Timeout=30,
                    Environment={"Variables": {"BENCH_NONCE": "0"}},
                    Tags=TAGS,
                )
                print(f"created {name(fn)} ({len(code) / 2**20:.2f} MB zipped, {args.memory} MB memory)")
                break
            except lam.exceptions.ResourceConflictException:
                current = lam.get_function(FunctionName=name(fn))["Configuration"]
                sha = base64.b64encode(hashlib.sha256(code).digest()).decode()
                if current["CodeSha256"] != sha:
                    print(f"{name(fn)} exists; updating its code")
                    lam.update_function_code(FunctionName=name(fn), ZipFile=code)
                    lam.get_waiter("function_updated_v2").wait(FunctionName=name(fn))
                if current["MemorySize"] != args.memory:
                    print(f"{name(fn)}: memory {current['MemorySize']} -> {args.memory} MB")
                    lam.update_function_configuration(FunctionName=name(fn), MemorySize=args.memory)
                if current["CodeSha256"] == sha and current["MemorySize"] == args.memory:
                    print(f"{name(fn)} exists, unchanged")
                break
            except lam.exceptions.InvalidParameterValueException as e:
                if "role" not in str(e).lower() or attempt == 11:
                    raise
                time.sleep(5)
        lam.get_waiter("function_active_v2").wait(FunctionName=name(fn))
        lam.get_waiter("function_updated_v2").wait(FunctionName=name(fn))
    with open(STATE, "w") as f:
        json.dump({"role_arn": role_arn, "memory": args.memory, "region": lam.meta.region_name}, f)
    print("ready")


def invoke(lam, fn, q):
    t = time.perf_counter()
    r = lam.invoke(
        FunctionName=name(fn),
        InvocationType="RequestResponse",
        LogType="Tail",
        Payload=json.dumps({"q": q}).encode(),
    )
    client_ms = (time.perf_counter() - t) * 1000
    body = json.loads(r["Payload"].read())
    log = base64.b64decode(r.get("LogResult", "")).decode("utf-8", "replace")
    m = REPORT.search(log)
    report = {k: (float(v) if v is not None else None) for k, v in m.groupdict().items()} if m else {}
    return {
        "client_ms": client_ms,
        "error": r.get("FunctionError"),
        "body": body,
        "report": report,
    }


def force_cold(lam, fn):
    lam.update_function_configuration(
        FunctionName=name(fn), Environment={"Variables": {"BENCH_NONCE": uuid.uuid4().hex}}
    )
    lam.get_waiter("function_updated_v2").wait(
        FunctionName=name(fn), WaiterConfig={"Delay": 1, "MaxAttempts": 120}
    )


def run(args):
    with open(STATE) as f:
        state = json.load(f)
    with open(os.path.join(HERE, "expected.json")) as f:
        expected = json.load(f)
    lam = boto3.client("lambda", config=config)
    lam.get_function(FunctionName=name("baseline"))  # opens the connection before any timing
    compare = [(fn, q) for fn in ("torgucols", "rugo") for q in QUERIES]
    sweep = [(fn, "q0") for fn in PADS]
    cases = [("baseline", "q0")] + {"all": compare + sweep, "compare": compare, "sweep": sweep}[
        args.cases
    ]
    total = args.rounds * len(cases)
    done = 0
    with open(RESULTS, "a") as out:
        for rnd in range(args.rounds):
            random.shuffle(cases)
            for fn, q in cases:
                force_cold(lam, fn)
                # cold, then 2 warm (the size sweep only needs the cold one)
                calls = [invoke(lam, fn, q) for _ in range(1 if fn in PADS else 3)]
                for i, c in enumerate(calls):
                    rows = c["body"].get("rows") if isinstance(c["body"], dict) else None
                    record = {
                        "time": time.time(),
                        "round": rnd,
                        "function": fn,
                        "query": q,
                        "call": i,
                        "memory": state["memory"],
                        "cold": c["report"].get("init") is not None,
                        "client_ms": c["client_ms"],
                        "error": c["error"],
                        # q0 (baseline, pads) returns no data to check
                        "correct": None if q == "q0" else rows == expected[q],
                        "report": c["report"],
                        "timings": c["body"].get("timings") if isinstance(c["body"], dict) else None,
                    }
                    if c["error"]:
                        record["body"] = c["body"]
                    out.write(json.dumps(record) + "\n")
                out.flush()
                done += 1
                cold = calls[0]
                print(
                    f"[{done}/{total}] {fn:9} {q}: cold client {cold['client_ms']:6.0f} ms, "
                    f"init {cold['report'].get('init')}, duration {cold['report'].get('duration')}"
                    + (f"  ERROR {cold['body']}" if cold["error"] else "")
                )


def median(values):
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def fmt(v):
    return "" if v is None else f"{v:.0f}" if v >= 10 else f"{v:.1f}"


def residual(r):
    """Client time not in Init Duration or Duration: overhead plus fetching the package"""
    rep = r["report"]
    if rep.get("init") is None or rep.get("duration") is None:
        return None
    return r["client_ms"] - rep["init"] - rep["duration"]


def bootstrap_diff(a, b, n=4000, seed=1):
    """Median of b - median of a, with a 95% bootstrap interval"""
    rng = random.Random(seed)
    a, b = [v for v in a if v is not None], [v for v in b if v is not None]
    if not a or not b:
        return None, None, None
    diffs = sorted(
        statistics.median(rng.choices(b, k=len(b))) - statistics.median(rng.choices(a, k=len(a)))
        for _ in range(n)
    )
    return statistics.median(b) - statistics.median(a), diffs[int(0.025 * n)], diffs[int(0.975 * n)]


def fit(points):
    """Least-squares (slope, intercept) of y on x"""
    xs, ys = [x for x, _ in points], [y for _, y in points]
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in points) / sxx
    return slope, my - slope * mx


def bootstrap_fit(points, n=4000, seed=2):
    """slope, intercept and a 95% interval for the slope (resampling points)"""
    rng = random.Random(seed)
    slope, intercept = fit(points)
    slopes = []
    for _ in range(n):
        sample = rng.choices(points, k=len(points))
        if len({x for x, _ in sample}) > 1:
            slopes.append(fit(sample)[0])
    slopes.sort()
    return slope, intercept, slopes[int(0.025 * len(slopes))], slopes[int(0.975 * len(slopes))]


def ci(triple):
    mid, lo, hi = triple
    return "" if mid is None else f"{fmt(mid)} [{fmt(lo)}, {fmt(hi)}]"


def report(args):
    with open(RESULTS) as f:
        rows = [json.loads(line) for line in f]
    lam = boto3.client("lambda", config=config)
    sizes = {}
    for fn in FUNCTIONS:
        try:
            sizes[fn] = lam.get_function(FunctionName=name(fn))["Configuration"]["CodeSize"] / 2**20
        except Exception:
            sizes[fn] = None
    problems = [r for r in rows if r["error"] or r["correct"] is False]
    not_cold = [r for r in rows if r["call"] == 0 and not r["cold"]]
    warm_but_reinit = [r for r in rows if r["call"] > 0 and r["cold"]]
    proactive = [r for r in not_cold if (r["timings"] or {}).get("first_call")]
    cold = [r for r in rows if r["call"] == 0 and r["cold"] and not r["error"]]
    warm = [r for r in rows if r["call"] > 0 and not r["cold"] and not r["error"]]

    def sel(rs, fn, q):
        return [r for r in rs if r["function"] == fn and r["query"] == q]

    base = median(r["client_ms"] for r in sel(cold, "baseline", "q0"))
    lines = [
        "# torgucols vs rugo on Lambda",
        "",
        f"Memory {sorted({r['memory'] for r in rows})} MB; {len(cold)} cold starts; medians. "
        "Zipped package: "
        + ", ".join(f"{fn} {sizes[fn]:.2f} MB" for fn in FUNCTIONS if sizes[fn] is not None)
        + ".",
        "",
        "## Cold (first request on a new instance)",
        "",
        "Rest = client time − Init Duration − Duration: overhead plus fetching and unpacking the package.",
        "",
        "| Function | Query | n | Client ms | Over baseline | Init Duration | Duration | Rest | "
        "Handler: import | load | count | Max memory MB |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for fn in ("baseline", "torgucols", "rugo"):
        for q in ["q0"] + QUERIES:
            rs = sel(cold, fn, q)
            if not rs:
                continue
            client = median(r["client_ms"] for r in rs)
            t = lambda k: median((r["timings"] or {}).get(k) for r in rs)  # noqa: E731
            lines.append(
                f"| {fn} | {q} | {len(rs)} | {fmt(client)} | "
                f"{fmt(client - base) if base is not None else ''} | "
                f"{fmt(median(r['report'].get('init') for r in rs))} | "
                f"{fmt(median(r['report'].get('duration') for r in rs))} | "
                f"{fmt(median(residual(r) for r in rs))} | "
                f"{fmt(t('import_ms'))} | {fmt(t('load_ms'))} | {fmt(t('count_ms'))} | "
                f"{fmt(median(r['report'].get('max_memory') for r in rs))} |"
            )

    lines += [
        "",
        "## rugo minus torgucols, cold (median difference, 95% bootstrap interval)",
        "",
        "| Query | Client ms | Init Duration | Duration | Rest |",
        "|---|---|---|---|---|",
    ]
    for q in QUERIES:
        a, b = sel(cold, "torgucols", q), sel(cold, "rugo", q)
        if not a or not b:
            continue
        metric = {
            "client": lambda r: r["client_ms"],
            "init": lambda r: r["report"].get("init"),
            "duration": lambda r: r["report"].get("duration"),
            "rest": residual,
        }
        cells = [ci(bootstrap_diff([m(r) for r in a], [m(r) for r in b])) for m in metric.values()]
        lines.append(f"| {q} | " + " | ".join(cells) + " |")

    sweep_fns = ["baseline"] + PADS
    points = [
        (sizes[r["function"]], residual(r))
        for r in cold
        if r["function"] in sweep_fns and sizes.get(r["function"]) is not None and residual(r) is not None
    ]
    if len({x for x, _ in points}) > 1:
        slope, intercept, lo, hi = bootstrap_fit(points)
        client_points = [
            (sizes[r["function"]], r["client_ms"])
            for r in cold
            if r["function"] in sweep_fns and sizes.get(r["function"]) is not None
        ]
        cslope, cint, clo, chi = bootstrap_fit(client_points)
        lines += [
            "",
            "## Package size (baseline handler + incompressible filler)",
            "",
            "| Function | Zipped MB | n | Client ms | Init Duration | Duration | Rest |",
            "|---|---|---|---|---|---|---|",
        ]
        for fn in sweep_fns:
            rs = sel(cold, fn, "q0")
            if not rs or sizes.get(fn) is None:
                continue
            lines.append(
                f"| {fn} | {sizes[fn]:.2f} | {len(rs)} | {fmt(median(r['client_ms'] for r in rs))} | "
                f"{fmt(median(r['report'].get('init') for r in rs))} | "
                f"{fmt(median(r['report'].get('duration') for r in rs))} | "
                f"{fmt(median(residual(r) for r in rs))} |"
            )
        lines += [
            "",
            f"- **Rest per zipped MB: {slope:.1f} ms** (95% interval {lo:.1f} to {hi:.1f}), "
            f"intercept {intercept:.0f} ms. Fitted on {len(points)} cold starts.",
            f"- Client time per zipped MB: {cslope:.1f} ms (95% interval {clo:.1f} to {chi:.1f}).",
            "",
            "Real data against the line (its Rest, against the fit's prediction for its zipped size):",
            "",
            "| Function | Zipped MB | Rest, median of all queries | Predicted |",
            "|---|---|---|---|",
        ]
        for fn in ("torgucols", "rugo"):
            rs = [r for r in cold if r["function"] == fn]
            if rs and sizes.get(fn) is not None:
                lines.append(
                    f"| {fn} | {sizes[fn]:.2f} | {fmt(median(residual(r) for r in rs))} | "
                    f"{fmt(intercept + slope * sizes[fn])} |"
                )

    lines += [
        "",
        "## Warm (the next two requests, same query)",
        "",
        "| Function | Query | n | Client ms | Duration | Handler: load | count |",
        "|---|---|---|---|---|---|---|",
    ]
    for fn in ("torgucols", "rugo"):
        for q in QUERIES:
            rs = sel(warm, fn, q)
            if not rs:
                continue
            t = lambda k: median((r["timings"] or {}).get(k) for r in rs)  # noqa: E731
            lines.append(
                f"| {fn} | {q} | {len(rs)} | {fmt(median(r['client_ms'] for r in rs))} | "
                f"{fmt(median(r['report'].get('duration') for r in rs))} | "
                f"{fmt(t('load_ms'))} | {fmt(t('count_ms'))} |"
            )
    lines += [
        "",
        "## Checks",
        "",
        f"- Errors or wrong answers: {len(problems)}",
        f"- First calls that weren't cold (excluded): {len(not_cold)}, of which proactively "
        f"initialised (first_call but no Init Duration): {len(proactive)}",
        f"- Warm calls that re-initialised (excluded): {len(warm_but_reinit)}",
        "",
        "q1 subjects by agency_county, race; q2 incidents by year, officer_race in 2023 "
        "(rugo prunes on date); q3 incidents by year, subject_action. Client ms is timed "
        "around boto3's invoke from the machine that ran bench.py.",
    ]
    text = "\n".join(lines) + "\n"
    with open(os.path.join(HERE, "report.md"), "w") as f:
        f.write(text)
    print(text)
    for r in problems[:5]:
        print("PROBLEM:", json.dumps(r)[:500])


def teardown(args):
    iam = boto3.client("iam", config=config)
    lam = boto3.client("lambda", config=config)
    logs = boto3.client("logs", config=config)
    for fn in FUNCTIONS:
        for action, call in (
            ("function", lambda: lam.delete_function(FunctionName=name(fn))),
            ("log group", lambda: logs.delete_log_group(logGroupName=f"/aws/lambda/{name(fn)}")),
        ):
            try:
                call()
                print(f"deleted {action} {name(fn)}")
            except Exception as e:
                print(f"{action} {name(fn)}: {type(e).__name__} (already gone?)")
    try:
        iam.detach_role_policy(
            RoleName=ROLE, PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
        )
        iam.delete_role(RoleName=ROLE)
        print(f"deleted role {ROLE}")
    except Exception as e:
        print(f"role {ROLE}: {type(e).__name__} (already gone?)")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("setup")
    s.add_argument("--memory", type=int, default=1024)
    r = sub.add_parser("run")
    r.add_argument("--rounds", type=int, default=10)
    r.add_argument("--cases", choices=["all", "compare", "sweep"], default="all")
    sub.add_parser("report")
    sub.add_parser("teardown")
    args = p.parse_args()
    {"setup": setup, "run": run, "report": report, "teardown": teardown}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
