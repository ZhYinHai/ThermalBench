from __future__ import annotations

import time
from typing import Optional

import temper_windows


CAL_OFFSET_C = 4.0  # calibration: subtract offset from raw probe reading


def read_ambient_c(*, cal_offset_c: float = CAL_OFFSET_C) -> float:
    """Read ambient temperature in °C from the TEMPer USB sensor.

    Raises:
        Exception: if the underlying sensor read fails.
    """
    t = float(temper_windows.get_temperature())
    return float(t - float(cal_offset_c))


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
