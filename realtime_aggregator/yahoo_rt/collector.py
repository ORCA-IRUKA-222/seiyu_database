"""検索語ごとに最新の投稿を取り、取りこぼしがありそうなら過去へさかのぼって補う。

さかのぼりでは、直前までに取った一覧のうち「最後から 2 番目の投稿 ID」を起点に次のページを頼む。
最後の投稿が次のページに重なって返ってくれば、ページの間を飛ばしていないと確かめられる。
Yahoo の読み込み口は公開仕様ではないため、起点 ID だけで送る方式（cursor）と通し番号も
送る方式（offset）を試し、重なりを確認できた方を使う。
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from typing import Callable

from .fetcher import FetchError, Page, YahooRealtimeClient
from .models import Post, fmt_jst
from .store import Store

# ページ送りがこの回数続けて失敗したら、このセッションでは使わない
PAGINATION_GIVE_UP = 3
# 更新（refresh）でさかのぼる最大ページ数。since に届けばその前に止まる
REFRESH_MAX_PAGES = 300
PROGRESS_EVERY = 10
PAGE_MODES = ("cursor", "offset")


@dataclass
class QueryStats:
    query: str
    fetched: int = 0
    added: int = 0
    pages: int = 0
    error: str = ""
    gap_suspected: bool = False


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
        should_stop: Callable[[], bool] = lambda: False,
    ):
        self.client = client
        self.store = store
        self.queries = queries
        self.since = since
        self.backfill_max_pages = backfill_max_pages
        self.log = log
        self.should_stop = should_stop
        self.page_mode: str | None = None
        self.pagination_failures = 0
        self._warned_pagination = False
        self._warned_gap = False

    @property
    def pagination_enabled(self) -> bool:
        return self.backfill_max_pages > 0 and self.pagination_failures < PAGINATION_GIVE_UP

    def run_cycle(self, refresh: bool = False, refresh_pages: int = REFRESH_MAX_PAGES) -> list[QueryStats]:
        stats = []
        for q in self.queries:
            if self.should_stop():
                break
            stats.append(self.collect_query(q, refresh=refresh, refresh_pages=refresh_pages))
        return stats

    def collect_query(self, query: str, *, refresh: bool = False, refresh_pages: int = REFRESH_MAX_PAGES) -> QueryStats:
        """最新ページを取得する。refresh のときは since までさかのぼり、既知の投稿のいいね数なども更新する。"""
        st = QueryStats(query)
        try:
            page = self.client.fetch_latest(query)
        except FetchError as e:
            st.error = str(e)
            self.store.log_fetch(query, "latest", False, message=st.error)
            return st
        if not page.result.recognized:
            st.error = "ページから投稿データを読み取れませんでした（doctor コマンドで確認してください）"
            self.store.log_fetch(query, "latest", False, message=st.error)
            return st
        timeline = page.result.timeline
        known = self.store.known_ids([p.id for p in timeline], query)
        st.added = self.store.upsert(page.result.posts, query)
        st.fetched, st.pages = len(page.result.posts), 1
        self.store.log_fetch(query, "latest", True, st.fetched, st.added)
        if not timeline:
            return st
        if refresh:
            self._walk_back(query, timeline, st, refresh_pages, stop_at_known=False)
        # 最新ページに既知の投稿が 1 件もない ＝ 前回との間に取りこぼしがあるかもしれない
        elif not known and self.pagination_enabled:
            self._walk_back(query, timeline, st, self.backfill_max_pages, stop_at_known=True)
        return st

    def request_older(self, query: str, last: int, second: int | None, count: int) -> tuple[Page, bool]:
        """last（取得済みで最も古い投稿）が先頭に重なるよう次のページを頼む。(ページ, 重なりを確認できたか)"""
        cursor = second if second is not None else last
        modes = [self.page_mode] if self.page_mode else list(PAGE_MODES)
        first: Page | None = None
        for mode in modes:
            start = None if mode == "cursor" else (count if second is not None else count + 1)
            page = self.client.fetch_older(query, str(cursor), start)
            got = [int(p.id) for p in page.result.timeline if p.id.isdigit()]
            if second is not None and last in got and all(g < second for g in got):
                self.page_mode = mode
                return page, True
            first = first or page
            if not page.result.recognized:
                break
        if self.page_mode is None:
            self.page_mode = "cursor"
        return first, False

    def _walk_back(self, query: str, timeline: list[Post], st: QueryStats, max_pages: int, *, stop_at_known: bool) -> None:
        seen = {int(p.id) for p in timeline if p.id.isdigit()}
        oldest_time = min(p.created_at for p in timeline)
        for _ in range(max_pages):
            if self.should_stop() or not seen:
                return
            if self.since is not None and oldest_time < self.since:
                return
            smallest = heapq.nsmallest(2, seen)
            last, second = smallest[0], (smallest[1] if len(smallest) > 1 else None)
            try:
                page, overlapped = self.request_older(query, last, second, len(seen))
            except FetchError as e:
                self._pagination_failed(str(e))
                if not stop_at_known:
                    st.error = f"さかのぼり取得が途中で止まりました（{e}）"
                return
            if not page.result.recognized:
                self._pagination_failed("応答を解析できませんでした")
                if not stop_at_known:
                    st.error = "さかのぼり取得の応答を解析できませんでした"
                return
            self.pagination_failures = 0
            new = [p for p in page.result.timeline if p.id.isdigit() and int(p.id) not in seen]
            if not new:
                return
            if not overlapped and second is not None:
                st.gap_suspected = True
                self._gap_found()
            known = self.store.known_ids([p.id for p in new], query)
            added = self.store.upsert(page.result.posts, query)
            self.store.log_fetch(query, "older", True, len(new), added)
            st.fetched += len(new)
            st.added += added
            st.pages += 1
            seen.update(int(p.id) for p in new)
            oldest_time = min(oldest_time, *(p.created_at for p in new))
            if st.pages % PROGRESS_EVERY == 0:
                self.log(f"  {query}: {st.pages}ページ目（{fmt_jst(oldest_time)} の投稿まで）")
            if known and stop_at_known:  # 前回までに集めた範囲に追いついた
                return

    def _gap_found(self) -> None:
        if not self._warned_gap:
            self._warned_gap = True
            self.log(
                "※ さかのぼり取得で、ページの間の投稿を飛ばした可能性があります。"
                "件数が実際より少なく出ることがあります。"
            )

    def _pagination_failed(self, message: str) -> None:
        self.pagination_failures += 1
        self.store.log_fetch(None, "older", False, message=message)
        if not self.pagination_enabled and not self._warned_pagination:
            self._warned_pagination = True
            self.log(
                "※ 過去分のさかのぼり取得が使えないため、最新分の定期取得だけを続けます。"
                "取りこぼした時間帯は、ブラウザで保存した HAR を import すると補えます。"
            )
