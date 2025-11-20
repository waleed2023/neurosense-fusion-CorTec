import time
import asyncio
import grpc
from .config import MAX_SAMPLES_PER_PACKET_EMG, MAX_WAIT_S_EMG, LOG_PREFIX
from .proto import emg_pb2, emg_pb2_grpc

class EmgService(emg_pb2_grpc.EmgServicer):
    """
    gRPC-Servicer: streamt EmgSample aus dem Broadcaster an Clients.
    Pro Client eine Queue; optionale Drosselung via max_rate_hz.
    """
    def __init__(self, broadcaster):
        self.broadcaster = broadcaster

    async def StreamEmgPackets(
        self,
        request: emg_pb2.SubscribeRequest,
        context: grpc.aio.ServicerContext,
    ):
        """
        Streams merged packets of EmgSample.
        Each reply contains all samples that are currently waiting in the broadcaster queue.
        """
        q = await self.broadcaster.subscribe()
        try:
            # Interpret max_rate_hz as "max packets per second"
            interval = 0.0
            if request.max_rate_hz > 0:
                interval = max(0.0, 1.0 / float(request.max_rate_hz))

            loop = asyncio.get_running_loop()
            last_send = 0.0

            while True:
                # 1) Wait for at least one sample (blocking)
                first: emg_pb2.EmgSample = await q.get()
                batch = [first]
                start = loop.time()

                # 2) Drain queue: merge all samples that arrive shortly after
                while len(batch) < MAX_SAMPLES_PER_PACKET_EMG:
                    remaining = MAX_WAIT_S_EMG - (loop.time() - start)
                    if remaining <= 0:
                        break
                    try:
                        nxt = await asyncio.wait_for(q.get(), timeout=remaining)
                        batch.append(nxt)
                    except asyncio.TimeoutError:
                        sys.stdout.write(f"{LOG_PREFIX} EmgService.StreamEmgPackets timeout\n")
                        break

                # 3) Optional f by packets per second
                if interval > 0.0:
                    now = loop.time()
                    delta = now - last_send
                    if delta < interval:
                        await asyncio.sleep(interval - delta)
                        last_send = loop.time()
                    else:
                        last_send = now

                # 4) Build packet and yield once
                packet = emg_pb2.EmgSamplePacket()
                packet.samples.extend(batch)

                yield packet

        except asyncio.CancelledError:
            sys.stderr.write(f"{LOG_PREFIX} EmgService.StreamEmgPackets cancelled\n")
            raise
        finally:
            system.stdout.write(f"{LOG_PREFIX} EmgService.StreamEmgPackets stopped\n")
            await self.broadcaster.unsubscribe(q)
