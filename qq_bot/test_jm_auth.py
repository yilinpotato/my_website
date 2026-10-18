import os
import unittest
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse


os.environ["JM_VIEWER_PASSWORD"] = "test-password"
os.environ["JM_SESSION_SECRET"] = "test-session-secret"
os.environ["JM_URL_PREFIX"] = ""

from jm_bot import app  # noqa: E402


class CardParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.card_ids = []
        self.card_titles = {}
        self._current_card = None
        self._inside_h2 = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and "card" in attrs.get("class", "").split():
            self._current_card = attrs.get("id")
            self.card_ids.append(self._current_card)
        elif tag == "h2" and self._current_card:
            self._inside_h2 = True

    def handle_endtag(self, tag):
        if tag == "h2":
            self._inside_h2 = False
        elif tag == "a" and self._current_card:
            self._current_card = None

    def handle_data(self, data):
        if self._inside_h2:
            self.card_titles[self._current_card] = (
                self.card_titles.get(self._current_card, "") + data
            ).strip()


class FooterTextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self._footer_depth = 0
        self.text = []

    def handle_starttag(self, tag, attrs):
        if tag == "footer":
            self._footer_depth = 1
        elif self._footer_depth:
            self._footer_depth += 1

    def handle_endtag(self, tag):
        if self._footer_depth:
            self._footer_depth -= 1

    def handle_data(self, data):
        if self._footer_depth and data.strip():
            self.text.append(data.strip())


class ViewerRootParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.html_attributes = {}
        self.body_attributes = {}

    def handle_starttag(self, tag, attrs):
        if tag == "html":
            self.html_attributes = dict(attrs)
        elif tag == "body":
            self.body_attributes = dict(attrs)


class JmAuthTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.client = app.test_client()

    def test_unauthenticated_viewer_redirects_to_password_page(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(urlparse(response.location).path, "/auth")

    def test_correct_password_unlocks_viewer_for_session(self):
        response = self.client.post(
            "/auth",
            data={"password": "test-password", "next": "/"},
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        viewer_html = response.get_data(as_text=True)
        root = ViewerRootParser()
        root.feed(viewer_html)
        self.assertIn("漫画查看器", viewer_html)
        self.assertEqual(root.html_attributes.get("data-jm-authorized"), "true")
        self.assertNotIn("data-jm-authorized", root.body_attributes)
        self.assertIn('id="searchBtn" data-analytics-id="comic-open"', viewer_html)
        self.assertNotIn('id="albumId" data-analytics-', viewer_html)

    def test_wrong_password_is_rejected(self):
        response = self.client.post(
            "/auth", data={"password": "wrong-password", "next": "/"}
        )

        self.assertEqual(response.status_code, 401)
        auth_html = response.get_data(as_text=True)
        self.assertIn("密码错误", auth_html)
        self.assertNotIn("data-jm-authorized", auth_html)
        self.assertNotIn("data-analytics-", auth_html)
        self.assertNotIn('id="password" data-analytics-', auth_html)

    def test_qq_callback_stays_public(self):
        response = self.client.get("/qq/callback?challenge=callback-ok")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_data(as_text=True), "callback-ok")


class HomepageCardTests(unittest.TestCase):
    def test_comic_viewer_is_renamed_and_last(self):
        parser = CardParser()
        parser.feed(Path(__file__).resolve().parents[1].joinpath("index.html").read_text())

        self.assertEqual(parser.card_ids[-1], "link-jm")
        self.assertEqual(parser.card_titles["link-jm"], "漫画查看器")

    def test_mygo_source_credit_is_only_in_mygo_footer(self):
        website_root = Path(__file__).resolve().parents[1]
        homepage = website_root.joinpath("index.html").read_text()
        mygo_page = website_root.joinpath("mygo-site/index.html").read_text()
        homepage_footer = FooterTextParser()
        mygo_footer = FooterTextParser()
        homepage_footer.feed(homepage)
        mygo_footer.feed(mygo_page)

        self.assertNotIn("来源：窜子", homepage)
        self.assertIn("来源：窜子", " ".join(mygo_footer.text))


if __name__ == "__main__":
    unittest.main()
