#!/usr/bin/env python3
"""
End-to-end smoke test against a running QuickSurf API in SANDBOX mode (VTpass sandbox keys,
Paystack test keys). Never point it at production: it buys airtime.

    python scripts/smoke_test.py https://staging.example.com \\
        --email you@example.com --password '...' --i-confirm-this-is-sandbox

The account must exist with a verified email (codes go to its inbox). Steps: health -> login ->
transaction PIN -> data plans -> top-up if the wallet is low (pay on Paystack's test checkout with a
test card) -> airtime to VTpass's sandbox success number -> the purchase's hold and capture in the
wallet ledger. Exits non-zero at the first failed step.
"""

import argparse
import sys
import time
import uuid

import requests

SANDBOX_SUCCESS_NUMBER = "08011111111"  # VTpass sandbox: always delivered
AIRTIME_AMOUNT = 100


class SmokeTestFailed(Exception):
    pass


def check(condition, message):
    if not condition:
        raise SmokeTestFailed(message)
    print(f"  ok  {message}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("base_url")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--pin", default="2580", help="Transaction PIN to use (set if the account has none)")
    parser.add_argument("--topup", type=int, default=1000, help="Top-up amount in naira if the wallet is low")
    parser.add_argument("--i-confirm-this-is-sandbox", action="store_true", required=True)
    args = parser.parse_args()

    api = args.base_url.rstrip("/") + "/api"
    session = requests.Session()
    session.timeout = 60

    try:
        health = session.get(f"{api}/health/", timeout=60)
        check(health.status_code == 200, f"health: {health.json().get('checks')}")

        login = session.post(f"{api}/users/login/", json={"email": args.email, "password": args.password}, timeout=30)
        check(login.status_code == 200, "login")
        session.headers["Authorization"] = f"Bearer {login.json()['access']}"

        if not session.get(f"{api}/users/pin/", timeout=30).json().get("has_pin"):
            set_pin = session.post(f"{api}/users/pin/", json={"pin": args.pin, "password": args.password}, timeout=30)
            check(set_pin.status_code == 200, "transaction PIN set")
        else:
            print("  ..  transaction PIN already set (using --pin)")

        plans = session.get(f"{api}/services/plans/mtn/", timeout=60)
        check(
            plans.status_code == 200 and plans.json()["plans"], f"{len(plans.json().get('plans', []))} MTN data plans"
        )

        balance = float(session.get(f"{api}/wallet/", timeout=30).json()["balance"])
        if balance < AIRTIME_AMOUNT:
            init = session.post(f"{api}/payments/init/", json={"amount": str(args.topup)}, timeout=60)
            check(init.status_code == 201, "top-up started")
            reference = init.json()["reference"]
            print(f"\n  Pay with a Paystack test card here, then come back:\n  {init.json()['authorization_url']}\n")
            for _ in range(120):
                state = session.get(f"{api}/payments/verify/{reference}/", timeout=60).json().get("status")
                if state in ("success", "failed", "abandoned", "review"):
                    break
                time.sleep(5)
            check(state == "success", f"top-up {reference} credited")

        reference = f"SMOKE-{uuid.uuid4().hex[:12]}"
        purchase = session.post(
            f"{api}/services/airtime/",
            json={
                "amount": str(AIRTIME_AMOUNT),
                "network": "mtn",
                "phone": SANDBOX_SUCCESS_NUMBER,
                "pin": args.pin,
                "client_reference": reference,
            },
            timeout=90,
        )
        check(purchase.status_code == 201, f"airtime purchase {reference} accepted")
        check(purchase.json()["status"] == "successful", "VTpass sandbox delivered it")

        ledger = session.get(f"{api}/wallet/transactions/", params={"page_size": 50}, timeout=30).json()["results"]
        types = sorted(row["type"] for row in ledger if row["reference"] == f"airtime:{reference}")
        check(types == ["capture", "hold"], "wallet ledger shows the hold and capture")
    except (SmokeTestFailed, requests.RequestException, KeyError, ValueError) as e:
        print(f"FAIL {e}")
        return 1

    print("\nSmoke test passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
