"""Bounded, expiring cache scoped to one QQ conversation and bot account."""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable

from .card_parser import Card


@dataclass(frozen=True)
class CachedMessage:
    message_id: str
    sender_id: str
    sender_name: str
    cards: tuple[Card, ...]
    received_at: float


class CardCache:
    def __init__(
        self,
        ttl: int = 300,
        per_session: int = 10,
        max_sessions: int = 200,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.ttl = ttl
        self.per_session = per_session
        self.max_sessions = max_sessions
        self.clock = clock
        self.sessions: OrderedDict[tuple[str, ...], list[CachedMessage]] = OrderedDict()

    def _purge(self) -> None:
        cutoff = self.clock() - self.ttl
        for key in list(self.sessions):
            live = [item for item in self.sessions[key] if item.received_at > cutoff]
            if live:
                self.sessions[key] = live
            else:
                del self.sessions[key]

    def put(self, scope: tuple[str, ...], message_id: str, sender_id: str,
            sender_name: str, cards: list[Card]) -> None:
        self._purge()
        if not cards or not message_id:
            return
        items = self.sessions.setdefault(scope, [])
        # Duplicate delivery must not refresh TTL or turn an old card into the newest.
        if any(item.message_id == message_id for item in items):
            return
        items.append(CachedMessage(message_id, sender_id, sender_name, tuple(cards), self.clock()))
        self.sessions[scope] = items[-self.per_session:]
        self.sessions.move_to_end(scope)
        while len(self.sessions) > self.max_sessions:
            self.sessions.popitem(last=False)

    def get(self, scope: tuple[str, ...], message_id: str) -> CachedMessage | None:
        return next((item for item in self.recent(scope) if item.message_id == message_id), None)

    def recent(self, scope: tuple[str, ...]) -> list[CachedMessage]:
        self._purge()
        return list(reversed(self.sessions.get(scope, [])))

    def clear(self) -> None:
        self.sessions.clear()
