-- =====================================================================
-- WordPress → 人物中心データベース 移行  20: 変換
--   SELECT * FROM wp_migration.run();
-- wp スキーマ（作業用）から本スキーマへ、追加・更新・非公開化を行う。
-- 何度実行しても同じ結果になる（定期実行すれば WordPress との同期になる）。
--
-- WordPress を正とするもの（毎回上書き）
--   タグ名・スラッグ、名鑑の各項目（field_map に登録した項目だけ）、出演作品、
--   記事の種別・URL・タイトル・公開状態・日時・タグ付け、号の情報
-- 新DBを正とするもの（移行では上書きしない）
--   タグと人物の紐付け、ユニット、イベント、連載のレギュラー、
--   登録済みの号の掲載情報（issue_items）、統合済みの人物
-- =====================================================================

CREATE FUNCTION wp_migration.mapped(p_field text) RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT EXISTS (SELECT 1 FROM wp_migration.field_map WHERE field = p_field)
$$;

CREATE FUNCTION wp_migration.build_path(p_template text, p_id bigint, p_slug text, p_category text) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
  SELECT replace(replace(replace(p_template,
           '{id}', p_id::text),
           '{slug}', COALESCE(NULLIF(p_slug, ''), p_id::text)),
           '{category}', COALESCE(NULLIF(p_category, ''), 'news'))
$$;

-- ---------------------------------------------------------------------
-- 号の掲載候補を issue_items に登録する
--   SELECT wp_migration.apply_issue_candidates();        -- 未登録の号すべて
--   SELECT wp_migration.apply_issue_candidates(176667);  -- 1号だけ
-- 登録した号は issue_seeds に記録され、以後の run() では触らない。
-- ---------------------------------------------------------------------
CREATE FUNCTION wp_migration.apply_issue_candidates(p_issue_id bigint DEFAULT NULL) RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
  v_issues integer;
BEGIN
  DROP TABLE IF EXISTS pg_temp.apply_items;
  CREATE TEMP TABLE apply_items ON COMMIT DROP AS
  SELECT c.issue_id, c.section_no, c.item_type, c.serial_title,
         min(c.heading)              AS heading,
         array_agg(DISTINCT c.person_id) AS person_ids,
         NULL::bigint                AS serial_id,
         NULL::bigint                AS issue_item_id
  FROM wp_migration.issue_item_candidates c
  JOIN magazine_issues mi ON mi.issue_id = c.issue_id
  WHERE (p_issue_id IS NULL OR c.issue_id = p_issue_id)
    AND NOT EXISTS (SELECT 1 FROM wp_migration.issue_seeds sd WHERE sd.issue_id = c.issue_id)
  GROUP BY c.issue_id, c.section_no, c.item_type, c.serial_title;

  -- 誌面連載: タイトルが一致する連載がなければ作る
  INSERT INTO serials (title, medium)
  SELECT DISTINCT ON (normalize_name(ai.serial_title)) ai.serial_title, 'magazine'
  FROM apply_items ai
  WHERE ai.serial_title IS NOT NULL
    AND NOT EXISTS (SELECT 1 FROM serials se WHERE normalize_name(se.title) = normalize_name(ai.serial_title))
  ORDER BY normalize_name(ai.serial_title), ai.serial_title;

  UPDATE apply_items ai
     SET serial_id = (SELECT min(se.serial_id) FROM serials se
                       WHERE normalize_name(se.title) = normalize_name(ai.serial_title))
   WHERE ai.serial_title IS NOT NULL;

  UPDATE apply_items SET issue_item_id = nextval(pg_get_serial_sequence('issue_items', 'issue_item_id'));

  INSERT INTO issue_items (issue_item_id, issue_id, item_type, title, serial_id, sort_order)
  SELECT ai.issue_item_id, ai.issue_id, ai.item_type, COALESCE(ai.serial_title, ai.heading), ai.serial_id,
         ai.section_no * 10 + COALESCE((SELECT cl.sort_order FROM code_labels cl
                                         WHERE cl.domain = 'issue_item' AND cl.code = ai.item_type), 9)
  FROM apply_items ai;

  INSERT INTO issue_item_persons (issue_item_id, person_id, sort_order)
  SELECT ai.issue_item_id, x.person_id, x.ord
  FROM apply_items ai
  CROSS JOIN LATERAL unnest(ai.person_ids) WITH ORDINALITY AS x(person_id, ord);

  -- 【好評連載中】の「名前『連載名』」は連載の執筆者として登録
  INSERT INTO serial_members (serial_id, person_id, role)
  SELECT DISTINCT ai.serial_id, x.person_id, 'author'
  FROM apply_items ai
  CROSS JOIN LATERAL unnest(ai.person_ids) AS x(person_id)
  WHERE ai.serial_id IS NOT NULL
  ON CONFLICT DO NOTHING;

  INSERT INTO wp_migration.issue_seeds (issue_id)
  SELECT DISTINCT issue_id FROM apply_items
  ON CONFLICT DO NOTHING;
  GET DIAGNOSTICS v_issues = ROW_COUNT;
  RETURN v_issues;
END $$;

-- ---------------------------------------------------------------------
-- 本体
-- ---------------------------------------------------------------------
CREATE FUNCTION wp_migration.run() RETURNS TABLE (step_name text, affected bigint)
LANGUAGE plpgsql AS $$
DECLARE
  cfg          wp_migration.settings%ROWTYPE;
  cnt          bigint;
  v_run        bigint;
  m_name_en    boolean := wp_migration.mapped('name_en');
  m_birthday   boolean := wp_migration.mapped('birthday');
  m_birthplace boolean := wp_migration.mapped('birthplace');
  m_blood      boolean := wp_migration.mapped('blood_type');
  m_hobbies    boolean := wp_migration.mapped('hobbies');
  m_bio        boolean := wp_migration.mapped('biography');
BEGIN
  SELECT * INTO cfg FROM wp_migration.settings;
  IF NOT EXISTS (SELECT 1 FROM wp.posts) THEN
    RAISE EXCEPTION 'wp.posts が空です'
      USING HINT = '先に extract_wp.py と load_staging.sql を実行してください';
  END IF;
  IF NOT wp_migration.mapped('name_kana') THEN
    RAISE EXCEPTION '名鑑のふりがなの meta_key が未登録です'
      USING HINT = 'SELECT * FROM wp_migration.v_meta_key_guesses; で確認し、wp_migration.field_map に登録してください';
  END IF;

  TRUNCATE wp_migration.problems;
  INSERT INTO wp_migration.runs DEFAULT VALUES RETURNING run_id INTO v_run;

  DROP TABLE IF EXISTS pg_temp.run_log, pg_temp.src_thumb, pg_temp.src_tags, pg_temp.src_dir,
                       pg_temp.want_aff, pg_temp.src_cast, pg_temp.tag_links, pg_temp.src_issue,
                       pg_temp.src_art, pg_temp.want_at;
  CREATE TEMP TABLE run_log (seq integer GENERATED ALWAYS AS IDENTITY, label text, n bigint) ON COMMIT DROP;

  -- アイキャッチ画像 URL
  CREATE TEMP TABLE src_thumb ON COMMIT DROP AS
  SELECT DISTINCT ON (t.post_id) t.post_id,
         CASE WHEN f.meta_value ~ '^https?://' THEN f.meta_value
              ELSE cfg.uploads_base_url || f.meta_value END AS url
  FROM wp.postmeta t
  JOIN wp.postmeta f ON f.meta_key = '_wp_attached_file'
                    AND f.post_id = CASE WHEN t.meta_value ~ '^[0-9]{1,18}$' THEN t.meta_value::bigint END
  WHERE t.meta_key = '_thumbnail_id'
  ORDER BY t.post_id;

  -- =================================================================
  -- 1. タグ
  -- =================================================================
  CREATE TEMP TABLE src_tags ON COMMIT DROP AS
  SELECT DISTINCT ON (w.term_id) w.term_id AS tag_id, w.slug, w.name
  FROM wp.terms w
  WHERE w.taxonomy = 'post_tag'
  ORDER BY w.term_id;

  INSERT INTO wp_migration.problems (kind, source_id, message, value)
  SELECT 'tag', st.tag_id, 'スラッグが新DBの別のタグと重複しているため取り込めません', st.slug
  FROM src_tags st JOIN tags t ON t.slug = st.slug AND t.tag_id <> st.tag_id;
  DELETE FROM src_tags st USING tags t WHERE t.slug = st.slug AND t.tag_id <> st.tag_id;

  INSERT INTO tags (tag_id, slug, name, tag_type)
  SELECT tag_id, slug, name, 'topic' FROM src_tags
  ON CONFLICT (tag_id) DO UPDATE SET slug = EXCLUDED.slug, name = EXCLUDED.name
  WHERE (tags.slug, tags.name) IS DISTINCT FROM (EXCLUDED.slug, EXCLUDED.name);
  GET DIAGNOSTICS cnt = ROW_COUNT;
  INSERT INTO run_log (label, n) VALUES ('タグ: 追加・更新', cnt);

  DELETE FROM tags t USING wp_migration.imported i
  WHERE i.kind = 'tag' AND i.id = t.tag_id AND t.tag_type = 'topic'
    AND NOT EXISTS (SELECT 1 FROM wp.terms w WHERE w.term_id = t.tag_id AND w.taxonomy = 'post_tag');
  GET DIAGNOSTICS cnt = ROW_COUNT;
  INSERT INTO run_log (label, n) VALUES ('タグ: 削除（WordPress で削除済み）', cnt);

  INSERT INTO wp_migration.problems (kind, source_id, message, value)
  SELECT 'tag', t.tag_id, 'WordPress で削除されたタグですが、人物・作品に紐付いているため残しています', t.name
  FROM tags t JOIN wp_migration.imported i ON i.kind = 'tag' AND i.id = t.tag_id
  WHERE t.tag_type <> 'topic'
    AND NOT EXISTS (SELECT 1 FROM wp.terms w WHERE w.term_id = t.tag_id AND w.taxonomy = 'post_tag');

  INSERT INTO wp_migration.imported (kind, id) SELECT 'tag', tag_id FROM src_tags ON CONFLICT DO NOTHING;

  -- =================================================================
  -- 2. 名鑑 → 人物・プロフィール・所属・出演作品
  -- =================================================================
  CREATE TEMP TABLE src_dir ON COMMIT DROP AS
  SELECT p.id AS person_id,
         btrim(regexp_replace(COALESCE(p.post_title, ''), '[\s　]+', ' ', 'g')) AS display_name,
         p.post_status,
         COALESCE(f.vals, '{}'::jsonb) AS vals,
         COALESCE(o.name_kana, wp_migration.clean_kana(f.vals ->> 'name_kana')) AS name_kana
  FROM wp.posts p
  LEFT JOIN LATERAL (
    SELECT jsonb_object_agg(fm.field, pm.meta_value) AS vals
    FROM wp_migration.field_map fm
    JOIN wp.postmeta pm ON pm.post_id = p.id AND pm.meta_key = fm.meta_key
    WHERE fm.field NOT LIKE 'issue\_%'
  ) f ON true
  LEFT JOIN wp_migration.kana_overrides o ON o.person_id = p.id
  WHERE p.post_type = cfg.directory_post_type
    AND p.post_status IN ('publish', 'future', 'draft', 'pending', 'private');

  INSERT INTO wp_migration.problems (kind, source_id, message, value)
  SELECT 'person', d.person_id,
         CASE WHEN d.display_name = '' THEN '名前が空のため取り込めません'
              WHEN d.name_kana IS NULL THEN 'ふりがなが空のため取り込めません（kana_overrides で指定できます）'
              ELSE 'ふりがなをひらがなにできないため取り込めません（kana_overrides で指定できます）' END,
         d.display_name || ' / ' || COALESCE(d.vals ->> 'name_kana', '')
  FROM src_dir d
  WHERE d.display_name = '' OR d.name_kana IS NULL OR d.name_kana !~ '^[ぁ-ゖ][ぁ-ゖー　 ・]*$';
  DELETE FROM src_dir d
  WHERE d.display_name = '' OR d.name_kana IS NULL OR d.name_kana !~ '^[ぁ-ゖ][ぁ-ゖー　 ・]*$';

  INSERT INTO wp_migration.problems (kind, source_id, message, value)
  SELECT 'person', d.person_id,
         '新DBで統合済みのため WordPress の更新は反映していません（統合先 ' || p.merged_into || '）', d.display_name
  FROM src_dir d JOIN persons p ON p.person_id = d.person_id AND p.status = 'merged';
  DELETE FROM src_dir d USING persons p WHERE p.person_id = d.person_id AND p.status = 'merged';

  -- 改名（表記の空白違いは除く）: 旧表示名を別名として残す
  INSERT INTO person_names (person_id, name, name_kana, name_kind)
  SELECT p.person_id, p.display_name, p.name_kana, 'alias'
  FROM persons p JOIN src_dir d ON d.person_id = p.person_id
  WHERE normalize_name(p.display_name) <> normalize_name(d.display_name)
  ON CONFLICT DO NOTHING;

  INSERT INTO persons AS pe (person_id, person_type, display_name, name_kana, name_en)
  SELECT d.person_id, 'individual', d.display_name, d.name_kana,
         CASE WHEN m_name_en THEN NULLIF(btrim(d.vals ->> 'name_en'), '') ELSE cur.name_en END
  FROM src_dir d
  LEFT JOIN persons cur ON cur.person_id = d.person_id
  ON CONFLICT (person_id) DO UPDATE
     SET display_name = EXCLUDED.display_name, name_kana = EXCLUDED.name_kana, name_en = EXCLUDED.name_en
   WHERE (pe.display_name, pe.name_kana, pe.name_en)
         IS DISTINCT FROM (EXCLUDED.display_name, EXCLUDED.name_kana, EXCLUDED.name_en);
  GET DIAGNOSTICS cnt = ROW_COUNT;
  INSERT INTO run_log (label, n) VALUES ('人物: 追加・更新', cnt);

  -- field_map に登録していない項目は新DBの値を保つ
  INSERT INTO person_profiles AS pp
         (person_id, birth_year, birth_month, birth_day, birthplace, blood_type,
          hobbies, biography, image_url, is_published)
  SELECT d.person_id,
         CASE WHEN m_birthday   THEN b.y ELSE cur.birth_year  END,
         CASE WHEN m_birthday   THEN b.m ELSE cur.birth_month END,
         CASE WHEN m_birthday   THEN b.d ELSE cur.birth_day   END,
         CASE WHEN m_birthplace THEN NULLIF(btrim(d.vals ->> 'birthplace'), '') ELSE cur.birthplace END,
         CASE WHEN m_blood      THEN wp_migration.parse_blood(d.vals ->> 'blood_type') ELSE cur.blood_type END,
         CASE WHEN m_hobbies    THEN NULLIF(btrim(d.vals ->> 'hobbies'), '') ELSE cur.hobbies END,
         CASE WHEN m_bio        THEN NULLIF(btrim(d.vals ->> 'biography'), '') ELSE cur.biography END,
         th.url,
         d.post_status = 'publish'
  FROM src_dir d
  CROSS JOIN LATERAL wp_migration.parse_birthday(d.vals ->> 'birthday') b
  LEFT JOIN person_profiles cur ON cur.person_id = d.person_id
  LEFT JOIN src_thumb th        ON th.post_id = d.person_id
  ON CONFLICT (person_id) DO UPDATE SET
     birth_year  = EXCLUDED.birth_year,  birth_month = EXCLUDED.birth_month, birth_day = EXCLUDED.birth_day,
     birthplace  = EXCLUDED.birthplace,  blood_type  = EXCLUDED.blood_type,  hobbies   = EXCLUDED.hobbies,
     biography   = EXCLUDED.biography,   image_url   = EXCLUDED.image_url,   is_published = EXCLUDED.is_published
  WHERE (pp.birth_year, pp.birth_month, pp.birth_day, pp.birthplace, pp.blood_type,
         pp.hobbies, pp.biography, pp.image_url, pp.is_published)
        IS DISTINCT FROM
        (EXCLUDED.birth_year, EXCLUDED.birth_month, EXCLUDED.birth_day, EXCLUDED.birthplace, EXCLUDED.blood_type,
         EXCLUDED.hobbies, EXCLUDED.biography, EXCLUDED.image_url, EXCLUDED.is_published);
  GET DIAGNOSTICS cnt = ROW_COUNT;
  INSERT INTO run_log (label, n) VALUES ('名鑑: 追加・更新', cnt);

  INSERT INTO wp_migration.problems (kind, source_id, message, value)
  SELECT 'person', d.person_id, '誕生日を読み取れません', d.vals ->> 'birthday'
  FROM src_dir d CROSS JOIN LATERAL wp_migration.parse_birthday(d.vals ->> 'birthday') b
  WHERE m_birthday AND btrim(COALESCE(d.vals ->> 'birthday', '')) <> '' AND b.m IS NULL;
  INSERT INTO wp_migration.problems (kind, source_id, message, value)
  SELECT 'person', d.person_id, '血液型を読み取れません', d.vals ->> 'blood_type'
  FROM src_dir d
  WHERE m_blood
    AND btrim(COALESCE(d.vals ->> 'blood_type', '')) NOT IN ('', '-', '－', '不明', '非公表')
    AND wp_migration.parse_blood(d.vals ->> 'blood_type') IS NULL;

  -- 所属: WordPress の値が変わったら現所属を閉じて新しい所属を始める（履歴が残る）
  IF wp_migration.mapped('agency') THEN
    INSERT INTO agencies (name)
    SELECT DISTINCT ON (normalize_name(x.a)) x.a
    FROM (SELECT btrim(regexp_replace(COALESCE(d.vals ->> 'agency', ''), '[\s　]+', ' ', 'g')) AS a FROM src_dir d) x
    WHERE x.a <> ''
      AND NOT EXISTS (SELECT 1 FROM agencies g WHERE normalize_name(g.name) = normalize_name(x.a))
    ORDER BY normalize_name(x.a), x.a;
    GET DIAGNOSTICS cnt = ROW_COUNT;
    INSERT INTO run_log (label, n) VALUES ('事務所: 追加', cnt);

    CREATE TEMP TABLE want_aff ON COMMIT DROP AS
    SELECT d.person_id,
           (SELECT min(g.agency_id) FROM agencies g
             WHERE normalize_name(g.name) = normalize_name(btrim(d.vals ->> 'agency'))) AS agency_id
    FROM src_dir d;

    UPDATE person_affiliations pa SET ended_on = current_date
    FROM want_aff w
    WHERE pa.person_id = w.person_id AND pa.ended_on IS NULL AND pa.agency_id IS DISTINCT FROM w.agency_id;
    GET DIAGNOSTICS cnt = ROW_COUNT;
    INSERT INTO run_log (label, n) VALUES ('所属: 終了（事務所の変更・削除）', cnt);

    INSERT INTO person_affiliations (person_id, agency_id, started_on)
    SELECT w.person_id, w.agency_id,
           CASE WHEN EXISTS (SELECT 1 FROM person_affiliations x WHERE x.person_id = w.person_id)
                THEN current_date END
    FROM want_aff w
    WHERE w.agency_id IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM person_affiliations x WHERE x.person_id = w.person_id AND x.ended_on IS NULL)
    ON CONFLICT DO NOTHING;
    GET DIAGNOSTICS cnt = ROW_COUNT;
    INSERT INTO run_log (label, n) VALUES ('所属: 追加', cnt);
  END IF;

  -- 出演作品: 名鑑の自由記述を分解して作品・役名に（WordPress を正として毎回作り直す）
  IF wp_migration.mapped('works') THEN
    CREATE TEMP TABLE src_cast ON COMMIT DROP AS
    SELECT d.person_id, w.ord, w.work_type, w.title, w.character_name
    FROM src_dir d
    CROSS JOIN LATERAL wp_migration.parse_works(d.vals ->> 'works') w;

    INSERT INTO wp_migration.problems (kind, source_id, message, value)
    SELECT 'person', d.person_id, '出演作品を読み取れません（『』で囲まれた作品名がありません）', left(d.vals ->> 'works', 100)
    FROM src_dir d
    WHERE btrim(COALESCE(d.vals ->> 'works', '')) <> ''
      AND NOT EXISTS (SELECT 1 FROM src_cast c WHERE c.person_id = d.person_id);

    INSERT INTO works (title, work_type)
    SELECT DISTINCT ON (normalize_name(c.title), c.work_type) c.title, c.work_type
    FROM src_cast c
    WHERE NOT EXISTS (SELECT 1 FROM works w
                       WHERE normalize_name(w.title) = normalize_name(c.title) AND w.work_type = c.work_type)
    ORDER BY normalize_name(c.title), c.work_type, c.title;
    GET DIAGNOSTICS cnt = ROW_COUNT;
    INSERT INTO run_log (label, n) VALUES ('作品: 追加', cnt);

    DELETE FROM castings c USING src_dir d WHERE c.person_id = d.person_id;
    INSERT INTO castings (person_id, work_id, character_name, sort_order)
    SELECT c.person_id,
           (SELECT min(w.work_id) FROM works w
             WHERE normalize_name(w.title) = normalize_name(c.title) AND w.work_type = c.work_type),
           c.character_name, c.ord
    FROM src_cast c
    ON CONFLICT DO NOTHING;
    GET DIAGNOSTICS cnt = ROW_COUNT;
    INSERT INTO run_log (label, n) VALUES ('出演作品: 登録', cnt);
  END IF;

  UPDATE person_profiles pp SET is_published = false
  FROM wp_migration.imported i
  WHERE i.kind = 'person' AND i.id = pp.person_id AND pp.is_published
    AND NOT EXISTS (SELECT 1 FROM wp.posts p WHERE p.id = pp.person_id AND p.post_type = cfg.directory_post_type);
  GET DIAGNOSTICS cnt = ROW_COUNT;
  INSERT INTO run_log (label, n) VALUES ('名鑑: 非公開化（WordPress で削除済み）', cnt);

  INSERT INTO wp_migration.imported (kind, id) SELECT 'person', person_id FROM src_dir ON CONFLICT DO NOTHING;

  -- =================================================================
  -- 3. タグを人物に自動で紐付け（名前が1人だけに一致するもの）
  -- =================================================================
  IF cfg.auto_link_tags THEN
    CREATE TEMP TABLE tag_links ON COMMIT DROP AS
    SELECT c.tag_id, min(c.person_id) AS person_id, count(DISTINCT c.person_id) AS persons,
           (SELECT count(*) FROM wp.term_relationships r
              JOIN wp.terms w ON w.term_taxonomy_id = r.term_taxonomy_id
             WHERE w.term_id = c.tag_id) AS wp_count
    FROM v_tag_link_candidates c
    GROUP BY c.tag_id;

    INSERT INTO wp_migration.problems (kind, source_id, message, value)
    SELECT 'tag', l.tag_id, '同じ名前の人物が複数いるため自動で紐付けていません', t.name
    FROM tag_links l JOIN tags t ON t.tag_id = l.tag_id
    WHERE l.persons > 1;

    -- 記事数が最も多いタグを代表タグに（すでに代表タグがある人物は別名タグとして追加）
    UPDATE tags t
       SET tag_type   = 'person',
           person_id  = r.person_id,
           is_primary = r.rn = 1 AND NOT EXISTS (SELECT 1 FROM tags x WHERE x.person_id = r.person_id AND x.is_primary)
      FROM (SELECT l.*, row_number() OVER (PARTITION BY l.person_id ORDER BY l.wp_count DESC, l.tag_id) AS rn
              FROM tag_links l WHERE l.persons = 1) r
     WHERE t.tag_id = r.tag_id;
    GET DIAGNOSTICS cnt = ROW_COUNT;
    INSERT INTO run_log (label, n) VALUES ('タグ: 人物に自動で紐付け', cnt);
  END IF;

  -- =================================================================
  -- 4. 雑誌の号
  -- =================================================================
  CREATE TEMP TABLE src_issue ON COMMIT DROP AS
  SELECT p.id AS issue_id, p.post_name, p.post_status, p.post_content, p.post_date, p.post_date_gmt,
         r.m[1]::integer AS y, r.m[2]::integer AS mo, NULLIF(btrim(r.m[3]), '') AS suffix,
         COALESCE(f.vals, '{}'::jsonb) AS vals,
         true AS is_issue,
         NULL::text AS issue_label
  FROM wp.posts p
  CROSS JOIN LATERAL (SELECT regexp_match(normalize(btrim(COALESCE(p.post_title, '')), NFKC),
                                          cfg.issue_title_regex) AS m) r
  LEFT JOIN LATERAL (
    SELECT jsonb_object_agg(fm.field, pm.meta_value) AS vals
    FROM wp_migration.field_map fm
    JOIN wp.postmeta pm ON pm.post_id = p.id AND pm.meta_key = fm.meta_key
    WHERE fm.field LIKE 'issue\_%'
  ) f ON true
  WHERE p.post_type = cfg.issue_post_type
    AND p.post_status IN ('publish', 'future', 'draft', 'pending', 'private')
    AND r.m IS NOT NULL
    AND r.m[2] ~ '^[0-9]{1,2}$' AND r.m[2]::integer BETWEEN 1 AND 12;
  UPDATE src_issue SET issue_label = y || '年' || mo || '月号' || COALESCE(suffix, '');

  INSERT INTO wp_migration.problems (kind, source_id, message, value)
  SELECT 'issue', d.issue_id, '同じ号のページが複数あるため、記事として取り込みました', d.issue_label
  FROM (SELECT issue_id, issue_label,
               row_number() OVER (PARTITION BY issue_label ORDER BY (post_status = 'publish') DESC, issue_id) AS rn
          FROM src_issue) d
  WHERE d.rn > 1;
  UPDATE src_issue si SET is_issue = false
  FROM (SELECT issue_id,
               row_number() OVER (PARTITION BY issue_label ORDER BY (post_status = 'publish') DESC, issue_id) AS rn
          FROM src_issue) d
  WHERE d.issue_id = si.issue_id AND d.rn > 1;

  INSERT INTO wp_migration.problems (kind, source_id, message, value)
  SELECT 'issue', si.issue_id, '新DBに同じ号が別IDで登録済みのため、記事として取り込みました', si.issue_label
  FROM src_issue si JOIN magazine_issues mi
    ON mi.publication = '声優グランプリ' AND mi.issue_label = si.issue_label AND mi.issue_id <> si.issue_id
  WHERE si.is_issue;
  UPDATE src_issue si SET is_issue = false
  FROM magazine_issues mi
  WHERE mi.publication = '声優グランプリ' AND mi.issue_label = si.issue_label AND mi.issue_id <> si.issue_id;

  INSERT INTO wp_migration.problems (kind, source_id, message, value)
  SELECT 'issue', si.issue_id, '発売日を読み取れないため、投稿日を仮の発売日にしました', si.vals ->> 'issue_release_date'
  FROM src_issue si
  WHERE si.is_issue AND wp_migration.parse_jp_date(si.vals ->> 'issue_release_date') IS NULL
    AND NOT EXISTS (SELECT 1 FROM magazine_issues mi WHERE mi.issue_id = si.issue_id);

  INSERT INTO magazine_issues AS mi
         (issue_id, issue_label, cover_month, release_date, path, cover_image_url, price_yen, is_published)
  SELECT si.issue_id, si.issue_label, make_date(si.y, si.mo, 1),
         COALESCE(wp_migration.parse_jp_date(si.vals ->> 'issue_release_date'),
                  wp_migration.to_ts(si.post_date, si.post_date_gmt, cfg.wp_timezone)::date,
                  make_date(si.y, si.mo, 1)),
         wp_migration.build_path(COALESCE((SELECT m.path_template FROM wp_migration.post_type_map m
                                            WHERE m.post_type = cfg.issue_post_type),
                                          '/' || cfg.issue_post_type || '/{id}/'),
                                 si.issue_id, si.post_name, NULL),
         th.url,
         wp_migration.parse_price(si.vals ->> 'issue_price'),
         si.post_status = 'publish'
  FROM src_issue si
  LEFT JOIN src_thumb th ON th.post_id = si.issue_id
  WHERE si.is_issue
  ON CONFLICT (issue_id) DO UPDATE SET
     issue_label = EXCLUDED.issue_label, cover_month = EXCLUDED.cover_month, path = EXCLUDED.path,
     cover_image_url = EXCLUDED.cover_image_url,
     price_yen = COALESCE(EXCLUDED.price_yen, mi.price_yen),
     is_published = EXCLUDED.is_published
  WHERE (mi.issue_label, mi.cover_month, mi.path, mi.cover_image_url, mi.price_yen, mi.is_published)
        IS DISTINCT FROM (EXCLUDED.issue_label, EXCLUDED.cover_month, EXCLUDED.path, EXCLUDED.cover_image_url,
                          COALESCE(EXCLUDED.price_yen, mi.price_yen), EXCLUDED.is_published);
  GET DIAGNOSTICS cnt = ROW_COUNT;
  INSERT INTO run_log (label, n) VALUES ('号: 追加・更新', cnt);

  -- 発売日は WordPress に値があるときだけ更新（仮の発売日を編集部が直した場合に上書きしない）
  UPDATE magazine_issues mi SET release_date = x.d
  FROM (SELECT issue_id, wp_migration.parse_jp_date(vals ->> 'issue_release_date') AS d
          FROM src_issue WHERE is_issue) x
  WHERE mi.issue_id = x.issue_id AND x.d IS NOT NULL AND mi.release_date <> x.d;

  UPDATE magazine_issues mi SET is_published = false
  FROM wp_migration.imported i
  WHERE i.kind = 'issue' AND i.id = mi.issue_id AND mi.is_published
    AND NOT EXISTS (SELECT 1 FROM src_issue si WHERE si.issue_id = mi.issue_id AND si.is_issue);
  GET DIAGNOSTICS cnt = ROW_COUNT;
  INSERT INTO run_log (label, n) VALUES ('号: 非公開化（WordPress で削除済み）', cnt);

  INSERT INTO wp_migration.imported (kind, id)
  SELECT 'issue', issue_id FROM src_issue WHERE is_issue ON CONFLICT DO NOTHING;

  -- =================================================================
  -- 5. 記事（号として取り込んだ投稿は除く）
  -- =================================================================
  CREATE TEMP TABLE src_art ON COMMIT DROP AS
  SELECT p.id AS article_id, p.post_type, COALESCE(p.post_name, '') AS post_name, m.article_type,
         wp_migration.build_path(m.path_template, p.id, p.post_name, cat.slug) AS path,
         COALESCE(NULLIF(btrim(p.post_title), ''), '（無題）') AS title,
         NULLIF(left(COALESCE(NULLIF(btrim(p.post_excerpt), ''), wp_migration.strip_html(p.post_content)), 120), '') AS excerpt,
         th.url AS thumbnail_url,
         CASE p.post_status WHEN 'publish' THEN 'published' WHEN 'future' THEN 'scheduled'
                            WHEN 'private' THEN 'private'   ELSE 'draft' END AS status,
         COALESCE(wp_migration.to_ts(p.post_date, p.post_date_gmt, cfg.wp_timezone),
                  wp_migration.to_ts(NULL, p.post_modified_gmt, cfg.wp_timezone)) AS published_at,
         NULL::text    AS serial_key,
         NULL::integer AS episode_no,
         NULL::text    AS serial_title,
         NULL::bigint  AS serial_id
  FROM wp.posts p
  JOIN wp_migration.post_type_map m ON m.post_type = p.post_type
  LEFT JOIN LATERAL (
    SELECT w.slug
    FROM wp.term_relationships tr
    JOIN wp.terms w ON w.term_taxonomy_id = tr.term_taxonomy_id AND w.taxonomy = 'category'
    WHERE tr.object_id = p.id
    ORDER BY w.term_id
    LIMIT 1
  ) cat ON true
  LEFT JOIN src_thumb th ON th.post_id = p.id
  WHERE p.post_status IN ('publish', 'future', 'draft', 'pending', 'private')
    AND NOT EXISTS (SELECT 1 FROM src_issue si WHERE si.issue_id = p.id AND si.is_issue);

  INSERT INTO wp_migration.problems (kind, source_id, message, value)
  SELECT 'article', sa.article_id, '公開日時がないため下書きとして取り込みました', sa.title
  FROM src_art sa WHERE sa.published_at IS NULL AND sa.status IN ('published', 'scheduled');
  UPDATE src_art SET status = 'draft' WHERE published_at IS NULL AND status IN ('published', 'scheduled');

  INSERT INTO wp_migration.problems (kind, source_id, message, value)
  SELECT 'article', d.article_id, 'URL が他の記事と重複するため /?p={ID} にしました', d.path
  FROM (SELECT article_id, path, count(*) OVER (PARTITION BY path) AS c FROM src_art) d
  WHERE d.c > 1;
  UPDATE src_art sa SET path = '/?p=' || sa.article_id
  FROM (SELECT article_id, count(*) OVER (PARTITION BY path) AS c FROM src_art) d
  WHERE d.article_id = sa.article_id AND d.c > 1;

  -- 連載: スラッグ（例 akabane144）から連載と回数を判定
  UPDATE src_art sa
     SET serial_key   = x.m[1],
         episode_no   = CASE WHEN x.m[2] ~ '^[0-9]{1,9}$' THEN x.m[2]::integer END,
         serial_title = COALESCE(NULLIF(btrim(regexp_replace(sa.title, '\s*第\s*[0-9０-９]+\s*回.*$', '')), ''), x.m[1])
    FROM (SELECT article_id, regexp_match(post_name, cfg.serial_slug_regex) AS m
            FROM src_art WHERE post_type = cfg.serial_post_type) x
   WHERE x.article_id = sa.article_id AND x.m IS NOT NULL;

  INSERT INTO serials (title, slug, medium, started_on)
  SELECT DISTINCT ON (sa.serial_key) sa.serial_title, sa.serial_key, 'web',
         (SELECT min(b.published_at)::date FROM src_art b WHERE b.serial_key = sa.serial_key)
  FROM src_art sa
  WHERE sa.serial_key IS NOT NULL
  ORDER BY sa.serial_key, sa.published_at DESC NULLS LAST
  ON CONFLICT (slug) DO NOTHING;
  GET DIAGNOSTICS cnt = ROW_COUNT;
  INSERT INTO run_log (label, n) VALUES ('連載: 追加', cnt);

  UPDATE src_art sa SET serial_id = se.serial_id FROM serials se WHERE se.slug = sa.serial_key;

  INSERT INTO wp_migration.problems (kind, source_id, message, value)
  SELECT 'article', d.article_id, '同じ連載で回数が重複しているため、この記事の回数は空にしました',
         d.serial_key || ' 第' || d.episode_no || '回'
  FROM (SELECT article_id, serial_key, episode_no,
               row_number() OVER (PARTITION BY serial_id, episode_no ORDER BY published_at NULLS LAST, article_id) AS rn
          FROM src_art WHERE serial_id IS NOT NULL AND episode_no IS NOT NULL) d
  WHERE d.rn > 1;
  UPDATE src_art sa SET episode_no = NULL
  FROM (SELECT article_id,
               row_number() OVER (PARTITION BY serial_id, episode_no ORDER BY published_at NULLS LAST, article_id) AS rn
          FROM src_art WHERE serial_id IS NOT NULL AND episode_no IS NOT NULL) d
  WHERE d.article_id = sa.article_id AND d.rn > 1;

  -- 回数が変わる記事は先に回数を外す（(連載, 回数) の一意制約に途中で当たらないように）
  UPDATE articles a SET episode_no = NULL
  FROM src_art sa
  WHERE a.article_id = sa.article_id AND a.episode_no IS NOT NULL AND sa.serial_id IS NOT NULL
    AND (a.serial_id, a.episode_no) IS DISTINCT FROM (sa.serial_id, sa.episode_no);

  -- 連載をスラッグから判定できない記事は、新DBで設定した連載を保つ
  INSERT INTO articles AS a
         (article_id, article_type, path, title, excerpt, thumbnail_url, serial_id, episode_no, status, published_at)
  SELECT sa.article_id, sa.article_type, sa.path, sa.title, sa.excerpt, sa.thumbnail_url,
         sa.serial_id, sa.episode_no, sa.status, sa.published_at
  FROM src_art sa
  ON CONFLICT (article_id) DO UPDATE SET
     article_type  = EXCLUDED.article_type,
     path          = EXCLUDED.path,
     title         = EXCLUDED.title,
     excerpt       = EXCLUDED.excerpt,
     thumbnail_url = EXCLUDED.thumbnail_url,
     serial_id     = COALESCE(EXCLUDED.serial_id, a.serial_id),
     episode_no    = CASE WHEN EXCLUDED.serial_id IS NULL THEN a.episode_no ELSE EXCLUDED.episode_no END,
     status        = EXCLUDED.status,
     published_at  = EXCLUDED.published_at
  WHERE (a.article_type, a.path, a.title, a.excerpt, a.thumbnail_url, a.status, a.published_at,
         a.serial_id, a.episode_no)
        IS DISTINCT FROM
        (EXCLUDED.article_type, EXCLUDED.path, EXCLUDED.title, EXCLUDED.excerpt, EXCLUDED.thumbnail_url,
         EXCLUDED.status, EXCLUDED.published_at, COALESCE(EXCLUDED.serial_id, a.serial_id),
         CASE WHEN EXCLUDED.serial_id IS NULL THEN a.episode_no ELSE EXCLUDED.episode_no END);
  GET DIAGNOSTICS cnt = ROW_COUNT;
  INSERT INTO run_log (label, n) VALUES ('記事: 追加・更新', cnt);

  UPDATE articles a SET status = 'private'
  FROM wp_migration.imported i
  WHERE i.kind = 'article' AND i.id = a.article_id AND a.status <> 'private'
    AND NOT EXISTS (SELECT 1 FROM src_art sa WHERE sa.article_id = a.article_id);
  GET DIAGNOSTICS cnt = ROW_COUNT;
  INSERT INTO run_log (label, n) VALUES ('記事: 非公開化（WordPress で削除・ゴミ箱）', cnt);

  INSERT INTO wp_migration.imported (kind, id) SELECT 'article', article_id FROM src_art ON CONFLICT DO NOTHING;

  -- =================================================================
  -- 6. 記事のタグ付け（WordPress の記事は WordPress と同じ状態にそろえる）
  -- =================================================================
  CREATE TEMP TABLE want_at ON COMMIT DROP AS
  SELECT DISTINCT r.object_id AS article_id, w.term_id AS tag_id
  FROM wp.term_relationships r
  JOIN wp.terms w ON w.term_taxonomy_id = r.term_taxonomy_id AND w.taxonomy = 'post_tag'
  JOIN src_art sa ON sa.article_id = r.object_id
  JOIN tags t     ON t.tag_id = w.term_id;

  DELETE FROM article_tags x USING src_art sa
  WHERE x.article_id = sa.article_id
    AND NOT EXISTS (SELECT 1 FROM want_at w WHERE w.article_id = x.article_id AND w.tag_id = x.tag_id);
  GET DIAGNOSTICS cnt = ROW_COUNT;
  INSERT INTO run_log (label, n) VALUES ('タグ付け: 削除', cnt);

  INSERT INTO article_tags (article_id, tag_id)
  SELECT article_id, tag_id FROM want_at
  ON CONFLICT DO NOTHING;
  GET DIAGNOSTICS cnt = ROW_COUNT;
  INSERT INTO run_log (label, n) VALUES ('タグ付け: 追加', cnt);

  -- =================================================================
  -- 7. 号の目次から掲載候補を抽出（掲載情報が未登録の号だけ）
  --   本文を【見出し】で区切り、見出しから企画の種類を、本文中の名前から人物を判定する。
  --   【好評連載中】は「名前『連載名』」の組で誌面連載として扱う。
  -- =================================================================
  DELETE FROM wp_migration.issue_item_candidates c
  WHERE NOT EXISTS (SELECT 1 FROM wp_migration.issue_seeds sd WHERE sd.issue_id = c.issue_id);

  INSERT INTO wp_migration.issue_item_candidates
         (issue_id, section_no, heading, item_type, serial_title, person_id, matched_name)
  WITH txt AS (
    SELECT si.issue_id, normalize(wp_migration.strip_html(si.post_content), NFKC) AS t
    FROM src_issue si
    WHERE si.is_issue
      AND NOT EXISTS (SELECT 1 FROM wp_migration.issue_seeds sd WHERE sd.issue_id = si.issue_id)
  ), cut AS (
    SELECT issue_id,
           CASE WHEN regexp_instr(t, cfg.issue_content_stop_regex) > 0
                THEN left(t, regexp_instr(t, cfg.issue_content_stop_regex) - 1) ELSE t END AS t
    FROM txt
  ), sec AS (
    SELECT c.issue_id, x.section_no::integer AS section_no,
           substring(x.part from '^【([^】]*)】') AS heading,
           regexp_replace(x.part, '^【[^】]*】', '') AS body
    FROM cut c
    CROSS JOIN LATERAL regexp_split_to_table(replace(c.t, '【', chr(1) || '【'), chr(1))
         WITH ORDINALITY AS x(part, section_no)
  ), units AS (
    SELECT sec.issue_id, sec.section_no, sec.heading, 0 AS chunk_no, NULL::text AS serial_title,
           normalize_name(sec.body) AS tn
    FROM sec
    WHERE NOT ('serial' = ANY (wp_migration.issue_heading_types(sec.heading)))
    UNION ALL
    SELECT sec.issue_id, sec.section_no, sec.heading, y.chunk_no::integer,
           btrim(substring(y.chunk from '「([^「」]*)$')),
           normalize_name(substring(y.chunk from '^(.*)「'))
    FROM sec
    CROSS JOIN LATERAL regexp_split_to_table(sec.body, '」') WITH ORDINALITY AS y(chunk, chunk_no)
    WHERE 'serial' = ANY (wp_migration.issue_heading_types(sec.heading))
      AND y.chunk ~ '「[^「」]+$'
  ), names AS (
    SELECT p.person_id, normalize_name(p.display_name) AS nn, p.display_name AS name
    FROM persons p WHERE p.status <> 'merged'
    UNION
    SELECT pn.person_id, normalize_name(pn.name), pn.name
    FROM person_names pn JOIN persons p ON p.person_id = pn.person_id
    WHERE p.status <> 'merged'
    UNION
    SELECT t.person_id, normalize_name(t.name), t.name
    FROM tags t WHERE t.person_id IS NOT NULL
  ), hits AS (
    SELECT u.issue_id, u.section_no, u.heading, u.chunk_no, u.serial_title, nm.person_id, nm.nn, nm.name
    FROM units u
    JOIN names nm ON length(nm.nn) >= 3 AND strpos(u.tn, nm.nn) > 0
  ), best AS (
    -- 長い名前の一部として一致しただけの短い名前は除く
    SELECT DISTINCT ON (h.issue_id, h.section_no, h.chunk_no, h.person_id) h.*
    FROM hits h
    WHERE NOT EXISTS (SELECT 1 FROM hits h2
                       WHERE h2.issue_id = h.issue_id AND h2.section_no = h.section_no
                         AND h2.chunk_no = h.chunk_no AND h2.person_id <> h.person_id
                         AND length(h2.nn) > length(h.nn) AND strpos(h2.nn, h.nn) > 0)
    ORDER BY h.issue_id, h.section_no, h.chunk_no, h.person_id, length(h.nn) DESC
  )
  SELECT b.issue_id, b.section_no, b.heading, ty.item_type, b.serial_title, b.person_id, b.name
  FROM best b
  CROSS JOIN LATERAL unnest(CASE WHEN b.serial_title IS NOT NULL THEN ARRAY['serial']
                                 ELSE wp_migration.issue_heading_types(b.heading) END) AS ty(item_type);

  -- 号の投稿に人物タグが付いていれば、本文に名前がなくても「掲載」として候補に加える
  INSERT INTO wp_migration.issue_item_candidates
         (issue_id, section_no, heading, item_type, serial_title, person_id, matched_name)
  SELECT DISTINCT ON (si.issue_id, t.person_id) si.issue_id, 0, NULL, 'appearance', NULL, t.person_id, t.name
  FROM src_issue si
  JOIN wp.term_relationships r ON r.object_id = si.issue_id
  JOIN wp.terms w ON w.term_taxonomy_id = r.term_taxonomy_id AND w.taxonomy = 'post_tag'
  JOIN tags t     ON t.tag_id = w.term_id AND t.person_id IS NOT NULL
  WHERE si.is_issue
    AND NOT EXISTS (SELECT 1 FROM wp_migration.issue_seeds sd WHERE sd.issue_id = si.issue_id)
    AND NOT EXISTS (SELECT 1 FROM wp_migration.issue_item_candidates c
                     WHERE c.issue_id = si.issue_id AND c.person_id = t.person_id)
  ORDER BY si.issue_id, t.person_id, t.is_primary DESC;

  SELECT count(DISTINCT issue_id) INTO cnt FROM wp_migration.issue_item_candidates c
  WHERE NOT EXISTS (SELECT 1 FROM wp_migration.issue_seeds sd WHERE sd.issue_id = c.issue_id);
  INSERT INTO run_log (label, n) VALUES ('号: 掲載候補を抽出した号（未登録）', cnt);

  IF cfg.auto_issue_items THEN
    cnt := wp_migration.apply_issue_candidates();
    INSERT INTO run_log (label, n) VALUES ('号: 掲載情報を登録', cnt);
  END IF;

  -- =================================================================
  SELECT count(*) INTO cnt FROM wp_migration.problems;
  INSERT INTO run_log (label, n) VALUES ('要確認（wp_migration.problems）', cnt);

  UPDATE wp_migration.runs
     SET finished_at = clock_timestamp(),
         steps = (SELECT jsonb_agg(jsonb_build_object('step', l.label, 'rows', l.n) ORDER BY l.seq) FROM run_log l)
   WHERE run_id = v_run;

  RETURN QUERY SELECT l.label, l.n FROM run_log l ORDER BY l.seq;
END $$;
