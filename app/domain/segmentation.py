"""Pure customer-segmentation rules (no I/O) — easy to unit test."""
from datetime import datetime

VIP_SPEND = 8000.0
VIP_ORDERS = 5
NEW_DAYS = 30
AT_RISK_DAYS = 60
CHURN_DAYS = 120

SEGMENTS = ["vip", "new", "active", "at_risk", "churned", "prospect"]


def compute_segment(order_count: int, total_spent: float, last_order_at: datetime | None, now: datetime) -> str:
    if order_count == 0 or last_order_at is None:
        return "prospect"
    days = (now - last_order_at).days
    if days > CHURN_DAYS:
        return "churned"
    if days > AT_RISK_DAYS:
        return "at_risk"
    if total_spent >= VIP_SPEND or order_count >= VIP_ORDERS:
        return "vip"
    if order_count == 1 and days <= NEW_DAYS:
        return "new"
    return "active"


def rfm_score(order_count: int, total_spent: float, last_order_at: datetime | None, now: datetime) -> int:
    """Recency/Frequency/Monetary each 1-5 -> 3..15 (0 for no purchases)."""
    if not order_count or last_order_at is None:
        return 0
    days = (now - last_order_at).days
    r = 5 if days <= 30 else 4 if days <= 60 else 3 if days <= 90 else 2 if days <= 150 else 1
    f = min(order_count, 5)
    m = 5 if total_spent >= 8000 else 4 if total_spent >= 4000 else 3 if total_spent >= 2000 else 2 if total_spent >= 800 else 1
    return r + f + m
