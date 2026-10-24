"""検索語ごとに最新の投稿を取り、取りこぼしがありそうなら過去へさかのぼって補う。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .fetcher import FetchError, YahooRealtimeClient
from .models import Post
from .parser import oldest_id
from .store import Store

# ページ送りがこの回数続けて失敗したら、このセッションでは使わない
PAGINATION_GIVE_UP = 3


@dataclass
class QueryStats:
    query: str
    fetched: int = 0
    added: int = 0
    pages: int = 0
    error: str = ""


class Collector:
    def __init__(
        self,
        client: YahooRealtimeClient,
        store: Store,
        queries: list[str],
        *,
        since: int | None = None,
        backfill_max_pages: int = 25,
        log: Callable[[str], None] = print,
    ):
        self.client = client
        self.store = store
        self.queries = queries
        self.since = since
        self.backfill_max_pages = backfill_max_pages
        self.log = log
        self.pagination_failures = 0
        self._warned_pagination = False

    @property
    def pagination_enabled(self) -> bool:
        return self.backfill_max_pages > 0 and self.pagination_failures < PAGINATION_GIVE_UP

    def run_cycle(self) -> list[QueryStats]:
        return [self.collect_query(q) for q in self.queries]

    def collect_query(self, query: str) -> QueryStats:
        st = QueryStats(query)
        try:
            page = self.client.fetch_latest(query)
        except FetchError as e:
            st.error = str(e)
            self.store.log_fetch(query, "latest", False, message=st.error)
            return st
        posts = page.result.posts
        if not page.result.recognized:
            st.error = "ページから投稿データを読み取れませんでした（doctor コマンドで確認してください）"
            self.store.log_fetch(query, "latest", False, message=st.error)
            return st
        known = self.store.known_ids([p.id for p in posts], query)
        st.added = self.store.upsert(posts, query)
        st.fetched, st.pages = len(posts), 1
        self.store.log_fetch(query, "latest", True, len(posts), st.added)
        # 最新ページに既知の投稿が 1 件もない ＝ 前回との間に取りこぼしがあるかもしれない
        if posts and not known and self.pagination_enabled:
            self._backfill(query, posts, page.result.cursor, st)
        return st

    def _backfill(self, query: str, posts: list[Post], cursor: str | None, st: QueryStats) -> None:
        start = len(posts) + 1
        cursor = cursor or oldest_id(posts)
        oldest_time = min(p.created_at for p in posts)
        for _ in range(self.backfill_max_pages):
            if cursor is None or (self.since is not None and oldest_time < self.since):
                return
            try:
                page = self.client.fetch_older(query, cursor, start)
            except FetchError as e:
                self._pagination_failed(str(e))
                return
            older = page.result.posts
            if not page.result.recognized:
                self._pagination_failed("応答を解析できませんでした")
                return
            self.pagination_failures = 0
            if not older:
                return
            known = self.store.known_ids([p.id for p in older], query)
            added = self.store.upsert(older, query)
            self.store.log_fetch(query, "older", True, len(older), added)
            st.fetched += len(older)
            st.added += added
            st.pages += 1
            if known:  # 前回までに集めた範囲に追いついた
                return
            next_cursor = page.result.cursor or oldest_id(older)
            if next_cursor == cursor:
                return
            cursor = next_cursor
            start += len(older)
            oldest_time = min(oldest_time, *(p.created_at for p in older))

    def _pagination_failed(self, message: str) -> None:
        self.pagination_failures += 1
        self.store.log_fetch(None, "older", False, message=message)
        if not self.pagination_enabled and not self._warned_pagination:
            self._warned_pagination = True
            self.log(
                "※ 過去分のさかのぼり取得が使えないため、最新分の定期取得だけを続けます。"
                "取りこぼした時間帯は、ブラウザで保存した HAR を import すると補えます。"
            )
