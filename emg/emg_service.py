import time
import asyncio
import grpc

from .proto import emg_pb2, emg_pb2_grpc

class EmgService(emg_pb2_grpc.EmgServicer):
    """
    gRPC-Servicer: streamt EmgSample aus dem Broadcaster an Clients.
    Pro Client eine Queue; optionale Drosselung via max_rate_hz.
    """
    def __init__(self, broadcaster):
        self.broadcaster = broadcaster

    async def StreamEmg(self, request: emg_pb2.SubscribeRequest, context: grpc.aio.ServicerContext):
        q = await self.broadcaster.subscribe()
        try:
            interval = 0.0
            if request.max_rate_hz > 0:
                interval = max(0.0, 1.0 / float(request.max_rate_hz))

            last_ts = 0.0
            while True:
                sample: emg_pb2.EmgSample = await q.get()
                if interval > 0.0:
                    now = time.perf_counter()
                    if (now - last_ts) < interval:
                        continue
                    last_ts = now
                yield sample
        except asyncio.CancelledError:
            raise
        finally:
            await self.broadcaster.unsubscribe(q)
