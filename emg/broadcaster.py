import asyncio
from typing import Set
from .config import CLIENT_QUEUE_MAX, DROP_OLDEST_ON_FULL

class Broadcaster:
    """
    Distributes samples (arbitrary Python objects, e.g. EmgSample protobuf) to all active subscribers.
    Each subscriber receives its own asyncio.Queue with a limit.
    When the queue is full, either the oldest entry is dropped (according to policy) or the new sample is discarded.
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
                            sys.stderr.write(f"{LOG_PREFIX} Broadcaster: queue full even after dropping oldest\n")
                            # stuck, give up
                            pass
                    # else:  # drop new item
