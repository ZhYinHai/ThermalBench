"""Layout/drawing helpers extracted from `ui/graph_preview.py`.

These functions are written to preserve behavior exactly.
"""

from __future__ import annotations

from typing import Any


def preview_apply_axes_rect(gp: Any, right_frac: float, left_margin_px: float) -> None:
    try:
        fig = gp._preview_fig
        ax = gp._preview_ax
        if fig is None or ax is None:
            return
        fig_w_px = float(fig.get_figwidth() * fig.dpi)
        if fig_w_px <= 1:
            return

        left = float(left_margin_px) / fig_w_px
        top = float(gp._preview_top_frac)
        bottom = float(gp._preview_bottom_frac)

        left = max(0.0, min(left, 0.95))
        right = max(left + 0.05, min(float(right_frac), 0.995))
        ax.set_position([left, bottom, right - left, top - bottom])
    except Exception:
        pass


def preview_required_left_margin_px(gp: Any, renderer, pad_px: int = 8) -> float:
    try:
        ax = gp._preview_ax
        if ax is None or renderer is None:
            return float(gp._preview_left_margin_px_base)

        bboxes = []
        for t in ax.get_yticklabels():
            if t.get_visible() and t.get_text():
                try:
                    bboxes.append(t.get_window_extent(renderer))
                except Exception:
                    pass
        try:
            ot = ax.yaxis.get_offset_text()
            if ot is not None and ot.get_visible() and ot.get_text():
                bboxes.append(ot.get_window_extent(renderer))
        except Exception:
            pass

        if not bboxes:
            return float(gp._preview_left_margin_px_base)

        min_x0 = min(bb.x0 for bb in bboxes)
        max_width = max(bb.width for bb in bboxes)

        if min_x0 < pad_px:
            extra = float(pad_px) - float(min_x0)
            return float(gp._preview_left_margin_px_base) + extra
        required = max_width + float(pad_px)
        return max(float(gp._preview_left_margin_px_base), required)
    except Exception:
        return float(getattr(gp, "_preview_left_margin_px_base", 60))


def preview_relayout_and_redraw(gp: Any) -> None:
    try:
        if gp._preview_canvas is None or gp._preview_ax is None:
            return
        if not gp._preview_canvas.isVisible():
            return

        gp._preview_canvas.draw()
        renderer = gp._preview_canvas.get_renderer()
        if renderer is None:
            return

        left_px = gp._preview_required_left_margin_px(renderer, pad_px=8)
        gp._preview_apply_axes_rect(right_frac=0.985, left_margin_px=left_px)

        gp._preview_invalidate_interaction_cache()
        gp._preview_canvas.draw()
        gp._on_preview_draw()
    except Exception:
        try:
            if gp._preview_canvas is not None:
                gp._preview_canvas.draw_idle()
        except Exception:
            pass
