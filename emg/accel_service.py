import time
import asyncio
import grpc
import sys

from .config import MAX_SAMPLES_PER_PACKET_ACCEL, MAX_WAIT_S_ACCEL, LOG_PREFIX
from .broadcaster import Broadcaster
from .proto import emg_pb2, emg_pb2_grpc


async def StreamAccelPackets(
        self,
        request: emg_pb2.AccelSubscribeRequest,
        context: grpc.aio.ServicerContext,
    ):
        """
        Streams merged packets of AccelSample.
        Each reply contains all samples that were waiting in the queue.
        """
        q = await self.broadcaster.subscribe()
        try:
            interval = 0.0
            if request.max_rate_hz > 0:
                interval = max(0.0, 1.0 / float(request.max_rate_hz))

            loop = asyncio.get_running_loop()
            last_send = 0.0

            while True:
                first: emg_pb2.AccelSample = await q.get()
                batch = [first]
                start = loop.time()

                while len(batch) < MAX_SAMPLES_PER_PACKET_ACCEL:
                    remaining = MAX_WAIT_S_ACCEL - (loop.time() - start)
                    if remaining <= 0:
                        break
                    try:
                        nxt = await asyncio.wait_for(q.get(), timeout=remaining)
                        batch.append(nxt)
                    except asyncio.TimeoutError:
                        sys.stdout.write(f"{LOG_PREFIX} AccelService.StreamAccelPackets timeout\n")
                        break
                
                if interval > 0.0:
                    now = loop.time()
                    delta = now - last_send
                    if delta < interval:
                        await asyncio.sleep(interval - delta)
                        last_send = loop.time()
                    else:
                        last_send = now

                packet = emg_pb2.AccelSamplePacket()
                packet.samples.extend(batch)

                yield packet
        except asyncio.CancelledError:
            sys.stderr.write(f"{LOG_PREFIX} AccelService.StreamAccelPackets cancelled\n")
            raise
        finally:
            await self.broadcaster.unsubscribe(q)
            sys.stdout.write(f"{LOG_PREFIX} AccelService.StreamAccelPackets stopped\n")