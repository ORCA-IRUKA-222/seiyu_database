import csv
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from helpers import BASE_ID, entry, html_page, next_data

from yahoo_rt import cli
from yahoo_rt.config import load_config
from yahoo_rt.store import Store

ROOT = Path(__file__).resolve().parent.parent


def run(*argv):
    out, err = StringIO(), StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class CliTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)
        self.config = self.dir / "config.json"
        shutil.copy(ROOT / "config.json", self.config)

    def write_har(self, entries):
        har = {"log": {"entries": [{
            "request": {"url": "https://search.yahoo.co.jp/realtime/search?p=x"},
            "response": {"content": {"mimeType": "text/html", "text": html_page(next_data(entries))}},
        }]}}
        path = self.dir / "saved.har"
        path.write_text(json.dumps(har, ensure_ascii=False), encoding="utf-8")
        return path

    def test_shipped_config_is_valid(self):
        cfg = load_config(ROOT / "config.json")
        cfg.validate()
        self.assertEqual(cfg.event_name, "声グラハロウィーン")

    def test_import_then_report(self):
        self.write_har([
            entry(1, "2026-10-24 18:05", text="最高だった！<script>alert(1)</script> #声グラハロウィーン", likesCount=10),
            entry(2, "2026-10-24 18:20", text="チケット譲渡します #声グラハロウィーン"),
            entry(3, "2026-10-23 12:00", text="前日の投稿"),
        ])
        code, out, _ = run("import", "-c", str(self.config), str(self.dir / "*.har"))
        self.assertEqual(code, 0, out)
        self.assertIn("新規 3件", out)

        code, out, err = run("report", "-c", str(self.config))
        self.assertEqual(code, 0, err)
        self.assertIn("投稿数: 1件", out)  # 前日分は since で、譲渡は除外ワードで外れる
        reports = self.dir / "reports"
        html = (reports / "report.html").read_text(encoding="utf-8")
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertNotIn("<script>alert", html)
        self.assertIn("https://twitter.com/user1/status/", html)
        raw = (reports / "posts.csv").read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        rows = list(csv.reader(raw.decode("utf-8-sig").splitlines()))
        self.assertEqual(rows[1][1], str(BASE_ID + 1))
        self.assertEqual(rows[1][11], "最高・楽しい")
        for name in ("timeline.csv", "words.csv", "hashtags.csv", "groups.csv"):
            self.assertTrue((reports / name).exists(), name)

    def test_report_anonymize(self):
        run("import", "-c", str(self.config), str(self.write_har([entry(1, "2026-10-24 18:05", likesCount=3)])))
        code, _, _ = run("report", "-c", str(self.config), "--anonymize")
        self.assertEqual(code, 0)
        html = (self.dir / "reports" / "report.html").read_text(encoding="utf-8")
        self.assertNotIn("user1", html.replace("user0001", ""))
        self.assertNotIn("twitter.com", html)

    def test_report_without_data(self):
        code, _, err = run("report", "-c", str(self.config))
        self.assertEqual(code, 1)
        self.assertIn("collect", err)

    def test_bad_config_values(self):
        data = json.loads(self.config.read_text(encoding="utf-8"))
        data["sinse"] = "2026-10-24"
        self.config.write_text("﻿" + json.dumps(data, ensure_ascii=False), encoding="utf-8")
        code, _, err = run("report", "-c", str(self.config))
        self.assertEqual(code, 2)
        self.assertIn("sinse", err)
        code, _, err = run("report", "-c", str(self.config.with_name("missing.json")))
        self.assertEqual(code, 2)
        del data["sinse"]
        data["since"] = "10/24"
        self.config.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        code, _, err = run("report", "-c", str(self.config))
        self.assertEqual(code, 2)
        self.assertIn("日時の形式", err)

    def test_collect_once_with_live_report(self):
        class Client:
            def search_url(self, q):
                return "https://example.invalid"

            def fetch_latest(self, query):
                from yahoo_rt.fetcher import Page
                from yahoo_rt.parser import parse_text

                body = html_page(next_data([entry(2, "2026-10-24 19:00"), entry(1, "2026-10-24 18:59")]))
                return Page("u", body, parse_text(body))

        original = cli._client
        cli._client = lambda cfg: Client()
        self.addCleanup(setattr, cli, "_client", original)
        code, out, err = run("collect", "-c", str(self.config), "-q", "声グラハロウィーン", "--once", "--live-report", "--max-pages", "0")
        self.assertEqual(code, 0, err)
        self.assertIn("新規 2件", out)
        self.assertTrue((self.dir / "reports" / "report.html").exists())
        with Store(self.dir / "data" / "seigura_halloween.sqlite3") as store:
            self.assertEqual(store.count(), 2)


if __name__ == "__main__":
    unittest.main()
