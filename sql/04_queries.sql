-- =====================================================================
-- seigura.com 人物中心データベース  04: よく使うクエリ
-- 値はサンプルデータのもの。アプリからはプレースホルダ（$1 など）で渡す。
-- =====================================================================

-- ---------------------------------------------------------------------
-- 個人ページ（/directory/{person_id}/）
-- ---------------------------------------------------------------------

-- 1ページ分をまとめて JSON で取得
SELECT person_page(1001);

-- 「関連記事をもっと見る」: キーセットページング（前ページ最後の published_at, article_id を渡す）
SELECT article_id, title, path, published_at, via_unit_name
FROM v_person_articles
WHERE person_id = 1001
  AND (published_at, article_id) < ('2026-06-01 12:00+09', 180006)
ORDER BY published_at DESC, article_id DESC
LIMIT 20;

-- ---------------------------------------------------------------------
-- タグページ（/tag/{slug}/）→ 人物タグなら個人ページへ 301
-- ---------------------------------------------------------------------
SELECT t.tag_type,
       CASE WHEN t.person_id IS NOT NULL
            THEN '/directory/' || canonical_person_id(t.person_id) || '/' END AS redirect_to
FROM tags t
WHERE t.slug = '%e6%98%a5%e9%a2%a8%e6%97%a5%e5%92%8c';   -- 旧芸名タグ「春風日和」→ /directory/1001/

-- ---------------------------------------------------------------------
-- 記事ページ: 「この記事に登場する声優」
-- ---------------------------------------------------------------------
SELECT person_id, display_name, url, image_url
FROM v_article_persons
WHERE article_id = 180002;

-- ---------------------------------------------------------------------
-- 名鑑トップ
-- ---------------------------------------------------------------------

-- 五十音索引（「は」行）
SELECT p.person_id, p.display_name, p.name_kana, ag.name AS agency
FROM persons p
JOIN person_profiles pp ON pp.person_id = p.person_id AND pp.is_published
LEFT JOIN person_affiliations pa ON pa.person_id = p.person_id AND pa.ended_on IS NULL
LEFT JOIN agencies ag ON ag.agency_id = pa.agency_id
WHERE p.kana_row = 'は' AND p.status <> 'merged'
ORDER BY p.name_kana;

-- 今日が誕生日の人物
SELECT p.person_id, p.display_name
FROM person_profiles pp
JOIN persons p USING (person_id)
WHERE pp.is_published
  AND pp.birth_month = extract(month FROM current_date)
  AND pp.birth_day   = extract(day   FROM current_date)
  AND p.status <> 'merged';

-- 名前検索（旧芸名・別表記・スペース有無を問わない）
SELECT DISTINCT p.person_id, p.display_name
FROM persons p
LEFT JOIN person_names pn ON pn.person_id = p.person_id
WHERE p.status <> 'merged'
  AND (normalize_name(p.display_name) = normalize_name('春風日和')
       OR normalize_name(pn.name)      = normalize_name('春風日和'));

-- ---------------------------------------------------------------------
-- 逆引き: イベントページの出演者／号ページの掲載人物
-- ---------------------------------------------------------------------
SELECT ep.role, p.person_id, p.display_name, p.person_type
FROM event_performers ep
JOIN persons p USING (person_id)
WHERE ep.event_id = 1 AND ep.appearance = 'confirmed'
ORDER BY ep.sort_order, p.name_kana;

SELECT ii.item_type, ii.title, ii.page_from, p.person_id, p.display_name
FROM issue_items ii
JOIN issue_item_persons iip USING (issue_item_id)
JOIN persons p USING (person_id)
WHERE ii.issue_id = 101
ORDER BY ii.sort_order, ii.page_from, iip.sort_order;

-- ---------------------------------------------------------------------
-- 移行・運用
-- ---------------------------------------------------------------------

-- (1) 人物に未紐付けのタグで、名前が人物と一致するもの（目視確認用）
SELECT * FROM v_tag_link_candidates ORDER BY article_count DESC;

-- (2) 確認したら人物タグに昇格（代表タグがなければ代表に）
UPDATE tags t
   SET tag_type   = 'person',
       person_id  = 1003,
       is_primary = NOT EXISTS (SELECT 1 FROM tags x WHERE x.person_id = 1003 AND x.is_primary)
 WHERE t.tag_id = 504;

-- (3) 重複人物の統合（50001 のタグ・出演情報などを 1002 へ移し、50001 は 1002 へリダイレクト）
SELECT merge_person(50001, 1002);

-- (4) 代表タグがない人物（編集画面で選べないので要対応）
SELECT * FROM v_persons_without_primary_tag;
