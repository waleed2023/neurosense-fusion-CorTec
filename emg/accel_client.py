#!/usr/bin/env python3
# accel_client.py — gRPC Accel Live-Plot (ASCII im Terminal)
import argparse
import sys
import time
from collections import deque

import grpc
import asciichartpy as asciichart
from emg.proto import emg_pb2, emg_pb2_grpc

def parse_args():
    p = argparse.ArgumentParser(description="Accel Live-Plot (gRPC → ASCII)")
    p.add_argument("--host", default="127.0.0.1", help="gRPC Host")
    p.add_argument("--port", type=int, default=50051, help="gRPC Port")
    p.add_argument("--axis", choices=["x","y","z","mag"], default="mag",
                   help="zu plottende Achse (Default: mag)")
    p.add_argument("--max-rate-hz", type=float, default=0.0,
                   help="Server-seitige Drosselung (0=keine)")

    # Plot-Optionen
    p.add_argument("--window", type=int, default=800, help="Fenstergröße in Samples")
    p.add_argument("--width", type=int, default=100, help="Plot-Breite in Zeichen")
    p.add_argument("--height", type=int, default=20, help="Plot-Höhe in Zeilen")
    p.add_argument("--ymin", type=float, default=-2.5, help="Y-Min (g)")
    p.add_argument("--ymax", type=float, default=+2.5, help="Y-Max (g)")
    p.add_argument("--refresh-hz", type=float, default=20.0, help="Refresh-Rate")
    p.add_argument("--csv", action="store_true",
                   help="CSV-Ausgabe: ts_ms,ax,ay,az,mag")
    p.add_argument("--limit", type=int, default=0, help="Nur N Samples (0=endlos)")
    return p.parse_args()

def clear_screen():
    sys.stdout.write("\x1b[2J\x1b[H")
    sys.stdout.flush()

def draw_chart(buf, args, extra_line=""):
    if not buf:
        return
    data = list(buf)[-max(1, args.width):]
    cfg = {
        "height": max(5, args.height),
        "min": args.ymin,
        "max": args.ymax,
        "format": "{:>+6.3f}g",
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
    stub = emg_pb2_grpc.AccelStub(channel)
    req = emg_pb2.AccelSubscribeRequest(max_rate_hz=int(args.max_rate_hz))

    buf = deque(maxlen=max(10, args.window))
    sample_count = 0
    last_draw = 0.0
    draw_interval = 1.0 / max(1e-6, args.refresh_hz)

    try:
        stream = stub.StreamAccel(req)
        clear_screen()
        for s in stream:
            ax, ay, az, mag, ts = s.ax, s.ay, s.az, s.mag, s.ts_ms
            if args.axis == "x":
                value = ax
            elif args.axis == "y":
                value = ay
            elif args.axis == "z":
                value = az
            else:
                value = mag

            buf.append(value)
            sample_count += 1

            if args.csv:
                print(f"{ts},{ax:.6f},{ay:.6f},{az:.6f},{mag:.6f}", flush=True)

            now = time.time()
            if (now - last_draw) >= draw_interval:
                info = (f"samples: {sample_count}  "
                        f"last[g]: X={ax:+.3f} Y={ay:+.3f} Z={az:+.3f} | mag={mag:+.3f}")
                draw_chart(buf, args, extra_line=info)
                last_draw = now

            if args.limit > 0 and sample_count >= args.limit:
                break

        draw_chart(buf, args, extra_line="[i] Stream beendet.")
        return 0
    except KeyboardInterrupt:
        draw_chart(buf, args, extra_line="[i] Abbruch durch Benutzer.")
        return 0
    except grpc.RpcError as e:
        sys.stderr.write(f"[!] gRPC Fehler: {e}\n")
        return 2
    finally:
        channel.close()

if __name__ == "__main__":
    sys.exit(main())
