from PySide6.QtWidgets import QApplication, QScrollBar
from ui.graph_preview.ui_legend_stats_popup import LegendStatsPopup

app = QApplication.instance() or QApplication([])
cols = [
    'CPU Package [°C]', 'GPU Temperature [°C]', 'CPU Package Power [W]',
    'GPU Power (Total) [W]', 'Drive Remaining Life [%]', 'Write Activity [%]',
    'Total Activity [%]', 'Read Activity [%]', 'Write Rate [MB/s]', 'Read Rate [MB/s]',
    'Write Total [MB]', 'Read Total [MB]'
]
stats = {
    'CPU Package [°C]': (70.0, 77.0, 73.7),
    'GPU Temperature [°C]': (41.0, 41.0, 41.0),
    'CPU Package Power [W]': (103.5, 103.9, 103.7),
    'GPU Power (Total) [W]': (22.4, 22.4, 22.4),
    'Drive Remaining Life [%]': (100.0, 100.0, 100.0),
    'Write Activity [%]': (0.0, 0.1, 0.0),
    'Total Activity [%]': (0.0, 0.1, 0.0),
    'Read Activity [%]': (0.0, 0.0, 0.0),
    'Write Rate [MB/s]': (0.0, 0.7, 0.3),
    'Read Rate [MB/s]': (0.0, 0.0, 0.0),
    'Write Total [MB]': (428949.0, 428950.0, 428949.7),
    'Read Total [MB]': (262330.0, 262330.0, 262330.0),
}
p = LegendStatsPopup(None, title='Legend and Stats for Temperature (°C)', columns=cols, active_set=set(cols), color_for=lambda _:'#ffffff', on_toggle=lambda *_: None, stats_map=stats, room_temperature=None, test_settings=None, theme_mode='light')
p.show()
for _ in range(5):
    app.processEvents()
    p._autosize_to_content()
    app.processEvents()

print('popup', p.width(), p.height())
print('tree geom', p.tree.geometry().getRect())
print('viewport', p.tree.viewport().width(), p.tree.viewport().height())
print('header length', p.tree.header().length())
print('cols', [p.tree.columnWidth(i) for i in range(p.tree.columnCount())])
print('hbar max', p.tree.horizontalScrollBar().maximum(), 'page', p.tree.horizontalScrollBar().pageStep(), 'visible', p.tree.horizontalScrollBar().isVisible())
for sb in p.findChildren(QScrollBar):
    print('scrollbar', sb.objectName(), sb.orientation().value, sb.maximum(), sb.isVisible(), sb.geometry().getRect(), type(sb.parent()).__name__ if sb.parent() else None)
