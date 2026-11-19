"""Read voltage, current, SOC and temperature from a DALY BMS over CAN and log them to CSV.

Protocol (DALY CAN protocol V1.0): 250 kbit/s, extended 29-bit IDs.
    request  ID = 0x18 <data id> 0x01 0x40   (host -> BMS), 8 data bytes
    response ID = 0x18 <data id> 0x40 0x01   (BMS -> host)
    data id 0x90: bytes 0-1 pack voltage (0.1 V), 2-3 gather voltage (0.1 V),
                  4-5 current ((raw - 30000) x 0.1 A), 6-7 SOC (0.1 %)
    data id 0x92: byte 0 max temperature (raw - 40 C), byte 2 min temperature (raw - 40 C)
16-bit values are read as big-endian.

Run it on the Raspberry Pi while a drive cycle is playing:

    python bms_logger.py --channel can0 --out logs/test_run.csv

The CAN interface must be up first, for example:

    sudo ip link set can0 up type can bitrate 250000
"""
import argparse
import csv
import os
import statistics
import time
from collections import deque

import can

BMS_ADDR = 0x01
HOST_ADDR = 0x40
DATA_VOLTAGE_CURRENT_SOC = 0x90
DATA_TEMPERATURE = 0x92

# Readings outside these limits are treated as bad packets and dropped
LIMITS = {
    "voltage": (0.0, 150.0),
    "current": (-200.0, 200.0),
    "soc": (0.0, 100.0),
    "temp": (-40.0, 120.0),
}


def request_id(data_id):
    return (0x18 << 24) | (data_id << 16) | (BMS_ADDR << 8) | HOST_ADDR


def response_id(data_id):
    return (0x18 << 24) | (data_id << 16) | (HOST_ADDR << 8) | BMS_ADDR


def decode_voltage_current_soc(data):
    """Return (voltage V, current A, SOC %) from an 8-byte 0x90 frame."""
    voltage = int.from_bytes(data[0:2], "big") * 0.1
    current = (int.from_bytes(data[4:6], "big") - 30000) * 0.1
    soc = int.from_bytes(data[6:8], "big") * 0.1
    return voltage, current, soc


def decode_temperature(data):
    """Return (max temperature C, min temperature C) from an 8-byte 0x92 frame."""
    return data[0] - 40, data[2] - 40


def in_range(name, value):
    low, high = LIMITS[name]
    return low <= value <= high


class Smoother:
    """Median of the last few good readings. Removes single-packet spikes."""

    def __init__(self, size=5):
        self.values = deque(maxlen=size)

    def add(self, value):
        self.values.append(value)
        return statistics.median(self.values)


def ask(bus, data_id, timeout=0.5):
    """Send a request and return the 8 data bytes of the reply, or None on timeout."""
    bus.send(can.Message(arbitration_id=request_id(data_id), data=bytes(8), is_extended_id=True))
    deadline = time.time() + timeout
    while time.time() < deadline:
        msg = bus.recv(timeout=max(0.0, deadline - time.time()))
        if msg is not None and msg.arbitration_id == response_id(data_id) and len(msg.data) == 8:
            return bytes(msg.data)
    return None


def main():
    parser = argparse.ArgumentParser(description="Log DALY BMS data over CAN to CSV")
    parser.add_argument("--interface", default="socketcan", help="python-can interface (default socketcan)")
    parser.add_argument("--channel", default="can0", help="CAN channel (default can0)")
    parser.add_argument("--out", default="logs/bms_log.csv", help="CSV file to write")
    parser.add_argument("--period", type=float, default=1.0, help="seconds between readings (default 1.0)")
    parser.add_argument("--duration", type=float, default=0, help="stop after this many seconds (0 = until Ctrl+C)")
    args = parser.parse_args()

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    filters = {name: Smoother() for name in LIMITS}
    last = {name: None for name in ("voltage", "current", "soc", "tmax", "tmin")}
    dropped = 0

    with can.Bus(interface=args.interface, channel=args.channel, bitrate=250000) as bus, \
            open(args.out, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["time_s", "voltage_V", "current_A", "soc_percent", "temp_max_C", "temp_min_C"])
        start = time.time()
        try:
            while True:
                tick = time.time()
                frame = ask(bus, DATA_VOLTAGE_CURRENT_SOC)
                if frame is not None:
                    v, i, s = decode_voltage_current_soc(frame)
                    if in_range("voltage", v) and in_range("current", i) and in_range("soc", s):
                        last["voltage"] = filters["voltage"].add(v)
                        last["current"] = filters["current"].add(i)
                        last["soc"] = filters["soc"].add(s)
                    else:
                        dropped += 1
                else:
                    dropped += 1

                frame = ask(bus, DATA_TEMPERATURE)
                if frame is not None:
                    t_max, t_min = decode_temperature(frame)
                    if in_range("temp", t_max) and in_range("temp", t_min):
                        last["tmax"] = filters["temp"].add(t_max)
                        last["tmin"] = t_min
                    else:
                        dropped += 1
                else:
                    dropped += 1

                elapsed = time.time() - start
                if all(value is not None for value in last.values()):
                    writer.writerow([f"{elapsed:.1f}", f"{last['voltage']:.1f}", f"{last['current']:.1f}",
                                     f"{last['soc']:.1f}", f"{last['tmax']:.0f}", f"{last['tmin']:.0f}"])
                    f.flush()
                    print(f"{elapsed:7.1f}s  {last['voltage']:6.1f} V  {last['current']:7.1f} A  "
                          f"SOC {last['soc']:5.1f} %  T {last['tmax']:.0f}/{last['tmin']:.0f} C")
                if args.duration and elapsed >= args.duration:
                    break
                time.sleep(max(0.0, args.period - (time.time() - tick)))
        except KeyboardInterrupt:
            pass
    print(f"Done. Bad or missing packets skipped: {dropped}. Log saved to {args.out}")


if __name__ == "__main__":
    main()
