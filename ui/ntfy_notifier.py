from __future__ import annotations

import threading
import time
from typing import Optional

import requests
from PySide6.QtCore import QObject, Signal


class NtfyNotifier(QObject):
    """Send a push notification using ntfy from a background thread.

    The user supplies either:
    - a full topic URL, e.g. https://ntfy.sh/my-topic
    - or just a topic name, e.g. my-topic (we'll prepend https://ntfy.sh/)
    """

    finished = Signal(bool, str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)

    def send(
        self,
        *,
        topic: str,
        title: str,
        message: str,
        timeout_sec: float = 6.0,
        retries: int = 2,
    ) -> None:
        url = self._normalize_topic(topic)
        if not url:
            try:
                self.finished.emit(False, "Push notification not configured (ntfy topic missing)")
            except Exception:
                pass
            return

        headers = {
            # ntfy supports "Title" header.
            "Title": str(title or "ThermalBench: test finished").strip(),
            "Content-Type": "text/plain; charset=utf-8",
        }
        payload = (str(message or "").strip() + "\n").encode("utf-8")

        def _worker() -> None:
            last_err = ""
            backoff = 1.0
            for attempt in range(int(retries) + 1):
                try:
                    resp = requests.post(url, data=payload, headers=headers, timeout=float(timeout_sec))
                    if 200 <= int(resp.status_code) < 300:
                        try:
                            self.finished.emit(True, "Push notification sent")
                        except Exception:
                            pass
                        return

                    txt = ""
                    try:
                        txt = str(resp.text or "").strip()
                    except Exception:
                        txt = ""
                    if txt and len(txt) > 200:
                        txt = txt[:200] + "…"
                    last_err = f"HTTP {resp.status_code}" + (f": {txt}" if txt else "")
                except Exception as e:
                    last_err = str(e)

                if attempt < int(retries):
                    try:
                        time.sleep(backoff)
                    except Exception:
                        pass
                    backoff = min(6.0, backoff * 2.0)

            try:
                self.finished.emit(False, f"Push notification failed: {last_err}".strip())
            except Exception:
                pass

        threading.Thread(target=_worker, daemon=True).start()

    @staticmethod
    def _normalize_topic(topic: str) -> str:
        t = str(topic or "").strip()
        if not t:
            return ""
        if t.startswith("http://") or t.startswith("https://"):
            return t
        t = t.lstrip("/")
        return f"https://ntfy.sh/{t}"
