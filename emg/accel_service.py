import time
import asyncio
import grpc

from .proto import emg_pb2, emg_pb2_grpc

class AccelService(emg_pb2_grpc.AccelServicer):
    """
    gRPC Servicer: streamt AccelSample aus dem Broadcaster an Clients.
    Pro Client eine Queue; optionale Drosselung per max_rate_hz.
    """
    def __init__(self, broadcaster):
        self.broadcaster = broadcaster

    async def StreamAccel(self, request: emg_pb2.AccelSubscribeRequest, context: grpc.aio.ServicerContext):
        q = await self.broadcaster.subscribe()
        try:
            interval = 0.0
            if request.max_rate_hz > 0:
                interval = max(0.0, 1.0 / float(request.max_rate_hz))

            last_ts = 0.0
            while True:
                sample: emg_pb2.AccelSample = await q.get()
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
