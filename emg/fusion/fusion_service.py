# emg/fusion/fusion_service.py
import time
import asyncio
import grpc

from .proto import fusion_pb2, fusion_pb2_grpc

class FusionService(fusion_pb2_grpc.FusionServicer):
    def __init__(self, broadcaster):
        self.broadcaster = broadcaster

    async def StreamFusion(self, request: fusion_pb2.FusionSubscribeRequest, context: grpc.aio.ServicerContext):
        q = await self.broadcaster.subscribe()
        try:
            max_rate = request.max_rate_hz or 0
            interval = 1.0 / max_rate if max_rate > 0 else 0.0

            last_ts = 0.0
            while True:
                sample: fusion_pb2.FusedSample = await q.get()
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
