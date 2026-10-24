import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from helpers import BASE_ID, api_json, entry, html_page, next_data

from yahoo_rt.fetcher import FetchError, YahooRealtimeClient


class Handler(BaseHTTPRequestHandler):
    requests = []
    fail_next = 0

    def log_message(self, *args):
        pass

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(url.query)
        Handler.requests.append((url.path, q, dict(self.headers)))
        if Handler.fail_next:
            Handler.fail_next -= 1
            self.send_response(503)
            self.end_headers()
            return
        if url.path == "/realtime/search":
            body = html_page(next_data([entry(2), entry(1)], oldest=str(BASE_ID + 1)))
            ctype = "text/html; charset=utf-8"
        elif url.path == "/realtime/api/v1/pagination":
            body = api_json([entry(0)])
            ctype = "application/json; charset=utf-8"
        else:
            self.send_response(404)
            self.end_headers()
            return
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class FetcherTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}/realtime"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        Handler.requests.clear()
        Handler.fail_next = 0
        self.sleeps = []
        self.client = YahooRealtimeClient(base_url=self.base, sleep=self.sleeps.append, min_delay=3)

    def test_latest_and_older(self):
        page = self.client.fetch_latest("声グラハロウィーン")
        self.assertEqual([p.id for p in page.result.posts], [str(BASE_ID + 2), str(BASE_ID + 1)])
        path, q, headers = Handler.requests[0]
        self.assertEqual(q["p"], ["声グラハロウィーン"])
        self.assertIn("Mozilla", headers["User-Agent"])

        older = self.client.fetch_older("声グラハロウィーン", page.result.cursor, 3)
        self.assertEqual([p.id for p in older.result.posts], [str(BASE_ID)])
        path, q, headers = Handler.requests[1]
        self.assertEqual((path, q["oldestTweetId"], q["start"]), ("/realtime/api/v1/pagination", [str(BASE_ID + 1)], ["3"]))
        self.assertTrue(headers["Referer"].startswith(self.base + "/search"))
        self.assertEqual(len(self.sleeps), 1)  # 2回目の前に間隔をあけた
        self.assertGreater(self.sleeps[0], 2)

    def test_retries_on_server_error(self):
        Handler.fail_next = 1
        page = self.client.fetch_latest("q")
        self.assertEqual(len(page.result.posts), 2)
        self.assertEqual(len(Handler.requests), 2)
        self.assertIn(10.0, self.sleeps)

    def test_gives_up_on_not_found(self):
        with self.assertRaises(FetchError) as cm:
            self.client.get(self.base + "/missing")
        self.assertEqual(cm.exception.status, 404)
        self.assertEqual(len(Handler.requests), 1)


if __name__ == "__main__":
    unittest.main()
