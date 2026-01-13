# ui_legend_stats_popup.py
"""Legend and statistics popup dialog for graph preview."""

import math
from typing import Optional, Callable

from PySide6.QtCore import QTimer, Qt, QEvent, QMimeData
from PySide6.QtGui import QPixmap, QIcon, QFontMetrics
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QHeaderView,
    QAbstractItemView,
    QToolButton,
    QLayout,
    QFrame,
    QSizePolicy,
    QPushButton,
    QApplication,
)


class LegendStatsPopup(QDialog):
    """
    Popup to toggle which sensors are drawn and show stats (min/max/avg).
    Stays open when clicking inside; GraphPreview installs a global event filter
    to close it when clicking outside.
    """

    def __init__(
        self,
        parent,
        *,
        title: str,
        columns: list[str],
        active_set: set[str],
        color_for: Callable[[str], str],
        on_toggle: Callable[[str, bool, Optional[list[str]]], None],
        stats_map: dict[str, tuple[float, float, float]] | None = None,
        room_temperature: Optional[float] = None,
        on_close: Optional[Callable[[], None]] = None,
    ):
        super().__init__(parent)

        # Not Qt.Popup (we close via global click filter)
        self.setWindowFlag(Qt.Tool, True)
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setModal(False)

        self._on_close = on_close

        self._columns = list(columns or [])
        self._color_for = color_for
        self._on_toggle = on_toggle
        self._stats_map = stats_map or {}
        self._room_temperature = room_temperature
        self._building = False

        root = QVBoxLayout(self)
        root.setSizeConstraint(QLayout.SetDefaultConstraint)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        # Title row + close button
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)

        title_area = QLabel(title)
        title_area.setStyleSheet("color:#EAEAEA; font-weight:600; font-size:13px;")

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

        self._title_full = title
        self._title_label = title_area
        self._close_btn = close_btn

        # Let title shrink instead of forcing the dialog wider than the table
        title_area.setMinimumWidth(0)
        title_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        title_row.addWidget(title_area)
        title_row.addStretch(1)
        title_row.addWidget(close_btn)
        root.addLayout(title_row)

        # Copy Table Button
        copy_btn_row = QHBoxLayout()
        copy_btn_row.setContentsMargins(0, 0, 0, 0)
        
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
        
        copy_btn_row.addStretch(1)
        copy_btn_row.addWidget(copy_btn)
        root.addLayout(copy_btn_row)

        # Table
        self.tree = QTreeWidget()
        # Add Room and Delta columns if room temperature is provided
        if self._room_temperature is not None:
            self.tree.setColumnCount(6)
            self.tree.setHeaderLabels(["Measurement", "Min", "Max", "Avg", "Room", "Delta"])
        else:
            self.tree.setColumnCount(4)
            self.tree.setHeaderLabels(["Measurement", "Min", "Max", "Avg"])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setSortingEnabled(False)
        self.tree.setSelectionMode(QAbstractItemView.NoSelection)
        self.tree.setFocusPolicy(Qt.NoFocus)
        self.tree.setEditTriggers(QAbstractItemView.NoEditTriggers)

        self.tree.setFrameShape(QFrame.NoFrame)
        self.tree.setLineWidth(0)

        hdr = self.tree.header()
        hdr.setSectionsClickable(False)
        hdr.setSectionsMovable(False)
        hdr.setOffset(0)
        hdr.setContentsMargins(0, 0, 0, 0)
        hdr.setStyleSheet("padding:0px; margin:0px; border:none;")
        hdr.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hdr.setStretchLastSection(False)

        # Give "Measurement" a fixed wider width so there's more gap before stats
        hdr.setSectionResizeMode(0, QHeaderView.Fixed)
        self.tree.setColumnWidth(0, 420)

        num_stat_cols = 5 if self._room_temperature is not None else 3
        for c in range(1, num_stat_cols + 1):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeToContents)

        self.tree.itemChanged.connect(self._item_changed)

        PAD = 14  # must match root.setContentsMargins(14,14,14,14)
        tree_wrap = QVBoxLayout()
        tree_wrap.setContentsMargins(-PAD, 0, -PAD, 0)  # bleed into dialog padding
        tree_wrap.setSpacing(0)
        tree_wrap.addWidget(self.tree, 1)
        root.addLayout(tree_wrap, 1)

        # One-press toggle: click anywhere in the row toggles the checkbox
        self.tree.viewport().installEventFilter(self)
        self.tree.installEventFilter(self)

        self.setSizeGripEnabled(False)

        # clear inherited size constraints
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)

        self.tree.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.tree.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        # Styling
        self.setStyleSheet(
            """
            QDialog { background: #1A1A1A; border: 1px solid #2A2A2A; border-radius: 10px; }
            QLabel { background: transparent; }

            QTreeWidget { background: transparent; border: none; color: #EAEAEA; outline: none; }

            QTreeWidget::item {
                padding: 8px 8px;
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
                padding: 8px 14px;
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

            /* ---------- Scrollbar hidden by default ---------- */
            QTreeWidget QScrollBar:vertical {
                background: transparent;
                width: 8px;
                margin: 0px;
            }

            QTreeWidget QScrollBar::groove:vertical {
                background: transparent;
                border-radius: 4px;
            }

            QTreeWidget QScrollBar::handle:vertical {
                background: transparent;
                border-radius: 4px;
                min-height: 28px;
            }

            QTreeWidget QScrollBar::add-line:vertical,
            QTreeWidget QScrollBar::sub-line:vertical {
                height: 0px;
                width: 0px;
                background: transparent;
            }
            QTreeWidget QScrollBar::add-page:vertical,
            QTreeWidget QScrollBar::sub-page:vertical {
                background: transparent;
            }

            /* ---------- Show scrollbar when hovering ---------- */
            QTreeWidget:hover QScrollBar::groove:vertical {
                background: rgba(255,255,255,0.06);
            }

            QTreeWidget:hover QScrollBar::handle:vertical {
                background: rgba(220,220,220,0.55);
            }

            QTreeWidget:hover QScrollBar::handle:vertical:hover {
                background: rgba(220,220,220,0.70);
            }

            QTreeWidget:hover QScrollBar::handle:vertical:pressed {
                background: rgba(220,220,220,0.85);
            }
            """
        )

        self._rebuild(active_set or set())

    def closeEvent(self, event):
        try:
            if callable(getattr(self, "_on_close", None)):
                self._on_close()
        except Exception:
            pass
        super().closeEvent(event)

    # ---------- Copy table to clipboard ----------
    def _copy_table_to_clipboard(self) -> None:
        """Copy the table data to clipboard in HTML table format for pasting into Word."""
        try:
            # Build header row
            if self._room_temperature is not None:
                headers = ["Sensor", "Min", "Max", "Avg", "Room", "Delta"]
            else:
                headers = ["Sensor", "Min", "Max", "Avg"]
            
            # Build plain text version (tab-delimited)
            text_lines = ["\t".join(headers)]
            
            # Build complete HTML document for better clipboard compatibility
            html_parts = [
                '<html>',
                '<head>',
                '<meta charset="utf-8">',
                '<style>',
                'table { border-collapse: collapse; width: auto; table-layout: auto; font-size: 9pt; }',
                'th, td { border: 1px solid black; padding: 0px 8px; text-align: left; white-space: nowrap; line-height: 0.8; font-size: 9pt; }',
                'td.number { text-align: right; }',
                'th:first-child, td:first-child { min-width: 200px; }',
                '</style>',
                '</head>',
                '<body>',
                '<table>',
                '<thead><tr style="background-color:#ebebeb;">'
            ]
            for i, header in enumerate(headers):
                if i == 0:
                    html_parts.append(f'<th style="background-color:#ebebeb;border:1px solid black;padding:0px 8px;font-weight:bold;color:#000000;white-space:nowrap;min-width:200px;line-height:0.8;font-size:9pt;">{header}</th>')
                else:
                    html_parts.append(f'<th style="background-color:#ebebeb;border:1px solid black;padding:0px 8px;font-weight:bold;color:#000000;white-space:nowrap;min-width:50px;line-height:0.8;font-size:9pt;">{header}</th>')
            html_parts.append('</tr></thead><tbody>')
            
            # Gather all rows in display order
            for i in range(self.tree.topLevelItemCount()):
                item = self.tree.topLevelItem(i)
                if item is None:
                    continue
                
                # Get sensor name
                sensor_name = str(item.data(0, Qt.UserRole) or item.text(0) or "").strip()
                
                # Get stats
                min_val = item.text(1)
                max_val = item.text(2)
                avg_val = item.text(3)
                
                if self._room_temperature is not None:
                    room_val = item.text(4)
                    delta_val = item.text(5)
                    row = [sensor_name, min_val, max_val, avg_val, room_val, delta_val]
                else:
                    row = [sensor_name, min_val, max_val, avg_val]
                
                # Add to plain text
                text_lines.append("\t".join(row))
                
                # Add to HTML table
                html_parts.append('<tr>')
                for j, cell in enumerate(row):
                    # First column (sensor name) gets normal td, numbers get number class
                    if j == 0:
                        html_parts.append(f'<td style="padding:0px 8px;white-space:nowrap;min-width:200px;line-height:0.8;font-size:9pt;">{cell}</td>')
                    else:
                        html_parts.append(f'<td class="number" style="padding:0px 8px;white-space:nowrap;line-height:0.8;font-size:9pt;">{cell}</td>')
                html_parts.append('</tr>')
            
            html_parts.extend(['</tbody></table>', '</body>', '</html>'])
            
            # Prepare clipboard with both plain text and HTML
            table_text = "\n".join(text_lines)
            table_html = "".join(html_parts)
            
            mime_data = QMimeData()
            mime_data.setText(table_text)
            mime_data.setHtml(table_html)
            
            clipboard = QApplication.clipboard()
            clipboard.setMimeData(mime_data)
            
            # Visual feedback - briefly change button text
            if hasattr(self, "sender") and self.sender():
                btn = self.sender()
                if isinstance(btn, QPushButton):
                    original_text = btn.text()
                    btn.setText("✓ Copied!")
                    QTimer.singleShot(1500, lambda: btn.setText(original_text))
        except Exception as e:
            # Fail silently or could show an error
            pass

    # ---------- helpers ----------
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
            return f"{v:.1f}"
        except Exception:
            return ""

    def _rebuild(self, active_set: set[str]) -> None:
        self._building = True
        try:
            self.tree.clear()
            aset = set(active_set or [])

            def _avg_for(name: str) -> float:
                t = self._stats_map.get(name)
                if not t or len(t) < 3:
                    return float("-inf")
                try:
                    av = float(t[2])
                    return av if math.isfinite(av) else float("-inf")
                except Exception:
                    return float("-inf")

            ordered = sorted(
                (str(c) for c in (self._columns or [])),
                key=lambda n: (_avg_for(n), n.lower()),
                reverse=True,
            )

            for name in ordered:
                it = QTreeWidgetItem(self.tree)
                it.setText(0, name)
                it.setData(0, Qt.UserRole, name)
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable | Qt.ItemIsEnabled)

                try:
                    it.setIcon(0, self._make_color_icon(self._color_for(name)))
                except Exception:
                    pass

                it.setCheckState(0, Qt.Checked if name in aset else Qt.Unchecked)

                mn, mx, av = self._stats_map.get(name, (float("nan"), float("nan"), float("nan")))
                it.setText(1, self._fmt_stat(mn))
                it.setText(2, self._fmt_stat(mx))
                it.setText(3, self._fmt_stat(av))

                # Add Room and Delta columns if room temperature is provided
                if self._room_temperature is not None:
                    # Room temperature value
                    it.setText(4, self._fmt_stat(self._room_temperature))
                    # Delta (avg - room temperature)
                    try:
                        delta = av - self._room_temperature
                        it.setText(5, self._fmt_stat(delta))
                    except Exception:
                        it.setText(5, "")

                num_stat_cols = 5 if self._room_temperature is not None else 3
                for col in range(1, num_stat_cols + 1):
                    it.setTextAlignment(col, Qt.AlignRight | Qt.AlignVCenter)

        finally:
            self._building = False
        self._reset_view_offsets()
        self._ensure_all_rows_present()

    def _reset_view_offsets(self) -> None:
        try:
            sbh = self.tree.horizontalScrollBar()
            if sbh is not None:
                sbh.setValue(0)
        except Exception:
            pass
        try:
            self.tree.header().setOffset(0)
        except Exception:
            pass


    def _autosize_to_content(self) -> None:
        try:
            self.layout().activate()
            self.tree.doItemsLayout()

            self.tree.setColumnWidth(0, 420)

            for c in (1, 2, 3):
                self.tree.resizeColumnToContents(c)

            sbh = self.tree.horizontalScrollBar()
            if sbh is not None:
                sbh.setValue(0)

            header_len = self.tree.header().length()
            tree_frame = self.tree.frameWidth() * 2
            vscroll_w = 0
            vsb = self.tree.verticalScrollBar()
            if vsb is not None and vsb.isVisible():
                vscroll_w = vsb.sizeHint().width()

            margins = self.layout().contentsMargins()
            ideal_w = header_len + tree_frame + vscroll_w + margins.left() + margins.right() + 2

            rows = self.tree.topLevelItemCount()

            row_h = self.tree.sizeHintForRow(0) if rows else 0
            if row_h <= 0:
                row_h = self.tree.fontMetrics().height() + 10
            row_h = max(22, int(row_h))

            header_h = int(self.tree.header().sizeHint().height())
            frame_h = int(self.tree.frameWidth() * 2)

            # Add a small buffer to avoid bottom-row clipping due to DPI/font rounding.
            tree_h = header_h + rows * row_h + frame_h + 4

            title_h = 0
            if getattr(self, "_title_label", None) is not None:
                title_h = max(title_h, self._title_label.sizeHint().height())
            if getattr(self, "_close_btn", None) is not None:
                title_h = max(title_h, self._close_btn.sizeHint().height())

            spacing = int(self.layout().spacing())
            ideal_h = margins.top() + margins.bottom() + title_h + spacing + tree_h

            screen = self.screen()
            if screen is not None:
                avail = screen.availableGeometry()
                max_w = int(avail.width() * 0.9)
                max_h = int(avail.height() * 0.85)
            else:
                max_w, max_h = 1100, 850

            w = min(int(ideal_w), max_w)

            self.tree.setFixedHeight(tree_h)
            h = int(ideal_h)
            self.setFixedSize(w, h)

            try:
                self.tree.scrollToTop()
                vsb = self.tree.verticalScrollBar()
                hsb = self.tree.horizontalScrollBar()
                if vsb:
                    vsb.setRange(0, 0)
                    vsb.setValue(0)
                if hsb:
                    hsb.setRange(0, 0)
                    hsb.setValue(0)
            except Exception:
                pass

            if getattr(self, "_title_label", None) is not None and getattr(self, "_close_btn", None) is not None:
                fm = QFontMetrics(self._title_label.font())
                close_w = self._close_btn.sizeHint().width()
                avail_title_w = max(10, w - margins.left() - margins.right() - close_w - 12)
                self._title_label.setText(fm.elidedText(self._title_full, Qt.ElideRight, avail_title_w))

            self.setFixedSize(w, h)

        except Exception:
            pass

    def _item_changed(self, item: QTreeWidgetItem, column: int = 0) -> None:
        if self._building or column != 0:
            return

        col = item.data(0, Qt.UserRole)
        checked = item.checkState(0) == Qt.Checked

        # If user is trying to uncheck the last remaining checked item, revert.
        if not checked and self._checked_count() == 0:
            self._building = True
            try:
                item.setCheckState(0, Qt.Checked)
            finally:
                self._building = False
            self._ensure_all_rows_present()
            self._reset_view_offsets()
            return

        self._on_toggle(str(col), checked, None)
        self._ensure_all_rows_present()
        self._reset_view_offsets()

    def _checked_count(self) -> int:
        try:
            n = 0
            for i in range(self.tree.topLevelItemCount()):
                it = self.tree.topLevelItem(i)
                if it is not None and it.checkState(0) == Qt.Checked:
                    n += 1
            return n
        except Exception:
            return 0

    def _active_set_from_ui(self) -> set[str]:
        s = set()
        try:
            for i in range(self.tree.topLevelItemCount()):
                it = self.tree.topLevelItem(i)
                if it is None:
                    continue
                name = str(it.data(0, Qt.UserRole) or it.text(0) or "").strip()
                if name and it.checkState(0) == Qt.Checked:
                    s.add(name)
        except Exception:
            pass
        return s

    def _ensure_all_rows_present(self) -> None:
        try:
            # If Qt ever "loses" rows (or duplicates/blank rows), rebuild from your source list.
            expected = [str(c) for c in (self._columns or [])]
            expected_set = set(expected)

            present: list[str] = []
            for i in range(self.tree.topLevelItemCount()):
                it = self.tree.topLevelItem(i)
                if it is None:
                    continue
                name = str(it.data(0, Qt.UserRole) or it.text(0) or "").strip()
                present.append(name)

            present_set = set(present)
            has_blank = any(not n for n in present)
            has_dupes = len(present) != len(present_set)

            # Rebuild if:
            # - count doesn't match
            # - names don't match expected set
            # - any blank name (Qt glitch)
            # - any duplicates (Qt glitch)
            if (
                self.tree.topLevelItemCount() != len(expected)
                or present_set != expected_set
                or has_blank
                or has_dupes
            ):
                aset = self._active_set_from_ui()
                if not aset and expected:
                    aset = {expected[0]}
                self._rebuild(aset)
                QTimer.singleShot(0, self._autosize_to_content)
        except Exception:
            pass

    def eventFilter(self, obj, event):
        if obj is self.tree.viewport() and event.type() == QEvent.Wheel:
            self._reset_view_offsets()
            return True

        if obj is self.tree and event.type() == QEvent.KeyPress:
            if event.key() in (
                Qt.Key_Up, Qt.Key_Down, Qt.Key_PageUp, Qt.Key_PageDown,
                Qt.Key_Home, Qt.Key_End
            ):
                self._reset_view_offsets()
                return True

        if obj is self.tree.viewport():
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                try:
                    pos = event.position().toPoint()
                except Exception:
                    pos = event.pos()

                item = self.tree.itemAt(pos)
                if item is None:
                    return False

                rect = self.tree.visualItemRect(item)
                if (pos.x() - rect.x()) < 24:
                    return False

                # Prevent unchecking the last remaining sensor
                if item.checkState(0) == Qt.Checked and self._checked_count() == 1:
                    self._reset_view_offsets()
                    return True  # swallow click, do nothing

                item.setCheckState(
                    0, Qt.Unchecked if item.checkState(0) == Qt.Checked else Qt.Checked
                )
                self._reset_view_offsets()
                self._ensure_all_rows_present()
                return True

        return super().eventFilter(obj, event)
