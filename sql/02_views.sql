-- =====================================================================
-- seigura.com 人物中心データベース  02: ビュー・関数
--
-- 個人ページの4セクションはすべてビューから自動で組み立てる。
--   v_person_articles  関連記事     … 人物タグ（別名タグ含む）が付いた公開記事
--   v_person_events    出演イベント … event_performers
--   v_person_issues    掲載号       … issue_item_persons
--   v_person_serials   連載         … serial_members + 連載回へのタグ付け・誌面掲載
-- ユニットに紐付いたコンテンツは、メンバーにも「在籍期間内のもの」だけ表示する。
-- 個人ページ全体は person_page(person_id) が JSON 1つで返す。
-- =====================================================================

-- ---------------------------------------------------------------------
-- 補助関数
-- ---------------------------------------------------------------------

CREATE FUNCTION code_label(p_domain text, p_code text) RETURNS text
LANGUAGE sql STABLE AS $$
  SELECT COALESCE((SELECT label FROM code_labels WHERE domain = p_domain AND code = p_code), p_code)
$$;

-- 日付 d がユニット在籍期間 [valid_from, valid_until] に入るか（NULL は無期限）
CREATE FUNCTION within_membership(d date, valid_from date, valid_until date) RETURNS boolean
LANGUAGE sql IMMUTABLE AS $$
  SELECT (valid_from IS NULL OR d >= valid_from) AND (valid_until IS NULL OR d <= valid_until)
$$;

-- 統合済みIDを統合先IDに解決（旧URL・旧タグからのリダイレクト用）
CREATE FUNCTION canonical_person_id(p_person_id bigint) RETURNS bigint
LANGUAGE sql STABLE AS $$
  WITH RECURSIVE chain AS (
    SELECT person_id, merged_into, 1 AS depth FROM persons WHERE person_id = p_person_id
    UNION ALL
    SELECT p.person_id, p.merged_into, c.depth + 1
    FROM persons p JOIN chain c ON p.person_id = c.merged_into
    WHERE c.depth < 10
  )
  SELECT person_id FROM chain WHERE merged_into IS NULL
$$;

-- ---------------------------------------------------------------------
-- 基本ビュー
-- ---------------------------------------------------------------------

-- 公開中の記事（公開判定の定義はここだけに置く）
CREATE VIEW published_articles AS
SELECT *
FROM articles
WHERE status IN ('published', 'scheduled')
  AND published_at <= now();

-- 人物が「自分のコンテンツ」として扱う主体 = 本人 + 在籍ユニット（期間つき）
CREATE VIEW person_scope AS
SELECT p.person_id,
       p.person_id      AS subject_id,
       NULL::bigint     AS via_unit_id,
       NULL::text       AS via_unit_name,
       NULL::date       AS valid_from,
       NULL::date       AS valid_until
FROM persons p
UNION ALL
SELECT um.member_id,
       um.unit_id,
       um.unit_id,
       u.display_name,
       um.joined_on,
       um.left_on
FROM unit_members um
JOIN persons u ON u.person_id = um.unit_id;

-- ---------------------------------------------------------------------
-- 個人ページ: 関連記事
-- ---------------------------------------------------------------------
CREATE VIEW v_person_articles AS
SELECT DISTINCT ON (s.person_id, a.article_id)
       s.person_id,
       a.article_id,
       a.article_type,
       a.title,
       a.path,
       a.excerpt,
       a.thumbnail_url,
       a.serial_id,
       a.episode_no,
       a.published_at,
       s.via_unit_id,                 -- NULL = 本人のタグ / 値あり = ユニット経由
       s.via_unit_name
FROM person_scope s
JOIN tags t               ON t.person_id = s.subject_id
JOIN article_tags atg     ON atg.tag_id = t.tag_id
JOIN published_articles a ON a.article_id = atg.article_id
WHERE within_membership(a.published_at::date, s.valid_from, s.valid_until)
ORDER BY s.person_id, a.article_id, s.via_unit_id NULLS FIRST;

-- ---------------------------------------------------------------------
-- 個人ページ: 出演イベント（出演キャンセルは除外）
-- ---------------------------------------------------------------------
CREATE VIEW v_person_events AS
SELECT DISTINCT ON (s.person_id, e.event_id)
       s.person_id,
       e.event_id,
       e.title,
       e.event_type,
       e.starts_at,
       e.ends_at,
       e.venue,
       e.official_url,
       e.status,
       ep.role,
       COALESCE(e.ends_at, e.starts_at)::date >= current_date AS is_upcoming,
       s.via_unit_id,
       s.via_unit_name
FROM person_scope s
JOIN event_performers ep ON ep.person_id = s.subject_id AND ep.appearance = 'confirmed'
JOIN events e            ON e.event_id = ep.event_id AND e.is_published
WHERE within_membership(e.starts_at::date, s.valid_from, s.valid_until)
ORDER BY s.person_id, e.event_id, s.via_unit_id NULLS FIRST;

-- ---------------------------------------------------------------------
-- 個人ページ: 掲載号（企画単位 → 号単位に集約）
-- ---------------------------------------------------------------------
CREATE VIEW v_person_issue_items AS
SELECT DISTINCT ON (s.person_id, ii.issue_item_id)
       s.person_id,
       mi.issue_id,
       mi.publication,
       mi.issue_label,
       mi.release_date,
       mi.path,
       mi.cover_image_url,
       ii.issue_item_id,
       ii.item_type,
       ii.title AS item_title,
       ii.page_from,
       ii.page_to,
       ii.serial_id,
       ii.sort_order,
       s.via_unit_id,
       s.via_unit_name
FROM person_scope s
JOIN issue_item_persons iip ON iip.person_id = s.subject_id
JOIN issue_items ii         ON ii.issue_item_id = iip.issue_item_id
JOIN magazine_issues mi     ON mi.issue_id = ii.issue_id AND mi.is_published
WHERE within_membership(mi.release_date, s.valid_from, s.valid_until)
ORDER BY s.person_id, ii.issue_item_id, s.via_unit_id NULLS FIRST;

CREATE VIEW v_person_issues AS
SELECT person_id,
       issue_id,
       publication,
       issue_label,
       release_date,
       path,
       cover_image_url,
       bool_or(item_type = 'cover') AS is_cover,
       jsonb_agg(jsonb_build_object(
                   'type',          item_type,
                   'label',         code_label('issue_item', item_type),
                   'title',         item_title,
                   'page_from',     page_from,
                   'page_to',       page_to,
                   'via_unit_name', via_unit_name)
                 ORDER BY sort_order, page_from NULLS FIRST, issue_item_id) AS items
FROM v_person_issue_items
GROUP BY person_id, issue_id, publication, issue_label, release_date, path, cover_image_url;

-- ---------------------------------------------------------------------
-- 個人ページ: 連載
--   レギュラー … serial_members（ユニットのレギュラーは在籍期間が連載期間と重なるメンバーに表示）
--   ゲスト     … 連載回の記事に人物タグが付いている／誌面の連載ページに掲載されている
-- ---------------------------------------------------------------------
CREATE VIEW v_person_serials AS
WITH involvement AS (
  SELECT s.person_id, sm.serial_id, sm.role, s.via_unit_id, s.via_unit_name,
         NULL::timestamptz AS appeared_at, NULL::text AS appearance_key
  FROM person_scope s
  JOIN serial_members sm ON sm.person_id = s.subject_id
  JOIN serials se        ON se.serial_id = sm.serial_id
  WHERE (s.valid_from  IS NULL OR se.ended_on   IS NULL OR s.valid_from  <= se.ended_on)
    AND (s.valid_until IS NULL OR se.started_on IS NULL OR s.valid_until >= se.started_on)
  UNION ALL
  SELECT pa.person_id, pa.serial_id, 'guest', pa.via_unit_id, pa.via_unit_name,
         pa.published_at, 'a' || pa.article_id
  FROM v_person_articles pa
  WHERE pa.serial_id IS NOT NULL
  UNION ALL
  SELECT pi.person_id, pi.serial_id, 'guest', pi.via_unit_id, pi.via_unit_name,
         pi.release_date::timestamptz, 'i' || pi.issue_item_id
  FROM v_person_issue_items pi
  WHERE pi.serial_id IS NOT NULL
)
SELECT i.person_id,
       se.serial_id,
       se.title,
       se.slug,
       se.medium,
       se.started_on,
       se.ended_on,
       se.ended_on IS NULL                                      AS is_ongoing,
       bool_or(i.role <> 'guest')                               AS is_regular,
       array_agg(DISTINCT i.role) FILTER (WHERE i.role <> 'guest') AS roles,
       count(DISTINCT i.appearance_key)                         AS appearance_count,
       max(i.appeared_at)                                       AS last_appeared_at,
       CASE WHEN bool_or(i.via_unit_id IS NULL) THEN NULL
            ELSE min(i.via_unit_name) END                       AS via_unit_name
FROM involvement i
JOIN serials se ON se.serial_id = i.serial_id
GROUP BY i.person_id, se.serial_id;

-- ---------------------------------------------------------------------
-- 逆方向: 記事ページの「この記事に登場する人物」
-- ---------------------------------------------------------------------
CREATE VIEW v_article_persons AS
SELECT DISTINCT ON (atg.article_id, p.person_id)
       atg.article_id,
       p.person_id,
       p.person_type,
       p.display_name,
       '/directory/' || p.person_id || '/' AS url,
       pp.image_url
FROM article_tags atg
JOIN tags t                 ON t.tag_id = atg.tag_id AND t.person_id IS NOT NULL
JOIN persons p              ON p.person_id = t.person_id
LEFT JOIN person_profiles pp ON pp.person_id = p.person_id AND pp.is_published
ORDER BY atg.article_id, p.person_id;

-- ---------------------------------------------------------------------
-- 移行・運用チェック
-- ---------------------------------------------------------------------

-- 人物に未紐付けのタグのうち、名前が人物名（旧芸名・別表記含む）と一致するもの
CREATE VIEW v_tag_link_candidates AS
WITH names AS (
  SELECT person_id, display_name AS name, 'display_name' AS matched_on
  FROM persons WHERE status <> 'merged'
  UNION ALL
  SELECT pn.person_id, pn.name, pn.name_kind
  FROM person_names pn JOIN persons p USING (person_id)
  WHERE p.status <> 'merged'
)
SELECT t.tag_id,
       t.slug,
       t.name           AS tag_name,
       n.person_id,
       p.display_name,
       n.matched_on,
       (SELECT count(*) FROM article_tags atg WHERE atg.tag_id = t.tag_id) AS article_count
FROM tags t
JOIN names n   ON normalize_name(n.name) = normalize_name(t.name)
JOIN persons p ON p.person_id = n.person_id
WHERE t.tag_type = 'topic';

-- 代表タグがない人物（編集画面で記事に付けられない状態）
CREATE VIEW v_persons_without_primary_tag AS
SELECT p.person_id, p.display_name, p.person_type
FROM persons p
WHERE p.status <> 'merged'
  AND NOT EXISTS (SELECT 1 FROM tags t WHERE t.person_id = p.person_id AND t.is_primary);

-- ---------------------------------------------------------------------
-- 個人ページ JSON
--   SELECT person_page(1001);          -- 各セクション最新20件
--   SELECT person_page(1001, 50);
-- 統合済みIDを渡すと統合先のページを返す（person_id <> requested_id ならリダイレクト）
-- ---------------------------------------------------------------------
CREATE FUNCTION person_page(p_person_id bigint, p_limit integer DEFAULT 20) RETURNS jsonb
LANGUAGE sql STABLE AS $$
SELECT jsonb_build_object(
  'person_id',    p.person_id,
  'requested_id', p_person_id,
  'url',          '/directory/' || p.person_id || '/',
  'person_type',  p.person_type,
  'name',         p.display_name,
  'name_kana',    p.name_kana,
  'name_en',      p.name_en,
  'status',       p.status,

  'profile', (
    SELECT jsonb_build_object(
             'birthday',   CASE WHEN pp.birth_month IS NOT NULL
                                THEN concat_ws('', pp.birth_year || '年', pp.birth_month || '月', pp.birth_day || '日') END,
             'birthplace', pp.birthplace,
             'blood_type', pp.blood_type,
             'hobbies',    pp.hobbies,
             'biography',  pp.biography,
             'image_url',  pp.image_url)
    FROM person_profiles pp
    WHERE pp.person_id = p.person_id AND pp.is_published),

  'agency', (
    SELECT jsonb_build_object('agency_id', ag.agency_id, 'name', ag.name, 'url', ag.url)
    FROM person_affiliations pa JOIN agencies ag USING (agency_id)
    WHERE pa.person_id = p.person_id AND pa.ended_on IS NULL),

  'other_names', COALESCE((
    SELECT jsonb_agg(jsonb_build_object('name', pn.name, 'kind', pn.name_kind)
                     ORDER BY pn.used_from NULLS LAST, pn.person_name_id)
    FROM person_names pn WHERE pn.person_id = p.person_id), '[]'),

  'tags', COALESCE((
    SELECT jsonb_agg(jsonb_build_object('slug', t.slug, 'name', t.name, 'is_primary', t.is_primary)
                     ORDER BY t.is_primary DESC, t.tag_id)
    FROM tags t WHERE t.person_id = p.person_id), '[]'),

  'units', COALESCE((
    SELECT jsonb_agg(jsonb_build_object('person_id', u.person_id, 'name', u.display_name,
                                        'joined_on', um.joined_on, 'left_on', um.left_on)
                     ORDER BY um.left_on IS NOT NULL, um.joined_on NULLS FIRST)
    FROM unit_members um JOIN persons u ON u.person_id = um.unit_id
    WHERE um.member_id = p.person_id), '[]'),

  'members', COALESCE((
    SELECT jsonb_agg(jsonb_build_object('person_id', m.person_id, 'name', m.display_name,
                                        'joined_on', um.joined_on, 'left_on', um.left_on)
                     ORDER BY um.left_on IS NOT NULL, um.joined_on NULLS FIRST, m.name_kana)
    FROM unit_members um JOIN persons m ON m.person_id = um.member_id
    WHERE um.unit_id = p.person_id), '[]'),

  'castings', COALESCE((
    SELECT jsonb_agg(jsonb_build_object('work_id', w.work_id, 'work', w.title, 'work_type', w.work_type,
                                        'character', c.character_name, 'is_main', c.is_main)
                     ORDER BY c.is_main DESC, c.sort_order, w.released_on DESC NULLS LAST)
    FROM castings c JOIN works w USING (work_id)
    WHERE c.person_id = p.person_id), '[]'),

  'articles_total', (SELECT count(*) FROM v_person_articles va WHERE va.person_id = p.person_id),
  'articles', COALESCE((
    SELECT jsonb_agg(to_jsonb(x) ORDER BY x.published_at DESC)
    FROM (SELECT va.article_id, va.title, va.path, va.article_type,
                 code_label('article_type', va.article_type) AS type_label,
                 va.thumbnail_url, va.published_at, va.via_unit_name
          FROM v_person_articles va
          WHERE va.person_id = p.person_id
          ORDER BY va.published_at DESC
          LIMIT p_limit) x), '[]'),

  'upcoming_events', COALESCE((
    SELECT jsonb_agg(to_jsonb(x) ORDER BY x.starts_at)
    FROM (SELECT ve.event_id, ve.title, ve.event_type, code_label('event_type', ve.event_type) AS type_label,
                 ve.starts_at, ve.ends_at, ve.venue, ve.status, ve.role,
                 code_label('event_role', ve.role) AS role_label, ve.via_unit_name
          FROM v_person_events ve
          WHERE ve.person_id = p.person_id AND ve.is_upcoming
          ORDER BY ve.starts_at
          LIMIT p_limit) x), '[]'),

  'past_events', COALESCE((
    SELECT jsonb_agg(to_jsonb(x) ORDER BY x.starts_at DESC)
    FROM (SELECT ve.event_id, ve.title, ve.event_type, code_label('event_type', ve.event_type) AS type_label,
                 ve.starts_at, ve.ends_at, ve.venue, ve.status, ve.role,
                 code_label('event_role', ve.role) AS role_label, ve.via_unit_name
          FROM v_person_events ve
          WHERE ve.person_id = p.person_id AND NOT ve.is_upcoming
          ORDER BY ve.starts_at DESC
          LIMIT p_limit) x), '[]'),

  'issues', COALESCE((
    SELECT jsonb_agg(to_jsonb(x) ORDER BY x.release_date DESC)
    FROM (SELECT vi.issue_id, vi.publication, vi.issue_label, vi.release_date, vi.path,
                 vi.cover_image_url, vi.is_cover, vi.items
          FROM v_person_issues vi
          WHERE vi.person_id = p.person_id
          ORDER BY vi.release_date DESC
          LIMIT p_limit) x), '[]'),

  'serials', COALESCE((
    -- 並び順: レギュラー → 本人名義（ユニット経由より先）→ 連載中 → 最近登場
    SELECT jsonb_agg(to_jsonb(x) ORDER BY x.is_regular DESC, x.via_unit_name IS NULL DESC,
                                          x.is_ongoing DESC, x.last_appeared_at DESC NULLS LAST)
    FROM (SELECT vs.serial_id, vs.title, vs.slug, vs.medium,
                 code_label('serial_medium', vs.medium) AS medium_label,
                 vs.is_ongoing, vs.is_regular,
                 COALESCE((SELECT array_agg(code_label('serial_role', r) ORDER BY r) FROM unnest(vs.roles) r),
                          ARRAY['ゲスト']) AS role_labels,
                 vs.appearance_count, vs.last_appeared_at, vs.via_unit_name
          FROM v_person_serials vs
          WHERE vs.person_id = p.person_id) x), '[]')
)
FROM persons p
WHERE p.person_id = canonical_person_id(p_person_id)
$$;

-- ---------------------------------------------------------------------
-- 名鑑にない人物（ユニット・スタッフ・歌手など）をタグから作る
--   SELECT create_person_from_tag(304, 'そらのーと', 'unit');
-- タグ名を表示名にして人物を作り、そのタグを代表タグとして紐付ける。
-- ---------------------------------------------------------------------
CREATE FUNCTION create_person_from_tag(p_tag_id bigint, p_name_kana text,
                                       p_person_type text DEFAULT 'individual') RETURNS bigint
LANGUAGE plpgsql AS $$
DECLARE
  v_person_id bigint;
BEGIN
  INSERT INTO persons (person_type, display_name, name_kana)
  SELECT p_person_type, t.name, p_name_kana
  FROM tags t
  WHERE t.tag_id = p_tag_id AND t.tag_type = 'topic'
  RETURNING person_id INTO v_person_id;

  IF v_person_id IS NULL THEN
    RAISE EXCEPTION 'create_person_from_tag: タグ % が存在しないか、すでに紐付け済みです', p_tag_id;
  END IF;

  UPDATE tags SET tag_type = 'person', person_id = v_person_id, is_primary = true
   WHERE tag_id = p_tag_id;
  RETURN v_person_id;
END $$;

-- ---------------------------------------------------------------------
-- 重複人物の統合（タグ移行で同一人物が2件できた場合など）
--   SELECT merge_person(50001, 1002);   -- 50001 を 1002 に統合
-- タグごと移すので、旧タグが付いた記事も自動で統合先のページに載る。
-- ---------------------------------------------------------------------
CREATE FUNCTION merge_person(p_from bigint, p_into bigint) RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
  IF p_from = p_into THEN
    RAISE EXCEPTION 'merge_person: 統合元と統合先が同じです (%)', p_from;
  END IF;

  PERFORM 1 FROM persons WHERE person_id IN (p_from, p_into) FOR UPDATE;
  IF (SELECT count(*) FROM persons WHERE person_id IN (p_from, p_into) AND status <> 'merged') <> 2 THEN
    RAISE EXCEPTION 'merge_person: 存在しないか統合済みの人物です (% -> %)', p_from, p_into;
  END IF;
  IF (SELECT count(DISTINCT person_type) FROM persons WHERE person_id IN (p_from, p_into)) <> 1 THEN
    RAISE EXCEPTION 'merge_person: person_type が異なります (% -> %)', p_from, p_into;
  END IF;

  -- タグ: 統合先に代表タグがあれば、統合元の代表タグは別名タグに降格
  UPDATE tags
     SET person_id  = p_into,
         is_primary = is_primary AND NOT EXISTS (
                        SELECT 1 FROM tags t2 WHERE t2.person_id = p_into AND t2.is_primary)
   WHERE person_id = p_from;

  -- 名前: 統合元の表示名も別名として残す
  INSERT INTO person_names (person_id, name, name_kana, name_kind)
  SELECT p_into, f.display_name, f.name_kana, 'alias'
  FROM persons f, persons t
  WHERE f.person_id = p_from AND t.person_id = p_into AND f.display_name <> t.display_name
  ON CONFLICT DO NOTHING;
  INSERT INTO person_names (person_id, name, name_kana, name_kind, used_from, used_until)
  SELECT p_into, name, name_kana, name_kind, used_from, used_until
  FROM person_names WHERE person_id = p_from
  ON CONFLICT DO NOTHING;
  DELETE FROM person_names WHERE person_id = p_from;

  -- 名鑑: 統合先の空欄だけ統合元で補完
  INSERT INTO person_profiles AS dst
         (person_id, birth_year, birth_month, birth_day, birthplace, blood_type,
          hobbies, biography, image_url, is_published)
  SELECT p_into, birth_year, birth_month, birth_day, birthplace, blood_type,
         hobbies, biography, image_url, is_published
  FROM person_profiles WHERE person_id = p_from
  ON CONFLICT (person_id) DO UPDATE SET
    birth_year   = COALESCE(dst.birth_year,  EXCLUDED.birth_year),
    birth_month  = COALESCE(dst.birth_month, EXCLUDED.birth_month),
    birth_day    = COALESCE(dst.birth_day,   EXCLUDED.birth_day),
    birthplace   = COALESCE(dst.birthplace,  EXCLUDED.birthplace),
    blood_type   = COALESCE(dst.blood_type,  EXCLUDED.blood_type),
    hobbies      = COALESCE(dst.hobbies,     EXCLUDED.hobbies),
    biography    = COALESCE(dst.biography,   EXCLUDED.biography),
    image_url    = COALESCE(dst.image_url,   EXCLUDED.image_url),
    is_published = dst.is_published OR EXCLUDED.is_published;
  DELETE FROM person_profiles WHERE person_id = p_from;

  -- 各リンク: 統合先へ付け替え（重複は捨てる）
  INSERT INTO person_affiliations (person_id, agency_id, started_on, ended_on)
  SELECT p_into, agency_id, started_on, ended_on FROM person_affiliations WHERE person_id = p_from
  ON CONFLICT DO NOTHING;
  DELETE FROM person_affiliations WHERE person_id = p_from;

  INSERT INTO castings (person_id, work_id, character_name, is_main, sort_order)
  SELECT p_into, work_id, character_name, is_main, sort_order FROM castings WHERE person_id = p_from
  ON CONFLICT DO NOTHING;
  DELETE FROM castings WHERE person_id = p_from;

  INSERT INTO unit_members (unit_id, member_id, joined_on, left_on)
  SELECT CASE WHEN unit_id   = p_from THEN p_into ELSE unit_id   END,
         CASE WHEN member_id = p_from THEN p_into ELSE member_id END,
         joined_on, left_on
  FROM unit_members WHERE p_from IN (unit_id, member_id)
  ON CONFLICT DO NOTHING;
  DELETE FROM unit_members WHERE p_from IN (unit_id, member_id);

  INSERT INTO event_performers (event_id, person_id, role, appearance, sort_order)
  SELECT event_id, p_into, role, appearance, sort_order FROM event_performers WHERE person_id = p_from
  ON CONFLICT DO NOTHING;
  DELETE FROM event_performers WHERE person_id = p_from;

  INSERT INTO issue_item_persons (issue_item_id, person_id, sort_order)
  SELECT issue_item_id, p_into, sort_order FROM issue_item_persons WHERE person_id = p_from
  ON CONFLICT DO NOTHING;
  DELETE FROM issue_item_persons WHERE person_id = p_from;

  INSERT INTO serial_members (serial_id, person_id, role, sort_order)
  SELECT serial_id, p_into, role, sort_order FROM serial_members WHERE person_id = p_from
  ON CONFLICT DO NOTHING;
  DELETE FROM serial_members WHERE person_id = p_from;

  -- 統合元を統合済みにし、統合元を指していたものも付け替える
  UPDATE persons SET merged_into = p_into WHERE merged_into = p_from;
  UPDATE persons SET status = 'merged', merged_into = p_into WHERE person_id = p_from;
END $$;
