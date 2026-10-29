"""The `Clock` port: services never call `datetime.now()` directly (D1 §4.7)."""

from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """An aware datetime in UTC."""
        ...
