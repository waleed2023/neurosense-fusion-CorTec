import asyncio
from typing import Set
from .config import CLIENT_QUEUE_MAX, DROP_OLDEST_ON_FULL

class Broadcaster:
    """
    Verteilt Samples (beliebige Python-Objekte, z.B. EmgSample protobuf) an alle
    aktiven Subscriber. Jeder Subscriber bekommt eine eigene asyncio.Queue mit Limit.
    Bei voller Queue wird entweder der älteste Eintrag gedroppt (Policy) oder
    das Sample verworfen.
    """
    def __init__(self):
        self._subs: Set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=CLIENT_QUEUE_MAX)
        async with self._lock:
            self._subs.add(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue):
        async with self._lock:
            self._subs.discard(q)

    async def publish(self, item):
        async with self._lock:
            dead = []
            for q in self._subs:
                try:
                    q.put_nowait(item)
                except asyncio.QueueFull:
                    if DROP_OLDEST_ON_FULL:
                        try:
                            _ = q.get_nowait()
                        except asyncio.QueueEmpty:
                            pass
                        try:
                            q.put_nowait(item)
                        except asyncio.QueueFull:
                            # Subscriber hängt dauerhaft zurück → überspringen
                            pass
                    # sonst einfach drop
