"""Content and navigation checks for the public portfolio (standard library only)."""
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit
import unittest

ROOT = Path(__file__).resolve().parents[1]


class Page(HTMLParser):
    def __init__(self, filename):
        super().__init__()
        self.elements = []
        self.feed((ROOT / filename).read_text(encoding="utf-8-sig"))

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


class PortfolioTests(unittest.TestCase):
    def test_all_profile_assets_exist(self):
        for tag, attrs in Page("about/index.html").elements:
            for key in ("href", "src"):
                target = urlsplit(attrs.get(key, "")).path
                if target.startswith("/about/"):
                    self.assertTrue((ROOT / target.lstrip("/")).exists(), target)

    def test_bilingual_text_and_images_have_both_languages(self):
        for filename in ("index.html", "about/index.html"):
            for tag, attrs in Page(filename).elements:
                if "data-zh" in attrs:
                    self.assertTrue(attrs["data-zh"])
                    self.assertTrue(attrs.get("data-en"))
                if tag == "img" and "data-alt-zh" in attrs:
                    self.assertTrue(attrs.get("data-alt-en"))
                    self.assertTrue(attrs.get("width"))
                    self.assertTrue(attrs.get("height"))

    def test_project_destinations_are_preserved(self):
        links = {a.get("id"): a.get("href") for tag, a in Page("index.html").elements if tag == "a"}
        for key, target in {"new": "/new/", "old": "/old/", "jm": "/jm/", "mygo": "/mygo/", "myagents": "/myagents/"}.items():
            self.assertEqual(links["link-" + key], target)
        self.assertNotIn("choose-projects", links)
        self.assertEqual(links["about-entry"], "/about/")

    def test_dialog_and_fallback_navigation(self):
        elements = Page("index.html").elements
        dialogs = [attrs for tag, attrs in elements if tag == "dialog"]
        self.assertEqual(len(dialogs), 0)
        self.assertTrue(any(tag == "noscript" for tag, _ in elements))

    def test_public_content_preserves_statuses_and_excludes_private_contacts(self):
        source = (ROOT / "about/index.html").read_text(encoding="utf-8")
        for fact in ("AAAI 2027 在审", "AAAI 2027 · Under review", "NAS 2026 已录用", "NAS 2026 · Accepted", "91.36", "9 / 90"):
            self.assertIn(fact, source)
        for filename in ("index.html", "about/index.html", "about/profile.js"):
            public = (ROOT / filename).read_text(encoding="utf-8-sig")
            self.assertNotRegex(public, r"(?<!\d)1[3-9]\d{9}(?!\d)")
            self.assertNotIn("tel:", public)
            self.assertNotIn("微信", public)


if __name__ == "__main__":
    unittest.main()
