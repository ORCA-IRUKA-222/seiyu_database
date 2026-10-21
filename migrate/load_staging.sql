-- =====================================================================
-- extract_wp.py の出力（wp_dump/*.tsv）を wp スキーマに読み込む
--   wp_dump/ があるディレクトリで実行する:
--   psql -d seigura -f migrate/load_staging.sql
-- 毎回すべて入れ替える（wp スキーマは作業用。本スキーマには触れない）
-- =====================================================================
\set ON_ERROR_STOP on

BEGIN;
TRUNCATE wp.posts, wp.postmeta, wp.terms, wp.term_relationships;
\copy wp.posts              FROM 'wp_dump/posts.tsv'
\copy wp.postmeta           FROM 'wp_dump/postmeta.tsv'
\copy wp.terms              FROM 'wp_dump/terms.tsv'
\copy wp.term_relationships FROM 'wp_dump/term_relationships.tsv'
COMMIT;

ANALYZE wp.posts;
ANALYZE wp.postmeta;
ANALYZE wp.terms;
ANALYZE wp.term_relationships;

SELECT 'posts' AS staging, count(*) FROM wp.posts
UNION ALL SELECT 'postmeta', count(*) FROM wp.postmeta
UNION ALL SELECT 'terms', count(*) FROM wp.terms
UNION ALL SELECT 'term_relationships', count(*) FROM wp.term_relationships;
