#!/usr/bin/env python3
"""ambient_logger.py

Small helper that logs ambient temperature readings from the TEMPer USB dongle
into a CSV file.

Design goals:
- Best-effort logging: keep running through transient read errors.
- Timestamp format compatible with the existing run window strings:
  "YYYY-MM-DD HH:MM:SS.mmm"
- Flush each row so data survives crashes/aborts.

This is used by `cli/run_case.ps1` to capture ambient temperature during the
warmup + logging window, then `cli/plot_hwinfo.py` slices/merges it into
`run_window.csv`.
"""

from __future__ import annotations

import argparse
import csv
import time
from datetime import datetime
from pathlib import Path

from core.ambient_sensor import read_ambient_c


def _now_ts_ms() -> str:
    # Match window-start/-end format in run_case.ps1
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="Output CSV path")
    ap.add_argument("--interval", type=float, default=1.0, help="Sample interval seconds")
    ap.add_argument("--cal-offset-c", type=float, default=None, help="Override calibration offset (°C)")
    args = ap.parse_args()

    outp = Path(str(args.out))
    try:
        outp.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    interval = float(args.interval)
    if interval <= 0:
        interval = 1.0

    # Create file + header if missing/empty.
    need_header = True
    try:
        if outp.exists() and outp.stat().st_size > 0:
            need_header = False
    except Exception:
        need_header = True

    cal_offset = args.cal_offset_c

    with open(outp, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if need_header:
            w.writerow(["timestamp", "ambient_c"])
            f.flush()

        while True:
            ts = _now_ts_ms()
            try:
                if cal_offset is None:
                    amb = read_ambient_c()
                else:
                    amb = read_ambient_c(cal_offset_c=float(cal_offset))

                w.writerow([ts, f"{float(amb):.4f}"])
            except Exception:
                # keep cadence even if sensor read fails
                w.writerow([ts, ""])
            try:
                f.flush()
            except Exception:
                pass

            time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
