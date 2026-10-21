// WordPress 移行の検証
//   WordPress と同じ構造の SQLite（make_wp_fixture.py）→ extract_wp.py → wp スキーマ → wp_migration.run()
//   cd verify && npm install && node verify_migration.mjs        （python が必要）
import { PGlite } from '@electric-sql/pglite';
import { readFileSync, mkdtempSync, rmSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';

const root = fileURLToPath(new URL('..', import.meta.url));
const python = process.env.PYTHON ?? 'python';
const work = mkdtempSync(join(tmpdir(), 'seigura-mig-'));

let passed = 0;
async function check(label, fn) {
  await fn();
  passed++;
  console.log(`ok  ${label}`);
}

// --- WordPress 側: フィクスチャ作成 → 書き出し ---------------------------
execFileSync(python, ['-I', join(root, 'verify', 'make_wp_fixture.py'), join(work, 'wp.db')]);
execFileSync(python, ['-I', join(root, 'migrate', 'extract_wp.py'), '--sqlite', join(work, 'wp.db'),
                      '--out', join(work, 'wp_dump')], { stdio: ['ignore', 'inherit', 'inherit'] });

// --- PostgreSQL 側 --------------------------------------------------------
const db = new PGlite();
await db.exec(`SET TIME ZONE 'Asia/Tokyo'`);
for (const f of ['sql/01_schema.sql', 'sql/02_views.sql', 'migrate/10_setup.sql', 'migrate/20_run.sql']) {
  await db.exec(readFileSync(join(root, f), 'utf8'));
}
// load_staging.sql の \copy と同じことを PGlite の COPY で行う
for (const t of ['posts', 'postmeta', 'terms', 'term_relationships']) {
  const blob = new Blob([readFileSync(join(work, 'wp_dump', `${t}.tsv`))]);
  await db.query(`COPY wp.${t} FROM '/dev/blob'`, [], { blob });
}

const rows = async (q, p = []) => (await db.query(q, p)).rows;
const ids = async (q, p = []) => (await rows(q, p)).map((r) => Number(Object.values(r)[0])).sort((a, b) => a - b);
const run = async () => Object.fromEntries((await rows('SELECT * FROM wp_migration.run()')).map((r) => [r.step_name, Number(r.affected)]));
const personArticles = (pid) => ids('SELECT article_id FROM v_person_articles WHERE person_id = $1', [pid]);
const problems = async () => (await rows('SELECT kind, source_id, message FROM wp_migration.problems ORDER BY problem_id'))
  .map((r) => `${r.kind}:${r.source_id}:${r.message}`);
const snapshot = async () => JSON.stringify(await rows(`
  SELECT (SELECT json_agg(x ORDER BY article_id) FROM (SELECT article_id, article_type, path, title, excerpt, thumbnail_url,
                 serial_id, episode_no, status, published_at, updated_at FROM articles) x) AS a,
         (SELECT json_agg(x ORDER BY person_id) FROM (SELECT person_id, display_name, name_kana, updated_at FROM persons) x) AS p,
         (SELECT json_agg(x ORDER BY person_id) FROM person_profiles x) AS pp,
         (SELECT json_agg(x ORDER BY tag_id) FROM tags x) AS t,
         (SELECT json_agg(x ORDER BY article_id, tag_id) FROM article_tags x) AS at,
         (SELECT json_agg(x ORDER BY person_id, sort_order) FROM (SELECT person_id, work_id, character_name, sort_order FROM castings) x) AS c,
         (SELECT json_agg(x ORDER BY affiliation_id) FROM person_affiliations x) AS af,
         (SELECT json_agg(x ORDER BY issue_id) FROM magazine_issues x) AS mi`));

// --- 書き出し --------------------------------------------------------------
await check('書き出し: ゴミ箱・対象外の投稿タイプは出ない／ACF の参照キーは出ない', async () => {
  assert.deepEqual(await ids(`SELECT id FROM wp.posts WHERE id IN (110, 111, 2009)`), []);
  assert.deepEqual(await ids(`SELECT count(*) FROM wp.postmeta WHERE meta_key LIKE '\\_%' AND meta_key NOT IN ('_thumbnail_id', '_wp_attached_file')`), [0]);
});
await check('書き出し: タブ・改行・バックスラッシュがそのまま戻る', async () => {
  const r = await rows('SELECT post_title, post_content FROM wp.posts WHERE id = 115');
  assert.equal(r[0].post_title, 'タブ\tと\\バックスラッシュ');
  assert.equal(r[0].post_content, '1行目\r\n2行目\tタブ');
});

// --- カスタムフィールドの対応付け -----------------------------------------
await check('field_map 未登録なら run() は止まる', async () =>
  assert.rejects(db.query('SELECT * FROM wp_migration.run()'), /ふりがな/));
await check('カスタムフィールドの推定', async () => {
  const g = Object.fromEntries((await rows('SELECT field, meta_key FROM wp_migration.v_field_map_guesses')).map((r) => [r.field, r.meta_key]));
  assert.deepEqual(g, {
    agency: 'office', birthday: 'birthday', birthplace: 'birthplace', blood_type: 'bloodtype', hobbies: 'hobby',
    issue_price: 'price', issue_release_date: 'release_date', name_kana: 'furigana', works: 'works',
  });
});
await db.exec('INSERT INTO wp_migration.field_map SELECT field, meta_key FROM wp_migration.v_field_map_guesses');

// --- 1回目 ---------------------------------------------------------------
const r1 = await run();
await check('1回目: 実行結果', async () => {
  assert.equal(r1['人物: 追加・更新'], 5);
  assert.equal(r1['記事: 追加・更新'], 15);
  assert.equal(r1['タグ: 人物に自動で紐付け'], 3);
});
await check('人物: ふりがな欠落の 2004 は取り込まず問題に記録', async () => {
  assert.deepEqual(await ids('SELECT person_id FROM persons'), [2001, 2002, 2003, 2007, 2008]);
  assert.ok((await problems()).includes('person:2004:ふりがなが空のため取り込めません（kana_overrides で指定できます）'));
});
await check('人物: 全角スペース・カタカナのふりがなを整形', async () => {
  const [p] = await rows('SELECT display_name, name_kana, kana_row FROM persons WHERE person_id = 2001');
  assert.deepEqual(p, { display_name: '春風 ひより', name_kana: 'はるかぜ ひより', kana_row: 'は' });
});
await check('名鑑: 誕生日・血液型・画像・公開状態', async () => {
  const pp = Object.fromEntries((await rows('SELECT * FROM person_profiles')).map((r) => [r.person_id, r]));
  assert.deepEqual([pp[2001].birth_year, pp[2001].birth_month, pp[2001].birth_day, pp[2001].blood_type], [null, 3, 15, 'A']);
  assert.equal(pp[2001].image_url, 'https://seigura.com/wp-content/uploads/2024/05/hiyori.jpg');
  assert.deepEqual([pp[2002].birth_year, pp[2002].birth_month, pp[2002].birth_day], [1999, 10, 21]);
  assert.deepEqual([pp[2003].birth_month, pp[2003].birth_day, pp[2003].is_published], [7, 7, false]);
  assert.equal(pp[2008].blood_type, 'AB');
  assert.ok((await problems()).includes('person:2007:誕生日を読み取れません'));
});
await check('所属: 事務所を名前で統合して現所属に', async () => {
  const r = await rows(`SELECT pa.person_id, a.name FROM person_affiliations pa JOIN agencies a USING (agency_id)
                        WHERE pa.ended_on IS NULL ORDER BY pa.person_id`);
  assert.deepEqual(r.map((x) => `${x.person_id}:${x.name}`), ['2001:スタジオ青嵐', '2002:オフィス木漏れ日', '2003:スタジオ青嵐']);
  assert.deepEqual(await ids('SELECT count(*) FROM agencies'), [2]);
});
await check('出演作品: 区分の引き継ぎ・括弧・「役ほか」を処理', async () => {
  const r = await rows(`SELECT w.title, w.work_type, c.character_name FROM castings c JOIN works w USING (work_id)
                        WHERE c.person_id = 2001 ORDER BY c.sort_order`);
  assert.deepEqual(r.map((x) => [x.title, x.work_type, x.character_name]), [
    ['きらめきステージ!', 'anime', '天音ルカ'], ['星降る町の喫茶店', 'anime', 'ミラ（第2期）'], ['ドリームアーク', 'game', 'ミラ']]);
  const [d] = await rows(`SELECT w.work_type FROM castings c JOIN works w USING (work_id) WHERE c.person_id = 2002`);
  assert.equal(d.work_type, 'dub');
});
await check('タグ: 名前が1人に一致するタグだけ自動で紐付け、同姓同名は保留', async () => {
  const t = Object.fromEntries((await rows('SELECT tag_id, tag_type, person_id, is_primary FROM tags')).map((r) => [r.tag_id, r]));
  assert.deepEqual([t[301].person_id, t[301].is_primary, t[302].person_id, t[303].person_id], [2001, true, 2002, 2003]);
  assert.equal(t[307].tag_type, 'topic');
  assert.ok((await problems()).includes('tag:307:同じ名前の人物が複数いるため自動で紐付けていません'));
});
await check('記事: 種別・URL・公開状態・抜粋・アイキャッチ', async () => {
  const a = Object.fromEntries((await rows('SELECT * FROM articles')).map((r) => [r.article_id, r]));
  assert.deepEqual(Object.keys(a).map(Number), [100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 113, 114, 115, 202, 203]);
  assert.deepEqual([a[100].path, a[104].path, a[106].path, a[113].path, a[102].path], ['/news/100/', '/senior/104/', '/column/hiyori12/', '/news/113/', '/news/102/']);
  assert.deepEqual([a[102].status, a[103].status, a[105].article_type, a[202].article_type], ['draft', 'scheduled', 'event', 'magazine']);
  assert.equal(a[100].excerpt, '春風ひよりさんの 1st写真集 が発売。');
  assert.equal(a[100].thumbnail_url, 'https://seigura.com/wp-content/uploads/2026/09/photo.jpg');
  assert.equal(a[100].published_at.toISOString(), '2026-09-01T03:00:00.000Z');
});
await check('連載: スラッグから連載・回数を判定、重複回は空に', async () => {
  const [s] = await rows(`SELECT serial_id, title, medium, started_on FROM serials WHERE slug = 'hiyori'`);
  assert.equal(s.title, '春風ひよりの陽だまり日記');
  const e = Object.fromEntries((await rows('SELECT article_id, serial_id, episode_no FROM articles WHERE article_id IN (106,107,108,109)'))
    .map((r) => [r.article_id, [r.serial_id === null ? null : Number(r.serial_id), r.episode_no]]));
  assert.deepEqual(e, { 106: [Number(s.serial_id), 12], 107: [Number(s.serial_id), 13], 108: [null, null], 109: [Number(s.serial_id), null] });
  assert.ok((await problems()).includes('article:109:同じ連載で回数が重複しているため、この記事の回数は空にしました'));
});
await check('個人ページ: 公開記事だけがタグ経由で出る', async () => {
  assert.deepEqual(await personArticles(2001), [100, 101, 106, 107, 202]);
  assert.deepEqual(await personArticles(2002), [101, 104]);
  assert.deepEqual(await personArticles(2003), [107]);
});
await check('号: タイトルから号を判定、発売日・定価はカスタムフィールドから', async () => {
  const mi = Object.fromEntries((await rows('SELECT *, release_date::text AS release FROM magazine_issues')).map((r) => [r.issue_id, r]));
  assert.deepEqual(Object.keys(mi).map(Number), [200, 201]);
  assert.equal(mi[200].issue_label, '2026年11月号');
  assert.equal(mi[200].release, '2026-10-09');
  assert.equal(mi[201].release, '2026-10-15');
  assert.equal(mi[200].price_yen, 1610);
  assert.equal(mi[200].cover_image_url, 'https://seigura.com/wp-content/uploads/2026/10/cover11.jpg');
  const p = await problems();
  assert.ok(p.includes('issue:201:発売日を読み取れないため、投稿日を仮の発売日にしました'));
  assert.ok(p.includes('issue:203:同じ号のページが複数あるため、記事として取り込みました'));
});
await check('号の目次: 見出しごとに企画の種類と人物を抽出（特典欄は除外）', async () => {
  const c = (await rows(`SELECT issue_id, item_type, serial_title, person_id FROM wp_migration.issue_item_candidates
                         ORDER BY issue_id, section_no, item_type, person_id`))
    .map((r) => `${r.issue_id}:${r.item_type}:${r.serial_title ?? ''}:${r.person_id}`);
  assert.deepEqual(c.sort(), [
    '200:cover::2001', '200:main_feature::2001',
    '200:feature::2001', '200:feature::2002', '200:feature::2003',
    '200:serial:くるみの絵本棚:2003', '200:serial:ひよりの陽だまり日記:2001',
    '201:appearance::2002'].sort());
  assert.deepEqual(await ids('SELECT count(*) FROM issue_items'), [0]);   // auto_issue_items = false
});

// --- 2回目（変更なし）: 何も変わらない ------------------------------------
const before = await snapshot();
const r2 = await run();
await check('2回目: 変更なしなら追加・更新は 0 件で、データも updated_at も変わらない', async () => {
  for (const k of ['タグ: 追加・更新', '人物: 追加・更新', '名鑑: 追加・更新', '記事: 追加・更新', '号: 追加・更新', 'タグ付け: 追加', 'タグ付け: 削除', '所属: 追加', '事務所: 追加', '作品: 追加']) {
    assert.equal(r2[k], 0, k);
  }
  assert.equal(await snapshot(), before);
});

// --- 掲載候補の登録 --------------------------------------------------------
await check('掲載候補を登録すると個人ページの掲載号・連載に出る', async () => {
  assert.deepEqual(await ids('SELECT wp_migration.apply_issue_candidates()'), [2]);
  const [i] = await rows('SELECT items FROM v_person_issues WHERE person_id = 2001 AND issue_id = 200');
  assert.deepEqual(i.items.map((x) => x.label), ['表紙', '巻頭特集', '特集', '連載']);
  assert.deepEqual(await ids('SELECT issue_id FROM v_person_issues WHERE person_id = 2002'), [200, 201]);
  const [s] = await rows(`SELECT title, is_regular, roles FROM v_person_serials WHERE person_id = 2003 AND title = 'くるみの絵本棚'`);
  assert.deepEqual([s.is_regular, s.roles], [true, ['author']]);
});
await check('登録済みの号は再実行しても重複しない', async () => {
  await run();
  // 200号: 表紙・巻頭特集・特集（3人）・連載×2 / 201号: 掲載
  assert.deepEqual(await ids('SELECT count(*) FROM issue_items'), [6]);
  assert.deepEqual(await ids('SELECT wp_migration.apply_issue_candidates()'), [0]);
});

// --- WordPress 側の変更と新DB側の編集を混ぜて再実行 ------------------------
const unitId = Number((await rows(`SELECT create_person_from_tag(304, 'そらのーと', 'unit') AS id`))[0].id);
await db.exec(`
  INSERT INTO unit_members (unit_id, member_id) VALUES (${unitId}, 2001);
  UPDATE articles SET serial_id = (SELECT serial_id FROM serials WHERE slug = 'hiyori') WHERE article_id = 108;
  INSERT INTO wp_migration.kana_overrides VALUES (2004, 'きりゅう たくま');
  UPDATE wp.postmeta SET meta_value = 'スタジオ青嵐' WHERE post_id = 2002 AND meta_key = 'office';
  DELETE FROM wp.posts WHERE id IN (101, 2008);
  DELETE FROM wp.term_relationships WHERE object_id = 101;
  UPDATE wp.terms SET name = 'イベント・ライブ情報' WHERE term_id = 305;
  INSERT INTO wp.term_relationships VALUES (113, 10302);
`);
const r3 = await run();
await check('変更後: 新DBで作った人物は 10億 以降の ID', async () => assert.ok(unitId >= 1_000_000_000));
await check('変更後: kana_overrides で 2004 を取り込み、タグも自動で紐付く', async () => {
  assert.deepEqual(await ids('SELECT person_id FROM tags WHERE tag_id = 306'), [2004]);
  assert.deepEqual(await ids(`SELECT count(*) FROM agencies WHERE name = 'アトリエ灯'`), [1]);
});
await check('変更後: 事務所の変更は履歴として残る', async () => {
  const r = await rows(`SELECT a.name, pa.started_on IS NOT NULL AS started, pa.ended_on IS NOT NULL AS ended
                        FROM person_affiliations pa JOIN agencies a USING (agency_id) WHERE pa.person_id = 2002 ORDER BY affiliation_id`);
  assert.deepEqual(r.map((x) => [x.name, x.started, x.ended]), [['オフィス木漏れ日', false, true], ['スタジオ青嵐', true, false]]);
});
await check('変更後: 削除された記事は非公開、名鑑は非公開、タグ名とタグ付けは追従', async () => {
  const [a] = await rows('SELECT status FROM articles WHERE article_id = 101');
  assert.equal(a.status, 'private');
  const [pp] = await rows('SELECT is_published FROM person_profiles WHERE person_id = 2008');
  assert.equal(pp.is_published, false);
  const [t] = await rows('SELECT name FROM tags WHERE tag_id = 305');
  assert.equal(t.name, 'イベント・ライブ情報');
  assert.deepEqual(await personArticles(2002), [104, 113]);
  assert.equal(r3['記事: 非公開化（WordPress で削除・ゴミ箱）'], 1);
});
await check('変更後: 新DB側の編集（ユニット化したタグ・手動の連載設定）は上書きされない', async () => {
  const [t] = await rows('SELECT tag_type, person_id FROM tags WHERE tag_id = 304');
  assert.deepEqual([t.tag_type, Number(t.person_id)], ['person', unitId]);
  assert.deepEqual(await personArticles(2001), [100, 105, 106, 107, 202]);  // 105 はユニット経由
  const [a] = await rows('SELECT serial_id FROM articles WHERE article_id = 108');
  assert.notEqual(a.serial_id, null);
});
await check('移行後の person_page', async () => {
  const j = (await rows('SELECT person_page(2001) AS j'))[0].j;
  assert.equal(j.agency.name, 'スタジオ青嵐');
  assert.equal(j.castings.length, 3);
  assert.equal(j.issues[0].issue_label, '2026年11月号');
  assert.deepEqual(j.serials.map((s) => s.title), ['ひよりの陽だまり日記', '春風ひよりの陽だまり日記']);
  assert.deepEqual(j.units.map((u) => u.name), ['ソラノート']);
});

console.log(`\n${passed} checks passed`);
console.log('\n問題の一覧（最後の実行）:');
console.table(await rows('SELECT kind, message, rows, sample_ids FROM wp_migration.v_problem_summary'));
await db.close();
rmSync(work, { recursive: true, force: true });
