import base64
import json
import unittest

from helpers import BASE_ID, api_json, entry, html_page, next_data

from yahoo_rt.models import parse_jst
from yahoo_rt.parser import parse_har, parse_text, parse_time, to_post


class ParseHtmlTest(unittest.TestCase):
    def test_reads_entries_from_next_data(self):
        quoted = entry(99, text="引用元の投稿")
        first = entry(1, text="\tSTART\t声グラハロウィーン\tEND\t最高！ #声グラハロウィーン #仮装", media=[{}, {}])
        first["quotedTweet"] = quoted
        data = next_data([first, entry(2)], oldest=str(BASE_ID + 2), bestTweet=entry(1))
        r = parse_text(html_page(data))

        self.assertTrue(r.recognized)
        self.assertEqual([p.id for p in r.posts], [str(BASE_ID + 1), str(BASE_ID + 2)])
        p = r.posts[0]
        self.assertEqual(p.text, "声グラハロウィーン最高！ #声グラハロウィーン #仮装")
        self.assertEqual(p.hashtags, ["声グラハロウィーン", "仮装"])
        self.assertEqual((p.screen_name, p.user_name, p.likes, p.media_count), ("user1", "ユーザー1", 1, 2))
        self.assertEqual(p.created_at, parse_jst("2026-10-24 18:00"))
        self.assertEqual(r.cursor, str(BASE_ID + 2))

    def test_cursor_falls_back_to_smallest_id(self):
        r = parse_text(html_page(next_data([entry(5), entry(3), entry(4)])))
        self.assertEqual(r.cursor, str(BASE_ID + 3))

    def test_page_without_data_is_not_recognized(self):
        r = parse_text("<html><body>アクセスが集中しています</body></html>")
        self.assertFalse(r.recognized)
        self.assertEqual(r.posts, [])

    def test_api_json(self):
        r = parse_text(api_json([entry(7), entry(6)], oldest=str(BASE_ID + 6)))
        self.assertEqual(len(r.posts), 2)
        self.assertEqual(r.cursor, str(BASE_ID + 6))


class KeyVariantsTest(unittest.TestCase):
    def test_alternative_key_names_and_nested_user(self):
        d = {
            "tweetId": "123456789",
            "text": "hello &amp; ハロウィン",
            "created_at": "2026-10-24T09:00:00Z",
            "user": {"screen_name": "@nested", "name": "ネスト"},
            "favoriteCount": "1,234",
            "retweetCount": 5,
        }
        p = to_post(d)
        self.assertEqual((p.id, p.screen_name, p.user_name), ("123456789", "nested", "ネスト"))
        self.assertEqual((p.likes, p.retweets, p.quotes), (1234, 5, None))
        self.assertEqual(p.text, "hello & ハロウィン")
        self.assertEqual(p.created_at, parse_jst("2026-10-24 18:00"))
        self.assertEqual(p.permalink, "https://x.com/nested/status/123456789")

    def test_non_posts_are_ignored(self):
        self.assertIsNone(to_post({"id": "abc", "text": "x", "createdAt": 1792800000}))
        self.assertIsNone(to_post({"id": "1", "title": "x", "createdAt": 1792800000}))
        self.assertIsNone(to_post({"id": "1", "text": "x"}))

    def test_time_formats(self):
        ts = parse_jst("2026-10-24 18:00")
        self.assertEqual(parse_time(ts), ts)
        self.assertEqual(parse_time(ts * 1000), ts)
        self.assertEqual(parse_time(str(ts)), ts)
        self.assertEqual(parse_time("2026-10-24T18:00:00+09:00"), ts)
        self.assertEqual(parse_time("Sat Oct 24 09:00:00 +0000 2026"), ts)
        self.assertIsNone(parse_time("昨日"))
        self.assertIsNone(parse_time(True))


class HarTest(unittest.TestCase):
    def test_reads_yahoo_responses_only(self):
        api = api_json([entry(11), entry(10)])
        har = {
            "log": {
                "entries": [
                    {"request": {"url": "https://search.yahoo.co.jp/realtime/search?p=x"},
                     "response": {"content": {"mimeType": "text/html", "text": html_page(next_data([entry(12)]))}}},
                    {"request": {"url": "https://search.yahoo.co.jp/realtime/api/v1/pagination?p=x"},
                     "response": {"content": {"mimeType": "application/json", "encoding": "base64",
                                              "text": base64.b64encode(api.encode()).decode()}}},
                    {"request": {"url": "https://example.com/other.json"},
                     "response": {"content": {"mimeType": "application/json", "text": api_json([entry(50)])}}},
                ]
            }
        }
        r = parse_text(json.dumps(har))
        self.assertEqual(sorted(int(p.id) - BASE_ID for p in r.posts), [10, 11, 12])
        self.assertEqual(len(parse_har(har, host_filter="").posts), 4)


if __name__ == "__main__":
    unittest.main()
