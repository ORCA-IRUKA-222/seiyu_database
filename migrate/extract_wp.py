#!/usr/bin/env python3
"""
WordPress（MySQL）から、移行に必要な行だけを PostgreSQL の COPY 形式（TSV）で書き出す。

    pip install pymysql
    export WP_DB_PASSWORD=...            # 未設定なら実行時に入力を求める
    python migrate/extract_wp.py --host db.example.jp --user wp_readonly --database wordpress --out wp_dump
    psql -d seigura -f migrate/load_staging.sql      # wp_dump/ を wp スキーマに読み込む

発行するのは SELECT だけ。読み取り専用ユーザーで、本番 DB のレプリカに対して実行することを推奨する。
"""
import argparse
import getpass
import os
import re
import sys
import time
from pathlib import Path

DEFAULT_POST_TYPES = "post,senior,column,movie,magazine_books,school,howto,directory"
DEFAULT_META_TYPES = "directory,magazine_books"     # カスタムフィールドを取り出す投稿タイプ
DEFAULT_FULL_CONTENT_TYPES = "magazine_books"       # 本文を全文取り出す投稿タイプ（号の目次を解析する）
CONTENT_HEAD_CHARS = 3000                           # それ以外は抜粋生成用に先頭だけ
SKIP_STATUSES = "'trash', 'auto-draft', 'inherit'"
BATCH = 5000


def sql_list(values):
    return ", ".join(f"'{v}'" for v in values) or "''"


def build_queries(prefix, post_types, meta_types, full_types):
    p = prefix
    types = sql_list(post_types)
    metas = sql_list(meta_types)
    fulls = sql_list(full_types)
    # 日時は CAST AS CHAR で文字列のまま受け取る（'0000-00-00 00:00:00' を壊さないため）
    return {
        "posts": [f"""
            SELECT ID, post_type, post_status, post_name, post_title, post_excerpt,
                   CASE WHEN post_type IN ({fulls}) THEN post_content
                        ELSE SUBSTRING(post_content, 1, {CONTENT_HEAD_CHARS}) END,
                   CAST(post_date AS CHAR), CAST(post_date_gmt AS CHAR), CAST(post_modified_gmt AS CHAR)
            FROM {p}posts
            WHERE post_type IN ({types}) AND post_status NOT IN ({SKIP_STATUSES})"""],
        "postmeta": [
            f"""
            SELECT m.post_id, m.meta_key, m.meta_value
            FROM {p}postmeta m
            JOIN {p}posts x ON x.ID = m.post_id
            WHERE x.post_type IN ({types}) AND x.post_status NOT IN ({SKIP_STATUSES})
              AND (m.meta_key = '_thumbnail_id'
                   OR (x.post_type IN ({metas}) AND SUBSTRING(m.meta_key, 1, 1) <> '_'))""",
            f"""
            SELECT m.post_id, m.meta_key, m.meta_value
            FROM {p}postmeta m
            WHERE m.meta_key = '_wp_attached_file'"""],
        "terms": [f"""
            SELECT tt.term_taxonomy_id, t.term_id, tt.taxonomy, t.slug, t.name
            FROM {p}terms t
            JOIN {p}term_taxonomy tt ON tt.term_id = t.term_id
            WHERE tt.taxonomy IN ('post_tag', 'category')"""],
        "term_relationships": [f"""
            SELECT DISTINCT r.object_id, r.term_taxonomy_id
            FROM {p}term_relationships r
            JOIN {p}term_taxonomy tt ON tt.term_taxonomy_id = r.term_taxonomy_id
            JOIN {p}posts x ON x.ID = r.object_id
            WHERE tt.taxonomy IN ('post_tag', 'category')
              AND x.post_type IN ({types}) AND x.post_status NOT IN ({SKIP_STATUSES})"""],
    }


def copy_field(value):
    """PostgreSQL COPY（text 形式）の1フィールドにエスケープする"""
    if value is None:
        return r"\N"
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "replace")
    s = str(value).replace("\x00", "")
    return s.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n").replace("\r", "\\r")


def connect(args):
    if args.sqlite:  # テスト用（verify/ のフィクスチャ）
        import sqlite3
        return sqlite3.connect(args.sqlite), False

    import pymysql
    import pymysql.cursors
    password = os.environ.get("WP_DB_PASSWORD")
    if password is None:
        if not sys.stdin.isatty():
            sys.exit("WP_DB_PASSWORD が未設定です")
        password = getpass.getpass(f"MySQL password for {args.user}@{args.host}: ")
    conn = pymysql.connect(host=args.host, port=args.port, user=args.user, password=password,
                           database=args.database, charset="utf8mb4",
                           cursorclass=pymysql.cursors.SSCursor)
    return conn, True


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=3306)
    ap.add_argument("--user")
    ap.add_argument("--database")
    ap.add_argument("--prefix", default="wp_", help="テーブル接頭辞（既定: wp_）")
    ap.add_argument("--post-types", default=DEFAULT_POST_TYPES,
                    help="取り出す投稿タイプ。wp_migration.post_type_map と名鑑・雑誌の投稿タイプに合わせる")
    ap.add_argument("--meta-types", default=DEFAULT_META_TYPES)
    ap.add_argument("--full-content-types", default=DEFAULT_FULL_CONTENT_TYPES)
    ap.add_argument("--out", default="wp_dump", help="出力ディレクトリ")
    ap.add_argument("--sqlite", help=argparse.SUPPRESS)
    args = ap.parse_args()

    if not re.fullmatch(r"[A-Za-z0-9_]+", args.prefix):
        sys.exit("--prefix が不正です")
    lists = {}
    for name in ("post_types", "meta_types", "full_content_types"):
        values = [v.strip() for v in getattr(args, name).split(",") if v.strip()]
        bad = [v for v in values if not re.fullmatch(r"[a-z0-9_-]+", v)]
        if bad:
            sys.exit(f"投稿タイプ名が不正です: {bad}")
        lists[name] = values
    if not args.sqlite and not (args.user and args.database):
        sys.exit("--user と --database を指定してください")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    conn, is_mysql = connect(args)
    cur = conn.cursor()
    if is_mysql:
        # 4ファイルを同じ時点のデータで揃える
        cur.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")

    started = time.time()
    queries = build_queries(args.prefix, lists["post_types"], lists["meta_types"], lists["full_content_types"])
    for name, sqls in queries.items():
        rows = 0
        with open(out / f"{name}.tsv", "w", encoding="utf-8", newline="\n") as f:
            for sql in sqls:
                cur.execute(sql)
                while True:
                    batch = cur.fetchmany(BATCH)
                    if not batch:
                        break
                    f.writelines("\t".join(copy_field(v) for v in row) + "\n" for row in batch)
                    rows += len(batch)
        print(f"{name:20s} {rows:>9,d} rows", file=sys.stderr)

    if is_mysql:
        conn.rollback()
    conn.close()
    print(f"done in {time.time() - started:.1f}s -> {out}/", file=sys.stderr)


if __name__ == "__main__":
    main()
