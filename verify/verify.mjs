// sql/ の DDL・ビュー・サンプルデータを PGlite（WASM版 PostgreSQL）で実行して検証する
//   cd verify && npm install && node verify.mjs
import { PGlite } from '@electric-sql/pglite';
import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';

const sql = (name) => readFileSync(new URL(`../sql/${name}`, import.meta.url), 'utf8');

async function freshDb() {
  const db = new PGlite();
  await db.exec(`SET TIME ZONE 'Asia/Tokyo'`);
  for (const f of ['01_schema.sql', '02_views.sql', '03_sample_data.sql']) await db.exec(sql(f));
  return db;
}

const ids = async (db, query, params = []) =>
  (await db.query(query, params)).rows.map((r) => Number(Object.values(r)[0])).sort((a, b) => a - b);

let passed = 0;
async function check(label, fn) {
  await fn();
  passed++;
  console.log(`ok  ${label}`);
}

async function rejects(db, label, statement) {
  await check(label, async () => {
    await assert.rejects(db.exec(`BEGIN; ${statement}; COMMIT;`));
    await db.exec('ROLLBACK');
  });
}

const db = await freshDb();
const articles = (pid) => ids(db, 'SELECT article_id FROM v_person_articles WHERE person_id = $1', [pid]);
const events = (pid, upcoming) =>
  ids(db, 'SELECT event_id FROM v_person_events WHERE person_id = $1 AND is_upcoming = $2', [pid, upcoming]);
const issues = (pid) => ids(db, 'SELECT issue_id FROM v_person_issues WHERE person_id = $1', [pid]);
const serials = async (pid) =>
  Object.fromEntries(
    (await db.query('SELECT serial_id, is_regular, appearance_count FROM v_person_serials WHERE person_id = $1', [pid]))
      .rows.map((r) => [Number(r.serial_id), { regular: r.is_regular, count: Number(r.appearance_count) }]),
  );

// --- 関連記事 -------------------------------------------------------------
await check('1001 記事: 本人タグ + 旧芸名タグ + ユニット経由。下書き・予約は出ない', async () =>
  assert.deepEqual(await articles(1001), [180001, 180002, 180003, 180004, 180005, 180006, 180009, 180011]));
await check('1002 記事: 卒業後のユニット記事(180004)は出ない／重複人物タグの記事は統合前なので出ない', async () =>
  assert.deepEqual(await articles(1002), [180002, 180003, 180011]));
await check('1003 記事: 加入前のユニット記事(180003)は出ない／未紐付けタグの記事は出ない', async () =>
  assert.deepEqual(await articles(1003), [180004]));
await check('2001(ユニット) 記事', async () => assert.deepEqual(await articles(2001), [180003, 180004]));
await check('3001(名鑑未登録) 記事', async () => assert.deepEqual(await articles(3001), [180004]));
await check('ユニット経由の記事には via_unit_name が付く', async () => {
  const r = await db.query('SELECT via_unit_name FROM v_person_articles WHERE person_id = 1001 AND article_id = 180004');
  assert.equal(r.rows[0].via_unit_name, 'ソラノート');
});

// --- 出演イベント ---------------------------------------------------------
await check('1001 イベント: 今後=2 / 過去=1,3,4（1,4 はユニット経由）', async () => {
  assert.deepEqual(await events(1001, true), [2]);
  assert.deepEqual(await events(1001, false), [1, 3, 4]);
});
await check('1002 イベント: 在籍中の1stのみ（卒業後の2ndは出ない）', async () => {
  assert.deepEqual(await events(1002, true), [2]);
  assert.deepEqual(await events(1002, false), [4]);
});
await check('1003 イベント: 出演キャンセル(3)と加入前(4)は出ない', async () =>
  assert.deepEqual(await events(1003, false), [1]));

// --- 掲載号 ---------------------------------------------------------------
await check('掲載号: 在籍期間で絞り込み', async () => {
  assert.deepEqual(await issues(1001), [101, 102, 103]);
  assert.deepEqual(await issues(1002), [101]);
  assert.deepEqual(await issues(1003), [101, 102, 103]);
});
await check('1001 の 11月号は表紙扱い、項目は表紙→巻頭→連載の順', async () => {
  const r = await db.query('SELECT is_cover, items FROM v_person_issues WHERE person_id = 1001 AND issue_id = 101');
  assert.equal(r.rows[0].is_cover, true);
  assert.deepEqual(r.rows[0].items.map((i) => i.label), ['表紙', '巻頭特集', '連載']);
});

// --- 連載 ---------------------------------------------------------------
await check('1001 連載: 自分の連載(1, 2回登場) + ユニットの連載(3)', async () =>
  assert.deepEqual(await serials(1001), { 1: { regular: true, count: 2 }, 3: { regular: true, count: 1 } }));
await check('1003 連載: 誌面連載(2, 2回) + ユニット連載(3)。ゲスト回はタグ未紐付けなのでまだ出ない', async () =>
  assert.deepEqual(await serials(1003), { 2: { regular: true, count: 2 }, 3: { regular: true, count: 1 } }));

// --- 移行: タグ紐付け候補 → 確定 -----------------------------------------
await check('紐付け候補: 「森野くるみ」タグ → 1003（スペース違いを吸収）', async () => {
  const r = await db.query('SELECT tag_id, person_id FROM v_tag_link_candidates');
  assert.deepEqual(r.rows.map((x) => [Number(x.tag_id), Number(x.person_id)]), [[504, 1003]]);
});
await check('代表タグなし: 1003 のみ', async () =>
  assert.deepEqual(await ids(db, 'SELECT person_id FROM v_persons_without_primary_tag'), [1003]));
await db.exec(`UPDATE tags SET tag_type = 'person', person_id = 1003, is_primary = true WHERE tag_id = 504`);
await check('紐付け後: 1003 にゲスト回の記事と連載(1, ゲスト)が自動で出る', async () => {
  assert.deepEqual(await articles(1003), [180004, 180006]);
  assert.deepEqual((await serials(1003))[1], { regular: false, count: 1 });
  assert.deepEqual(await ids(db, 'SELECT person_id FROM v_persons_without_primary_tag'), []);
});

// --- 統合 ---------------------------------------------------------------
await db.exec('SELECT merge_person(50001, 1002)');
await check('統合後: 重複人物タグの記事(180010)が 1002 に出る', async () =>
  assert.deepEqual(await articles(1002), [180002, 180003, 180010, 180011]));
await check('統合後: 移したタグは別名タグに降格、旧表示名は別名に', async () => {
  const t = await db.query('SELECT person_id, is_primary FROM tags WHERE tag_id = 507');
  assert.deepEqual([Number(t.rows[0].person_id), t.rows[0].is_primary], [1002, false]);
  const n = await db.query(`SELECT name_kind FROM person_names WHERE person_id = 1002 AND name = '月島蒼'`);
  assert.equal(n.rows[0].name_kind, 'alias');
});
await check('統合後: person_page(50001) は 1002 のページを返す', async () => {
  const r = await db.query('SELECT person_page(50001) AS j');
  assert.equal(Number(r.rows[0].j.person_id), 1002);
  assert.equal(Number(r.rows[0].j.requested_id), 50001);
});

// --- person_page ----------------------------------------------------------
await check('person_page(1001): 全セクションが埋まる', async () => {
  const j = (await db.query('SELECT person_page(1001) AS j')).rows[0].j;
  assert.equal(j.name, '春風 ひより');
  assert.equal(j.agency.name, 'スタジオ青嵐');
  assert.equal(j.profile.birthday, '3月15日');
  assert.equal(j.articles_total, 8);
  assert.equal(j.articles[0].article_id, 180011);         // 新しい順
  assert.deepEqual(j.upcoming_events.map((e) => e.role_label), ['MC']);
  assert.equal(j.issues[0].issue_label, '2026年12月号');
  assert.deepEqual(j.serials.map((s) => s.title), ['ひよりの陽だまり日記', 'ソラノートのそらばなし']);
  assert.deepEqual(j.units.map((u) => u.name), ['ソラノート']);
});
await check('person_page(3001): 名鑑未登録でも記事・イベントは出る', async () => {
  const j = (await db.query('SELECT person_page(3001) AS j')).rows[0].j;
  assert.equal(j.profile, null);
  assert.equal(j.past_events[0].role_label, 'ゲスト');
});
await check('person_page(2001): ユニットページにメンバー一覧', async () => {
  const j = (await db.query('SELECT person_page(2001) AS j')).rows[0].j;
  assert.deepEqual(j.members.map((m) => m.name), ['春風 ひより', '森野 くるみ', '月島 蒼']);
});
await check('person_page(存在しないID) は NULL', async () =>
  assert.equal((await db.query('SELECT person_page(999999) AS j')).rows[0].j, null));

// --- 名鑑 ---------------------------------------------------------------
await check('五十音索引', async () => {
  const r = await db.query(`SELECT person_id, kana_row FROM persons WHERE status <> 'merged' ORDER BY person_id`);
  assert.deepEqual(r.rows.map((x) => x.kana_row), ['は', 'た', 'ま', 'さ', 'か']);
});

// --- 制約 ---------------------------------------------------------------
await rejects(db, '制約: person タグに person_id 必須', `INSERT INTO tags (slug, name, tag_type) VALUES ('x', 'x', 'person')`);
await rejects(db, '制約: 代表タグは1人1つ', `INSERT INTO tags (slug, name, tag_type, person_id, is_primary) VALUES ('y', 'y', 'person', 1001, true)`);
await rejects(db, '制約: ユニットでない人物をユニットにできない', `INSERT INTO unit_members (unit_id, member_id) VALUES (1001, 1002)`);
await rejects(db, '制約: 現所属は1件まで', `INSERT INTO person_affiliations (person_id, agency_id, started_on) VALUES (1001, 2, '2026-01-01')`);
await rejects(db, '制約: ふりがなはひらがな', `INSERT INTO persons (display_name, name_kana) VALUES ('X', 'カタカナ')`);
await rejects(db, '制約: 公開記事は公開日時必須', `INSERT INTO articles (article_type, path, title, status) VALUES ('news', '/news/1/', 't', 'published')`);
await rejects(db, '制約: 統合済み人物は再統合できない', `SELECT merge_person(50001, 1001)`);

// --- 04_queries.sql が新しいDBでそのまま通るか ---------------------------
await check('04_queries.sql 全文が実行できる', async () => {
  const db2 = await freshDb();
  await db2.exec(sql('04_queries.sql'));
  const j = (await db2.query('SELECT person_page(1003) AS j')).rows[0].j;
  assert.equal(j.articles_total, 2);
  await db2.close();
});

console.log(`\n${passed} checks passed`);
if (process.argv.includes('--print')) {
  console.log(JSON.stringify((await db.query('SELECT person_page(1003) AS j')).rows[0].j, null, 2));
}
await db.close();
