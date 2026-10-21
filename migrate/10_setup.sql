-- =====================================================================
-- WordPress → 人物中心データベース 移行  10: 準備
-- sql/01_schema.sql・sql/02_views.sql の後に1回だけ実行する。
--
--   wp           … extract_wp.py の出力をそのまま入れる作業用スキーマ（毎回入れ替え）
--   wp_migration … 設定・対応表・取り込み履歴・問題ログ・補助関数
-- =====================================================================

CREATE SCHEMA wp;
CREATE SCHEMA wp_migration;

-- ---------------------------------------------------------------------
-- 作業用スキーマ（列の順番は extract_wp.py の SELECT と一致させる）
-- ---------------------------------------------------------------------
CREATE TABLE wp.posts (
  id                bigint PRIMARY KEY,
  post_type         text   NOT NULL,
  post_status       text   NOT NULL,
  post_name         text,
  post_title        text,
  post_excerpt      text,
  post_content      text,
  post_date         text,            -- サイトのタイムゾーン（'0000-00-00 00:00:00' もありうる）
  post_date_gmt     text,
  post_modified_gmt text
);
CREATE INDEX ON wp.posts (post_type);

CREATE TABLE wp.postmeta (
  post_id    bigint NOT NULL,
  meta_key   text   NOT NULL,
  meta_value text
);
CREATE INDEX ON wp.postmeta (post_id, meta_key);
CREATE INDEX ON wp.postmeta (meta_key);

CREATE TABLE wp.terms (
  term_taxonomy_id bigint PRIMARY KEY,
  term_id          bigint NOT NULL,
  taxonomy         text   NOT NULL,
  slug             text   NOT NULL,
  name             text   NOT NULL
);

CREATE TABLE wp.term_relationships (
  object_id        bigint NOT NULL,
  term_taxonomy_id bigint NOT NULL,
  PRIMARY KEY (object_id, term_taxonomy_id)
);

-- ---------------------------------------------------------------------
-- 設定（1行だけ）
-- ---------------------------------------------------------------------
CREATE TABLE wp_migration.settings (
  singleton                boolean PRIMARY KEY DEFAULT true CHECK (singleton),
  wp_timezone              text    NOT NULL DEFAULT 'Asia/Tokyo',
  uploads_base_url         text    NOT NULL DEFAULT 'https://seigura.com/wp-content/uploads/',
  directory_post_type      text    NOT NULL DEFAULT 'directory',
  issue_post_type          text    NOT NULL DEFAULT 'magazine_books',
  -- 号として扱うタイトル（NFKC 正規化後に照合。(年)(月)(増刊などの残り)）
  issue_title_regex        text    NOT NULL DEFAULT '^声優グランプリ\s*([0-9]{4})年\s*([0-9]{1,2})月号\s*(.*)$',
  -- 号ページ本文のうち、これ以降（法人別購入特典など）は人物の抽出に使わない
  issue_content_stop_regex text    NOT NULL DEFAULT '\.\.\.\s*and more|声優グランプリ\s*[0-9]{1,2}月号\s*(法人別)?購入特典',
  serial_post_type         text    NOT NULL DEFAULT 'column',
  -- 連載回のスラッグ → (連載キー)(回数)。例: akabane144 → akabane / 144
  serial_slug_regex        text    NOT NULL DEFAULT '^([a-z][a-z_-]*[a-z])[-_]?([0-9]+)$',
  auto_link_tags           boolean NOT NULL DEFAULT true,   -- 名前が一意に一致するタグを人物に自動で紐付ける
  auto_issue_items         boolean NOT NULL DEFAULT false   -- 号の目次から抽出した掲載情報を自動で登録する
);
INSERT INTO wp_migration.settings DEFAULT VALUES;

-- 投稿タイプ → 記事種別・URL（{id} {slug} {category} を置換）
CREATE TABLE wp_migration.post_type_map (
  post_type     text PRIMARY KEY,
  article_type  text NOT NULL
                CHECK (article_type IN ('news', 'interview', 'column', 'event', 'magazine', 'school', 'other')),
  path_template text NOT NULL
);
INSERT INTO wp_migration.post_type_map VALUES
  ('post',           'news',      '/{category}/{id}/'),
  ('senior',         'interview', '/senior/{id}/'),
  ('column',         'column',    '/column/{slug}/'),
  ('movie',          'event',     '/movie/{id}/'),
  ('magazine_books', 'magazine',  '/magazine_books/{id}/'),   -- 号以外（写真集など）
  ('school',         'school',    '/school/{id}/'),
  ('howto',          'other',     '/howto/{id}/');

-- 名鑑・雑誌のカスタムフィールド（meta_key）との対応。
-- 実際のキー名は v_meta_key_guesses で確認して登録する
CREATE TABLE wp_migration.field_map (
  field    text PRIMARY KEY
           CHECK (field IN ('name_kana', 'name_en', 'birthplace', 'birthday', 'blood_type', 'agency',
                            'hobbies', 'works', 'biography', 'issue_release_date', 'issue_price')),
  meta_key text NOT NULL
);

-- ふりがなが WordPress 側で欠けている・読めない人物の手動指定
CREATE TABLE wp_migration.kana_overrides (
  person_id bigint PRIMARY KEY,
  name_kana text   NOT NULL
);

-- WordPress から取り込んだ行（削除・非公開化の検出に使う）
CREATE TABLE wp_migration.imported (
  kind text   NOT NULL CHECK (kind IN ('tag', 'person', 'article', 'issue')),
  id   bigint NOT NULL,
  PRIMARY KEY (kind, id)
);

-- 号の目次から抽出した掲載候補（未登録の号の分だけ毎回作り直す）
CREATE TABLE wp_migration.issue_item_candidates (
  issue_id     bigint  NOT NULL,
  section_no   integer NOT NULL,
  heading      text,                 -- 【】内の見出し。NULL = 見出しなし
  item_type    text    NOT NULL,
  serial_title text,                 -- 連載欄の「」内
  person_id    bigint  NOT NULL,
  matched_name text
);
CREATE INDEX ON wp_migration.issue_item_candidates (issue_id);

-- 掲載情報を登録済みの号（以後は自動で触らない。編集部の修正を上書きしないため）
CREATE TABLE wp_migration.issue_seeds (
  issue_id  bigint PRIMARY KEY,
  seeded_at timestamptz NOT NULL DEFAULT now()
);

-- 直近の実行で見つかった問題（実行のたびに作り直す）
CREATE TABLE wp_migration.problems (
  problem_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  kind       text   NOT NULL,
  source_id  bigint,
  message    text   NOT NULL,
  value      text
);

CREATE TABLE wp_migration.runs (
  run_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  started_at  timestamptz NOT NULL DEFAULT clock_timestamp(),
  finished_at timestamptz,
  steps       jsonb
);

-- ---------------------------------------------------------------------
-- 新DBで直接作る行の採番を 10億 から始める。
-- WordPress の ID（post ID・term_id）と衝突させないため。
-- ---------------------------------------------------------------------
DO $$
DECLARE
  r record;
BEGIN
  FOR r IN SELECT * FROM (VALUES ('persons', 'person_id'), ('tags', 'tag_id'),
                                 ('articles', 'article_id'), ('magazine_issues', 'issue_id')) v(tbl, col)
  LOOP
    EXECUTE format(
      'SELECT setval(pg_get_serial_sequence(%L, %L), GREATEST(1000000000, (SELECT COALESCE(max(%I), 0) + 1 FROM %I)), false)',
      r.tbl, r.col, r.col, r.tbl);
  END LOOP;
END $$;

-- ---------------------------------------------------------------------
-- 補助関数
-- ---------------------------------------------------------------------

-- WordPress の日時文字列 → timestamptz（GMT 列を優先、ゼロ日付は NULL）
CREATE FUNCTION wp_migration.to_ts(p_local text, p_gmt text, p_tz text) RETURNS timestamptz
LANGUAGE sql STABLE AS $$
  SELECT CASE
           WHEN p_gmt   ~ '^[1-9][0-9]{3}-' THEN p_gmt::timestamp AT TIME ZONE 'UTC'
           WHEN p_local ~ '^[1-9][0-9]{3}-' THEN p_local::timestamp AT TIME ZONE p_tz
         END
$$;

-- HTML を素のテキストに（タグ除去・主な実体参照の復元・空白の圧縮）
CREATE FUNCTION wp_migration.strip_html(s text) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
  SELECT btrim(regexp_replace(
           replace(replace(replace(replace(replace(replace(
             regexp_replace(COALESCE(s, ''), '<[^>]*>', ' ', 'g'),
             '&nbsp;', ' '), '&amp;', '&'), '&lt;', '<'), '&gt;', '>'), '&quot;', '"'), '&#039;', ''''),
           '\s+', ' ', 'g'))
$$;

-- ふりがなの整形: 全角半角統一 → カタカナをひらがなに → 空白を1つに
CREATE FUNCTION wp_migration.clean_kana(s text) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
  SELECT NULLIF(btrim(regexp_replace(
           translate(normalize(COALESCE(s, ''), NFKC),
                     'ァアィイゥウェエォオカガキギクグケゲコゴサザシジスズセゼソゾタダチヂッツヅテデトドナニヌネノハバパヒビピフブプヘベペホボポマミムメモャヤュユョヨラリルレロヮワヰヱヲンヴヵヶ',
                     'ぁあぃいぅうぇえぉおかがきぎくぐけげこごさざしじすずせぜそぞただちぢっつづてでとどなにぬねのはばぱひびぴふぶぷへべぺほぼぽまみむめもゃやゅゆょよらりるれろゎわゐゑをんゔゕゖ'),
           '\s+', ' ', 'g')), '')
$$;

-- 誕生日: '9月 9日' '１９９９年１０月２１日' '1999/10/21' '10/21' など
CREATE FUNCTION wp_migration.parse_birthday(s text, OUT y smallint, OUT m smallint, OUT d smallint)
LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE WHEN r[1]::int BETWEEN 1900 AND 2100 THEN r[1]::smallint END, r[2]::smallint, r[3]::smallint
  FROM (SELECT COALESCE(
                 regexp_match(normalize(COALESCE(s, ''), NFKC),
                              '(?:([0-9]{4})\s*年)?\s*([0-9]{1,2})\s*月\s*([0-9]{1,2})\s*日'),
                 regexp_match(normalize(COALESCE(s, ''), NFKC),
                              '^\s*(?:([0-9]{4})[/.-])?([0-9]{1,2})[/.-]([0-9]{1,2})\s*$')) AS r) x
  WHERE r[2]::int BETWEEN 1 AND 12 AND r[3]::int BETWEEN 1 AND 31
$$;

CREATE FUNCTION wp_migration.parse_blood(s text) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE WHEN v IN ('A', 'B', 'O', 'AB') THEN v END
  FROM (SELECT upper(regexp_replace(normalize(COALESCE(s, ''), NFKC), '[\s型]', '', 'g')) AS v) x
$$;

-- '2026年08月07日' '2026/8/7' → date（存在しない日付は NULL）
CREATE FUNCTION wp_migration.parse_jp_date(s text) RETURNS date
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
  r text[] := regexp_match(normalize(COALESCE(s, ''), NFKC),
                           '([0-9]{4})\s*[年/.-]\s*([0-9]{1,2})\s*[月/.-]\s*([0-9]{1,2})');
BEGIN
  RETURN make_date(r[1]::int, r[2]::int, r[3]::int);
EXCEPTION WHEN others THEN
  RETURN NULL;
END $$;

-- '1610円（税込）' → 1610
CREATE FUNCTION wp_migration.parse_price(s text) RETURNS integer
LANGUAGE sql IMMUTABLE AS $$
  SELECT replace((regexp_match(normalize(COALESCE(s, ''), NFKC), '([0-9][0-9,]*)\s*円'))[1], ',', '')::integer
$$;

-- 名鑑の「出演作品」欄を分解する
--   'アニメ『A』役1、『B』役2、ゲーム『C』役3ほか'
--   → (アニメ, anime, A, 役1) (アニメ, anime, B, 役2) (ゲーム, game, C, 役3)
-- 区分（アニメ・ゲーム…）は次の区分が出てくるまで引き継ぐ。
CREATE FUNCTION wp_migration.parse_works(s text)
RETURNS TABLE (ord integer, category text, work_type text, title text, character_name text)
LANGUAGE sql IMMUTABLE AS $$
  WITH m AS (
    SELECT x.ord::integer AS ord,
           NULLIF(btrim(x.r[1], E' \t\r\n　/／・:：'), '') AS prefix,
           btrim(x.r[2], ' 　') AS title,
           NULLIF(btrim(regexp_replace(
                    regexp_replace(btrim(x.r[3], E' \t\r\n　'), '^[（(](.*)[）)]$', '\1'),
                    '(役)?(ほか|他|など)$', ''), ' 　'), '') AS character_name
    FROM regexp_matches(COALESCE(s, ''), '([^『』、，,\n]*)『([^』]+)』([^『』、，,\n]*)', 'g')
         WITH ORDINALITY AS x(r, ord)
  ), g AS (
    SELECT m.*, count(m.prefix) OVER (ORDER BY m.ord) AS grp FROM m
  ), c AS (
    SELECT g.*, max(g.prefix) OVER (PARTITION BY g.grp) AS cat FROM g
  )
  SELECT c.ord, c.cat,
         CASE
           WHEN normalize(c.cat, NFKC) ~ 'ゲーム|アプリ'             THEN 'game'
           WHEN normalize(c.cat, NFKC) ~ '吹き?替|外画|海外ドラマ|洋画' THEN 'dub'
           WHEN normalize(c.cat, NFKC) ~ 'アニメ|OVA|OAD'            THEN 'anime'
           WHEN normalize(c.cat, NFKC) ~ '映画|劇場'                 THEN 'film'
           WHEN normalize(c.cat, NFKC) ~ 'CD'                        THEN 'drama_cd'
           WHEN normalize(c.cat, NFKC) ~ 'ラジオ'                    THEN 'radio'
           WHEN normalize(c.cat, NFKC) ~ '舞台|ミュージカル|朗読'    THEN 'stage'
           ELSE 'other'
         END,
         c.title, c.character_name
  FROM c
  WHERE c.title <> ''
$$;

-- 号ページの見出し → 企画の種類（1つの見出しが複数の種類になることがある）
--   '表紙+巻頭大特集' → {cover, main_feature}   '好評連載中' → {serial}   NULL → {appearance}
CREATE FUNCTION wp_migration.issue_heading_types(h text) RETURNS text[]
LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE WHEN h IS NULL THEN ARRAY['appearance'] ELSE
    COALESCE(NULLIF(array_remove(ARRAY[
      CASE WHEN h ~ '表紙|カバー'                         THEN 'cover' END,
      CASE WHEN h ~ '巻頭'                                THEN 'main_feature' END,
      CASE WHEN h ~* '巻末|特集|pick ?up|ピックアップ|special|スペシャル' AND h !~ '巻頭|連載' THEN 'feature' END,
      CASE WHEN h ~ 'グラビア'                            THEN 'gravure' END,
      CASE WHEN h ~ 'インタビュー'                        THEN 'interview' END,
      CASE WHEN h ~ '連載'                                THEN 'serial' END,
      CASE WHEN h ~ '付録|ポスター|ピンナップ|ブロマイド' THEN 'appendix' END
    ], NULL), '{}'), ARRAY['feature']) END
$$;

-- ---------------------------------------------------------------------
-- 確認用ビュー
-- ---------------------------------------------------------------------

-- 名鑑・雑誌のカスタムフィールド一覧と、値の傾向から推定した対応先
CREATE VIEW wp_migration.v_meta_key_guesses AS
WITH v AS (
  SELECT p.post_type, pm.post_id, pm.meta_key, btrim(pm.meta_value) AS val
  FROM wp.posts p
  JOIN wp.postmeta pm ON pm.post_id = p.id
  CROSS JOIN wp_migration.settings cfg
  WHERE p.post_type IN (cfg.directory_post_type, cfg.issue_post_type)
    AND left(pm.meta_key, 1) <> '_'
    AND btrim(COALESCE(pm.meta_value, '')) <> ''
), k AS (
  SELECT post_type, meta_key,
         count(DISTINCT post_id)                                                        AS posts_filled,
         avg((val ~ '^[ぁ-ゖー・ 　]+$')::int)                                          AS r_hiragana,
         avg((normalize(val, NFKC) ~ '[0-9]{1,2}\s*月\s*[0-9]{1,2}\s*日')::int)        AS r_month_day,
         avg((normalize(val, NFKC) ~ '^\s*(A|B|O|AB)\s*型?\s*$')::int)                 AS r_blood,
         avg((val ~ '『')::int)                                                         AS r_works,
         avg((val ~ '(都|道|府|県)\s*$')::int)                                          AS r_pref,
         avg((normalize(val, NFKC) ~ '[0-9]{4}\s*年\s*[0-9]{1,2}\s*月\s*[0-9]{1,2}\s*日')::int) AS r_full_date,
         avg((normalize(val, NFKC) ~ '[0-9][0-9,]*\s*円')::int)                        AS r_yen,
         (array_agg(left(val, 40) ORDER BY post_id DESC))[1:3]                          AS samples
  FROM v
  GROUP BY post_type, meta_key
)
SELECT k.post_type, k.meta_key, k.posts_filled, k.samples,
       CASE
         WHEN k.post_type = cfg.issue_post_type THEN
           CASE WHEN k.r_full_date >= 0.5 OR k.meta_key ~* 'release|hatsubai' THEN 'issue_release_date'
                WHEN k.r_yen >= 0.5       OR k.meta_key ~* 'price|teika'      THEN 'issue_price' END
         ELSE
           CASE WHEN k.r_hiragana >= 0.9  OR k.meta_key ~* 'yomi|kana|furigana|ruby'        THEN 'name_kana'
                WHEN k.r_blood >= 0.5     OR k.meta_key ~* 'blood|ketsu'                    THEN 'blood_type'
                WHEN k.r_month_day >= 0.5 OR k.meta_key ~* 'birthday|tanjo'                 THEN 'birthday'
                WHEN k.r_works >= 0.5     OR k.meta_key ~* 'work|sakuhin|shutsuen|syutsuen' THEN 'works'
                WHEN k.r_pref >= 0.5      OR k.meta_key ~* 'birthplace|shusshin|syusshin|hometown' THEN 'birthplace'
                WHEN k.meta_key ~* 'jimusho|jimusyo|agency|office|shozoku|syozoku|production' THEN 'agency'
                WHEN k.meta_key ~* 'hobby|hobbies|shumi|syumi|tokugi|skill'                 THEN 'hobbies'
                WHEN k.meta_key ~* 'profile|bio|introduction'                               THEN 'biography'
           END
       END AS guessed_field
FROM k
CROSS JOIN wp_migration.settings cfg;

-- 推定結果から field_map に入れる候補（各項目で入力率が最も高いキー）
CREATE VIEW wp_migration.v_field_map_guesses AS
SELECT DISTINCT ON (guessed_field) guessed_field AS field, meta_key, posts_filled, samples
FROM wp_migration.v_meta_key_guesses
WHERE guessed_field IS NOT NULL
ORDER BY guessed_field, posts_filled DESC, meta_key;

-- 連載のレギュラー候補（半数以上の回にタグが付いている人物）
CREATE VIEW wp_migration.v_serial_member_candidates AS
WITH eps AS (
  SELECT serial_id, count(*) AS total_episodes FROM articles WHERE serial_id IS NOT NULL GROUP BY serial_id
)
SELECT se.serial_id, se.title, t.person_id, p.display_name,
       count(DISTINCT a.article_id) AS tagged_episodes, e.total_episodes
FROM articles a
JOIN eps e           ON e.serial_id = a.serial_id
JOIN serials se      ON se.serial_id = a.serial_id
JOIN article_tags atg ON atg.article_id = a.article_id
JOIN tags t          ON t.tag_id = atg.tag_id AND t.person_id IS NOT NULL
JOIN persons p       ON p.person_id = t.person_id
WHERE NOT EXISTS (SELECT 1 FROM serial_members sm WHERE sm.serial_id = a.serial_id AND sm.person_id = t.person_id)
GROUP BY se.serial_id, se.title, t.person_id, p.display_name, e.total_episodes
HAVING count(DISTINCT a.article_id) >= greatest(2, e.total_episodes * 0.5);

CREATE VIEW wp_migration.v_problem_summary AS
SELECT kind, message, count(*) AS rows, (array_agg(source_id ORDER BY source_id))[1:5] AS sample_ids
FROM wp_migration.problems
GROUP BY kind, message
ORDER BY kind, count(*) DESC;
