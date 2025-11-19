#!/usr/bin/env python3
import asyncio

from emg.broadcaster import Broadcaster
from emg.proto import emg_pb2
from emg.fusion.fusion_manager import FusionManager, now_ms
from emg.config import LOG_PREFIX


async def emg_producer(b: Broadcaster, n_samples: int, hz: float) -> int:
    """Generate n_samples EMG samples and publish them into the broadcaster."""
    period = 1.0 / hz
    produced = 0

    for _ in range(n_samples):
        sample = emg_pb2.EmgSample(
            value=100,      # simple dummy value
            ts_ms=now_ms(),
            battery=90,
        )
        await b.publish(sample)
        produced += 1
        await asyncio.sleep(period)

    print(f"{LOG_PREFIX} EMG producer done, produced={produced}")
    return produced


async def acc_producer(b: Broadcaster, n_samples: int, hz: float) -> int:
    """Generate n_samples accel samples and publish them into the broadcaster."""
    period = 1.0 / hz
    produced = 0

    for _ in range(n_samples):
        sample = emg_pb2.AccelSample(
            ax=0.0,
            ay=0.0,
            az=1.0,        # gravity
            mag=1.0,
            ts_ms=now_ms(),
        )
        await b.publish(sample)
        produced += 1
        await asyncio.sleep(period)

    print(f"{LOG_PREFIX} ACC producer done, produced={produced}")
    return produced


async def main_async() -> None:
    # 1) Create broadcasters for EMG, ACC and fusion
    emg_b = Broadcaster()
    acc_b = Broadcaster()
    fusion_b = Broadcaster()

    # 2) Create FusionManager with debug counters
    fusion_mgr = FusionManager(emg_b, acc_b, fusion_b)
    fusion_mgr.start()

    # 3) Start producers
    #    You can adjust these numbers if you want to stress the queues more.
    N_EMG = 100
    N_ACC = 1000

    emg_task = asyncio.create_task(emg_producer(emg_b, N_EMG, hz=10.0))
    acc_task = asyncio.create_task(acc_producer(acc_b, N_ACC, hz=100.0))

    emg_produced, acc_produced = await asyncio.gather(emg_task, acc_task)

    # 4) Give the FusionManager some time to consume all remaining samples
    await asyncio.sleep(1.0)

    # 5) Stop the FusionManager
    await fusion_mgr.stop()

    # 6) Compare produced vs. read
    print()
    print("==== Fusion queue test result ====")
    print(f"EMG produced:            {emg_produced}")
    print(f"EMG read by FusionMgr:   {fusion_mgr.debug_emg_read}")
    print()
    print(f"ACC produced:            {acc_produced}")
    print(f"ACC read by FusionMgr:   {fusion_mgr.debug_acc_read}")
    print("==================================")

    if emg_produced == fusion_mgr.debug_emg_read:
        print("[OK] All EMG samples that reached emg_b were read by FusionManager.")
    else:
        missing = emg_produced - fusion_mgr.debug_emg_read
        print(f"[WARN] EMG samples missing between producer and FusionManager ({missing} samples).")

    if acc_produced == fusion_mgr.debug_acc_read:
        print("[OK] All ACC samples that reached acc_b were read by FusionManager.")
    else:
        missing = acc_produced - fusion_mgr.debug_acc_read
        print(f"[WARN] ACC samples missing between producer and FusionManager ({missing} samples).")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
