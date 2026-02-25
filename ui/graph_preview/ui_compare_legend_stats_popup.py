# ui_compare_legend_stats_popup.py
"""Legend and stats popup for compare-mode.

Shows one combined stats table for all compared runs.
Each compared run contributes a Min/Max/Avg column block labeled by run name.
Styled to match the single-run Legend & Stats popup.
"""

from __future__ import annotations

import math
import html
from collections import OrderedDict
from typing import Optional

from PySide6.QtCore import Qt, QTimer, QMimeData
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ui.graph_preview.graph_plot_helpers import group_columns_by_unit, get_measurement_type_label


def _last_visible_row_bottom(tree: QTreeWidget) -> int:
    """Bottom y (viewport coords) of the last visible row."""
    try:
        vp = tree.viewport()
        if vp is None:
            return 0

        h = int(vp.height())
        if h <= 0:
            return 0

        from PySide6.QtCore import QPoint

        idx = tree.indexAt(QPoint(0, h - 1))
        if idx.isValid():
            r = tree.visualRect(idx)
            if r.isValid() and int(r.bottom()) > 0:
                return min(int(r.bottom()), h)

        # Fallback: last item in the tree.
        last_item = None
        tc = int(tree.topLevelItemCount())
        if tc > 0:
            last_top = tree.topLevelItem(tc - 1)
            if last_top is not None:
                cc = int(last_top.childCount())
                last_item = last_top.child(cc - 1) if cc > 0 else last_top
        if last_item is not None:
            r = tree.visualRect(tree.indexFromItem(last_item))
            if r.isValid() and int(r.bottom()) > 0:
                return min(int(r.bottom()), h)

        return 0
    except Exception:
        return 0


class _StatsCellDelegate(QStyledItemDelegate):
    """Custom delegate for numeric columns.

    - Adds a small per-cell horizontal padding so the run separator line reads as
      centered between blocks.
    - Disables text eliding for numeric columns so values don't turn into "1...".
    """

    def __init__(self, parent=None, *, pad_x: int = 8):
        super().__init__(parent)
        self._pad_x = int(pad_x)

    def initStyleOption(self, option: QStyleOptionViewItem, index) -> None:
        super().initStyleOption(option, index)
        try:
            if int(index.column()) > 0:
                option.textElideMode = Qt.ElideNone
        except Exception:
            pass

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        try:
            opt = QStyleOptionViewItem(option)
            self.initStyleOption(opt, index)

            try:
                col = int(index.column())
            except Exception:
                col = 0

            if col > 0:
                r = opt.rect
                # Clamp padding so we never end up with an empty paint rect.
                max_pad = max(0, int((int(r.width()) - 4) / 2))
                pad = max(0, min(int(self._pad_x), int(max_pad)))
                opt.rect = r.adjusted(int(pad), 0, -int(pad), 0)

            w = opt.widget
            style = w.style() if w is not None else QApplication.style()
            style.drawControl(QStyle.CE_ItemViewItem, opt, painter, w)
        except Exception:
            super().paint(painter, option, index)


class CompareLegendStatsPopup(QDialog):
    def __init__(
        self,
        parent,
        *,
        title: str,
        sensors: list[str],
        run_tables: list[dict],
        on_close=None,
    ):
        super().__init__(parent)

        self.setWindowFlag(Qt.Tool, True)
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setModal(False)

        self._on_close = on_close
        self._sensors = [str(s) for s in (sensors or []) if str(s).strip()]
        self._run_tables = list(run_tables or [])
        self._run_count = int(len(self._run_tables))
        self._dt_only_mode = False
        # Column layout is driven off a 4-result baseline.
        self._fit_max_results = 4
        # Keep a small per-cell padding so separators don't hug values.
        # (Also used when calculating the fixed dialog width.)
        self._cell_pad_x = 2
        self._apply_fixed_dialog_size()

        root = QVBoxLayout(self)
        root.setSizeConstraint(QLayout.SetDefaultConstraint)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        # Title row + close button
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)

        title_area = QLabel(str(title or "Legend & stats"))
        title_area.setStyleSheet("color:#EAEAEA; font-weight:600; font-size:13px;")
        title_area.setMinimumWidth(0)
        title_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        close_btn = QToolButton()
        close_btn.setText("✕")
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.clicked.connect(self.close)
        close_btn.setStyleSheet(
            """
            QToolButton { color:#9A9A9A; background: transparent; border: none; padding: 4px 6px; }
            QToolButton:hover { color:#EAEAEA; background: rgba(255,255,255,0.06); border-radius: 6px; }
            """
        )

        title_row.addWidget(title_area)
        title_row.addStretch(1)
        title_row.addWidget(close_btn)
        root.addLayout(title_row)

        # Buttons row (match single-run Legend & Stats popup)
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)

        settings_btn = QPushButton("Settings")
        settings_btn.setCursor(Qt.PointingHandCursor)
        settings_btn.setCheckable(True)
        settings_btn.setChecked(False)
        settings_btn.clicked.connect(self._toggle_settings_panel)
        settings_btn.setStyleSheet(
            """
            QPushButton {
                background: #2A2A2A;
                color: #EAEAEA;
                border: 1px solid #3A3A3A;
                border-radius: 6px;
                padding: 6px 14px;
                font-size: 12px;
            }
            QPushButton:hover {
                background: #333333;
                border-color: #4A4A4A;
            }
            QPushButton:pressed {
                background: #252525;
            }
            QPushButton:checked {
                background: #1F2B1F;
                border-color: #2E4A2E;
            }
            """
        )

        btn_row.addStretch(1)
        btn_row.addWidget(settings_btn)

        dt_btn = QPushButton("ΔT only")
        dt_btn.setCursor(Qt.PointingHandCursor)
        dt_btn.setCheckable(True)
        dt_btn.setChecked(False)
        dt_btn.clicked.connect(self._toggle_dt_only_mode)
        dt_btn.setStyleSheet(
            """
            QPushButton {
                background: #2A2A2A;
                color: #EAEAEA;
                border: 1px solid #3A3A3A;
                border-radius: 6px;
                padding: 6px 14px;
                font-size: 12px;
            }
            QPushButton:hover {
                background: #333333;
                border-color: #4A4A4A;
            }
            QPushButton:pressed {
                background: #252525;
            }
            QPushButton:checked {
                background: #1F2B1F;
                border-color: #2E4A2E;
            }
            """
        )
        btn_row.addWidget(dt_btn)

        copy_btn = QPushButton("Copy Table")
        copy_btn.setCursor(Qt.PointingHandCursor)
        copy_btn.clicked.connect(self._copy_table_to_clipboard)
        copy_btn.setStyleSheet(
            """
            QPushButton {
                background: #2A2A2A;
                color: #EAEAEA;
                border: 1px solid #3A3A3A;
                border-radius: 6px;
                padding: 6px 14px;
                font-size: 12px;
            }
            QPushButton:hover {
                background: #333333;
                border-color: #4A4A4A;
            }
            QPushButton:pressed {
                background: #252525;
            }
            """
        )

        btn_row.addWidget(copy_btn)
        root.addLayout(btn_row)

        self._settings_btn = settings_btn
        self._dt_btn = dt_btn
        self._copy_btn = copy_btn

        tree = self._build_combined_tree()
        self._tree = tree
        try:
            tree.setItemDelegate(_StatsCellDelegate(tree, pad_x=int(self._cell_pad_x)))
        except Exception:
            pass
        hdr_sc = self._build_combined_header(tree)
        self._hdr_sc = hdr_sc

        # Container-spanning separators (cover header + table with no gaps)
        self._table_sep_lines: list[QFrame] = []

        # Header container reserves space for the tree's vertical scrollbar
        # so the columns line up with the tree viewport.
        hdr_row = QHBoxLayout()
        hdr_row.setContentsMargins(0, 0, 0, 0)
        hdr_row.setSpacing(0)
        hdr_row.addWidget(hdr_sc, 1)
        try:
            sbw = int(tree.style().pixelMetric(QStyle.PM_ScrollBarExtent))
        except Exception:
            sbw = 16
        sb_spacer = QWidget()
        try:
            sb_spacer.setFixedWidth(max(0, sbw))
        except Exception:
            pass
        hdr_row.addWidget(sb_spacer, 0)
        self._hdr_sb_spacer = sb_spacer

        # Stack header + table with zero gap so separators appear continuous.
        table_container = QFrame()
        table_container.setFrameShape(QFrame.NoFrame)
        self._table_container = table_container

        table_stack = QVBoxLayout(table_container)
        table_stack.setContentsMargins(0, 0, 0, 0)
        table_stack.setSpacing(0)
        table_stack.addLayout(hdr_row, 0)
        table_stack.addWidget(tree, 1)

        # Body row: table (left) + settings panel (right)
        body_row = QHBoxLayout()
        body_row.setContentsMargins(0, 0, 0, 0)
        body_row.setSpacing(12)
        body_row.addWidget(table_container, 1)

        self._settings_panel = QFrame()
        self._settings_panel.setObjectName("SettingsPanel")
        self._settings_panel.setFrameShape(QFrame.NoFrame)
        self._settings_panel.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        self._settings_panel.setMinimumWidth(170)
        self._settings_panel.setMaximumWidth(240)
        self._settings_panel.setVisible(False)

        sp_root = QVBoxLayout(self._settings_panel)
        sp_root.setContentsMargins(8, 8, 8, 8)
        sp_root.setSpacing(6)

        sp_title = QLabel("Test Settings")
        sp_title.setStyleSheet("color:#9A9A9A; font-weight:600; font-size:11px;")
        sp_root.addWidget(sp_title)

        self._settings_label = QLabel()
        self._settings_label.setTextFormat(Qt.RichText)
        self._settings_label.setWordWrap(True)
        self._settings_label.setStyleSheet("color:#EAEAEA; font-size:11px;")

        # Wrap the label in a scroll area for long multi-run settings.
        sp_sc = QScrollArea()
        sp_sc.setFrameShape(QFrame.NoFrame)
        sp_sc.setWidgetResizable(True)
        sp_sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sp_sc.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        sp_inner = QWidget()
        sp_inner_l = QVBoxLayout(sp_inner)
        sp_inner_l.setContentsMargins(0, 0, 0, 0)
        sp_inner_l.setSpacing(0)
        sp_inner_l.addWidget(self._settings_label, 1)
        sp_sc.setWidget(sp_inner)

        sp_root.addWidget(sp_sc, 1)

        self._render_compare_settings()

        # Disable tooltip if nothing is recorded anywhere.
        try:
            has_any = False
            for rt in (self._run_tables or []):
                ts = rt.get("test_settings")
                if isinstance(ts, dict) and ts:
                    has_any = True
                    break
            if not has_any:
                self._settings_btn.setToolTip("No settings recorded for these runs")
        except Exception:
            pass

        body_row.addWidget(self._settings_panel, 0)
        root.addLayout(body_row, 1)

        self._ensure_table_separators()
        self._update_table_separators()
        try:
            tree.horizontalScrollBar().valueChanged.connect(lambda *_: self._update_table_separators())
        except Exception:
            pass
        try:
            tree.verticalScrollBar().valueChanged.connect(lambda *_: self._update_table_separators())
        except Exception:
            pass
        try:
            tree.header().sectionResized.connect(lambda *_: self._update_table_separators())
        except Exception:
            pass

        # Keep header horizontally aligned with table scroll.
        try:
            tree.horizontalScrollBar().valueChanged.connect(hdr_sc.horizontalScrollBar().setValue)
        except Exception:
            pass

        # Keep header widths aligned with table column widths.
        try:
            tree.header().sectionResized.connect(lambda *_: self._update_header_widths(tree))
        except Exception:
            pass

        # Update right-side spacer width based on whether the vertical scrollbar is needed.
        try:
            vsb = tree.verticalScrollBar()
            vsb.rangeChanged.connect(lambda *_: self._update_header_scrollbar_spacer(tree))
        except Exception:
            pass

        self.setStyleSheet(
            """
            QDialog { background: #1A1A1A; border: 1px solid #2A2A2A; border-radius: 10px; }
            QLabel { background: transparent; }

            QFrame#SettingsPanel { background: #151515; border: 1px solid #2A2A2A; border-radius: 10px; }

            QTreeWidget { background: transparent; border: none; color: #EAEAEA; outline: none; }

            QTreeWidget::item {
                padding: 6px 4px;
                border-radius: 0px;
                background: transparent;
            }

            QTreeWidget::item:hover {
                background: rgba(255,255,255,0.06);
            }

            QTreeWidget::item:selected,
            QTreeWidget::item:selected:hover {
                background: transparent;
            }

            QHeaderView::section {
                background: transparent;
                color: #9A9A9A;
                font-weight: 600;
                padding: 6px 8px;
                border: none;
            }

            QHeaderView {
                background: #151515;
            }

            QHeaderView::viewport {
                background: #151515;
                margin: 0px;
                padding: 0px;
                border: none;
            }
            """
        )

        self.setSizeGripEnabled(False)
        # Fixed-size dialog (same for all result counts).

        QTimer.singleShot(0, self._autosize_columns)
        QTimer.singleShot(0, lambda: self._update_header_widths(tree))
        QTimer.singleShot(0, lambda: self._update_header_scrollbar_spacer(tree))
        QTimer.singleShot(0, lambda: self._fit_columns_if_needed(tree))
        QTimer.singleShot(0, self._update_table_separators)

    def closeEvent(self, event):
        try:
            if callable(getattr(self, "_on_close", None)):
                self._on_close()
        except Exception:
            pass
        super().closeEvent(event)

    # ---------- Copy table to clipboard ----------
    def _copy_table_to_clipboard(self) -> None:
        """Copy the combined compare table to clipboard (text + HTML) for Word."""
        try:
            tree = getattr(self, "_tree", None)
            if tree is None:
                return

            dt_only = bool(getattr(self, "_dt_only_mode", False))

            # Word paste behavior is more consistent with inline pixel sizes.
            # Keep this very small so pasted tables are compact.
            FONT_PX = 6
            # Slightly larger line-height + 1px vertical padding prevents glyph clipping
            # in Word (notably in header cells).
            LINE_PX = 14
            run_tables = list(getattr(self, "_run_tables", None) or [])
            run_labels = [str((rt or {}).get("label") or "").strip() for rt in run_tables]

            dt_col_indices = [int(1 + (4 * i) + 3) for i in range(int(len(run_labels)))]

            # Plain-text header row (tab-delimited)
            headers: list[str] = ["Sensor" if dt_only else "Measurement"]
            for lbl in run_labels:
                if dt_only:
                    headers.append(str(lbl))
                else:
                    headers.extend([f"{lbl} Min", f"{lbl} Max", f"{lbl} Avg", f"{lbl} ΔT"])

            text_lines: list[str] = ["\t".join(headers)]

            # HTML table with a 2-row header
            col_count = int(1 + ((1 if dt_only else 4) * len(run_labels)))

            # Measurement column width: compute from longest sensor name so names stay on
            # one line in Word, and so the stat columns get the remaining width.
            # We keep stat columns at a small fixed width for consistency.
            STAT_COL_PX = 70
            stat_cols = int((1 if dt_only else 4) * len(run_labels))

            longest_name = "Measurement"
            try:
                for i in range(int(tree.topLevelItemCount())):
                    item = tree.topLevelItem(i)
                    if item is None:
                        continue
                    if dt_only:
                        try:
                            if item.isHidden():
                                continue
                        except Exception:
                            pass
                    is_group_header = False
                    try:
                        is_group_header = bool(item.data(0, Qt.UserRole + 1))
                    except Exception:
                        is_group_header = False
                    if is_group_header:
                        continue
                    nm = (item.text(0) or "").strip()
                    if nm and len(nm) > len(longest_name):
                        longest_name = nm
            except Exception:
                pass

            # Approximate pixel width using a Word-ish font. Add padding+buffer.
            MEAS_COL_PX = 260
            try:
                from PySide6.QtGui import QFont, QFontMetrics

                f = QFont("Calibri")
                try:
                    f.setPixelSize(int(FONT_PX))
                except Exception:
                    pass
                fm = QFontMetrics(f)
                w = int(fm.horizontalAdvance(str(longest_name)))
                w_hdr = int(fm.horizontalAdvance("Measurement"))
                w = max(int(w), int(w_hdr))
                # CSS uses padding: 3px 8px => 16px horizontal padding, plus borders/buffer
                MEAS_COL_PX = int(w + 16 + 10)
                MEAS_COL_PX = max(80, int(MEAS_COL_PX))
            except Exception:
                MEAS_COL_PX = 260

            # Total preferred width (lets the table grow wider as more runs are added,
            # instead of Word shrinking the Measurement column).
            table_width_px = int(int(MEAS_COL_PX) + (int(stat_cols) * int(STAT_COL_PX)))

            def px_to_pt(px: int) -> int:
                # Word tends to behave more consistently with point widths.
                try:
                    return int(round(float(px) * 0.75))
                except Exception:
                    return int(px)

            MEAS_COL_PT = px_to_pt(int(MEAS_COL_PX))
            STAT_COL_PT = px_to_pt(int(STAT_COL_PX))

            # Note: we intentionally avoid forcing an overall table width. When the
            # table gets wider (more runs), Word can otherwise scale columns down to
            # fit the page, shrinking the Measurement column. Fixed per-column widths
            # + width:auto keeps Measurement at the longest-sensor width.

            def esc(s: str) -> str:
                try:
                    return html.escape(str(s))
                except Exception:
                    return ""

            def cell_text(s: str) -> str:
                """Return HTML for a table cell.

                Word can apply default paragraph formatting to *empty* cells, which
                can change row height. To keep row heights consistent, always emit a
                styled span even when the value is blank.
                """
                try:
                    txt = str(s or "")
                except Exception:
                    txt = ""
                if not txt.strip():
                    return (
                        f"<span style='font-size:{int(FONT_PX)}px;line-height:{int(LINE_PX)}px;'>"
                        "&nbsp;"
                        "</span>"
                    )
                return esc(txt)

            html_parts: list[str] = [
                "<html>",
                "<head>",
                "<meta charset='utf-8'>",
                "<style>",
                "body { margin: 0; padding: 0; }",
                f"table#tbTable {{ border-collapse: collapse; width: {int(table_width_px)}px; table-layout: fixed; margin-left: auto; margin-right: auto; }}",
                # Word-specific bits (mso-*) help keep line-height exact and avoid
                # row-height differences between cells.
                f"table#tbTable th, table#tbTable td {{ border: 1px solid black; padding: 3px 8px; text-align: left; white-space: nowrap; overflow-wrap: normal; word-break: keep-all; vertical-align: middle; mso-line-height-rule: at-least; }}",
                f"table#tbTable .measureCell {{ width:{int(MEAS_COL_PX)}px; min-width:{int(MEAS_COL_PX)}px; max-width:{int(MEAS_COL_PX)}px; }}",
                "p { margin: 0; padding: 0; }",
                "col.measure { }",
                "col.stat { }",
                "</style>",
                "</head>",
                "<body style='margin:0;padding:0;'>",
                "<table role='presentation' cellpadding='0' cellspacing='0' border='0' style='width:100%;border-collapse:collapse;border:none;'>",
                "<tr>",
                "<td align='center' style='padding:0;margin:0;border:none;width:100%;'>",
                f"<table id='tbTable' width='{int(table_width_px)}' align='center' style='border-collapse:collapse; table-layout:fixed; font-size:{int(FONT_PX)}px; line-height:{int(LINE_PX)}px; width:{int(table_width_px)}px; display:inline-table; margin-left:auto; margin-right:auto; mso-table-lspace:0pt; mso-table-rspace:0pt; mso-table-layout-alt:fixed;'>",
                "<thead>",
                "<tr style='background-color:#ebebeb;'>",
                f"<th class='measureCell' width='{int(MEAS_COL_PX)}' style='width:{int(MEAS_COL_PX)}px;width:{int(MEAS_COL_PT)}pt;"
                "background-color:#ebebeb;border:1px solid black;font-weight:bold;color:#000000;"
                f"font-size:{int(FONT_PX)}px;line-height:{int(LINE_PX)}px;'"
                f"{' rowspan=\'2\'' if not dt_only else ''}"
                f">{'Sensor' if dt_only else 'Measurement'}</th>",
            ]

            # Insert a colgroup right after the <table> tag so Word uses fixed column widths.
            try:
                t_idx = 0
                for k, v in enumerate(html_parts):
                    vv = str(v).lstrip()
                    if vv.startswith("<table") and "id='tbTable'" in vv:
                        t_idx = int(k)
                        break
                insert_at = int(t_idx + 1)
                cols: list[str] = [
                    "<colgroup>",
                    f"<col class='measure' width='{int(MEAS_COL_PX)}' style='width:{int(MEAS_COL_PX)}px;width:{int(MEAS_COL_PT)}pt;'>",
                ]

                for _ in range(int(stat_cols)):
                    cols.append(
                        f"<col class='stat' width='{int(STAT_COL_PX)}' style='width:{int(STAT_COL_PX)}px;width:{int(STAT_COL_PT)}pt;'>"
                    )
                cols.append("</colgroup>")
                html_parts[insert_at:insert_at] = cols
            except Exception:
                pass

            for lbl in run_labels:
                html_parts.append(
                    f"<th colspan='{1 if dt_only else 4}' style='background-color:#ebebeb;border:1px solid black;font-weight:bold;color:#000000;"
                    f"font-size:{int(FONT_PX)}px;line-height:{int(LINE_PX)}px;'>"
                    f"{cell_text(lbl)}"
                    "</th>"
                )

            html_parts.append("</tr>")

            # Full table: emit the stat header row. ΔT-only: omit it (closer to desired Word layout).
            if not dt_only:
                html_parts.append("<tr style='background-color:#ebebeb;'>")
                for _ in run_labels:
                    for stat in ("Min", "Max", "Avg", "ΔT"):
                        html_parts.append(
                            f"<th style='background-color:#ebebeb;border:1px solid black;font-weight:bold;color:#000000;"
                            f"font-size:{int(FONT_PX)}px;line-height:{int(LINE_PX)}px;'>"
                            f"{cell_text(stat)}"
                            "</th>"
                        )
                html_parts.append("</tr>")
            html_parts.append("</thead><tbody>")

            # Rows in display order (including group headers)
            for i in range(int(tree.topLevelItemCount())):
                item = tree.topLevelItem(i)
                if item is None:
                    continue

                if dt_only:
                    try:
                        if item.isHidden():
                            continue
                    except Exception:
                        pass

                is_group_header = False
                try:
                    is_group_header = bool(item.data(0, Qt.UserRole + 1))
                except Exception:
                    is_group_header = False

                if is_group_header:
                    group_name = (item.text(0) or "").strip()
                    if not group_name:
                        continue
                    text_lines.append("\t".join([group_name] + [""] * (col_count - 1)))
                    html_parts.append(
                        f"<tr><td colspan='{int(col_count)}' "
                        "style='padding:1px 8px;white-space:nowrap;background-color:#f3f3f3;"
                        f"border:1px solid black;font-size:{int(FONT_PX)}px;line-height:{int(LINE_PX)}px;font-weight:bold;'>"
                        f"{cell_text(group_name)}"
                        "</td></tr>"
                    )
                    continue

                # Sensor row
                sensor_name = (item.text(0) or "").strip()
                if not sensor_name:
                    continue

                row: list[str] = [sensor_name]
                if dt_only:
                    for c in dt_col_indices:
                        if int(c) < int(tree.columnCount()):
                            row.append(item.text(int(c)) or "")
                        else:
                            row.append("")
                else:
                    for c in range(1, int(tree.columnCount())):
                        row.append(item.text(int(c)) or "")

                # Normalize to expected column count
                if len(row) < col_count:
                    row.extend([""] * (col_count - len(row)))
                row = row[:col_count]

                text_lines.append("\t".join(row))

                html_parts.append("<tr>")
                for j, cell in enumerate(row):
                    if j == 0:
                        html_parts.append(
                            f"<td class='measureCell' width='{int(MEAS_COL_PX)}' style='width:{int(MEAS_COL_PX)}px;width:{int(MEAS_COL_PT)}pt;"
                            f"padding:3px 8px;white-space:nowrap;"
                            f"font-size:{int(FONT_PX)}px;line-height:{int(LINE_PX)}px;'>"
                            f"{cell_text(cell)}"
                            "</td>"
                        )
                    else:
                        html_parts.append(
                            f"<td style='padding:3px 8px;white-space:nowrap;text-align:right;"
                            f"font-size:{int(FONT_PX)}px;line-height:{int(LINE_PX)}px;'>"
                            f"{cell_text(cell)}"
                            "</td>"
                        )
                html_parts.append("</tr>")

            html_parts.extend([
                "</tbody></table>",
                "</td></tr></table>",
                "</body>",
                "</html>",
            ])

            table_text = "\n".join(text_lines)
            table_html = "".join(html_parts)

            mime_data = QMimeData()
            mime_data.setText(table_text)
            mime_data.setHtml(table_html)

            clipboard = QApplication.clipboard()
            clipboard.setMimeData(mime_data)

            # Visual feedback
            try:
                btn = getattr(self, "_copy_btn", None)
                if isinstance(btn, QPushButton):
                    original_text = btn.text()
                    btn.setText("✓ Copied!")
                    QTimer.singleShot(1500, lambda: btn.setText(original_text))
            except Exception:
                pass
        except Exception:
            pass

    def _toggle_dt_only_mode(self) -> None:
        try:
            btn = getattr(self, "_dt_btn", None)
            want_on = bool(btn.isChecked()) if btn is not None else (not bool(getattr(self, "_dt_only_mode", False)))
            self._set_dt_only_mode(want_on)
        except Exception:
            pass

    def _set_dt_only_mode(self, enabled: bool) -> None:
        try:
            self._dt_only_mode = bool(enabled)

            tree = getattr(self, "_tree", None)
            if tree is None:
                return

            run_count = int(getattr(self, "_run_count", 0) or 0)
            cols = int(tree.columnCount())

            # Columns: hide/show Min/Max/Avg.
            stat_lbls = list(getattr(self, "_hdr_stat_lbls", []) or [])
            for run_idx in range(int(run_count)):
                base = int(1 + (4 * int(run_idx)))
                dt_col = int(base + 3)

                for off in range(4):
                    col = int(base + int(off))
                    if col >= cols:
                        continue
                    is_dt = bool(int(off) == 3)
                    if self._dt_only_mode:
                        if is_dt:
                            try:
                                tree.setColumnHidden(int(col), False)
                            except Exception:
                                pass
                        else:
                            try:
                                tree.setColumnHidden(int(col), True)
                            except Exception:
                                pass
                            try:
                                tree.setColumnWidth(int(col), 0)
                            except Exception:
                                pass
                    else:
                        try:
                            tree.setColumnHidden(int(col), False)
                        except Exception:
                            pass

                # Header stat label visibility matches the table.
                try:
                    idx0 = int((4 * int(run_idx)) + 0)
                    if idx0 + 3 < len(stat_lbls):
                        if self._dt_only_mode:
                            # Hide the entire stat header row in ΔT-only mode (cleaner layout).
                            for k in range(4):
                                try:
                                    stat_lbls[idx0 + k].setVisible(False)
                                except Exception:
                                    pass
                        else:
                            for k in range(4):
                                try:
                                    stat_lbls[idx0 + k].setVisible(True)
                                except Exception:
                                    pass
                except Exception:
                    pass

                # Ensure ΔT column is visible.
                if int(dt_col) < cols:
                    try:
                        tree.setColumnHidden(int(dt_col), False)
                    except Exception:
                        pass

            # Rows: in ΔT-only mode show only Temperature group + temperature sensors.
            current_group = ""
            group_visible = True
            for i in range(int(tree.topLevelItemCount())):
                it = tree.topLevelItem(i)
                if it is None:
                    continue

                is_group_header = False
                try:
                    is_group_header = bool(it.data(0, Qt.UserRole + 1))
                except Exception:
                    is_group_header = False

                if is_group_header:
                    current_group = (it.text(0) or "").strip()
                    if self._dt_only_mode:
                        group_visible = bool(current_group.lower() == "temperature")
                        try:
                            it.setHidden(not group_visible)
                        except Exception:
                            pass
                    else:
                        group_visible = True
                        try:
                            it.setHidden(False)
                        except Exception:
                            pass
                    continue

                if not self._dt_only_mode:
                    try:
                        it.setHidden(False)
                    except Exception:
                        pass
                    continue

                name = (it.text(0) or "").strip()
                s = name.lower()
                is_temp = ("°c" in s) or ("[°c]" in s) or ("(°c)" in s)
                want = bool(group_visible and is_temp)
                try:
                    it.setHidden(not want)
                except Exception:
                    pass

            # Re-apply sizing rules and resync header.
            try:
                # Always apply the baseline sizing rules; ΔT-only will keep
                # compact ΔT columns and rely on wrapped run names.
                self._fit_columns_if_needed(tree)
            except Exception:
                pass
            try:
                # In ΔT-only mode we *allow* multi-line run labels (like full table)
                # so the table doesn't grow unnecessarily wide.
                try:
                    for lab in (getattr(self, "_hdr_run_lbls", []) or []):
                        try:
                            lab.setWordWrap(True)
                        except Exception:
                            pass
                except Exception:
                    pass

                self._update_header_widths(tree)
                self._update_header_scrollbar_spacer(tree)
            except Exception:
                pass
            try:
                self._update_table_separators()
            except Exception:
                pass

            # Ensure we don't end up scrolled off-screen after hiding columns.
            try:
                hs = tree.horizontalScrollBar()
                if hs is not None:
                    hs.setValue(0)
            except Exception:
                pass
            try:
                sc = getattr(self, "_hdr_sc", None)
                if sc is not None:
                    hhs = sc.horizontalScrollBar()
                    if hhs is not None:
                        hhs.setValue(0)
            except Exception:
                pass
        except Exception:
            pass

    def resizeEvent(self, event):
        super().resizeEvent(event)
        try:
            tree = getattr(self, "_tree", None)
            if tree is not None:
                QTimer.singleShot(0, lambda: self._fit_columns_if_needed(tree))
                QTimer.singleShot(0, self._update_table_separators)
        except Exception:
            pass

    def _ensure_table_separators(self) -> None:
        try:
            cont = getattr(self, "_table_container", None)
            tree = getattr(self, "_tree", None)
            if cont is None or tree is None:
                return

            # Separators only between run blocks (no line after Measurement).
            desired = 0
            try:
                # With N runs, we need N-1 separators between them.
                desired = max(0, int(self._run_count) - 1)
            except Exception:
                desired = 0

            while len(self._table_sep_lines) < int(desired):
                ln = QFrame(cont)
                ln.setFrameShape(QFrame.NoFrame)
                ln.setStyleSheet("background: rgba(255,255,255,0.10);")
                try:
                    ln.setAttribute(Qt.WA_TransparentForMouseEvents, True)
                except Exception:
                    pass
                ln.setFixedWidth(1)
                ln.show()
                try:
                    ln.raise_()
                except Exception:
                    pass
                self._table_sep_lines.append(ln)

            while len(self._table_sep_lines) > int(desired):
                ln = self._table_sep_lines.pop()
                try:
                    ln.hide()
                    ln.deleteLater()
                except Exception:
                    pass
        except Exception:
            return

    def _update_table_separators(self) -> None:
        try:
            cont = getattr(self, "_table_container", None)
            tree = getattr(self, "_tree", None)
            if cont is None or tree is None:
                return

            self._ensure_table_separators()

            # Compute in container coords using QWidget mapping.
            vp = tree.viewport()
            if vp is None:
                return

            try:
                from PySide6.QtCore import QPoint
            except Exception:
                QPoint = None  # type: ignore[assignment]

            # Extend separators to the bottom of the visible table area.
            # Using the last row's bottom can cut lines short when there is
            # extra vertical space in the popup.
            y_end_vp = 0
            try:
                y_end_vp = int(vp.height())
            except Exception:
                y_end_vp = int(_last_visible_row_bottom(tree))
            y_end_vp = max(0, y_end_vp)

            y0 = 0
            if QPoint is not None:
                try:
                    y1 = int(vp.mapTo(cont, QPoint(0, y_end_vp)).y())
                except Exception:
                    y1 = int(cont.height())
            else:
                # Fallback: approximate using widget geometries.
                y1 = int(tree.y()) + int(vp.y()) + int(y_end_vp)

            y1 = max(int(y0), int(y1))
            y1 = min(int(y1), int(cont.height()))

            x_positions: list[int] = []

            def _edge_x_in_cont(col: int) -> int:
                x_vp = int(tree.columnViewportPosition(col)) + int(tree.columnWidth(col)) - 1
                if QPoint is not None:
                    return int(vp.mapTo(cont, QPoint(int(x_vp), 0)).x())
                return int(tree.x()) + int(vp.x()) + int(x_vp)

            # After each run block (after ΔT col): 4, 8, 12... excluding last
            try:
                for col in range(4, int(tree.columnCount()) - 1, 4):
                    x_positions.append(_edge_x_in_cont(int(col)))
            except Exception:
                pass

            desired = max(0, int(getattr(self, "_run_count", 0)) - 1)
            x_positions = x_positions[: int(desired)]

            for i, ln in enumerate(list(self._table_sep_lines)):
                if i >= len(x_positions):
                    try:
                        ln.hide()
                    except Exception:
                        pass
                    continue
                try:
                    ln.setGeometry(int(x_positions[i]), int(y0), 1, int(y1 - y0))
                    ln.show()
                    ln.raise_()
                except Exception:
                    pass
        except Exception:
            return

    # -----------------
    # Build helpers
    # -----------------
    def _make_color_icon(self, hex_color: str) -> QIcon:
        try:
            pix = QPixmap(10, 10)
            pix.fill(hex_color)
            return QIcon(pix)
        except Exception:
            return QIcon()

    def _fmt_stat(self, v) -> str:
        try:
            v = float(v)
            if not math.isfinite(v):
                return ""
            v1 = round(v, 1)
            if v1 == 0:
                v1 = 0.0
            return f"{v1:.1f}"
        except Exception:
            return ""

    def _measurement_group_order(self) -> list[str]:
        return [
            "Temperature",
            "Power (W)",
            "RPM",
            "Voltage (V)",
            "Percentage (%)",
            "Clock (MHz)",
            "Timing (T)",
        ]

    def _ordered_groups(self) -> list[tuple[str, list[str]]]:
        # Preserve sensor order within a unit by feeding sensors in their existing order.
        groups = group_columns_by_unit(list(self._sensors)) if self._sensors else {}

        # Convert to label -> cols for easier ordering
        label_to_cols: dict[str, list[str]] = {}
        for unit, cols in (groups or {}).items():
            label = str(get_measurement_type_label(unit))
            label_to_cols.setdefault(label, []).extend([str(c) for c in (cols or [])])

        ordered: list[tuple[str, list[str]]] = []
        seen = set()
        for lab in self._measurement_group_order():
            cols = label_to_cols.get(lab)
            if cols:
                ordered.append((lab, cols))
                seen.add(lab)

        # Any remaining labels (unknown units) sorted after
        for lab in sorted([k for k in label_to_cols.keys() if k not in seen], key=lambda s: s.lower()):
            ordered.append((lab, label_to_cols[lab]))

        return ordered

    def _build_combined_tree(self) -> QTreeWidget:
        run_tables = list(self._run_tables or [])

        # Columns: Measurement + (Min/Max/Avg/ΔT) per run.
        col_count = 1 + (4 * len(run_tables))
        tree = QTreeWidget()
        tree.setColumnCount(max(1, int(col_count)))

        # Header is custom (two-row) and lives above the tree; keep the built-in header hidden.
        header_labels: list[str] = ["Measurement"]
        header_labels.extend(["Min", "Max", "Avg", "ΔT"] * max(0, len(run_tables)))
        tree.setHeaderLabels(header_labels[: tree.columnCount()])

        tree.setRootIsDecorated(False)
        tree.setUniformRowHeights(True)
        tree.setSortingEnabled(False)
        tree.setSelectionMode(QAbstractItemView.NoSelection)
        tree.setFocusPolicy(Qt.NoFocus)
        tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        tree.setFrameShape(QFrame.NoFrame)
        tree.setLineWidth(0)

        try:
            tree.header().hide()
        except Exception:
            pass

        # Configure sizing/scrolling behavior based on number of results.
        self._configure_tree_layout(tree)

        # Sensible default widths.
        try:
            run_count = int(getattr(self, "_run_count", 0) or 0)
        except Exception:
            run_count = 0

        # Minimum stat width that can show typical numeric values cleanly.
        min_stat = 76
        try:
            fm = QFontMetrics(tree.font())
            pad_x = int(getattr(self, "_cell_pad_x", 0) or 0)
            sample = "0000.0"
            min_stat = max(int(min_stat), int(fm.horizontalAdvance(sample) + 12 + (2 * int(pad_x))))
        except Exception:
            pass

        # For 4+ results we keep a consistent compact Measurement column and
        # rely on horizontal scrolling instead of squeezing.
        meas_w = 360
        if run_count >= 4:
            meas_w = 220

        try:
            tree.setColumnWidth(0, int(meas_w))
        except Exception:
            pass
        for c in range(1, tree.columnCount()):
            try:
                tree.setColumnWidth(c, int(min_stat))
            except Exception:
                pass

        # Build group headers + rows
        for gname, cols in self._ordered_groups():
            header = QTreeWidgetItem(tree)
            header.setData(0, Qt.UserRole + 1, True)
            header.setText(0, str(gname))
            header.setFlags(Qt.ItemIsEnabled)
            try:
                f = QFont(tree.font())
                f.setBold(True)
                header.setData(0, Qt.FontRole, f)
                tree.setFirstItemColumnSpanned(header, True)
            except Exception:
                pass

            for name in (cols or []):
                it = QTreeWidgetItem(tree)
                it.setText(0, str(name))

                is_temp = "\u00b0c" in str(name).lower() or "[\u00b0c]" in str(name).lower() or "(\u00b0c)" in str(name).lower()
                is_ambient = bool(is_temp and ("ambient" in str(name).lower()))

                # Fill stats for each run (Min/Max/Avg/ΔT).
                for run_idx, rt in enumerate(run_tables):
                    stats_map = rt.get("stats_map") or {}
                    mn, mx, av = stats_map.get(str(name), (float("nan"), float("nan"), float("nan")))
                    base = 1 + (4 * int(run_idx))
                    it.setText(base + 0, self._fmt_stat(mn))
                    it.setText(base + 1, self._fmt_stat(mx))
                    it.setText(base + 2, self._fmt_stat(av))

                    # ΔT: Avg(temp) - Avg(ambient). Ambient itself shows 0.0.
                    dt_txt = ""
                    try:
                        if is_temp:
                            if is_ambient:
                                dt_txt = "0.0"
                            else:
                                amb_avg = rt.get("ambient_avg")
                                amb_avg = float(amb_avg) if amb_avg is not None else float("nan")
                                av_f = float(av)
                                if math.isfinite(av_f) and math.isfinite(amb_avg):
                                    dt_txt = self._fmt_stat(av_f - amb_avg)
                    except Exception:
                        dt_txt = ""
                    it.setText(base + 3, str(dt_txt))

                    for col in range(base, base + 4):
                        try:
                            it.setTextAlignment(col, Qt.AlignRight | Qt.AlignVCenter)
                        except Exception:
                            pass

        # Keep the header readable if many columns; rely on horizontal scrollbar.
        try:
            tree.header().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            tree.header().setStretchLastSection(False)
        except Exception:
            pass

        return tree

    def _configure_tree_layout(self, tree: QTreeWidget) -> None:
        """Layout rules:

        - For 2–3 results: try to fit the dialog width (no horizontal scrollbar).
        - For 4+ results: allow horizontal scrolling (consistent spacing).
        """
        try:
            if self._run_count <= int(getattr(self, "_fit_max_results", 5)):
                # Default to AsNeeded; we'll disable it after a successful fit.
                tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
                try:
                    tree.setTextElideMode(Qt.ElideRight)
                except Exception:
                    pass
                try:
                    hdr = tree.header()
                    # We'll explicitly size columns to fit the viewport, so keep them fixed.
                    for c in range(0, int(tree.columnCount())):
                        hdr.setSectionResizeMode(c, QHeaderView.Fixed)
                except Exception:
                    pass
            else:
                tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
                try:
                    # Measurement names can elide; numeric values are handled by the delegate.
                    tree.setTextElideMode(Qt.ElideRight)
                except Exception:
                    pass
                try:
                    hdr = tree.header()
                    for c in range(0, int(tree.columnCount())):
                        hdr.setSectionResizeMode(c, QHeaderView.Interactive)
                except Exception:
                    pass
        except Exception:
            pass

    def _fit_columns_if_needed(self, tree: QTreeWidget) -> None:
        try:
            self._apply_4_baseline_layout(tree)
            self._update_header_widths(tree)
            self._update_header_scrollbar_spacer(tree)
        except Exception:
            pass

    def _min_stat_width(self, tree: QTreeWidget) -> int:
        try:
            pad_x = 0
            try:
                pad_x = int(getattr(self, "_cell_pad_x", 0) or 0)
            except Exception:
                pad_x = 0
            min_stat = 46
            fm = QFontMetrics(tree.font())
            sample = "0000.0"
            return max(int(min_stat), int(fm.horizontalAdvance(sample) + 12 + (2 * int(pad_x))))
        except Exception:
            return 76

    def _apply_4_baseline_layout(self, tree: QTreeWidget) -> None:
        """Layout rules per UX:

        - Popup size is constant.
        - Exactly 4 results: no horizontal scrollbar; all content fits.
        - <4 results: same popup size; run blocks align to the right.
        - >4 results: keep the same Measurement→first-run padding as 4 and allow scrolling.
        """
        try:
            run_count = int(getattr(self, "_run_count", 0) or 0)
            cols = int(tree.columnCount())
            if run_count <= 0 or cols <= 1:
                return

            vp_w = int(tree.viewport().width())
            if vp_w <= 0:
                return

            min_stat = int(self._min_stat_width(tree))

            dt_only = bool(getattr(self, "_dt_only_mode", False))
            dt_cols: list[int] = []
            if dt_only:
                dt_cols = [int(1 + (4 * i) + 3) for i in range(int(run_count)) if int(1 + (4 * i) + 3) < int(cols)]

            # Baseline (4 results): keep consistent Measurement→first-run padding.
            meas_base = 220
            stat_w_base = int(min_stat)
            # In ΔT-only mode we intentionally make each ΔT column as wide as a full
            # 4-stat run block (Min/Max/Avg/ΔT) so the visual width matches the full table.
            dt_w_base = int(stat_w_base) * 4
            stat_cols_base = 16  # Always model the 4-run baseline as 16 "stat-units"

            # If the viewport is too narrow, allow scrolling even for 4.
            baseline_total = int(meas_base + (int(stat_cols_base) * int(stat_w_base)))
            if dt_only:
                # 4 runs * (1 ΔT col per run) where each ΔT col == 4 stat units.
                baseline_total = int(meas_base + (4 * int(dt_w_base)))
            baseline_fits = bool(baseline_total <= int(vp_w))

            # Choose behavior by run_count.
            stat_cols = int(len(dt_cols)) if dt_only else int(cols - 1)

            if run_count == 4:
                try:
                    tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff if baseline_fits else Qt.ScrollBarAsNeeded)
                except Exception:
                    pass
                try:
                    hdr = tree.header()
                    for c in range(int(cols)):
                        hdr.setSectionResizeMode(c, QHeaderView.Fixed)
                except Exception:
                    pass

                # Use compact baseline widths (do not expand stat columns).
                tree.setColumnWidth(0, int(meas_base))
                if dt_only:
                    # Only ΔT columns are sized; hidden Min/Max/Avg remain at 0.
                    dt_w = int(dt_w_base)
                    for run_idx in range(int(run_count)):
                        base = int(1 + (4 * int(run_idx)))
                        for off in range(3):
                            col = int(base + off)
                            if col < cols:
                                tree.setColumnWidth(int(col), 0)
                        dt_col = int(base + 3)
                        if dt_col < cols:
                            tree.setColumnWidth(int(dt_col), int(dt_w))
                else:
                    for i in range(int(stat_cols)):
                        tree.setColumnWidth(1 + i, int(stat_w_base))

            elif run_count < 4:
                # Keep stat widths at the 4-run baseline, and push the run blocks right
                # by giving all slack to the Measurement column.
                try:
                    tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
                except Exception:
                    pass
                try:
                    hdr = tree.header()
                    for c in range(int(cols)):
                        hdr.setSectionResizeMode(c, QHeaderView.Fixed)
                except Exception:
                    pass

                stats_total = int(stat_cols) * int(dt_w_base if dt_only else stat_w_base)
                # Right-align: ensure the stats end at the right edge.
                meas_w = int(max(int(meas_base), int(vp_w - int(stats_total))))

                tree.setColumnWidth(0, int(meas_w))
                if dt_only:
                    dt_w = int(dt_w_base)
                    for run_idx in range(int(run_count)):
                        base = int(1 + (4 * int(run_idx)))
                        for off in range(3):
                            col = int(base + off)
                            if col < cols:
                                tree.setColumnWidth(int(col), 0)
                        dt_col = int(base + 3)
                        if dt_col < cols:
                            tree.setColumnWidth(int(dt_col), int(dt_w))
                else:
                    for i in range(int(stat_cols)):
                        tree.setColumnWidth(1 + i, int(stat_w_base))

            else:
                # >4: fixed padding before first run block matches 4-run baseline.
                # Use scrolling for the additional columns to the right.
                try:
                    tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
                except Exception:
                    pass
                try:
                    hdr = tree.header()
                    for c in range(int(cols)):
                        hdr.setSectionResizeMode(c, QHeaderView.Interactive)
                except Exception:
                    pass

                tree.setColumnWidth(0, int(meas_base))
                if dt_only:
                    dt_w = int(dt_w_base)
                    for run_idx in range(int(run_count)):
                        base = int(1 + (4 * int(run_idx)))
                        for off in range(3):
                            col = int(base + off)
                            if col < cols:
                                tree.setColumnWidth(int(col), 0)
                        dt_col = int(base + 3)
                        if dt_col < cols:
                            tree.setColumnWidth(int(dt_col), int(dt_w))
                else:
                    for i in range(int(stat_cols)):
                        tree.setColumnWidth(1 + i, int(stat_w_base))

        except Exception:
            return

    def _apply_fixed_dialog_size(self) -> None:
        """Apply a predictable popup size.

        - 2–3 results: sized to fit the table (no horizontal scrollbar)
        - 4+ results: sized to the 4-run baseline; additional runs scroll
        """
        try:
            scr = None
            try:
                scr = self.screen()
            except Exception:
                scr = None
            if scr is None:
                try:
                    scr = QApplication.primaryScreen()
                except Exception:
                    scr = None

            max_w = 0
            max_h = 0
            if scr is not None:
                try:
                    ag = scr.availableGeometry()
                    max_w = max(0, int(ag.width()) - 80)
                    max_h = max(0, int(ag.height()) - 80)
                except Exception:
                    max_w = 0
                    max_h = 0

            # Size derived from baseline column widths.
            # Only grow the popup when 4+ results are selected:
            # - 2-3 results: size to exactly fit those stat blocks (no horizontal scrollbar)
            # - 4+ results: size to the 4-run baseline; additional runs scroll
            pad_x = 0
            try:
                pad_x = int(getattr(self, "_cell_pad_x", 0) or 0)
            except Exception:
                pad_x = 0

            min_stat = 76
            try:
                fm = QFontMetrics(self.font())
                sample = "0000.0"
                min_stat = max(46, int(fm.horizontalAdvance(sample) + 12 + (2 * int(pad_x))))
            except Exception:
                pass

            meas_w = 220
            rc = 0
            try:
                rc = int(getattr(self, "_run_count", 0) or 0)
            except Exception:
                rc = 0
            rc_for_size = 4 if rc >= 4 else max(1, rc)
            stat_cols = int(4 * int(rc_for_size))
            table_w = int(meas_w + (int(stat_cols) * int(min_stat)))

            # Add dialog layout margins (root uses 14px on both sides).
            target_w = int(table_w + 28 + 2)
            target_h = 500
            if max_w > 0:
                target_w = min(int(target_w), int(max_w))
            if max_h > 0:
                target_h = min(int(target_h), int(max_h))

            # Avoid forcing a size larger than the screen.
            if max_w > 0:
                target_w = max(520, min(int(target_w), int(max_w)))
            if max_h > 0:
                target_h = max(420, min(int(target_h), int(max_h)))

            self._base_fixed_w = int(target_w)
            self._base_fixed_h = int(target_h)
            self._screen_max_w = int(max_w)
            self._screen_max_h = int(max_h)

            self.setFixedSize(int(target_w), int(target_h))
        except Exception:
            pass

    def _toggle_settings_panel(self) -> None:
        try:
            sp = getattr(self, "_settings_panel", None)
            btn = getattr(self, "_settings_btn", None)
            if sp is None:
                return

            want_on = bool(btn.isChecked()) if btn is not None else (not sp.isVisible())
            sp.setVisible(want_on)
            self._update_fixed_size_for_settings_panel(want_on)
        except Exception:
            pass

    def _update_fixed_size_for_settings_panel(self, want_on: bool) -> None:
        try:
            base_w = int(getattr(self, "_base_fixed_w", 0) or 0)
            base_h = int(getattr(self, "_base_fixed_h", 0) or 0)
            if base_w <= 0 or base_h <= 0:
                try:
                    base_w = int(self.width())
                    base_h = int(self.height())
                except Exception:
                    return

            extra = 0
            if want_on:
                try:
                    # Body row spacing is 12, plus margins are already included in base_w.
                    spw = int(getattr(self._settings_panel, "maximumWidth", lambda: 0)())
                    if spw <= 0:
                        spw = int(self._settings_panel.sizeHint().width())
                    extra = int(max(170, spw) + 12)
                except Exception:
                    extra = 220

            w = int(base_w + extra)
            max_w = int(getattr(self, "_screen_max_w", 0) or 0)
            if max_w > 0:
                w = min(int(w), int(max_w))

            self.setFixedSize(int(w), int(base_h))
        except Exception:
            pass

    def _render_compare_settings(self) -> None:
        try:
            lab = getattr(self, "_settings_label", None)
            if lab is None:
                return

            def _render_one_settings(s: dict) -> list[str]:
                def g(key: str, default: str = "") -> str:
                    try:
                        v = s.get(key)
                        return str(v).strip() if v is not None else default
                    except Exception:
                        return default

                warm = g("warmup_display") or g("warmup_total_sec")
                logt = g("log_display") or g("log_total_sec")
                stress = g("stress_mode")
                demo = g("furmark_demo")
                res = g("furmark_resolution_display") or g("furmark_resolution")

                lines: list[str] = []
                if warm:
                    lines.append(f"Warm up time: {warm}")
                if logt:
                    lines.append(f"Log time: {logt}")
                if stress:
                    lines.append(f"Stresstest: {stress}")
                if demo:
                    lines.append(f"FurMark demo: {demo}")
                if res:
                    lines.append(f"FurMark resolution: {res}")
                return lines

            blocks: list[str] = []

            # Add a top divider so the first run name has the same subtle
            # grey "border" separation as subsequent run blocks.
            try:
                if self._run_tables:
                    blocks.append("<div style='height:1px; background: rgba(255,255,255,0.08); margin: 4px 0 10px 0;'></div>")
            except Exception:
                pass

            for rt in (self._run_tables or []):
                lbl = str(rt.get("label") or "")
                ts = rt.get("test_settings")

                col = str(rt.get("color") or "").strip()
                if not col.startswith("#"):
                    col = "#EAEAEA"

                safe_lbl = html.escape(lbl)

                blocks.append(
                    "<div style='margin-bottom:8px;'>"
                    f"<span style='color:{col}; font-weight:600;'>{safe_lbl}</span>"
                    "</div>"
                )
                if not isinstance(ts, dict) or not ts:
                    blocks.append("<div style='margin-bottom:10px; color:#9A9A9A;'>No settings recorded for this run.</div>")
                    continue

                lines = _render_one_settings(ts)
                if not lines:
                    blocks.append("<div style='margin-bottom:10px; color:#9A9A9A;'>No settings recorded for this run.</div>")
                    continue

                blocks.append("<div style='margin-bottom:10px;'>" + "<br>".join(lines) + "</div>")

                # Divider between runs (subtle)
                blocks.append("<div style='height:1px; background: rgba(255,255,255,0.08); margin: 8px 0 10px 0;'></div>")

            # Remove trailing divider if present
            try:
                if blocks and "height:1px" in blocks[-1]:
                    blocks = blocks[:-1]
            except Exception:
                pass

            if not blocks:
                lab.setText("<span style='color:#9A9A9A;'>No settings recorded for these runs.</span>")
                return

            lab.setText("".join(blocks))
        except Exception:
            try:
                self._settings_label.setText("<span style='color:#9A9A9A;'>No settings recorded for these runs.</span>")
            except Exception:
                pass

    def _fit_columns_to_view(self, tree: QTreeWidget) -> bool:
        """Try to fit all columns into the viewport.

        Returns True if the sum of widths is <= viewport width (so nothing is clipped),
        otherwise False (caller should allow horizontal scrolling).
        """
        try:
            cols = int(tree.columnCount())
            if cols <= 1:
                return True

            # Viewport width excludes scrollbars; exactly what we want to fill.
            avail = int(tree.viewport().width())
            if avail <= 0:
                return False

            dt_only = bool(getattr(self, "_dt_only_mode", False))
            run_count = int(getattr(self, "_run_count", 0) or 0)
            dt_cols: list[int] = []
            if dt_only:
                dt_cols = [int(1 + (4 * i) + 3) for i in range(int(run_count)) if int(1 + (4 * i) + 3) < int(cols)]
            stat_cols = int(len(dt_cols)) if dt_only else int(cols - 1)
            # Minimum stat width should be wide enough to display typical numeric values.
            # Keep it reasonably small so 5 results can still fit; if it can't fit,
            # we fall back to horizontal scrolling.
            min_stat = 46
            pad_x = 0
            try:
                pad_x = int(getattr(self, "_cell_pad_x", 0) or 0)
            except Exception:
                pad_x = 0
            try:
                fm = QFontMetrics(tree.font())
                # Typical formatting is one decimal place; allow 5+ digits without truncation.
                sample = "0000.0"
                # +12 covers base item padding; +2*pad_x accounts for per-cell delegate padding.
                min_stat = max(min_stat, int(fm.horizontalAdvance(sample) + 12 + (2 * int(pad_x))))
            except Exception:
                pass
            # Keep measurement column compact so stats start earlier.
            if int(getattr(self, "_run_count", 0)) >= 5:
                # For 5 results, be more aggressive: elide long measurement names.
                meas_floor = 110
                meas_min = 130
                meas_max = 220
            else:
                meas_floor = 140
                meas_min = 180
                meas_max = 320

            # Start from a proportional guess but respect constraints.
            # Ensure we can allocate at least min_stat to each stat column.
            min_needed = meas_floor + (stat_cols * (int(min_stat) * 4 if dt_only else int(min_stat)))
            if avail < min_needed:
                # Too narrow to fit even at minimums; tell caller to allow scroll.
                return False

            meas_w = int(avail * (0.24 if int(getattr(self, "_run_count", 0)) >= 5 else 0.28))
            meas_w = max(meas_min, min(meas_max, meas_w))
            max_meas = max(meas_floor, avail - (stat_cols * min_stat))
            meas_w = min(meas_w, max_meas)
            meas_w = max(meas_floor, min(meas_w, meas_max, avail))

            remaining = max(0, avail - meas_w)
            stat_w = max(min_stat, int(remaining / stat_cols))

            # Make sum exact by giving remainder to the last stat column.
            used = meas_w + (stat_w * stat_cols)
            extra = avail - used

            tree.setColumnWidth(0, int(meas_w))

            if dt_only:
                # Keep hidden columns at 0 and only size ΔT columns.
                for run_idx in range(int(run_count)):
                    base = int(1 + (4 * int(run_idx)))
                    for off in range(3):
                        col = int(base + off)
                        if col < cols:
                            tree.setColumnWidth(int(col), 0)

                for i, col in enumerate(dt_cols):
                    # Make each ΔT column as wide as a full run block (4 stats).
                    w = int((stat_w * 4) + (extra if i == len(dt_cols) - 1 else 0))
                    tree.setColumnWidth(int(col), max(int(min_stat) * 4, int(w)))
            else:
                for i in range(stat_cols):
                    w = int(stat_w + (extra if i == stat_cols - 1 else 0))
                    tree.setColumnWidth(1 + i, max(min_stat, w))
            # Successful fit if we did not exceed viewport.
            if dt_only:
                final_used = int(meas_w) + sum(int(tree.columnWidth(int(c))) for c in dt_cols)
            else:
                final_used = int(meas_w) + sum(int(tree.columnWidth(1 + i)) for i in range(stat_cols))
            return bool(final_used <= avail)
        except Exception:
            return False

    def _update_header_scrollbar_spacer(self, tree: QTreeWidget) -> None:
        """Reserve header space only when the tree shows a vertical scrollbar."""
        try:
            spacer = getattr(self, "_hdr_sb_spacer", None)
            if spacer is None:
                return
            vsb = tree.verticalScrollBar()
            needed = False
            try:
                needed = int(vsb.maximum()) > 0
            except Exception:
                needed = False
            if needed:
                try:
                    w = int(tree.style().pixelMetric(QStyle.PM_ScrollBarExtent))
                except Exception:
                    w = 16
                spacer.setFixedWidth(max(0, int(w)))
            else:
                spacer.setFixedWidth(0)
        except Exception:
            pass

    def _build_combined_header(self, tree: QTreeWidget) -> QScrollArea:
        """Build a two-row header widget aligned to the combined tree.

        Row 0: Run name spanning its 4 stat columns.
        Row 1: Min / Max / Avg / ΔT labels.
        """
        run_tables = list(self._run_tables or [])

        sc = QScrollArea()
        sc.setFrameShape(QFrame.NoFrame)
        # IMPORTANT: keep content at its intrinsic width so horizontal scrolling
        # matches the tree's horizontal scrolling.
        sc.setWidgetResizable(False)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sc.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        frame = QFrame()
        frame.setFrameShape(QFrame.NoFrame)
        sc.setWidget(frame)
        self._hdr_frame = frame

        from PySide6.QtWidgets import QGridLayout

        gl = QGridLayout(frame)
        self._hdr_grid_layout = gl
        gl.setContentsMargins(0, 0, 0, 0)
        gl.setHorizontalSpacing(0)
        gl.setVerticalSpacing(0)

        pad_extra = 0
        try:
            pad_extra = int(getattr(self, "_cell_pad_x", 0) or 0)
        except Exception:
            pad_extra = 0

        def _mk_lbl(
            text: str,
            *,
            color: str = "#9A9A9A",
            bold: bool = True,
            align=Qt.AlignCenter,
            pad_l: int = 6,
            pad_r: int = 6,
        ) -> QLabel:
            lab = QLabel(text)
            lab.setAlignment(align)
            lab.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            fw = 600 if bold else 400
            lab.setStyleSheet(
                f"background:#151515; color:{color}; font-weight:{fw}; padding:6px {int(pad_r)}px 6px {int(pad_l)}px; border:none;"
            )
            return lab

        # Measurement header spans both rows (rowSpan=2).
        self._hdr_measurement_lbl = _mk_lbl(
            "Measurement",
            color="#9A9A9A",
            bold=True,
            align=Qt.AlignLeft | Qt.AlignVCenter,
        )
        gl.addWidget(self._hdr_measurement_lbl, 0, 0, 2, 1)

        self._hdr_run_lbls: list[QLabel] = []
        self._hdr_stat_lbls: list[QLabel] = []

        # Run name headers (row 0) + stat headers (row 1)
        for run_idx, rt in enumerate(run_tables):
            run_label = str(rt.get("label") or "")
            run_color = str(rt.get("color") or "#EAEAEA")
            start_col = 1 + (4 * int(run_idx))

            run_lab = _mk_lbl(
                run_label,
                color=run_color,
                bold=True,
                align=Qt.AlignCenter,
            )
            try:
                run_lab.setProperty("_full_text", str(run_label))
                run_lab.setWordWrap(True)
            except Exception:
                pass
            self._hdr_run_lbls.append(run_lab)
            gl.addWidget(run_lab, 0, start_col, 1, 4)

            for off, stat_name in enumerate(["Min", "Max", "Avg", "ΔT"]):
                # Right-align to better match right-aligned numeric values.
                stat_lab = _mk_lbl(
                    stat_name,
                    color="#9A9A9A",
                    bold=True,
                    align=Qt.AlignRight | Qt.AlignVCenter,
                    pad_l=6 + int(pad_extra),
                    pad_r=6 + int(pad_extra),
                )
                self._hdr_stat_lbls.append(stat_lab)
                gl.addWidget(stat_lab, 1, start_col + off, 1, 1)

        # Header/body separators are drawn by container overlay lines.

        # Keep height compact and similar to QHeaderView.
        try:
            frame.setFixedHeight(64)
        except Exception:
            pass

        return sc

    def _update_header_widths(self, tree: QTreeWidget) -> None:
        """Synchronize custom header cell widths with the tree's column widths."""
        try:
            if not hasattr(self, "_hdr_measurement_lbl"):
                return

            dt_only = bool(getattr(self, "_dt_only_mode", False))

            # Ensure the header grid columns exactly match the tree's visible column widths.
            # This is critical in ΔT-only mode where Min/Max/Avg columns are hidden; without
            # this, the grid can allocate phantom width and shift the run blocks right.
            try:
                gl = getattr(self, "_hdr_grid_layout", None)
                if gl is not None:
                    for c in range(int(tree.columnCount())):
                        w = 0
                        try:
                            if tree.isColumnHidden(int(c)):
                                w = 0
                            else:
                                w = int(tree.columnWidth(int(c)))
                        except Exception:
                            w = int(tree.columnWidth(int(c))) if int(c) < int(tree.columnCount()) else 0
                        try:
                            gl.setColumnMinimumWidth(int(c), max(0, int(w)))
                            gl.setColumnStretch(int(c), 0)
                        except Exception:
                            pass
            except Exception:
                pass

            def _split_two_lines(text: str, fm: QFontMetrics, max_w: int) -> tuple[str, str]:
                s = " ".join(str(text or "").replace("_", " ").split())
                if not s:
                    return "", ""
                if int(max_w) <= 0 or int(fm.horizontalAdvance(s)) <= int(max_w):
                    return s, ""

                parts = [p for p in s.split(" ") if p]
                best = 0
                for i in range(1, len(parts)):
                    left = " ".join(parts[:i])
                    if int(fm.horizontalAdvance(left)) <= int(max_w):
                        best = i

                if best > 0:
                    return " ".join(parts[:best]), " ".join(parts[best:])

                # Fallback: hard split by character.
                for i in range(1, len(s) + 1):
                    if int(fm.horizontalAdvance(s[:i])) > int(max_w):
                        cut = max(1, i - 1)
                        return s[:cut], s[cut:].lstrip()
                return s, ""

            w0 = int(tree.columnWidth(0)) if tree.columnCount() > 0 else 0
            try:
                self._hdr_measurement_lbl.setFixedWidth(max(0, w0))
            except Exception:
                pass

            # Numeric columns are 1..N. We keep per-column widths for Min/Max/Avg/ΔT and
            # set each run header width to the sum of its four columns.
            num_runs = int(len(getattr(self, "_hdr_run_lbls", []) or []))

            # Per-stat labels: 4 per run.
            stat_labels = list(getattr(self, "_hdr_stat_lbls", []) or [])
            for idx, lab in enumerate(stat_labels):
                col = 1 + int(idx)
                if col >= tree.columnCount():
                    break
                try:
                    if dt_only and ((int(col - 1) % 4) != 3):
                        # Hidden Min/Max/Avg columns must contribute zero width to
                        # keep header aligned with the visible tree columns.
                        lab.setFixedWidth(0)
                    else:
                        lab.setFixedWidth(int(tree.columnWidth(col)))
                except Exception:
                    pass

            # Run-name spanning widths.
            for run_idx, run_lab in enumerate(getattr(self, "_hdr_run_lbls", []) or []):
                start = 1 + (4 * int(run_idx))
                if start >= tree.columnCount():
                    break
                span_w = 0
                # Sum visible widths (hidden columns should be 0 in ΔT-only).
                for off in range(4):
                    col = start + off
                    if col < tree.columnCount():
                        if dt_only and (off != 3):
                            continue
                        span_w += int(tree.columnWidth(col))
                try:
                    run_lab.setFixedWidth(max(0, span_w))
                except Exception:
                    pass

                # Run label text handling: elide in ΔT-only; otherwise allow 2-line split.
                try:
                    full = run_lab.property("_full_text")
                    full = str(full) if full is not None else str(run_lab.text() or "")
                    fm = QFontMetrics(run_lab.font())
                    if dt_only:
                        try:
                            run_lab.setWordWrap(True)
                        except Exception:
                            pass
                        a, b = _split_two_lines(full, fm, int(span_w) - 12)
                        new_text = a if not b else f"{a}\n{b}"
                    else:
                        a, b = _split_two_lines(full, fm, int(span_w) - 12)
                        new_text = a if not b else f"{a}\n{b}"

                    if str(run_lab.text() or "") != str(new_text):
                        run_lab.setText(str(new_text))
                except Exception:
                    pass

            # Set the header content width to match the *visible* table columns width.
            total_w = 0
            try:
                for c in range(int(tree.columnCount())):
                    try:
                        if tree.isColumnHidden(int(c)):
                            continue
                    except Exception:
                        pass
                    total_w += int(tree.columnWidth(c))
            except Exception:
                total_w = 0

            try:
                fr = getattr(self, "_hdr_frame", None)
                if fr is not None and total_w > 0:
                    fr.setFixedWidth(int(total_w))
            except Exception:
                pass

            # Prevent stat header row from getting clipped when run labels wrap.
            # Compute required header height from actual label size hints.
            try:
                run_row_h = 0
                for lab in (getattr(self, "_hdr_run_lbls", []) or []):
                    try:
                        run_row_h = max(int(run_row_h), int(lab.sizeHint().height()))
                    except Exception:
                        pass

                stat_row_h = 0
                for lab in (getattr(self, "_hdr_stat_lbls", []) or []):
                    try:
                        if hasattr(lab, "isVisible") and not bool(lab.isVisible()):
                            continue
                        stat_row_h = max(int(stat_row_h), int(lab.sizeHint().height()))
                    except Exception:
                        pass

                target_h = int(max(64, int(run_row_h) + int(stat_row_h)))
                fr = getattr(self, "_hdr_frame", None)
                if fr is not None:
                    fr.setFixedHeight(int(target_h))
                try:
                    sc = getattr(self, "_hdr_sc", None)
                    if sc is not None:
                        sc.setFixedHeight(int(target_h))
                except Exception:
                    pass
            except Exception:
                pass

            # Header/body separators are handled by container overlay lines.
        except Exception:
            pass

    def _autosize_columns(self) -> None:
        try:
            for tw in self.findChildren(QTreeWidget):
                try:
                    dt_only = bool(getattr(self, "_dt_only_mode", False))

                    if dt_only:
                        # ΔT-only: do not force-fit (it would shrink columns and hide run names).
                        pass
                    elif getattr(self, "_run_count", 0) <= int(getattr(self, "_fit_max_results", 5)):
                        # Force-fit all columns into view (no horizontal scrolling).
                        self._fit_columns_to_view(tw)
                    else:
                        # Scrollable mode: numeric columns sized to contents.
                        if dt_only:
                            run_count = int(getattr(self, "_run_count", 0) or 0)
                            cols = int(tw.columnCount())
                            for run_idx in range(int(run_count)):
                                dt_col = int(1 + (4 * int(run_idx)) + 3)
                                if int(dt_col) < int(cols):
                                    tw.resizeColumnToContents(int(dt_col))
                        else:
                            for c in range(1, int(tw.columnCount())):
                                tw.resizeColumnToContents(c)
                except Exception:
                    pass
            try:
                # After autosizing, re-sync custom header widths.
                for tw in self.findChildren(QTreeWidget):
                    self._update_header_widths(tw)
                    self._update_header_scrollbar_spacer(tw)
            except Exception:
                pass
        except Exception:
            pass
