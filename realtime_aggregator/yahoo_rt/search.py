"""画面（gui.py）から使う検索処理。tkinter に依存しないので、画面なしで試せる。"""

from __future__ import annotations

import datetime as dt
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .analyze import analyze
from .collector import Collector
from .config import Config
from .fetcher import YahooRealtimeClient
from .models import JST, Post, parse_jst
from .report import write_reports
from .store import Store
from .variants import expand_queries, parse_groups, parse_lines

APP_DIR = Path(__file__).resolve().parent.parent
_UNSAFE_RE = re.compile(r'[\\/:*?"<>|\s]+')
_LIST_SEP_RE = re.compile(r"[,，、\s]+")


@dataclass
class Form:
    """画面の入力欄の内容。gui_settings.json にもこの形で保存する。"""

    name: str = "声グラハロウィーン"
    keywords: str = "声グラハロウィーン"
    synonyms: str = "声グラ = 声優グランプリ\nハロウィーン = ハロウィン"
    split: bool = True
    since: str = ""
    until: str = ""
    exclude_users: str = "@seigura"
    exclude_words: str = ""
    exclude_trades: bool = True
    focus: str = ""
    follow: bool = False
    interval: int = 60


@dataclass
class SearchResult:
    classified: list[tuple[Post, str | None]]  # 見つかった投稿と、集計から外した理由（対象なら None）
    report: Path | None

    @property
    def total(self) -> int:
        return len(self.classified)

    @property
    def kept(self) -> int:
        return sum(1 for _, r in self.classified if r is None)


def safe_name(name: str) -> str:
    """ファイル名に使えない文字を _ にする。"""
    return _UNSAFE_RE.sub("_", name.strip()).strip("._") or "search"


def split_list(text: str) -> list[str]:
    return [w for w in _LIST_SEP_RE.split(text) if w]


def today_start() -> str:
    return dt.datetime.now(JST).strftime("%Y-%m-%d 00:00")


def preview_queries(form: Form) -> tuple[list[str], bool]:
    return expand_queries(parse_lines(form.keywords), parse_groups(form.synonyms), split=form.split)


def build_config(form: Form, base_dir: Path = APP_DIR) -> tuple[Config, list[str]]:
    """入力内容から設定を作る。(設定, 知らせること)。入力に誤りがあれば日本語の ValueError。"""
    keywords = parse_lines(form.keywords)
    if not keywords:
        raise ValueError("キーワードを入力してください。")
    queries, truncated = preview_queries(form)
    notes = [f"検索語が多すぎるため、最初の{len(queries)}件だけを検索します。"] if truncated else []
    if not form.since.strip():
        raise ValueError("期間の始まりを入力してください（例: 2026-10-24 00:00）。")
    try:
        since = parse_jst(form.since)
        until = parse_jst(form.until) if form.until.strip() else None
    except ValueError as e:
        raise ValueError(f"期間の{e}") from None
    if until is not None and until <= since:
        raise ValueError("期間の終わりは、始まりより後の日時にしてください。")
    name = form.name.strip() or keywords[0]
    focus = {g[0]: g for g in parse_groups(form.focus)}
    cfg = Config(
        event_name=name,
        queries=queries,
        db=f"data/{safe_name(name)}.sqlite3",
        out_dir=f"reports/{safe_name(name)}",
        since=form.since.strip(),
        until=form.until.strip() or None,
        interval_seconds=max(20, int(form.interval or 60)),
        backfill_max_pages=100,
        keywords=focus,
        exclude_users=split_list(form.exclude_users),
        exclude_words=split_list(form.exclude_words),
        exclude_trades=form.exclude_trades,
        synonyms=[g for g in parse_groups(form.synonyms) if len(g) > 1],
        base_dir=base_dir,
    )
    cfg.validate()
    return cfg, notes


def load_results(cfg: Config, write: bool = True) -> SearchResult:
    """保存済みの投稿から、期間と検索語に合うものを読み、集計してレポートを書く。"""
    with Store(cfg.resolve(cfg.db)) as store:
        posts = store.load_posts(cfg.since_ts, cfg.until_ts, cfg.queries)
    a = analyze(posts, cfg)
    report = None
    if write and posts:
        report = write_reports(a, cfg.resolve(cfg.out_dir), anonymize=cfg.anonymize)["html"]
    return SearchResult(a.classified, report)


def run_search(
    cfg: Config,
    *,
    follow: bool,
    log: Callable[[str], None],
    on_results: Callable[[SearchResult], None],
    should_stop: Callable[[], bool],
    client=None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.time,
) -> None:
    """期間の始まりまでさかのぼって集め、結果を on_results に渡す。

    follow のときは、停止するか期間の終わりになるまで interval_seconds ごとに新しい投稿を取りに行く。
    """
    client = client or YahooRealtimeClient(min_delay=cfg.request_delay_seconds)
    with Store(cfg.resolve(cfg.db)) as store:
        collector = Collector(
            client, store, cfg.queries, since=cfg.since_ts, backfill_max_pages=cfg.backfill_max_pages,
            log=log, should_stop=should_stop,
        )
        for q in cfg.queries:
            if should_stop():
                break
            log(f"「{q}」を検索しています…")
            st = collector.collect_query(q, refresh=True)
            log(f"「{q}」: {st.fetched:,}件を確認（{st.pages}ページ）、新しく保存 {st.added:,}件")
            if st.error:
                log(f"  {st.error}")
        on_results(load_results(cfg))
        if not follow:
            return
        log(f"{cfg.interval_seconds}秒ごとに新しい投稿を取りに行きます。止めるときは「停止」を押してください。")
        while not should_stop():
            if cfg.until_ts and now() >= cfg.until_ts:
                log("期間の終わりになったので、取得を終えました。")
                return
            for _ in range(cfg.interval_seconds):
                if should_stop():
                    return
                sleep(1)
            added = 0
            for q in cfg.queries:
                if should_stop():
                    return
                st = collector.collect_query(q)
                added += st.added
                if st.error:
                    log(f"「{q}」: {st.error}")
            log(f"{dt.datetime.now(JST):%H:%M:%S} 新しい投稿 {added:,}件")
            on_results(load_results(cfg))
