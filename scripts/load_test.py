#!/usr/bin/env python3
"""
Load test of the purchase path, with correctness checks. Run it against a NON-production backend
whose VTpass is the sandbox or a stand-in: every purchase here is real to that backend.

  1. python manage.py loadtest_users --count 40 --output users.json     (on the test backend)
  2. python scripts/load_test.py https://staging.example.com users.json

Phases:
  steady       every user buys airtime repeatedly, many users at once
  same-ref     each user sends one order several times at once: it must be charged exactly once
  overspend    each user sends more orders at once than their balance covers: it must never go negative
Afterwards each wallet must equal the sum of its history, with nothing left on hold.
The test backend needs PURCHASE_RATE raised (e.g. 1000/hour), or the per-user limit answers 429.
"""

import argparse
import json
import random
import statistics
import sys
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import requests

AMOUNT = 100


class Client:
    def __init__(self, base, user):
        self.base = base.rstrip("/") + "/api/v1"
        self.user = user
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {user['access']}"

    def buy(self, reference, phone):
        started = time.perf_counter()
        try:
            r = self.session.post(
                f"{self.base}/services/airtime/",
                json={
                    "network": "mtn",
                    "phone": phone,
                    "amount": str(AMOUNT),
                    "pin": self.user["pin"],
                    "client_reference": reference,
                },
                timeout=60,
            )
            body = r.json() if r.headers.get("Content-Type", "").startswith("application/json") else {}
            code = (
                r.status_code
                if r.status_code < 400
                else f"{r.status_code} {body.get('error', {}).get('code', '')}".strip()
            )
            return code, time.perf_counter() - started, body
        except requests.RequestException as e:
            print(f"  connection problem on {reference}: {str(e)[:160]}", file=sys.stderr)
            return f"error {type(e).__name__}", time.perf_counter() - started, {}

    def get(self, path):
        r = self.session.get(f"{self.base}{path}", timeout=30)
        r.raise_for_status()
        return r.json()

    def history_total(self):
        total, page = Decimal("0"), 1
        while True:
            data = self.get(f"/wallet/history/?page={page}&page_size=100")
            total += sum(Decimal(line["amount"]) for line in data["results"])
            if not data["next"]:
                return total
            page += 1


def phone():
    return f"0803{random.randint(100000, 999999)}{random.choice('12345678')}"  # never the fakes' 0000/9999 endings


def report(name, results, elapsed):
    codes = Counter(code for code, _, _ in results)
    times = sorted(t for _, t, _ in results)
    p = lambda q: times[min(len(times) - 1, int(q * len(times)))]  # noqa: E731
    print(f"\n[{name}] {len(results)} requests in {elapsed:.1f}s ({len(results) / elapsed:.1f}/s)")
    print(f"  statuses: {dict(codes)}")
    print(f"  latency: median {statistics.median(times):.2f}s, p95 {p(0.95):.2f}s, max {times[-1]:.2f}s")
    return {
        "requests": len(results),
        "seconds": round(elapsed, 1),
        "statuses": {str(k): v for k, v in codes.items()},
        "median": round(statistics.median(times), 2),
        "p95": round(p(0.95), 2),
        "max": round(times[-1], 2),
    }


def run(fn, jobs, workers):
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda job: fn(*job), jobs))
    return results, time.perf_counter() - started


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("base_url")
    parser.add_argument("users_file")
    parser.add_argument("--per-user", type=int, default=10, help="Purchases per user in the steady phase")
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--same-ref-copies", type=int, default=5)
    args = parser.parse_args()

    setup = json.load(open(args.users_file))
    clients = [Client(args.base_url, u) for u in setup["users"]]
    summary, failures = {}, []

    steady_jobs = [(c, f"lt-{uuid.uuid4().hex}", phone()) for c in clients for _ in range(args.per_user)]
    random.shuffle(steady_jobs)
    results, elapsed = run(lambda c, ref, ph: c.buy(ref, ph), steady_jobs, args.concurrency)
    summary["steady"] = report("steady", results, elapsed)

    # Same order, several copies at once: one charge per reference, whatever the responses say.
    same_jobs, refs = [], {}
    for c in clients:
        ref, ph = f"lt-same-{uuid.uuid4().hex}", phone()
        refs[c.user["email"]] = ref
        same_jobs += [(c, ref, ph)] * args.same_ref_copies
    results, elapsed = run(lambda c, ref, ph: c.buy(ref, ph), same_jobs, args.concurrency)
    summary["same-ref"] = report("same-ref", results, elapsed)
    for c in clients:
        lines = [
            line
            for line in c.get("/wallet/history/?kind=purchase&page_size=100")["results"]
            if line["reference"] == refs[c.user["email"]]
        ]
        if len(lines) != 1:
            failures.append(f"{c.user['email']}: reference {refs[c.user['email']]} charged {len(lines)} times")

    # More orders at once than the balance covers.
    balances = {c.user["email"]: Decimal(c.get("/wallet/")["balance"]) for c in clients}
    over_jobs = [
        (c, f"lt-over-{uuid.uuid4().hex}", phone())
        for c in clients
        for _ in range(int(balances[c.user["email"]] // AMOUNT) + 5)
    ]
    random.shuffle(over_jobs)
    results, elapsed = run(lambda c, ref, ph: c.buy(ref, ph), over_jobs, args.concurrency)
    summary["overspend"] = report("overspend", results, elapsed)

    # Every wallet: never negative, nothing stuck on hold, balance equals the sum of its history.
    for c in clients:
        wallet = c.get("/wallet/")
        balance, locked = Decimal(wallet["balance"]), Decimal(wallet["locked"])
        if balance < 0 or locked != 0:
            failures.append(f"{c.user['email']}: balance {balance}, locked {locked}")
        total = c.history_total()
        if total != balance:
            failures.append(f"{c.user['email']}: history adds up to {total}, balance is {balance}")
    summary["failures"] = failures

    print("\nCorrectness:", "all wallets consistent" if not failures else "\n  " + "\n  ".join(failures))
    print(json.dumps(summary))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
