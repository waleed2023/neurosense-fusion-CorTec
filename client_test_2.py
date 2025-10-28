#!/usr/bin/env python3
# client_test.py — gRPC-EMG Live-Plot im Terminal
# deps: grpcio, asciichartpy
import argparse
import sys
import time
from collections import deque

import grpc
import asciichartpy as asciichart

from emg.proto import emg_pb2, emg_pb2_grpc


def parse_args():
    p = argparse.ArgumentParser(description="EMG Live-Plot (gRPC → ASCII)")
    p.add_argument("--host", default="127.0.0.1", help="gRPC Host (Default: %(default)s)")
    p.add_argument("--port", type=int, default=50051, help="gRPC Port (Default: %(default)s)")
    p.add_argument("--max-rate-hz", type=float, default=0.0,
                   help="Server-seitige Drosselung (0 = keine)")

    # ASCII-Plot-Optionen
    p.add_argument("--ascii", action="store_true", help="Live-Plot im Terminal aktivieren")
    p.add_argument("--window", type=int, default=800,
                   help="Fenstergröße in Samples (Default: 800)")
    p.add_argument("--width", type=int, default=100,
                   help="Plot-Breite in Zeichen (Default: 100)")
    p.add_argument("--height", type=int, default=20,
                   help="Plot-Höhe in Zeilen (Default: 20)")
    p.add_argument("--ymin", type=float, default=0.0, help="Y-Min (Default: 0)")
    p.add_argument("--ymax", type=float, default=1023.0, help="Y-Max (Default: 1023)")
    p.add_argument("--refresh-hz", type=float, default=20.0,
                   help="Plot-Refresh-Rate (Default: 20 Hz)")

    # Ausgabe
    p.add_argument("--csv", action="store_true", help="CSV: unix_ts_ms,value,battery")
    p.add_argument("--limit", type=int, default=0,
                   help="Nur N Samples lesen (0 = endlos)")
    return p.parse_args()


def clear_screen():
    sys.stdout.write("\x1b[2J\x1b[H")  # clear + cursor home
    sys.stdout.flush()


def draw_chart(buf, args, extra_line=""):
    if not buf:
        return
    # Auf gewünschte Breite beschneiden
    data = list(buf)[-max(1, args.width):]

    cfg = {
        "height": max(5, args.height),
        "min": args.ymin,
        "max": args.ymax,
        "format": "{:>5.0f}",
    }
    chart = asciichart.plot(data, cfg)
    clear_screen()
    sys.stdout.write(chart + "\n")
    if extra_line:
        sys.stdout.write(extra_line + "\n")
    sys.stdout.flush()


def main():
    args = parse_args()

    target = f"{args.host}:{args.port}"
    channel = grpc.insecure_channel(target)
    stub = emg_pb2_grpc.EmgStub(channel)
    req = emg_pb2.SubscribeRequest(max_rate_hz=int(args.max_rate_hz))

    data_buf = deque(maxlen=max(10, args.window))
    sample_count = 0
    last_draw = 0.0
    draw_interval = 1.0 / max(1e-6, args.refresh_hz)  # Schutz vor div/0

    stream = None
    try:
        stream = stub.StreamEmg(req)
        if args.ascii:
            clear_screen()

        for sample in stream:
            # sample: EmgSample(value, ts_ms, battery)
            v = int(sample.value)
            ts = int(sample.ts_ms)
            batt = int(sample.battery)

            data_buf.append(v)
            sample_count += 1

            # CSV optional
            if args.csv:
                print(f"{ts},{v},{batt}", flush=True)

            # ASCII-Plot throttlen
            if args.ascii:
                now = time.time()
                if (now - last_draw) >= draw_interval:
                    info = f"samples: {sample_count}  battery: {batt if batt != 255 else 'n/a'}%"
                    draw_chart(data_buf, args, extra_line=info)
                    last_draw = now

            # limit?
            if args.limit > 0 and sample_count >= args.limit:
                break

        # falls Stream normal endet (Server-Stop etc.)
        if args.ascii:
            draw_chart(data_buf, args, extra_line="[i] Stream beendet.")
        return 0

    except KeyboardInterrupt:
        if args.ascii:
            draw_chart(data_buf, args, extra_line="[i] Abbruch durch Benutzer.")
        return 0
    except grpc.RpcError as e:
        sys.stderr.write(f"[!] gRPC Fehler: {e}\n")
        return 2
    except Exception as e:
        sys.stderr.write(f"[!] Fehler: {e}\n")
        return 1
    finally:
        # Kanal ordentlich schließen
        try:
            if stream is not None:
                # bei blocking stub kann man nicht explizit schließen; Channel genügt
                pass
        finally:
            channel.close()


if __name__ == "__main__":
    sys.exit(main())
