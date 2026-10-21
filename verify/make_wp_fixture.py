"""
移行テスト用に、WordPress と同じテーブル構造の SQLite DB を作る（人物・作品はすべて架空）。
    python make_wp_fixture.py <出力先.db>
"""
import sqlite3
import sys

DDL = """
CREATE TABLE wp_posts (ID INTEGER PRIMARY KEY, post_type TEXT, post_status TEXT, post_name TEXT,
  post_title TEXT, post_excerpt TEXT, post_content TEXT,
  post_date TEXT, post_date_gmt TEXT, post_modified_gmt TEXT);
CREATE TABLE wp_postmeta (meta_id INTEGER PRIMARY KEY, post_id INTEGER, meta_key TEXT, meta_value TEXT);
CREATE TABLE wp_terms (term_id INTEGER PRIMARY KEY, name TEXT, slug TEXT);
CREATE TABLE wp_term_taxonomy (term_taxonomy_id INTEGER PRIMARY KEY, term_id INTEGER, taxonomy TEXT);
CREATE TABLE wp_term_relationships (object_id INTEGER, term_taxonomy_id INTEGER, PRIMARY KEY (object_id, term_taxonomy_id));
"""

ZERO = "0000-00-00 00:00:00"


def post(cur, pid, ptype, status, name, title, content="", date="2026-01-01 12:00:00", excerpt=""):
    gmt = ZERO if status == "draft" else date.replace(" 12:", " 03:")
    cur.execute("INSERT INTO wp_posts VALUES (?,?,?,?,?,?,?,?,?,?)",
                (pid, ptype, status, name, title, excerpt, content, date, gmt, gmt))


def meta(cur, pid, key, value):
    cur.execute("INSERT INTO wp_postmeta (post_id, meta_key, meta_value) VALUES (?,?,?)", (pid, key, value))


def term(cur, tid, name, slug, taxonomy="post_tag"):
    cur.execute("INSERT INTO wp_terms VALUES (?,?,?)", (tid, name, slug))
    cur.execute("INSERT INTO wp_term_taxonomy VALUES (?,?,?)", (tid + 10000, tid, taxonomy))  # tt_id ≠ term_id


def tag(cur, pid, *tids):
    for t in tids:
        cur.execute("INSERT INTO wp_term_relationships VALUES (?,?)", (pid, t + 10000))


def directory(cur, pid, title, status="publish", thumb=None, **fields):
    post(cur, pid, "directory", status, str(pid), title)
    meta(cur, pid, "_edit_lock", "1700000000:1")
    for k, v in fields.items():
        meta(cur, pid, k, v)
        meta(cur, pid, "_" + k, "field_" + k)          # ACF の参照キー（取り込まない）
    if thumb:
        meta(cur, pid, "_thumbnail_id", str(thumb))


def main(path):
    db = sqlite3.connect(path)
    cur = db.cursor()
    cur.executescript(DDL)

    # --- 添付ファイル ---
    meta(cur, 9001, "_wp_attached_file", "2024/05/hiyori.jpg")
    meta(cur, 9002, "_wp_attached_file", "2026/09/photo.jpg")
    meta(cur, 9003, "_wp_attached_file", "2026/10/cover11.jpg")

    # --- 名鑑 ---
    directory(cur, 2001, "春風　ひより", thumb=9001,
              furigana="ハルカゼ ヒヨリ", birthplace="東京都", birthday="３月１５日", bloodtype="A型",
              office="スタジオ青嵐", hobby="カメラ",
              works="アニメ『きらめきステージ!』天音ルカ、『星降る町の喫茶店』ミラ（第2期）、ゲーム『ドリームアーク』ミラ役ほか")
    directory(cur, 2002, "月島 蒼", furigana="つきしま あおい", birthplace="神奈川県", birthday="1999年10月21日",
              bloodtype="O", office="オフィス木漏れ日", works="吹き替え『ロスト・ハーバー』ジャック")
    directory(cur, 2003, "森野 くるみ", status="draft", furigana="もりの くるみ", birthday="7/7",
              office="スタジオ青嵐", hobby="絵本集め")
    directory(cur, 2004, "桐生 拓真", furigana="", office="アトリエ灯")           # ふりがな欠落
    directory(cur, 2007, "佐藤 花", furigana="さとう はな", birthday="不明")      # 同姓同名
    directory(cur, 2008, "佐藤 花", furigana="さとう はな", bloodtype="AB")
    post(cur, 2009, "directory", "trash", "2009", "ゴミ箱の人")

    # --- タグ・カテゴリー ---
    term(cur, 301, "春風ひより", "harukaze-hiyori")
    term(cur, 302, "月島蒼", "%e6%9c%88%e5%b3%b6%e8%92%bc")
    term(cur, 303, "森野くるみ", "morino")
    term(cur, 304, "ソラノート", "soranote")
    term(cur, 305, "イベント・ライブ", "event")
    term(cur, 306, "桐生拓真", "kiryu")
    term(cur, 307, "佐藤花", "sato-hana")
    term(cur, 308, "春風日和", "harukaze-old")    # 旧芸名（名鑑には未登録）
    term(cur, 401, "NEWS", "news", "category")
    term(cur, 402, "特集", "feature", "category")

    # --- 記事 ---
    post(cur, 100, "post", "publish", "100", "春風ひより、写真集発売", "<p>春風ひよりさんの<b>1st写真集</b>が発売。</p>",
         "2026-09-01 12:00:00")
    meta(cur, 100, "_thumbnail_id", "9002")
    tag(cur, 100, 301); tag(cur, 100, 401)
    post(cur, 101, "post", "publish", "101", "春風ひより×月島蒼 対談", date="2026-08-15 12:00:00")
    tag(cur, 101, 301, 302, 401)
    post(cur, 102, "post", "draft", "", "（下書き）春風ひより 新番組")
    tag(cur, 102, 301)
    post(cur, 103, "post", "future", "103", "（予約）春風ひより 新ビジュアル", date="2099-01-01 12:00:00")
    tag(cur, 103, 301)
    post(cur, 104, "senior", "publish", "104", "月島蒼インタビュー", date="2026-07-01 12:00:00")
    tag(cur, 104, 302)
    post(cur, 105, "movie", "publish", "105", "ソラノート 2nd LIVE レポート", date="2026-07-20 12:00:00")
    tag(cur, 105, 304, 305)
    post(cur, 106, "column", "publish", "hiyori12", "春風ひよりの陽だまり日記 第12回", date="2026-05-01 12:00:00")
    tag(cur, 106, 301)
    post(cur, 107, "column", "publish", "hiyori13", "春風ひよりの陽だまり日記 第13回 ゲスト：森野くるみ",
         date="2026-06-01 12:00:00")
    tag(cur, 107, 301, 303)
    post(cur, 108, "column", "publish", "hiyori-sp", "春風ひよりの陽だまり日記 特別編", date="2026-06-15 12:00:00")
    post(cur, 109, "column", "publish", "hiyori_13", "春風ひよりの陽だまり日記 第13回（再掲）", date="2026-06-20 12:00:00")
    post(cur, 110, "glossary", "publish", "110", "用語：アフレコ")
    post(cur, 111, "post", "trash", "111", "ゴミ箱の記事")
    tag(cur, 111, 301)
    post(cur, 113, "post", "publish", "113", "特集とニュースの両方", date="2026-04-01 12:00:00")
    tag(cur, 113, 402, 401)
    post(cur, 114, "post", "publish", "114", "春風日和、初のソロイベント", date="2020-11-01 12:00:00")
    tag(cur, 114, 308)
    post(cur, 115, "post", "publish", "115", "タブ\tと\\バックスラッシュ", "1行目\r\n2行目\tタブ", "2026-02-01 12:00:00")

    # --- 雑誌 ---
    post(cur, 200, "magazine_books", "publish", "200", "声優グランプリ　2026年　11月号", date="2026-09-20 12:00:00",
         content=("<p>法人別購入特典情報はコチラ</p>"
                  "<p>【表紙+巻頭大特集】<br />春風ひより<br />1st写真集発売！</p>"
                  "<p>【Pickup】<br />月島蒼<br />ソラノート（春風ひより・森野くるみ）</p>"
                  "<p>【好評連載中】<br />森野くるみ「くるみの絵本棚」<br />春風ひより「ひよりの陽だまり日記」<br />…and more</p>"
                  "<p>声優グランプリ11月号 法人別購入特典</p><p>＜アニメイト＞・佐藤 花ブロマイド</p>"))
    meta(cur, 200, "release_date", "2026年10月09日")
    meta(cur, 200, "price", "1610円（税込）")
    meta(cur, 200, "_thumbnail_id", "9003")
    post(cur, 201, "magazine_books", "publish", "201", "声優グランプリ 2026年12月号", date="2026-10-15 12:00:00",
         content="<p>表紙+巻頭大特集 月島 蒼 カバーストーリー</p>")
    meta(cur, 201, "price", "1610円（税込）")
    post(cur, 202, "magazine_books", "publish", "202", "春風ひより1st写真集 ひより", date="2026-08-01 12:00:00")
    tag(cur, 202, 301)
    post(cur, 203, "magazine_books", "draft", "203", "声優グランプリ 2026年11月号")

    db.commit()
    db.close()


if __name__ == "__main__":
    main(sys.argv[1])
