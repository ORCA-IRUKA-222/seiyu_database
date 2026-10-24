import unittest

from helpers import BASE_ID, entry

from yahoo_rt.models import parse_jst
from yahoo_rt.parser import to_post
from yahoo_rt.store import Store


def post(n, when="2026-10-24 18:00", **kw):
    return to_post(entry(n, when, **kw))


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")

    def tearDown(self):
        self.store.close()

    def test_upsert_counts_only_new_posts_and_refreshes_counts(self):
        self.assertEqual(self.store.upsert([post(1), post(2)], "q"), 2)
        self.assertEqual(self.store.upsert([post(2, likesCount=50), post(3)], "q"), 1)
        self.assertEqual(self.store.count(), 3)
        likes = {p.id: p.likes for p in self.store.load_posts()}
        self.assertEqual(likes[str(BASE_ID + 2)], 50)

    def test_missing_counts_do_not_erase_stored_values(self):
        self.store.upsert([post(1, likesCount=10)], "q")
        self.store.upsert([post(1, likesCount=None)], "q")
        self.assertEqual(self.store.load_posts()[0].likes, 10)

    def test_known_ids_per_query(self):
        self.store.upsert([post(1)], "a")
        ids = [str(BASE_ID + 1), str(BASE_ID + 2)]
        self.assertEqual(self.store.known_ids(ids), {str(BASE_ID + 1)})
        self.assertEqual(self.store.known_ids(ids, "a"), {str(BASE_ID + 1)})
        self.assertEqual(self.store.known_ids(ids, "b"), set())

    def test_load_posts_filters(self):
        self.store.upsert([post(1, "2026-10-24 10:00"), post(2, "2026-10-24 18:00")], "a")
        self.store.upsert([post(3, "2026-10-25 01:00")], "b")
        since, until = parse_jst("2026-10-24 12:00"), parse_jst("2026-10-25 00:00")
        self.assertEqual([p.id for p in self.store.load_posts(since, until)], [str(BASE_ID + 2)])
        self.assertEqual([p.id for p in self.store.load_posts(queries=["b"])], [str(BASE_ID + 3)])
        self.assertEqual(self.store.load_posts()[0].hashtags, [])


if __name__ == "__main__":
    unittest.main()
