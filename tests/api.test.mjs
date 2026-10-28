// Проверка api/comments.js без сети: GitHub API подменён памятью теста.
// Запуск: node tests/api.test.mjs
import handler from '../api/comments.js';

const files = new Map(); let fail = 0, n = 0;
const ok = (name, cond, fact) => { n++; if (!cond) fail++; console.log((cond ? 'PASS ' : 'FAIL ') + name + ' — ' + fact); };
globalThis.fetch = async (url, init = {}) => {
  const u = String(url), m = init.method || 'GET', J = (s, b) => ({ok: s < 300, status: s, json: async () => b});
  if (u.includes('/git/ref/heads/inbox')) return J(200, {object: {sha: 'x'}});
  if (m === 'PUT') { const name = u.split('/contents/inbox/')[1]; files.set(name, JSON.parse(init.body).content); return J(201, {}); }
  if (/\/contents\/inbox\?ref=/.test(u)) return J(200, [...files.keys()].map((name) => ({name})));
  const name = (u.split('/contents/inbox/')[1] || '').split('?')[0];
  if (files.has(name)) return J(200, {content: files.get(name)});
  return J(404, {});
};
const call = async (method, body) => { let st = 0, out; const res = {status(s) { st = s; return this; }, json(b) { out = b; return this; }}; await handler({method, body}, res); return {st, out}; };

delete process.env.GITHUB_TOKEN;
let r = await call('POST', {items: [{taskId: 't1', action: 'done', at: new Date().toISOString()}]});
ok('Без GITHUB_TOKEN — честная ошибка 503, не «ок»', r.st === 503 && r.out.ok === false, `ответ ${r.st}: ${r.out.error}`);
r = await call('GET');
ok('GET без токена — тоже 503', r.st === 503, `ответ ${r.st}`);

process.env.GITHUB_TOKEN = 'test';
r = await call('POST', {items: [{taskId: 't1', action: 'done', at: new Date().toISOString()}, {taskId: 't2', action: 'reopen', at: new Date().toISOString()}]});
ok('POST сохраняет файл в ветку inbox', r.st === 200 && r.out.count === 2 && files.size === 1, `ответ ${r.st}, отметок ${r.out.count}, файлов ${files.size}`);
r = await call('POST', {items: [{taskId: 't3', action: 'взломать', text: ''}]});
ok('Неизвестное действие не принимается', r.st === 400, `ответ ${r.st}`);
r = await call('GET');
ok('GET отдаёт отметки за 48 ч для других устройств', r.st === 200 && r.out.items.length === 2 && r.out.items[1].action === 'reopen', `ответ ${r.st}, отметок ${r.out.items && r.out.items.length}`);
files.set('2026-01-01T10-00-00-000Z.json', Buffer.from(JSON.stringify({items: [{taskId: 'old', action: 'done'}]})).toString('base64'));
r = await call('GET');
ok('Старше 48 ч не отдаются', !r.out.items.some((x) => x.taskId === 'old'), `старых в ответе: ${r.out.items.filter((x) => x.taskId === 'old').length}`);
console.log(`\nИтог API: ${n - fail} из ${n}`); process.exitCode = fail ? 1 : 0;
