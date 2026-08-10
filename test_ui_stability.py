import re
import unittest
from pathlib import Path


HTML = Path(__file__).parent / "static" / "index.html"


class MobileShellStabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = HTML.read_text()

    def test_document_cannot_overscroll_or_bounce(self):
        self.assertRegex(
            self.text,
            r"html,body\s*\{[^}]*overflow\s*:\s*hidden",
        )
        self.assertRegex(
            self.text,
            r"body\s*\{[^}]*position\s*:\s*fixed",
        )

    def test_app_uses_stable_small_viewport_height(self):
        self.assertRegex(
            self.text,
            r"#app\s*\{[^}]*height\s*:\s*100svh",
        )

    def test_dock_is_anchored_to_app_not_visual_viewport(self):
        nav = re.search(r"#nav\s*\{(?P<body>[^}]*)\}", self.text)
        self.assertIsNotNone(nav)
        self.assertIn("position: absolute", nav.group("body"))
        self.assertNotIn("position: fixed", nav.group("body"))

    def test_only_screen_scrolls(self):
        screen = re.search(r"#screen\s*\{(?P<body>[^}]*)\}", self.text)
        self.assertIsNotNone(screen)
        self.assertIn("overscroll-behavior-y: contain", screen.group("body"))
        self.assertIn("min-height:0", screen.group("body").replace(" ", ""))

    def test_root_and_safe_area_follow_active_theme(self):
        self.assertRegex(self.text, r"html\s*\{[^}]*background\s*:\s*var\(--bg\)")
        safe_area = re.search(r"#app::after\s*\{(?P<body>[^}]*)\}", self.text)
        self.assertIsNotNone(safe_area)
        self.assertIn("background:var(--bg)", safe_area.group("body").replace(" ", ""))

    def test_theme_updates_root_class_and_browser_chrome(self):
        self.assertIn("document.documentElement.classList", self.text)
        self.assertIn("meta[name=\"theme-color\"]", self.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
