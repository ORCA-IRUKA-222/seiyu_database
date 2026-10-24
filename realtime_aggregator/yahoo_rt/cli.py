"""コマンドライン入口。

  python -m yahoo_rt collect -c config.json   # 定期取得（Ctrl+C で停止）
  python -m yahoo_rt report  -c config.json   # 集計して reports/report.html を作る
  python -m yahoo_rt import  -c config.json 保存したファイル.har
  python -m yahoo_rt doctor  -c config.json   # 取得できるかの診断
"""

from __future__ import annotations

import argparse
import datetime as dt
import glob
import sys
import time
import webbrowser
from pathlib import Path

from . import __version__
from .analyze import analyze
from .collector import Collector
from .config import Config, load_config
from .fetcher import DEFAULT_USER_AGENT, FetchError, YahooRealtimeClient
from .models import JST, fmt_jst
from .parser import parse_text
from .report import print_summary, write_reports
from .store import Store


def _now() -> str:
    return dt.datetime.now(JST).strftime("%H:%M:%S")


def _config(args) -> Config:
    cfg = load_config(args.config) if args.config else Config(base_dir=Path.cwd())
    if getattr(args, "query", None):
        cfg.queries = args.query
    for name in ("since", "until", "db"):
        value = getattr(args, name, None)
        if value:
            setattr(cfg, name, value)
    if getattr(args, "interval", None):
        cfg.interval_seconds = args.interval
    if getattr(args, "max_pages", None) is not None:
        cfg.backfill_max_pages = args.max_pages
    if getattr(args, "anonymize", False):
        cfg.anonymize = True
    cfg.validate()
    return cfg


def _client(cfg: Config) -> YahooRealtimeClient:
    return YahooRealtimeClient(
        user_agent=cfg.user_agent or DEFAULT_USER_AGENT,
        min_delay=cfg.request_delay_seconds,
    )


def _make_report(cfg: Config, store: Store, quiet: bool = False):
    a = analyze(store.load_posts(cfg.since_ts, cfg.until_ts), cfg)
    paths = write_reports(a, cfg.resolve(cfg.out_dir), anonymize=cfg.anonymize)
    if not quiet:
        print_summary(a, paths)
    return a, paths


def cmd_collect(args) -> int:
    cfg = _config(args)
    if not cfg.queries:
        print("検索語がありません。config.json の queries か -q で指定してください。", file=sys.stderr)
        return 2
    store = Store(cfg.resolve(cfg.db))
    collector = Collector(
        _client(cfg), store, cfg.queries, since=cfg.since_ts, backfill_max_pages=cfg.backfill_max_pages
    )
    stop_at = cfg.until_ts
    print(f"収集を開始します（{cfg.interval_seconds}秒ごと、Ctrl+C で停止）")
    print(f"検索語: {' / '.join(cfg.queries)}")
    print(f"保存先: {cfg.resolve(cfg.db)}（現在 {store.count():,}件）")
    if stop_at:
        print(f"{fmt_jst(stop_at)} になったら自動で停止します")
    try:
        while True:
            for st in collector.run_cycle():
                if st.error:
                    print(f"[{_now()}] {st.query}: 取得失敗 - {st.error}")
                else:
                    extra = f"（{st.pages}ページ）" if st.pages > 1 else ""
                    print(f"[{_now()}] {st.query}: 取得 {st.fetched}件{extra} / 新規 {st.added}件")
            print(f"[{_now()}] 累計 {store.count():,}件")
            if args.live_report:
                _, paths = _make_report(cfg, store, quiet=True)
                print(f"[{_now()}] レポート更新: {paths['html']}")
            if args.once or (stop_at and time.time() >= stop_at):
                break
            time.sleep(cfg.interval_seconds)
    except KeyboardInterrupt:
        print("\n収集を停止しました。")
    finally:
        total = store.count()
        store.close()
    print(f"保存済み: {total:,}件。集計は report コマンドで行えます。")
    return 0


def cmd_report(args) -> int:
    cfg = _config(args)
    with Store(cfg.resolve(cfg.db)) as store:
        if store.count() == 0:
            print("保存された投稿がありません。先に collect か import を実行してください。", file=sys.stderr)
            return 1
        _, paths = _make_report(cfg, store)
    if args.open:
        webbrowser.open(paths["html"].resolve().as_uri())
    return 0


def cmd_import(args) -> int:
    cfg = _config(args)
    files: list[str] = []
    for pattern in args.files:  # Windows のコマンドプロンプトはワイルドカードを展開しない
        files.extend(sorted(glob.glob(pattern)) or [pattern])
    total_added = 0
    with Store(cfg.resolve(cfg.db)) as store:
        for name in files:
            path = Path(name)
            if not path.is_file():
                print(f"{name}: ファイルが見つかりません", file=sys.stderr)
                continue
            result = parse_text(path.read_text(encoding="utf-8-sig", errors="replace"))
            label = args.label or (cfg.queries[0] if len(cfg.queries) == 1 else "import")
            added = store.upsert(result.posts, label)
            store.log_fetch(label, "import", True, len(result.posts), added, message=str(path))
            total_added += added
            print(f"{name}: 投稿 {len(result.posts)}件を読み込み（新規 {added}件）")
        print(f"新規 {total_added:,}件を追加しました（累計 {store.count():,}件）")
    return 0


def cmd_doctor(args) -> int:
    cfg = _config(args)
    query = cfg.queries[0] if cfg.queries else "声グラ"
    client = _client(cfg)
    debug_dir = cfg.resolve("debug")
    debug_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    print(f"検索語「{query}」で取得を試します: {client.search_url(query)}")
    try:
        page = client.fetch_latest(query)
    except FetchError as e:
        print(f"NG: {e}")
        print("ネットワーク接続やプロキシ設定を確認してください。")
        return 1
    saved = debug_dir / f"latest_{stamp}.html"
    saved.write_text(page.body, encoding="utf-8")
    r = page.result
    print(f"OK: ページを取得しました（{len(page.body):,}文字）→ {saved}")
    if not r.recognized:
        print("NG: ページ内に投稿データ（__NEXT_DATA__）が見つかりません。Yahoo のページ構造が変わった可能性があります。")
        print(f"   保存したファイル {saved} を開発者に共有してください。")
        return 1
    print(f"{'OK' if r.posts else 'NG'}: 投稿 {len(r.posts)}件を読み取りました（次ページ用ID: {r.cursor}）")
    for p in r.posts[:3]:
        print(f"   - {fmt_jst(p.created_at)} @{p.screen_name} ♥{p.likes} RP{p.retweets}: {p.text[:50]}")
    if r.posts and r.posts[0].raw:
        print(f"   元データの項目: {', '.join(sorted(r.posts[0].raw))}")
    if not r.posts or not r.cursor:
        return 1
    print("過去分のさかのぼり取得を試します…")
    try:
        older = client.fetch_older(query, r.cursor, len(r.posts) + 1)
    except FetchError as e:
        print(f"NG: {e}")
        print("   → 最新分の定期取得だけで動きます。取りこぼし分は HAR の import で補えます。")
        return 0
    saved_older = debug_dir / f"older_{stamp}.txt"
    saved_older.write_text(older.body, encoding="utf-8")
    new_ids = {p.id for p in older.result.posts} - {p.id for p in r.posts}
    if new_ids:
        print(f"OK: さらに古い投稿 {len(new_ids)}件を取得できました → {saved_older}")
    else:
        print(f"NG: 古い投稿を取得できませんでした → {saved_older}")
        print("   → 最新分の定期取得だけで動きます。取りこぼし分は HAR の import で補えます。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="yahoo_rt", description="Yahoo!リアルタイム検索の感想集計ツール")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)

    def common(p, *, period=True):
        p.add_argument("-c", "--config", help="設定ファイル（JSON）")
        p.add_argument("-q", "--query", action="append", help="検索語（複数指定可。設定ファイルより優先）")
        p.add_argument("--db", help="保存先の SQLite ファイル")
        if period:
            p.add_argument("--since", help="この日時以降（JST, 例: 2026-10-24 17:00）")
            p.add_argument("--until", help="この日時より前（JST）")

    p = sub.add_parser("collect", help="投稿を定期的に取得して保存する")
    common(p)
    p.add_argument("--interval", type=int, help="取得間隔（秒、20以上）")
    p.add_argument("--max-pages", type=int, help="1回にさかのぼる最大ページ数（0で無効）")
    p.add_argument("--once", action="store_true", help="1回だけ取得して終了")
    p.add_argument("--live-report", action="store_true", help="取得のたびにレポートを更新する")
    p.set_defaults(func=cmd_collect)

    p = sub.add_parser("report", help="保存した投稿を集計してレポートを作る")
    common(p)
    p.add_argument("--anonymize", action="store_true", help="アカウント名を伏せ、投稿リンクを出さない")
    p.add_argument("--open", action="store_true", help="作成後にブラウザで開く")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("import", help="保存した HTML / JSON / HAR から投稿を取り込む")
    common(p, period=False)
    p.add_argument("files", nargs="+", help="取り込むファイル（*.har など）")
    p.add_argument("--label", help="取り込んだ投稿に付ける検索語ラベル")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("doctor", help="Yahoo から取得・解析できるかを診断する")
    common(p, period=False)
    p.set_defaults(func=cmd_doctor)
    return ap


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows の古いコンソールでも文字化けで落ちないように
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, OSError) as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 2

