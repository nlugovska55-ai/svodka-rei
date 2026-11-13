// Проверка api/tg.js без сети: Telegram и GitHub подменены. Запуск: node tests/tg.test.mjs
import handler, { secretOf, gorit, nuzhno, dengi } from '../api/tg.js';

let fail = 0, n = 0; const sent = [], saved = [];
const ok = (name, cond, fact = '') => { n++; if (!cond) fail++; console.log((cond ? 'PASS ' : 'FAIL ') + name + (fact ? ' — ' + fact : '')); };
globalThis.fetch = async (url, init = {}) => {
  const u = String(url), J = (s, b) => ({ ok: s < 300, status: s, json: async () => b });
  if (u.startsWith('https://api.telegram.org/')) { sent.push({ method: u.split('/').pop(), body: JSON.parse(init.body || '{}') }); return J(200, { ok: true, result: { username: 'test_bot' } }); }
  if (u.includes('/git/ref/heads/inbox')) return J(200, { object: { sha: 'c1' } });
  if (u.includes('/contents/inbox/') && init.method === 'PUT') { saved.push(JSON.parse(Buffer.from(JSON.parse(init.body).content, 'base64').toString())); return J(201, {}); }
  return J(404, {});
};
const call = async (body, headers = {}, method = 'POST', query = {}) => { let st = 0; const res = { setHeader() {}, status(s) { st = s; return this; }, json() { return this; }, send() { return this; }, end() { return this; } }; await handler({ method, body, headers, query }, res); return st; };

process.env.TELEGRAM_BOT_TOKEN = 'T';
process.env.GITHUB_TOKEN = 'G';
const H = { 'x-telegram-bot-api-secret-token': await secretOf('T') };
const msg = (text, from = 42) => ({ message: { chat: { id: from }, from: { id: from }, date: 1700000000, text } });

ok('Без секретного заголовка — 401', (await call(msg('hi'))) === 401);
delete process.env.TELEGRAM_ALLOWED_IDS;
sent.length = 0; await call(msg('/start'), H);
ok('Чужой id: бот закрыт и называет id', sent[0]?.body.text.includes('Ваш id: 42'));
process.env.TELEGRAM_ALLOWED_IDS = '42';
sent.length = 0; await call(msg('/start'), H);
ok('/start для Ольги', sent[0]?.body.text.includes('/gorit'));
sent.length = 0; saved.length = 0; await call(msg('Позвони Лене'), H);
ok('Текст ложится в inbox с source telegram', saved[0]?.source === 'telegram' && saved[0].items[0].text === 'Позвони Лене');
ok('Ответ «передано»', sent[0]?.body.text.startsWith('Принято'));
sent.length = 0; saved.length = 0; await call({ message: { chat: { id: 42 }, from: { id: 42 }, date: 1, document: {} } }, H);
ok('Файлы не принимаются и не сохраняются', saved.length === 0 && sent[0]?.body.text.includes('Файлы'));
sent.length = 0; saved.length = 0;
await call({ callback_query: { id: 'q', from: { id: 42 }, data: 'task1|Да', message: { chat: { id: 42 }, message_id: 7 } } }, H);
ok('Кнопка: в inbox с taskId', saved[0]?.items[0].taskId === 'task1' && saved[0].items[0].text.endsWith('Да'));
ok('Кнопки снимаются после нажатия', sent.some((s) => s.method === 'editMessageReplyMarkup'));
saved.length = 0; await call({ callback_query: { id: 'q', from: { id: 1 }, data: 'x|Да' } }, H);
ok('Чужая кнопка не сохраняется', saved.length === 0);
sent.length = 0; await call(msg('/gorit'), H);
ok('/gorit отвечает по data/svodka.json', /Срочн/.test(sent[0]?.body.text || ''));

const d = { updated: '13.11 09:31', tasks: [
  { client: 'А', title: 'x', urgency: 'сегодня' }, { client: 'Б', title: 'y', waiting: 'Ольга' },
  { client: 'В', title: 'z', money: 'ждём', amount: 1000 }, { client: 'Г', title: 'w', urgency: 'сегодня', done: true }] };
ok('gorit без закрытых', gorit(d).includes('(1)'));
ok('nuzhno', nuzhno(d).includes('Б: y'));
ok('dengi с суммой', /1\s000 ₽/u.test(dengi(d)));
console.log(`\n${n - fail} из ${n}`); process.exit(fail ? 1 : 0);
