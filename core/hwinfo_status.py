# hwinfo_status.py
from __future__ import annotations

def try_open_hwinfo_sm2() -> tuple[bool | None, str]:
    """
    Returns:
      (True,  msg)  -> definitely accessible (ON)
      (False, msg)  -> definitely not found (OFF)
      (None,  msg)  -> uncertain (e.g., access denied)
    """
    try:
        import mmap
    except Exception as e:
        return None, f"mmap import failed: {e}"

    names = [
        "Local\\HWiNFO_SENS_SM2",
        "Global\\HWiNFO_SENS_SM2",
        "HWiNFO_SENS_SM2",
    ]

    last_err = None
    for name in names:
        try:
            mm = mmap.mmap(-1, 1, tagname=name, access=mmap.ACCESS_READ)
            mm.close()
            return True, f"opened mapping: {name}"
        except PermissionError as e:
            return None, f"permission denied opening {name}: {e}"
        except OSError as e:
            last_err = f"{name}: {e}"
        except Exception as e:
            last_err = f"{name}: {e}"

    return False, f"not found (tried Global/Local): {last_err or 'no details'}"
