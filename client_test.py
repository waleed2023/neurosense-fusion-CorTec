#!/usr/bin/env python3
import grpc
from emg.proto import emg_pb2, emg_pb2_grpc

def main():
    channel = grpc.insecure_channel("127.0.0.1:50051")
    stub = emg_pb2_grpc.EmgStub(channel)
    req = emg_pb2.SubscribeRequest(max_rate_hz=0)
    for i, sample in enumerate(stub.StreamEmg(req)):
        print(f"{i:06d}  value={sample.value:4d}  battery={sample.battery:3d}%  ts={sample.ts_ms}")
        if i >= 50:
            break

if __name__ == "__main__":
    main()
