// Проверка api/comments.js без сети: GitHub API подменён памятью теста (contents + git trees/blobs).
// Запуск: node tests/api.test.mjs
import handler from '../api/comments.js';

const files = new Map(); let fail = 0, n = 0, loseNextPutResponse = false, truncated = false;
const ok = (name, cond, fact) => { n++; if (!cond) fail++; console.log((cond ? 'PASS ' : 'FAIL ') + name + ' — ' + fact); };
globalThis.fetch = async (url, init = {}) => {
  const u = String(url), m = init.method || 'GET', J = (s, b) => ({ok: s < 300, status: s, json: async () => b});
  if (u.includes('/git/ref/heads/inbox')) return J(200, {object: {sha: 'c1'}});
  if (u.includes('/git/commits/c1')) return J(200, {tree: {sha: 'root'}});
  if (u.endsWith('/git/trees/root')) return J(200, {truncated: false, tree: [{path: 'inbox', type: 'tree', sha: 'inboxtree'}, {path: 'index.html', type: 'blob', sha: 'x'}]});
  if (u.endsWith('/git/trees/inboxtree')) return J(200, {truncated, tree: [...files.keys()].map((name) => ({path: name, type: 'blob', sha: 'b:' + name}))});
  if (u.includes('/git/blobs/b:')) { const name = decodeURIComponent(u.split('/git/blobs/b:')[1]); return files.has(name) ? J(200, {content: files.get(name), encoding: 'base64'}) : J(404, {}); }
  const name = (u.split('/contents/inbox/')[1] || '').split('?')[0];
  if (m === 'PUT') {
    if (files.has(name)) return J(422, {message: 'sha wasn\'t supplied'});
    files.set(name, JSON.parse(init.body).content);
    if (loseNextPutResponse) { loseNextPutResponse = false; throw new Error('socket hang up'); }
    return J(201, {});
  }
  if (files.has(name)) return J(200, {content: files.get(name)});
  return J(404, {});
};
const call = async (method, body, query = {}) => { let st = 0, out; const res = {status(s) { st = s; return this; }, json(b) { out = b; return this; }}; await handler({method, body, query}, res); return {st, out}; };
const stamp = (d) => d.toISOString().replace(/[:.]/g, '-');
const put = (name, items) => files.set(name, Buffer.from(JSON.stringify({sentAt: new Date().toISOString(), items})).toString('base64'));

delete process.env.GITHUB_TOKEN;
let r = await call('POST', {items: [{taskId: 't1', action: 'done', at: new Date().toISOString()}]});
ok('Без GITHUB_TOKEN — честная ошибка 503, не «ок»', r.st === 503 && r.out.ok === false, `ответ ${r.st}: ${r.out.error}`);
r = await call('GET');
ok('GET без токена — тоже 503', r.st === 503, `ответ ${r.st}`);

process.env.GITHUB_TOKEN = 'test';
r = await call('POST', {items: [{taskId: 't1', action: 'done', at: new Date().toISOString(), eid: 'e1aaaaaa'}, {taskId: 't2', action: 'reopen', at: new Date().toISOString(), eid: 'e2aaaaaa'}]});
ok('POST сохраняет файл в ветку inbox', r.st === 200 && r.out.count === 2 && files.size === 1, `ответ ${r.st}, отметок ${r.out.count}, файлов ${files.size}`);
r = await call('POST', {items: [{taskId: 't3', action: 'взломать', text: ''}]});
ok('Неизвестное действие не принимается', r.st === 400, `ответ ${r.st}`);
r = await call('GET');
ok('GET отдаёт отметки за 48 ч с id события', r.st === 200 && r.out.items.length === 2 && r.out.items[1].action === 'reopen' && r.out.items[0].eid === 'e1aaaaaa', `ответ ${r.st}, отметок ${r.out.items && r.out.items.length}`);
files.set('2026-01-01T10-00-00-000Z.json', Buffer.from(JSON.stringify({items: [{taskId: 'old', action: 'done'}]})).toString('base64'));
r = await call('GET');
ok('Старше 48 ч не отдаются', !r.out.items.some((x) => x.taskId === 'old'), `старых в ответе: ${r.out.items.filter((x) => x.taskId === 'old').length}`);

// Codex (4): PUT прошёл, ответ потерялся → 502; повтор того же batchId → 200 и один файл
files.clear();
const batchId = stamp(new Date()) + '-abc12345', items = [{taskId: 't9', action: 'done', at: new Date().toISOString(), eid: 'e9aaaaaa'}];
loseNextPutResponse = true;
r = await call('POST', {batchId, items});
const first = r.st;
r = await call('POST', {batchId, items});
ok('Потерянный ответ PUT: повтор той же партии — 200 и ОДИН файл', first === 502 && r.st === 200 && r.out.duplicate === true && files.size === 1, `первый ответ ${first}, повтор ${r.st}, файлов ${files.size}`);
r = await call('POST', {batchId, items: [{taskId: 't9', action: 'later', at: new Date().toISOString(), eid: 'e9aaaaaa'}]});
ok('Тот же batchId с другим содержимым — ошибка 409', r.st === 409 && r.out.ok === false && files.size === 1, `ответ ${r.st}: ${r.out.error}`);

// Codex (5): 41 свежий файл — все отметки через пагинацию, без молчаливого среза
files.clear();
const t0 = Date.now() - 3600e3;
for (let i = 0; i < 41; i++) put(stamp(new Date(t0 + i * 1000)) + '-p' + String(i).padStart(7, '0') + '.json', [{taskId: 'k' + i, action: 'done', at: new Date(t0 + i * 1000).toISOString(), eid: 'ek' + String(i).padStart(6, '0')}]);
let got = [], cursor = '', pages = 0;
do { r = await call('GET', undefined, cursor ? {cursor} : {}); got.push(...r.out.items); cursor = r.out.nextCursor; pages++; } while (cursor && pages < 10);
ok('41 файл: первая страница с nextCursor, все 41 отметка получены', pages === 2 && got.length === 41 && new Set(got.map((x) => x.taskId)).size === 41, `страниц ${pages}, отметок ${got.length}`);
// 1500 файлов: Contents API отдал бы 1000, git trees — все
files.clear();
for (let i = 0; i < 1500; i++) put(stamp(new Date(t0 + i * 100)) + '-q' + String(i).padStart(7, '0') + '.json', [{taskId: 'q' + i, action: 'eve', at: new Date().toISOString()}]);
got = []; cursor = ''; pages = 0;
do { r = await call('GET', undefined, cursor ? {cursor} : {}); got.push(...r.out.items); cursor = r.out.nextCursor; pages++; } while (cursor && pages < 100);
ok('1500 файлов (больше лимита Contents 1000) — все отметки', got.length === 1500, `страниц ${pages}, отметок ${got.length}`);
truncated = true;
r = await call('GET');
ok('Обрезанный список GitHub — честная ошибка 502, не неполные данные', r.st === 502 && /неполный/.test(r.out.error), `ответ ${r.st}: ${r.out.error}`);

console.log(`\nИтог API: ${n - fail} из ${n}`); process.exitCode = fail ? 1 : 0;
