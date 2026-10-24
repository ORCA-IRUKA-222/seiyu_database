"""検索語ごとに最新の投稿を取り、取りこぼしがありそうなら過去へさかのぼって補う。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .fetcher import FetchError, YahooRealtimeClient
from .models import Post, fmt_jst
from .parser import oldest_id
from .store import Store

# ページ送りがこの回数続けて失敗したら、このセッションでは使わない
PAGINATION_GIVE_UP = 3
# 更新（refresh）でさかのぼる最大ページ数。since に届けばその前に止まる
REFRESH_MAX_PAGES = 300
PROGRESS_EVERY = 10


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

    def run_cycle(self, refresh: bool = False, refresh_pages: int = REFRESH_MAX_PAGES) -> list[QueryStats]:
        return [self.collect_query(q, refresh=refresh, refresh_pages=refresh_pages) for q in self.queries]

    def collect_query(self, query: str, *, refresh: bool = False, refresh_pages: int = REFRESH_MAX_PAGES) -> QueryStats:
        """最新ページを取得する。refresh のときは since までさかのぼり、既知の投稿のいいね数なども更新する。"""
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
        if refresh:
            if posts:
                self._walk_back(query, posts, page.result.cursor, st, refresh_pages, stop_at_known=False)
        # 最新ページに既知の投稿が 1 件もない ＝ 前回との間に取りこぼしがあるかもしれない
        elif posts and not known and self.pagination_enabled:
            self._walk_back(query, posts, page.result.cursor, st, self.backfill_max_pages, stop_at_known=True)
        return st

    def _walk_back(
        self, query: str, posts: list[Post], cursor: str | None, st: QueryStats, max_pages: int, *, stop_at_known: bool
    ) -> None:
        start = len(posts) + 1
        cursor = cursor or oldest_id(posts)
        oldest_time = min(p.created_at for p in posts)
        for _ in range(max_pages):
            if cursor is None or (self.since is not None and oldest_time < self.since):
                return
            try:
                page = self.client.fetch_older(query, cursor, start)
            except FetchError as e:
                self._pagination_failed(str(e))
                if not stop_at_known:
                    st.error = f"さかのぼり取得が途中で止まりました（{e}）"
                return
            older = page.result.posts
            if not page.result.recognized:
                self._pagination_failed("応答を解析できませんでした")
                if not stop_at_known:
                    st.error = "さかのぼり取得の応答を解析できませんでした"
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
            oldest_time = min(oldest_time, *(p.created_at for p in older))
            if st.pages % PROGRESS_EVERY == 0:
                self.log(f"  {query}: {st.pages}ページ目（{fmt_jst(oldest_time)} の投稿まで）")
            if known and stop_at_known:  # 前回までに集めた範囲に追いついた
                return
            next_cursor = page.result.cursor or oldest_id(older)
            if next_cursor == cursor:
                return
            cursor = next_cursor
            start += len(older)

    def _pagination_failed(self, message: str) -> None:
        self.pagination_failures += 1
        self.store.log_fetch(None, "older", False, message=message)
        if not self.pagination_enabled and not self._warned_pagination:
            self._warned_pagination = True
            self.log(
                "※ 過去分のさかのぼり取得が使えないため、最新分の定期取得だけを続けます。"
                "取りこぼした時間帯は、ブラウザで保存した HAR を import すると補えます。"
            )
