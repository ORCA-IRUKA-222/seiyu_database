-- =====================================================================
-- seigura.com 人物中心データベース  03: サンプルデータ（人物・作品はすべて架空）
--
-- 移行の途中状態を再現している:
--   * タグ 504「森野くるみ」はまだ人物に紐付いていない（topic のまま）
--   * 人物 50001 はタグ移行で重複作成された「月島 蒼」（1002 に統合予定）
-- 04_queries.sql の「紐付け確定」「統合」で解消する。
-- =====================================================================

INSERT INTO agencies (agency_id, name, name_kana) VALUES
  (1, 'スタジオ青嵐',     'すたじおせいらん'),
  (2, 'オフィス木漏れ日', 'おふぃすこもれび'),
  (3, 'アトリエ灯',       'あとりえあかり');

-- 名鑑ID をそのまま person_id に使う
INSERT INTO persons (person_id, person_type, display_name, name_kana, name_en) VALUES
  (1001,  'individual', '春風 ひより', 'はるかぜ ひより', 'Hiyori Harukaze'),
  (1002,  'individual', '月島 蒼',     'つきしま あおい', 'Aoi Tsukishima'),
  (1003,  'individual', '森野 くるみ', 'もりの くるみ',   'Kurumi Morino'),
  (2001,  'unit',       'ソラノート',  'そらのーと',      'Sora Note'),
  (3001,  'individual', '桐生 拓真',   'きりゅう たくま', NULL),   -- 名鑑未登録（タグのみの人物）
  (50001, 'individual', '月島蒼',      'つきしま あおい', NULL);   -- 重複登録
SELECT setval(pg_get_serial_sequence('persons', 'person_id'), (SELECT max(person_id) FROM persons));

INSERT INTO person_names (person_id, name, name_kana, name_kind, used_from, used_until) VALUES
  (1001, '春風 日和', 'はるかぜ ひより', 'former', '2017-04-01', '2021-03-31');

INSERT INTO unit_members (unit_id, member_id, joined_on, left_on) VALUES
  (2001, 1001, '2022-04-01', NULL),
  (2001, 1002, '2022-04-01', '2025-03-31'),   -- 卒業
  (2001, 1003, '2025-04-01', NULL);           -- 新メンバー

INSERT INTO person_profiles (person_id, birth_year, birth_month, birth_day, birthplace, blood_type, hobbies, is_published) VALUES
  (1001, NULL, 3,  15, '東京都',   'A',  'カメラ・紅茶',     true),
  (1002, 1999, 10, 21, '神奈川県', 'O',  'ボルダリング',     true),
  (1003, NULL, 7,  7,  '北海道',   NULL, '絵本集め・水彩画', true);

INSERT INTO person_affiliations (person_id, agency_id, started_on, ended_on) VALUES
  (1001, 3, '2017-04-01', '2021-03-31'),
  (1001, 1, '2021-04-01', NULL),
  (1002, 2, '2019-04-01', NULL),
  (1003, 1, '2020-04-01', NULL);

INSERT INTO works (work_id, title, title_kana, work_type, released_on) VALUES
  (1, 'きらめきステージ!',  'きらめきすてーじ',    'anime', '2024-04-05'),
  (2, 'ドリームアーク',     'どりーむあーく',      'game',  '2023-09-01'),
  (3, '星降る町の喫茶店',   'ほしふるまちのきっさてん', 'anime', '2025-10-03');

INSERT INTO castings (person_id, work_id, character_name, is_main) VALUES
  (1001, 1, '天音 ルカ',   true),
  (1001, 2, 'ミラ',        false),
  (1002, 1, '黒崎 レイ',   true),
  (1003, 3, '小鳥遊 ナナ', true);

-- tag_id = WordPress term_id
INSERT INTO tags (tag_id, slug, name, tag_type, person_id, work_id, is_primary) VALUES
  (501, 'harukaze-hiyori',                    '春風ひより',   'person', 1001,  NULL, true),
  (502, '%e6%98%a5%e9%a2%a8%e6%97%a5%e5%92%8c', '春風日和',   'person', 1001,  NULL, false), -- 旧芸名タグ
  (503, 'tsukishima-aoi',                     '月島蒼',       'person', 1002,  NULL, true),
  (504, 'morino-kurumi',                      '森野くるみ',   'topic',  NULL,  NULL, false), -- 未紐付け
  (505, 'soranote',                           'ソラノート',   'person', 2001,  NULL, true),
  (506, 'kiryu-takuma',                       '桐生拓真',     'person', 3001,  NULL, true),
  (507, 'tsukishima-aoi-2',                   '月島 蒼',      'person', 50001, NULL, true),  -- 重複人物のタグ
  (508, 'event',                              'イベント',     'topic',  NULL,  NULL, false),
  (509, '%e5%a3%b0%e5%84%aa%e3%82%b0%e3%83%a9%e3%83%b3%e3%83%97%e3%83%aa', '声優グランプリ', 'topic', NULL, NULL, false),
  (510, 'kirasute',                           'きらめきステージ!', 'work', NULL, 1,  true);
SELECT setval(pg_get_serial_sequence('tags', 'tag_id'), (SELECT max(tag_id) FROM tags));

INSERT INTO serials (serial_id, title, slug, medium, started_on) VALUES
  (1, 'ひよりの陽だまり日記',   'hiyori-hidamari',      'web',      '2026-05-01'),
  (2, 'くるみの絵本棚',         'kurumi-ehon',          'magazine', '2025-06-10'),
  (3, 'ソラノートのそらばなし', 'soranote-sorabanashi', 'both',     '2023-01-01');
SELECT setval(pg_get_serial_sequence('serials', 'serial_id'), (SELECT max(serial_id) FROM serials));

INSERT INTO serial_members (serial_id, person_id, role) VALUES
  (1, 1001, 'author'),
  (2, 1003, 'author'),
  (3, 2001, 'host');

-- article_id = WordPress post ID
INSERT INTO articles (article_id, article_type, path, title, serial_id, episode_no, status, published_at) VALUES
  (180001, 'news',      '/news/180001/',   '春風ひより、1st写真集の発売が決定',             NULL, NULL, 'published', '2026-09-01 12:00+09'),
  (180002, 'interview', '/senior/180002/', '春風ひより×月島蒼 対談「同じ夢の、違う景色」', NULL, NULL, 'published', '2026-08-15 18:00+09'),
  (180003, 'news',      '/news/180003/',   'ソラノート、新曲「青空ノート」配信開始',        NULL, NULL, 'published', '2024-06-01 10:00+09'),
  (180004, 'event',     '/movie/180004/',  '【ライブレポート】ソラノート 2nd LIVE「そらいろ」', NULL, NULL, 'published', '2026-07-20 20:00+09'),
  (180005, 'column',    '/rensai/180005/', 'ひよりの陽だまり日記 第1回',                    1,    1,    'published', '2026-05-01 12:00+09'),
  (180006, 'column',    '/rensai/180006/', 'ひよりの陽だまり日記 第2回 ゲスト：森野くるみ', 1,    2,    'published', '2026-06-01 12:00+09'),
  (180007, 'news',      '/news/180007/',   '（下書き）春風ひより 新番組',                   NULL, NULL, 'draft',     NULL),
  (180008, 'news',      '/news/180008/',   '（予約投稿）春風ひより 新ビジュアル',           NULL, NULL, 'scheduled', now() + interval '30 days'),
  (180009, 'news',      '/news/180009/',   '春風日和、初のソロイベント開催',                NULL, NULL, 'published', '2020-11-01 12:00+09'),
  (180010, 'news',      '/news/180010/',   '月島蒼、カバーアルバム発売',                    NULL, NULL, 'published', '2026-03-03 12:00+09'),
  (180011, 'magazine',  '/news/180011/',   '【声グラ11月号】表紙・巻頭は春風ひより＆月島蒼', NULL, NULL, 'published', '2026-10-09 12:00+09');

INSERT INTO article_tags (article_id, tag_id) VALUES
  (180001, 501),
  (180002, 501), (180002, 503),
  (180003, 505),
  (180004, 505), (180004, 506), (180004, 508),
  (180005, 501),
  (180006, 501), (180006, 504),
  (180007, 501),
  (180008, 501),
  (180009, 502),
  (180010, 507),
  (180011, 501), (180011, 503), (180011, 509);

INSERT INTO events (event_id, title, event_type, starts_at, ends_at, venue) VALUES
  (1, 'ソラノート 2nd LIVE「そらいろ」',           'live',  '2026-07-18 17:00+09', '2026-07-18 20:00+09', 'ベイサイドホール'),
  (2, '声グラ presents トークイベント vol.12',     'event', date_trunc('day', now()) + interval '33 days 18 hours', NULL, '神田メディアホール'),
  (3, 'ドリームアーク 3rd Anniversary ステージ',  'stage', '2026-09-20 13:00+09', '2026-09-20 15:00+09', '幕張ステージ'),
  (4, 'ソラノート 1st LIVE「はじまりのそら」',     'live',  '2023-08-11 18:00+09', '2023-08-11 21:00+09', '中野サンシャインホール');
SELECT setval(pg_get_serial_sequence('events', 'event_id'), (SELECT max(event_id) FROM events));

INSERT INTO event_performers (event_id, person_id, role, appearance) VALUES
  (1, 2001, 'performer', 'confirmed'),   -- ユニットで登録 → 在籍メンバーに表示
  (1, 3001, 'guest',     'confirmed'),
  (2, 1001, 'mc',        'confirmed'),
  (2, 1002, 'guest',     'confirmed'),
  (3, 1001, 'performer', 'confirmed'),
  (3, 1003, 'performer', 'cancelled'),   -- 出演キャンセル → 表示しない
  (4, 2001, 'performer', 'confirmed');

INSERT INTO event_articles (event_id, article_id, relation) VALUES
  (1, 180004, 'report');

INSERT INTO magazine_issues (issue_id, issue_label, cover_month, release_date, path) VALUES
  (101, '2026年11月号', '2026-11-01', '2026-10-10', '/magazine_books/180100/'),
  (102, '2026年12月号', '2026-12-01', '2026-11-10', '/magazine_books/180200/'),
  (103, '2025年8月号',  '2025-08-01', '2025-07-10', '/magazine_books/170300/');
SELECT setval(pg_get_serial_sequence('magazine_issues', 'issue_id'), (SELECT max(issue_id) FROM magazine_issues));

INSERT INTO issue_items (issue_item_id, issue_id, item_type, title, page_from, page_to, serial_id, sort_order) VALUES
  (1, 101, 'cover',        '春風ひより＆月島蒼',              NULL, NULL, NULL, 1),
  (2, 101, 'main_feature', '巻頭特集 春風ひより×月島蒼',       4,    19,   NULL, 2),
  (3, 101, 'serial',       'くるみの絵本棚 第17回',            98,   99,   2,    9),
  (8, 101, 'serial',       'ソラノートのそらばなし 誌面版',    100,  101,  3,    9),
  (4, 102, 'gravure',      '森野くるみ 秋の休日',              30,   37,   NULL, 4),
  (5, 102, 'interview',    'ソラノート 新体制インタビュー',    40,   45,   NULL, 5),
  (7, 102, 'serial',       'くるみの絵本棚 第18回',            98,   99,   2,    9),
  (6, 103, 'interview',    'ソラノート 夏の特大号インタビュー', 50,   55,   NULL, 5);
SELECT setval(pg_get_serial_sequence('issue_items', 'issue_item_id'), (SELECT max(issue_item_id) FROM issue_items));

INSERT INTO issue_item_persons (issue_item_id, person_id) VALUES
  (1, 1001), (1, 1002),
  (2, 1001), (2, 1002),
  (3, 1003),
  (8, 2001),
  (4, 1003),
  (5, 2001),
  (7, 1003),
  (6, 2001);
