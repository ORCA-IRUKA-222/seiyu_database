"""収集した投稿を SQLite に保存する。

同じ投稿を何度取得しても 1 件として扱い、いいね数などは最新の値で上書きする。
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from .models import Post

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id          TEXT PRIMARY KEY,
    created_at  INTEGER NOT NULL,
    text        TEXT NOT NULL,
    screen_name TEXT,
    user_name   TEXT,
    user_id     TEXT,
    likes       INTEGER,
    retweets    INTEGER,
    replies     INTEGER,
    quotes      INTEGER,
    url         TEXT,
    hashtags    TEXT NOT NULL DEFAULT '[]',
    media_count INTEGER NOT NULL DEFAULT 0,
    first_seen  INTEGER NOT NULL,
    last_seen   INTEGER NOT NULL,
    raw         TEXT
);
CREATE INDEX IF NOT EXISTS posts_created_at ON posts(created_at);

-- どの検索語で見つかった投稿か（1 投稿が複数の検索語に当たることがある）
CREATE TABLE IF NOT EXISTS post_queries (
    post_id TEXT NOT NULL REFERENCES posts(id),
    query   TEXT NOT NULL,
    PRIMARY KEY (post_id, query)
);

CREATE TABLE IF NOT EXISTS fetch_log (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    at      INTEGER NOT NULL,
    query   TEXT,
    kind    TEXT NOT NULL,
    ok      INTEGER NOT NULL,
    found   INTEGER NOT NULL DEFAULT 0,
    added   INTEGER NOT NULL DEFAULT 0,
    message TEXT
);
"""

UPSERT = """
INSERT INTO posts (id, created_at, text, screen_name, user_name, user_id,
                   likes, retweets, replies, quotes, url, hashtags, media_count,
                   first_seen, last_seen, raw)
VALUES (:id, :created_at, :text, :screen_name, :user_name, :user_id,
        :likes, :retweets, :replies, :quotes, :url, :hashtags, :media_count,
        :now, :now, :raw)
ON CONFLICT(id) DO UPDATE SET
    text        = excluded.text,
    screen_name = COALESCE(NULLIF(excluded.screen_name, ''), posts.screen_name),
    user_name   = COALESCE(NULLIF(excluded.user_name, ''), posts.user_name),
    likes       = COALESCE(excluded.likes, posts.likes),
    retweets    = COALESCE(excluded.retweets, posts.retweets),
    replies     = COALESCE(excluded.replies, posts.replies),
    quotes      = COALESCE(excluded.quotes, posts.quotes),
    url         = COALESCE(NULLIF(excluded.url, ''), posts.url),
    hashtags    = excluded.hashtags,
    media_count = MAX(excluded.media_count, posts.media_count),
    last_seen   = excluded.last_seen,
    raw         = excluded.raw
"""


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def upsert(self, posts: list[Post], query: str | None = None) -> int:
        """保存して、新しく増えた投稿の件数を返す。"""
        if not posts:
            return 0
        now = int(time.time())
        before = self.count()
        with self.conn:
            self.conn.executemany(
                UPSERT,
                [
                    {
                        "id": p.id,
                        "created_at": p.created_at,
                        "text": p.text,
                        "screen_name": p.screen_name,
                        "user_name": p.user_name,
                        "user_id": p.user_id,
                        "likes": p.likes,
                        "retweets": p.retweets,
                        "replies": p.replies,
                        "quotes": p.quotes,
                        "url": p.url,
                        "hashtags": json.dumps(p.hashtags, ensure_ascii=False),
                        "media_count": p.media_count,
                        "now": now,
                        "raw": json.dumps(p.raw, ensure_ascii=False) if p.raw is not None else None,
                    }
                    for p in posts
                ],
            )
            if query:
                self.conn.executemany(
                    "INSERT OR IGNORE INTO post_queries (post_id, query) VALUES (?, ?)",
                    [(p.id, query) for p in posts],
                )
        return self.count() - before

    def known_ids(self, ids: list[str], query: str | None = None) -> set[str]:
        if not ids:
            return set()
        marks = ",".join("?" * len(ids))
        if query is None:
            sql = f"SELECT id FROM posts WHERE id IN ({marks})"
            args: list = list(ids)
        else:
            sql = f"SELECT post_id FROM post_queries WHERE query = ? AND post_id IN ({marks})"
            args = [query, *ids]
        return {row[0] for row in self.conn.execute(sql, args)}

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]

    def load_posts(
        self,
        since: int | None = None,
        until: int | None = None,
        queries: list[str] | None = None,
    ) -> list[Post]:
        where, args = [], []
        if since is not None:
            where.append("created_at >= ?")
            args.append(since)
        if until is not None:
            where.append("created_at < ?")
            args.append(until)
        if queries:
            where.append(
                f"id IN (SELECT post_id FROM post_queries WHERE query IN ({','.join('?' * len(queries))}))"
            )
            args.extend(queries)
        sql = (
            "SELECT id, created_at, text, screen_name, user_name, user_id, likes, retweets,"
            " replies, quotes, url, hashtags, media_count,"
            " (SELECT group_concat(query, char(10)) FROM post_queries WHERE post_id = posts.id)"
            " FROM posts"
        )
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at, id"
        return [
            Post(
                id=r[0],
                created_at=r[1],
                text=r[2],
                screen_name=r[3] or "",
                user_name=r[4] or "",
                user_id=r[5] or "",
                likes=r[6],
                retweets=r[7],
                replies=r[8],
                quotes=r[9],
                url=r[10] or "",
                hashtags=json.loads(r[11] or "[]"),
                media_count=r[12] or 0,
                queries=sorted((r[13] or "").split("\n")) if r[13] else [],
            )
            for r in self.conn.execute(sql, args)
        ]

    def log_fetch(
        self, query: str | None, kind: str, ok: bool, found: int = 0, added: int = 0, message: str = ""
    ) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO fetch_log (at, query, kind, ok, found, added, message)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (int(time.time()), query, kind, int(ok), found, added, message),
            )
