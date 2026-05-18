from __future__ import annotations

import time
from typing import Optional

import temper_windows
import pywinusb.hid as hid


CAL_OFFSET_C = 4.0  # calibration: subtract offset from raw probe reading

# VID/PID pairs tried in order.  The first entry matches the temper_windows
# library's own filter; the remaining entries cover common TEMPer2 clone
# variants that ship with different USB controller chips.
_TEMPER_VID_PIDS: list[tuple[int, int]] = [
    (0x413D, 0x2107),  # RDing TEMPer / TEMPerHUM (handled by temper_windows)
    (0x1A86, 0xE025),  # TEMPer2 clone – WCH CH340/CH341 chip
    (0x0C45, 0x7401),  # TEMPer clone – Sonix/Microdia chip
    (0x0C45, 0x7402),  # TEMPer clone – Sonix/Microdia variant
]

_CMD = [0x00, 0x01, 0x80, 0x33, 0x01, 0x00, 0x00, 0x00, 0x00]


def _read_raw_temper(vid: int, pid: int) -> Optional[float]:
    """Try to read temperature from any device matching (vid, pid).

    Returns the first successful reading, or None if no device responds.
    Uses the same protocol as the temper_windows library.
    """
    devices = hid.HidDeviceFilter(vendor_id=vid, product_id=pid).get_devices()
    if not devices:
        return None

    result: list[Optional[float]] = [None]
    received: list[bool] = [False]

    def _handler(data: list) -> None:
        try:
            result[0] = float(data[3] * 256 + data[4]) / 100.0
        except (IndexError, TypeError):
            pass
        received[0] = True

    device = devices[0]
    try:
        device.open()
        device.set_raw_data_handler(_handler)
        received[0] = False
        device.send_output_report(_CMD)

        sleep = 0.01
        deadline = time.monotonic() + 2.0
        while not received[0] and time.monotonic() < deadline:
            time.sleep(sleep)
            sleep = 0.05
    finally:
        device.close()

    return result[0]


def read_ambient_c(*, cal_offset_c: float = CAL_OFFSET_C) -> float:
    """Read ambient temperature in °C from the TEMPer USB sensor.

    Tries the primary VID/PID first (via temper_windows), then falls back to
    additional known VID/PID pairs so that TEMPer2 clone variants are
    recognised automatically.

    Raises:
        RuntimeError: if no TEMPer device could be found or read.
    """
    # Primary path: temper_windows (VID 0x413D / PID 0x2107)
    try:
        t = temper_windows.get_temperature()
        if t is not None:
            return float(t) - float(cal_offset_c)
    except Exception:
        pass

    # Fallback: try all known VID/PID pairs (skipping the first – already tried)
    for vid, pid in _TEMPER_VID_PIDS[1:]:
        try:
            t = _read_raw_temper(vid, pid)
            if t is not None:
                return float(t) - float(cal_offset_c)
        except Exception:
            continue

    raise RuntimeError(
        "No TEMPer USB temperature sensor found. "
        "Make sure the sensor is plugged in and its driver is installed."
    )


def _debug_main() -> None:
    # Running stats (Welford for mean)
    n = 0
    mean = 0.0
    t_min: Optional[float] = None
    t_max: Optional[float] = None

    def update_stats(x: float) -> None:
        nonlocal n, mean, t_min, t_max
        n += 1
        mean += (x - mean) / n
        t_min = x if t_min is None else min(t_min, x)
        t_max = x if t_max is None else max(t_max, x)

    print(f'{"timestamp":23} | {"ambient_c":9} || {"min_c":9} {"max_c":9} {"avg_c":9} {"n":6}')
    print("-" * (23 + 3 + 9 + 4 + 9 + 1 + 9 + 1 + 9 + 1 + 6))

    try:
        while True:
            ambient_c = read_ambient_c()
            update_stats(ambient_c)

            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            ms = int((time.time() % 1.0) * 1000.0)
            ts_full = f"{ts}.{ms:03d}"

            print(f"{ts_full} | {ambient_c:9.2f} || {t_min:9.2f} {t_max:9.2f} {mean:9.2f} {n:6d}")
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    _debug_main()
