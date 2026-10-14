"""In-memory fake WooCommerce store emitting payloads in the real Woo REST v3 shape (demo + tests)."""
import random
from datetime import datetime, timedelta
from typing import Iterator

FIRST = ["Aarav", "Diya", "Rohan", "Meera", "Kabir", "Ananya", "Vikram", "Isha", "Arjun", "Sneha",
         "Rahul", "Priya", "Neha", "Siddharth", "Tara", "Manish", "Pooja", "Aditya", "Kavya", "Nikhil"]
LAST = ["Naik", "Desai", "Shetty", "Kamat", "Fernandes", "Gaonkar", "Prabhu", "D'Souza", "Borkar", "Sawant"]
CITIES = ["Panaji", "Margao", "Vasco", "Mapusa", "Ponda", "Mumbai", "Pune", "Bengaluru"]
PRODUCTS = [("Cashew Gift Box", 799), ("Goan Spice Kit", 449), ("Handloom Kunbi Saree", 3499),
            ("Bebinca Hamper", 1299), ("Coconut Feni (500ml)", 899), ("Terracotta Planter Set", 1599),
            ("Organic Kokum Syrup", 299), ("Brass Diya Pair", 650)]
STATUSES = ["completed", "completed", "completed", "processing", "on-hold", "cancelled", "refunded"]


class MockWooStore:
    def __init__(self, seed: int = 7, now: datetime | None = None):
        self._rng = random.Random(seed)
        self._now = now or datetime.utcnow()
        self._customers: list[dict] = []
        self._orders: list[dict] = []
        self._next_order = 1000

    def seed(self, customers: int = 40, orders: int = 80) -> "MockWooStore":
        for i in range(customers):
            self.add_customer(self._rng.choice(FIRST), self._rng.choice(LAST), days_ago=self._rng.randint(5, 330), n=i)
        for _ in range(orders):
            c = self._rng.choice(self._customers[: int(customers * 0.75)])  # last quarter stay prospects
            joined = datetime.fromisoformat(c["date_created"])
            span = max((self._now - joined).days, 1)
            when = joined + timedelta(days=self._rng.randint(0, span))
            if self._rng.random() < 0.3:  # recent-activity bias so "new"/"active" segments exist
                when = max(joined, self._now - timedelta(days=self._rng.randint(0, 25)))
            self.add_order(c["id"], status=self._rng.choice(STATUSES), created=when)
        return self

    def add_customer(self, first: str, last: str, days_ago: int = 0, n: int | None = None) -> dict:
        cid = len(self._customers) + 1
        email = f"{first}.{last}{n if n is not None else cid}@example.com".lower().replace("'", "")
        c = {"id": cid, "email": email, "first_name": first, "last_name": last,
             "date_created": (self._now - timedelta(days=days_ago)).isoformat(),
             "billing": {"first_name": first, "last_name": last, "email": email,
                         "phone": f"+91 98{self._rng.randint(10000000, 99999999)}",
                         "city": self._rng.choice(CITIES), "country": "IN"}}
        self._customers.append(c)
        return c

    def add_order(self, customer_id: int, status: str = "processing", created: datetime | None = None) -> dict:
        cust = next(c for c in self._customers if c["id"] == customer_id)
        lines = []
        for name, price in self._rng.sample(PRODUCTS, self._rng.randint(1, 3)):
            q = self._rng.randint(1, 2)
            lines.append({"name": name, "quantity": q, "total": f"{price * q:.2f}"})
        self._next_order += 1
        order = {"id": self._next_order, "number": str(self._next_order), "status": status, "currency": "INR",
                 "total": f"{sum(float(li['total']) for li in lines):.2f}", "customer_id": customer_id,
                 "date_created": (created or self._now).isoformat(), "billing": cust["billing"], "line_items": lines}
        self._orders.append(order)
        return order

    def set_status(self, order_id: int, status: str) -> dict:
        order = next(o for o in self._orders if o["id"] == order_id)
        order["status"] = status
        return order

    # WooClient port
    def customers(self) -> Iterator[dict]:
        return iter(list(self._customers))

    def orders(self) -> Iterator[dict]:
        return iter(list(self._orders))
