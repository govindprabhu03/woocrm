"""WooCommerce adapter: client port (Protocol), HTTP implementation, payload mappers, webhook signature."""
import base64
import hashlib
import hmac
from datetime import datetime
from typing import Iterator, Protocol

import httpx


class WooClient(Protocol):
    """Port the sync service depends on. Implemented by HttpWooClient (real) and MockWooStore (demo/tests)."""

    def customers(self) -> Iterator[dict]: ...
    def orders(self) -> Iterator[dict]: ...


class HttpWooClient:
    """Real WooCommerce REST v3 client (basic auth with consumer key/secret, paginated)."""

    def __init__(self, base_url: str, key: str, secret: str, per_page: int = 100, http: httpx.Client | None = None):
        self._base = base_url.rstrip("/") + "/wp-json/wc/v3"
        self._http = http or httpx.Client(auth=(key, secret), timeout=30)
        self._per_page = per_page

    def _paginate(self, path: str) -> Iterator[dict]:
        page = 1
        while True:
            r = self._http.get(f"{self._base}/{path}", params={"per_page": self._per_page, "page": page})
            r.raise_for_status()
            rows = r.json()
            yield from rows
            if len(rows) < self._per_page:
                return
            page += 1

    def customers(self) -> Iterator[dict]:
        return self._paginate("customers")

    def orders(self) -> Iterator[dict]:
        return self._paginate("orders")


def sign_payload(secret: str, body: bytes) -> str:
    """Woo signs webhook bodies: base64(HMAC-SHA256(secret, body)) in X-WC-Webhook-Signature."""
    return base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()


def verify_signature(secret: str, body: bytes, signature: str) -> bool:
    return hmac.compare_digest(sign_payload(secret, body), signature or "")


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "")).replace(tzinfo=None)


def map_customer(raw: dict) -> dict:
    billing = raw.get("billing") or {}
    return {
        "woo_id": raw.get("id") or None,
        "email": (raw.get("email") or billing.get("email") or "").strip().lower(),
        "first_name": raw.get("first_name") or billing.get("first_name") or "",
        "last_name": raw.get("last_name") or billing.get("last_name") or "",
        "phone": billing.get("phone") or "",
        "city": billing.get("city") or "",
        "country": billing.get("country") or "",
        "created_at": parse_dt(raw.get("date_created")),
    }


def map_order(raw: dict) -> dict:
    billing = raw.get("billing") or {}
    return {
        "woo_id": raw["id"],
        "number": str(raw.get("number") or raw["id"]),
        "status": raw.get("status", "pending"),
        "total": float(raw.get("total") or 0),
        "currency": raw.get("currency", "INR"),
        "items": [
            {"name": li.get("name"), "quantity": li.get("quantity", 1), "total": float(li.get("total") or 0)}
            for li in raw.get("line_items", [])
        ],
        "created_at": parse_dt(raw.get("date_created")),
        "woo_customer_id": raw.get("customer_id") or None,  # 0 => guest checkout
        "billing": map_customer({"billing": billing, "email": billing.get("email")}),
    }
