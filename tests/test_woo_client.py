"""Real WooCommerce HTTP client, exercised against a fake transport (pagination, auth, error handling)."""
import base64

import httpx
import pytest

from app.integrations.woo import HttpWooClient


def make_client(handler, per_page=2):
    http = httpx.Client(transport=httpx.MockTransport(handler), auth=("ck_key", "cs_secret"))
    return HttpWooClient("https://shop.example.com/", "ck_key", "cs_secret", per_page=per_page, http=http)


def test_paginates_until_short_page_and_uses_v3_path_and_basic_auth():
    seen = []

    def handler(req: httpx.Request):
        seen.append(req)
        page = int(req.url.params["page"])
        data = {1: [{"id": 1}, {"id": 2}], 2: [{"id": 3}, {"id": 4}], 3: [{"id": 5}]}[page]
        return httpx.Response(200, json=data)

    rows = list(make_client(handler).customers())
    assert [r["id"] for r in rows] == [1, 2, 3, 4, 5]
    assert len(seen) == 3 and seen[0].url.path == "/wp-json/wc/v3/customers"
    assert seen[0].headers["authorization"] == "Basic " + base64.b64encode(b"ck_key:cs_secret").decode()
    assert seen[0].url.params["per_page"] == "2"


def test_orders_endpoint_and_empty_store():
    paths = []
    client = make_client(lambda r: (paths.append(r.url.path), httpx.Response(200, json=[]))[1])
    assert list(client.orders()) == [] and paths == ["/wp-json/wc/v3/orders"]


def test_http_errors_surface_instead_of_silently_returning_partial_data():
    client = make_client(lambda r: httpx.Response(401, json={"code": "woocommerce_rest_cannot_view"}))
    with pytest.raises(httpx.HTTPStatusError):
        list(client.customers())
