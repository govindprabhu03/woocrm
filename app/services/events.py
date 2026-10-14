"""Tiny synchronous domain event bus (Observer). Sync/webhook code publishes; workflow engine subscribes."""
from dataclasses import dataclass, field
from typing import Callable

from sqlalchemy.orm import Session


@dataclass
class Event:
    type: str  # customer.created | order.created | order.status:<status> | segment.changed
    customer_id: int
    order_id: int | None = None
    data: dict = field(default_factory=dict)


Handler = Callable[[Session, Event], None]


class EventBus:
    def __init__(self) -> None:
        self._handlers: list[Handler] = []

    def subscribe(self, handler: Handler) -> None:
        self._handlers.append(handler)

    def publish(self, db: Session, event: Event) -> None:
        for h in self._handlers:
            h(db, event)
