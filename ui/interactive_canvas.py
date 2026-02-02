# interactive_canvas.py
from PySide6.QtCore import Signal, QEvent
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas


class InteractiveCanvas(FigureCanvas):
    """
    FigureCanvas that emits signals on Qt lifecycle / focus changes.
    GraphPreview is NOT a QWidget, so this is the correct place to catch events.
    """

    shown_or_resized = Signal()
    hidden = Signal()
    pointer_left = Signal()

    focus_lost = Signal()
    focus_gained = Signal()

    def event(self, event):
        try:
            et = event.type()

            if et in (QEvent.Show, QEvent.Resize):
                self.shown_or_resized.emit()

            elif et == QEvent.Hide:
                self.hidden.emit()

            elif et == QEvent.Leave:
                self.pointer_left.emit()

            # Critical: switching to another app triggers these
            elif et in (QEvent.WindowDeactivate, QEvent.FocusOut):
                self.focus_lost.emit()

            elif et in (QEvent.WindowActivate, QEvent.FocusIn):
                self.focus_gained.emit()

        except Exception:
            pass

        return super().event(event)
