"""Razorpay TEST MODE checkout.

Creates a real Order against Razorpay's test environment so the demo can show
a genuine order id and amount. Test mode is free and moves no money
(CLAUDE.md section 8).

Safety rails:
  * refuses to run with a live key -- test keys start with "rzp_test_"
  * creates orders only; it never captures a payment or handles card details
  * with no keys configured it returns a clearly-labelled SIMULATED order so
    the rest of the demo still runs

The point this makes in the pitch: the fraudulent order and the honest order
are indistinguishable at the payments layer. Same customer, same merchant,
same card, correctly authenticated. Only the amount differs, and nothing in
the payment tells you which amount the human actually agreed to.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


@dataclass
class Order:
    order_id: str
    amount_inr: float
    currency: str
    status: str
    simulated: bool
    receipt: str
    error: str = ""

    @property
    def amount_paise(self) -> int:
        return int(round(self.amount_inr * 100))


def keys_available() -> bool:
    return bool(
        os.getenv("RAZORPAY_KEY_ID", "").strip()
        and os.getenv("RAZORPAY_KEY_SECRET", "").strip()
    )


def _assert_test_mode(key_id: str) -> None:
    if not key_id.startswith("rzp_test_"):
        raise RuntimeError(
            "Refusing to run: RAZORPAY_KEY_ID is not a test key. "
            "This project must never touch live payment credentials."
        )


def create_order(amount_inr: float, *, note: str = "") -> Order:
    """Create a Razorpay test-mode order for `amount_inr`."""
    receipt = f"sentr-{uuid.uuid4().hex[:12]}"

    if not keys_available():
        return Order(
            order_id=f"order_SIMULATED_{uuid.uuid4().hex[:10]}",
            amount_inr=amount_inr,
            currency="INR",
            status="simulated",
            simulated=True,
            receipt=receipt,
            error="no Razorpay test keys in .env -- order not sent to Razorpay",
        )

    key_id = os.getenv("RAZORPAY_KEY_ID", "").strip()
    key_secret = os.getenv("RAZORPAY_KEY_SECRET", "").strip()
    _assert_test_mode(key_id)

    try:
        import razorpay

        client = razorpay.Client(auth=(key_id, key_secret))
        payload: dict[str, Any] = {
            "amount": int(round(amount_inr * 100)),
            "currency": "INR",
            "receipt": receipt,
            "notes": {"source": "sentr-demo", "note": note[:200]},
        }
        res = client.order.create(data=payload)
        return Order(
            order_id=res["id"],
            amount_inr=res["amount"] / 100,
            currency=res["currency"],
            status=res["status"],
            simulated=False,
            receipt=receipt,
        )
    except Exception as e:
        return Order(
            order_id=f"order_FAILED_{uuid.uuid4().hex[:8]}",
            amount_inr=amount_inr,
            currency="INR",
            status="error",
            simulated=True,
            receipt=receipt,
            error=str(e)[:300],
        )
