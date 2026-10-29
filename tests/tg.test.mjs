// Проверка двух ботов api/tg.js без сети: Telegram, GitHub, Anthropic и OpenAI подменены памятью теста.
// Запуск: node tests/tg.test.mjs
import { createHash } from 'node:crypto';

let fail = 0, n = 0;
const ok = (name, cond, fact) => { n++; if (!cond) fail++; console.log((cond ? 'PASS ' : 'FAIL ') + name + ' — ' + fact); };

Object.assign(process.env, {
  TELEGRAM_BOT_TOKEN: '111:claude', TELEGRAM_GPT_BOT_TOKEN: '222:gpt', TELEGRAM_ALLOWED_IDS: '42', GITHUB_TOKEN: 'g',
});
delete process.env.ANTHROPIC_API_KEY; delete process.env.OPENAI_API_KEY;

const calls = [];
let anthropicBody = null, openaiBody = null, githubDown = false;
globalThis.fetch = async (url, init = {}) => {
  const u = String(url), body = init.body ? JSON.parse(String(init.body)) : null;
  const J = (s, b) => new Response(JSON.stringify(b), { status: s, headers: { 'content-type': 'application/json', 'request-id': 'r' } });
  if (u.includes('api.anthropic.com')) {
    anthropicBody = body;
    return J(200, { id: 'm', type: 'message', role: 'assistant', model: body.model, content: [{ type: 'text', text: 'Ответ Claude' }], stop_reason: 'end_turn', usage: { input_tokens: 1, output_tokens: 1 } });
  }
  if (u.includes('api.openai.com')) {
    openaiBody = body;
    return J(200, { output: [{ type: 'reasoning', content: [] }, { type: 'message', content: [{ type: 'output_text', text: 'Ответ GPT' }] }] });
  }
  if (u.includes('api.github.com')) {
    if (githubDown) return J(500, {});
    if (u.includes('/git/ref/heads/inbox')) return J(200, { object: { sha: 'c1' } });
    calls.push({ to: 'github', path: u.split('/contents/')[1], body: JSON.parse(Buffer.from(body.content, 'base64').toString()) });
    return J(201, {});
  }
  const m = /bot([^/]+)\/(\w+)$/.exec(u);
  calls.push({ to: 'tg', token: m[1], method: m[2], body });
  return J(200, { ok: true, result: { username: m[1].startsWith('111') ? 'claude_bot' : 'gpt_bot' } });
};

const { default: handler } = await import('../api/tg.js');
const secret = (t) => createHash('sha256').update('svodka-rei-tg:' + t).digest('hex').slice(0, 48);
const post = async (bot, update, hdr) => {
  calls.length = 0;
  const res = { code: 0, status(c) { this.code = c; return this; }, json() { return this; }, send(x) { this.out = x; return this; }, end() { return this; }, setHeader() {} };
  const token = bot === 'gpt' ? '222:gpt' : '111:claude';
  await handler({ method: 'POST', headers: { 'x-telegram-bot-api-secret-token': hdr ?? secret(token) }, body: update, query: bot === 'gpt' ? { bot: 'gpt' } : {} }, res);
  return res.code;
};
const msg = (text, from = 42) => ({ message: { chat: { id: from }, from: { id: from }, date: 1761732000, text } });
const sent = () => calls.filter((c) => c.to === 'tg' && c.method === 'sendMessage');
const saved = () => calls.filter((c) => c.to === 'github');

ok('Неверный секрет — 401', await post('claude', msg('hi'), 'x') === 401, 'ответ 401');
ok('Секрет бота Claude не открывает бота GPT', await post('gpt', msg('hi'), secret('111:claude')) === 401, 'секрет у каждого бота свой');

await post('claude', msg('hi', 7));
ok('Чужой chat id: бот называет id и больше ничего', sent().length === 1 && sent()[0].body.text.includes('Ваш id: 7') && !saved().length, sent()[0]?.body.text.split('\n')[0]);

await post('claude', msg('/nuzhno'));
ok('/nuzhno отвечает по сводке без ИИ', sent()[0]?.body.text.startsWith('Нужно от вас') && !anthropicBody, sent()[0]?.body.text.split('\n')[0]);
await post('gpt', msg('/dengi'));
ok('/dengi работает и у бота GPT, отвечает его токен', sent()[0]?.token === '222:gpt' && /Ждём|Выставить|Денежных/.test(sent()[0].body.text), sent()[0]?.body.text.split('\n')[0]);
await post('claude', msg('/gorit'));
ok('/gorit отдаёт срочное', /^Срочное на сегодня|^Срочного нет/.test(sent()[0]?.body.text), sent()[0]?.body.text.split('\n')[0]);

await post('claude', msg('Позвони УКС завтра'));
ok('Без ANTHROPIC_API_KEY: сохранено на разбор, честно сказано про разбор', saved()[0]?.body.source === 'telegram' && sent()[0].body.text.includes('после ближайшего разбора') && !anthropicBody,
  saved()[0]?.path + ' / ' + sent()[0]?.body.text);

process.env.ANTHROPIC_API_KEY = 'k';
await post('claude', msg('Сколько ждём денег?'));
ok('С ключом: живой ответ Claude по сводке + запись в inbox', sent()[0]?.body.text.startsWith('Ответ Claude') && saved().length === 1 && anthropicBody.system[1].text.startsWith('Сводка:'),
  sent()[0]?.body.text.replace(/\n+/g, ' / '));
ok('Claude: модель по умолчанию claude-opus-5-5, effort low, fallbacks default', anthropicBody.model === 'claude-opus-5-5' && anthropicBody.output_config.effort === 'low' && anthropicBody.fallbacks === 'default',
  JSON.stringify({ model: anthropicBody.model, fallbacks: anthropicBody.fallbacks }));

await post('gpt', msg('Идея для поста'));
ok('Бот GPT без OPENAI_API_KEY: только на разбор, источник telegram-gpt', saved()[0]?.body.source === 'telegram-gpt' && !openaiBody && sent()[0].token === '222:gpt', saved()[0]?.path);
process.env.OPENAI_API_KEY = 'o';
await post('gpt', msg('Идея для поста'));
ok('Бот GPT с ключом: ответ GPT из output_text', sent()[0]?.body.text.startsWith('Ответ GPT') && openaiBody.model === 'gpt-5', sent()[0]?.body.text.replace(/\n+/g, ' / '));
const leaked = JSON.stringify(openaiBody);
ok('В GPT не уходит сводка: ни задач, ни клиентов', !leaked.includes('Сводка:') && !leaked.includes('"tasks"') && openaiBody.input === 'Идея для поста', 'длина запроса ' + leaked.length + ' знаков');

await post('claude', { message: { chat: { id: 42 }, from: { id: 42 }, date: 1, document: { file_id: 'f' }, caption: 'договор' } });
ok('Файлы не принимаются и не сохраняются', !saved().length && sent()[0].body.text.startsWith('Файлы через бота не принимаю'), sent()[0]?.body.text.slice(0, 40));

githubDown = true;
await post('claude', msg('Поручение при сбое GitHub'));
ok('Сбой GitHub: бот честно говорит, что не сохранил', sent()[0]?.body.text.includes('Не удалось сохранить'), sent()[0]?.body.text.split('\n').pop());
githubDown = false;

await post('claude', { callback_query: { id: 'q', from: { id: 42 }, data: 'gea1|Да', message: { chat: { id: 42 }, message_id: 5 } } });
ok('Кнопка «Да» из outbox: ответ в inbox с taskId, кнопки сняты', saved()[0]?.body.items[0].taskId === 'gea1' && saved()[0].body.items[0].text === 'Ответ кнопкой: Да'
  && calls.some((c) => c.method === 'editMessageReplyMarkup'), saved()[0]?.body.items[0].text);

// setup регистрирует оба бота, у GPT свой адрес и свой секрет
calls.length = 0;
const res = { status() { return this; }, send(x) { this.out = x; return this; }, setHeader() {} };
await handler({ method: 'GET', headers: { host: 'svodka-rei.vercel.app' }, query: { setup: '1' } }, res);
const hooks = calls.filter((c) => c.method === 'setWebhook');
ok('setup: два webhook, у GPT ?bot=gpt и свой секрет', hooks.length === 2 && hooks[1].body.url.endsWith('/api/tg?bot=gpt') && hooks[1].body.secret_token === secret('222:gpt') && hooks[0].body.url.endsWith('/api/tg'),
  hooks.map((h) => h.body.url).join(', '));
delete process.env.TELEGRAM_GPT_BOT_TOKEN;
calls.length = 0;
await handler({ method: 'GET', headers: { host: 'x' }, query: { setup: '1' } }, res);
ok('setup без токена GPT: бот GPT пропущен, Claude подключён', calls.filter((c) => c.method === 'setWebhook').length === 1 && res.out.includes('TELEGRAM_GPT_BOT_TOKEN, пропущен'), res.out.split('\n')[1]);

console.log(`\nИтог ботов: ${n - fail} из ${n}`);
process.exit(fail ? 1 : 0);
