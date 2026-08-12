import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QScrollArea

from ui.graph_preview.ui_legend_stats_popup import LegendStatsPopup


class LegendSettingsClipboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = QApplication.instance() or QApplication([])

    def test_build_settings_clipboard_text_formats_single_run_settings(self):
        popup = LegendStatsPopup.__new__(LegendStatsPopup)
        popup._test_settings = {
            "warmup_display": "10 min",
            "log_display": "30 min",
            "stress_mode": "FurMark",
            "stress_gpu": True,
            "furmark_demo": "Demo",
            "furmark_resolution_display": "1920x1080",
            "selected_sensor_devices": ["CPU", "GPU"],
        }

        text = popup._build_settings_clipboard_text()

        self.assertIn("Warm up time: 10 min", text)
        self.assertIn("Log time: 30 min", text)
        self.assertIn("Stresstest: FurMark", text)
        self.assertIn("FurMark resolution: 1920x1080", text)
        self.assertIn("- CPU", text)
        self.assertIn("- GPU", text)

    def test_build_settings_clipboard_text_hides_prime95_when_cpu_stress_disabled(self):
        popup = LegendStatsPopup.__new__(LegendStatsPopup)
        popup._test_settings = {
            "warmup_display": "10 min",
            "log_display": "30 min",
            "stress_mode": "GPU only",
            "stress_cpu": False,
            "stress_gpu": True,
            "furmark_demo": "Demo",
            "furmark_resolution_display": "1920x1080",
            "prime95_torture_settings_display": "Small FFTs, 1 thread",
            "prime95_inferred_preset_name": "Small FFTs",
            "selected_sensor_devices": ["GPU"],
        }

        text = popup._build_settings_clipboard_text()

        self.assertIn("Stresstest: GPU only", text)
        self.assertNotIn("Prime95", text)

    def test_single_result_settings_panel_uses_scroll_area(self):
        popup = LegendStatsPopup(
            None,
            title="Test",
            columns=["CPU Package"],
            active_set={"CPU Package"},
            color_for=lambda _: "#ffffff",
            on_toggle=lambda *args, **kwargs: None,
            stats_map={"CPU Package": (30.0, 60.0, 45.0)},
            test_settings={
                "warmup_display": "10 min",
                "log_display": "30 min",
                "stress_mode": "CPU only",
                "prime95_torture_settings_display": "A very long Prime95 config line " * 20,
            },
        )

        scroll_areas = popup._settings_panel.findChildren(QScrollArea)
        self.assertTrue(scroll_areas, "single-result settings panel should contain a scroll area")

    def test_compare_result_settings_panel_labels_are_bold(self):
        from ui.graph_preview.ui_compare_legend_stats_popup import CompareLegendStatsPopup

        popup = CompareLegendStatsPopup.__new__(CompareLegendStatsPopup)
        popup._theme = {
            "text": "#111111",
            "secondary_text": "#666666",
            "empty_text": "#777777",
            "dialog_bg": "#ffffff",
            "dialog_border": "#dddddd",
            "panel_bg": "#f5f5f5",
            "header_bg": "#f5f5f5",
            "hover_bg": "#eeeeee",
            "button_bg": "#ffffff",
            "button_border": "#cccccc",
            "button_hover_bg": "#f0f0f0",
            "button_hover_border": "#bbbbbb",
            "button_pressed_bg": "#e8e8e8",
            "button_checked_bg": "#dce9dc",
            "button_checked_border": "#afc6af",
            "scroll_groove": "rgba(0,0,0,0.08)",
            "scroll_handle": "rgba(0,0,0,0.28)",
            "scroll_handle_hover": "rgba(0,0,0,0.38)",
            "scroll_handle_pressed": "rgba(0,0,0,0.48)",
        }
        popup._run_tables = [{
            "label": "Run 1",
            "color": "#ff0000",
            "test_settings": {
                "warmup_display": "10 min",
                "log_display": "30 min",
                "stress_mode": "CPU only",
                "stress_cpu": True,
                "stress_gpu": False,
                "selected_sensor_devices": ["CPU"],
            },
        }]
        popup._settings_label = type("DummyLabel", (), {"setText": lambda self, text: setattr(self, "text", text)})()

        popup._render_compare_settings()

        self.assertIn("<b>Warm up time:</b>", popup._settings_label.text)
        self.assertIn("<b>Log time:</b>", popup._settings_label.text)
        self.assertIn("<b>Stresstest:</b>", popup._settings_label.text)


if __name__ == "__main__":
    unittest.main()
