# emg/fusion/fusion_service.py
import time
import asyncio
import grpc

from .proto import fusion_pb2, fusion_pb2_grpc

    async def StreamFusionPackets(
        self,
        request: fusion_pb2.FusionSubscribeRequest,
        context: grpc.aio.ServicerContext,
    ):
        """
        Streams merged packets of FusedSample.
        Each reply is a packet containing multiple fused samples.
        """
        q = await self.broadcaster.subscribe()
        try:
            max_rate = request.max_rate_hz or 0
            interval = 1.0 / max_rate if max_rate > 0 else 0.0

            loop = asyncio.get_running_loop()
            last_send = 0.0

            MAX_SAMPLES_PER_PACKET = 32 # max samples per packet
            MAX_WAIT_S = 0.05

            while True:
                first: fusion_pb2.FusedSample = await q.get()
                batch = [first]
                start = loop.time()

                while len(batch) < MAX_SAMPLES_PER_PACKET:
                    remaining = MAX_WAIT_S - (loop.time() - start)
                    if remaining <= 0:
                        break
                    try:
                        nxt = await asyncio.wait_for(q.get(), timeout=remaining)
                        batch.append(nxt)
                    except asyncio.TimeoutError:
                        break

                if interval > 0.0:
                    now = loop.time()
                    delta = now - last_send
                    if delta < interval:
                        await asyncio.sleep(interval - delta)
                        last_send = loop.time()
                    else:
                        last_send = now

                packet = fusion_pb2.FusedSamplePacket()
                packet.samples.extend(batch)

                yield packet
        except asyncio.CancelledError:
            raise
        finally:
            await self.broadcaster.unsubscribe(q)
