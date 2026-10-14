"""Quick load/latency smoke test: python scripts/smoke_load.py http://127.0.0.1:8100 [requests] [concurrency]"""
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8100"
n = int(sys.argv[2]) if len(sys.argv) > 2 else 300
workers = int(sys.argv[3]) if len(sys.argv) > 3 else 20
paths = ["/api/stats", "/api/customers", "/api/orders", "/api/communications", "/api/workflows", "/"]


client = httpx.Client(base_url=base, timeout=30, limits=httpx.Limits(max_connections=workers))  # shared: avoids per-request TLS setup


def hit(i):
    t = time.perf_counter()
    r = client.get(paths[i % len(paths)])
    return r.status_code, (time.perf_counter() - t) * 1000


t0 = time.perf_counter()
with ThreadPoolExecutor(workers) as ex:
    res = list(ex.map(hit, range(n)))
wall = time.perf_counter() - t0
lat = sorted(l for _, l in res)
ok = sum(1 for s, _ in res if s == 200)
print(f"{n} requests, {workers} concurrent: {ok} OK, {n - ok} failed, {n / wall:.0f} req/s")
print(f"latency ms  p50={statistics.median(lat):.0f}  p95={lat[int(n * .95) - 1]:.0f}  max={lat[-1]:.0f}")
