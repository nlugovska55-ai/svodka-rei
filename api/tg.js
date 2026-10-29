// Два Telegram-бота пульта: «Claude» и «GPT». Один код, бот выбирается параметром webhook (?bot=gpt).
//  POST /api/tg[?bot=gpt] — webhook Telegram. Пароль сайта не нужен (middleware пропускает), вместо него
//                          секретный заголовок, который Telegram шлёт сам (выводится из токена своего бота).
//  GET  /api/tg?setup=1   — за паролем сайта: регистрирует webhook и меню команд у всех ботов, чей токен задан.
// Команды /nuzhno /dengi /gorit /id отвечают по data/svodka.json сами, без ИИ. Любой другой текст ложится
// в ветку inbox (разбор 10/13/18) и, если задан ключ, получает живой ответ своего ИИ.
// Бот GPT сводку не получает: данные клиентов в сторонний ИИ не передаём (правило Ольги), он отвечает без неё.
// Переменные Vercel:
//  TELEGRAM_BOT_TOKEN      — бот Claude, токен от @BotFather;
//  TELEGRAM_GPT_BOT_TOKEN  — бот GPT, второй токен от @BotFather;
//  TELEGRAM_ALLOWED_IDS    — chat id Ольги (через запятую); пока пусто, боты только называют id;
//  ANTHROPIC_API_KEY, ANTHROPIC_MODEL (по умолчанию claude-opus-5-5) — живые ответы бота Claude;
//  OPENAI_API_KEY, OPENAI_MODEL (по умолчанию gpt-5)                 — живые ответы бота GPT;
//  GITHUB_TOKEN            — уже есть для кнопки «Отправить Claude»: сообщения кладутся в ветку inbox.
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import Anthropic from '@anthropic-ai/sdk';
import { saveInbox } from '../lib/inbox.js';

const TG = 'https://api.telegram.org/bot';
// server-side fallbacks принимают только эти модели (Haiku — нет)
const FALLBACK_MODELS = ['claude-opus-5-5', 'claude-opus-5', 'claude-sonnet-5-5', 'claude-fable-5-1'];

const BOTS = {
  claude: { name: 'Claude', tokenVar: 'TELEGRAM_BOT_TOKEN', keyVar: 'ANTHROPIC_API_KEY', source: 'telegram', ask: askClaude },
  gpt: { name: 'GPT', tokenVar: 'TELEGRAM_GPT_BOT_TOKEN', keyVar: 'OPENAI_API_KEY', source: 'telegram-gpt', ask: askGpt },
};

const COMMANDS = [
  { command: 'nuzhno', description: 'Что нужно от меня' },
  { command: 'dengi', description: 'Деньги: ждём, пришло, выставить' },
  { command: 'gorit', description: 'Срочное на сегодня' },
  { command: 'id', description: 'Мой chat id' },
];

async function secretOf(token) {
  const hash = await crypto.subtle.digest('SHA-256', new TextEncoder().encode('svodka-rei-tg:' + token));
  return [...new Uint8Array(hash)].slice(0, 24).map((b) => b.toString(16).padStart(2, '0')).join('');
}

async function tg(token, method, body) {
  const res = await fetch(TG + token + '/' + method, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  });
  return res.json().catch(() => ({ ok: false }));
}

// Telegram режет сообщения на 4096 знаках
async function say(token, chatId, text) {
  for (let i = 0; i < text.length; i += 4000) await tg(token, 'sendMessage', { chat_id: chatId, text: text.slice(i, i + 4000) });
}

async function svodka() {
  return JSON.parse(await readFile(path.join(process.cwd(), 'data', 'svodka.json'), 'utf8'));
}

const rub = (n) => Math.round(Number(n) || 0).toLocaleString('ru-RU') + ' ₽';
const line = (t) => '• ' + (t.client ? t.client + ': ' : '') + (t.short || t.title || t.text) + (t.amount ? ' — ' + rub(t.amount) : '') + (t.due ? ' (до ' + t.due + ')' : '');

export function nuzhno(d) {
  const ask = d.summary.filter((s) => s.section === 'Нужно от вас');
  const mine = d.tasks.filter((t) => t.waiting === 'я' && !ask.some((s) => s.id === t.id));
  const out = ['Нужно от вас (' + ask.length + '):', ...ask.map(line)];
  if (mine.length) out.push('', 'Ждут вас в делах (' + mine.length + '):', ...mine.slice(0, 15).map(line));
  return out.join('\n');
}

export function dengi(d) {
  const by = {};
  for (const t of d.tasks) if (t.money) (by[t.money] ||= []).push(t);
  const order = ['выставить счёт', 'ждём подпись', 'ждём', 'проверяется', 'пришло'];
  const keys = [...order.filter((k) => by[k]), ...Object.keys(by).filter((k) => !order.includes(k))];
  return keys.map((k) => {
    const sum = by[k].reduce((s, t) => s + (Number(t.amount) || 0), 0);
    return k[0].toUpperCase() + k.slice(1) + ': ' + by[k].length + (sum ? ', ' + rub(sum) : '') + '\n' + by[k].map(line).join('\n');
  }).join('\n\n') || 'Денежных задач нет.';
}

// Как первый экран пульта: срочность «сегодня» или ручной приоритет (просроченные сроки пульт уже учёл в urgency)
export function gorit(d) {
  const hot = d.tasks.filter((t) => t.urgency === 'сегодня' || t.prio);
  return hot.length ? 'Срочное на сегодня (' + hot.length + '):\n' + hot.slice(0, 25).map(line).join('\n') : 'Срочного нет.';
}

// Компактная выжимка сводки для Claude: только поля, нужные для ответа
function context(d) {
  const keep = ['section', 'client', 'short', 'title', 'next', 'amount', 'due', 'money', 'mgr', 'owner', 'waiting', 'urgency', 'prio', 'evening', 'waitSince'];
  const tasks = d.tasks.map((t) => Object.fromEntries(keep.filter((k) => t[k]).map((k) => [k, t[k]])));
  return JSON.stringify({ updated: d.updated, summary: d.summary, tasks, lena_clients: d.lena_clients, prognoz: d.prognoz });
}

const RULES = `Отвечай по-русски, коротко: 1–6 строк, без таблиц и markdown-разметки, суммы с пробелами и «₽».
Ты ничего не делаешь сам: не отправляешь письма, не платишь, не подписываешь. Просьбы и поручения Ольги
сохраняются автоматически и попадают Claude на разбор в 10:00, 13:00 или 18:00 — так и отвечай на них.
Не проси прислать договоры, акты, сканы или персональные данные: документы клиентов через бота не передаются.`;

const SYSTEM_CLAUDE = `Ты Claude, помощник Ольги Луговской (ООО «Русэкоинновации», сертификация) в Telegram.
${RULES}
Источник фактов — только выжимка сводки ниже (поле updated — когда собрана). Если ответа в ней нет, так и скажи.`;

const SYSTEM_GPT = `Ты GPT, второй ИИ-помощник Ольги Луговской (ООО «Русэкоинновации», сертификация) в Telegram:
идеи, второе мнение, тексты, мозговой штурм.
${RULES}
Данных о клиентах, задачах и деньгах у тебя нет: на такие вопросы советуй бота Claude или команды /nuzhno, /dengi, /gorit.`;

async function askClaude(d, text) {
  const model = process.env.ANTHROPIC_MODEL || 'claude-opus-5-5';
  const client = new Anthropic();
  const params = {
    model,
    max_tokens: 2000,
    output_config: { effort: 'low' },
    system: [
      { type: 'text', text: SYSTEM_CLAUDE },
      { type: 'text', text: 'Сводка:\n' + context(d), cache_control: { type: 'ephemeral' } },
    ],
    messages: [{ role: 'user', content: text }],
  };
  const res = FALLBACK_MODELS.includes(model)
    ? await client.beta.messages.create({ ...params, betas: ['server-side-fallback-2026-07-01'], fallbacks: 'default' })
    : await client.messages.create(params);
  if (res.stop_reason === 'refusal') return 'Claude не стал отвечать на этот вопрос. Сообщение сохранено на разбор.';
  return res.content.filter((b) => b.type === 'text').map((b) => b.text).join('\n').trim() || 'Пустой ответ.';
}

// OpenAI Responses API без SDK: один запрос, текст собирается из output[].content[].output_text
async function askGpt(_d, text) {
  const res = await fetch('https://api.openai.com/v1/responses', {
    method: 'POST',
    headers: { authorization: 'Bearer ' + process.env.OPENAI_API_KEY, 'content-type': 'application/json' },
    body: JSON.stringify({ model: process.env.OPENAI_MODEL || 'gpt-5', instructions: SYSTEM_GPT, input: text, max_output_tokens: 2000 }),
  });
  const j = await res.json().catch(() => ({}));
  if (!res.ok) throw Object.assign(new Error(j.error?.message || 'OpenAI ' + res.status), { status: res.status });
  const out = (j.output || []).flatMap((o) => o.content || []).filter((c) => c.type === 'output_text').map((c) => c.text).join('\n').trim();
  return out || 'Пустой ответ.';
}

function allowed(id) {
  return (process.env.TELEGRAM_ALLOWED_IDS || '').split(/[\s,;]+/).filter(Boolean).includes(String(id));
}

async function setup(req, res) {
  const lines = [];
  for (const [key, bot] of Object.entries(BOTS)) {
    const token = process.env[bot.tokenVar];
    if (!token) { lines.push('Бот ' + bot.name + ': не задан ' + bot.tokenVar + ', пропущен.'); continue; }
    const url = 'https://' + req.headers.host + '/api/tg' + (key === 'claude' ? '' : '?bot=' + key);
    const hook = await tg(token, 'setWebhook', {
      url,
      secret_token: await secretOf(token),
      allowed_updates: ['message', 'callback_query'],
      drop_pending_updates: true,
    });
    await tg(token, 'setMyCommands', { commands: COMMANDS });
    const me = await tg(token, 'getMe', {});
    lines.push(hook.ok
      ? 'Бот ' + bot.name + ' @' + (me.result?.username || '?') + ': подключён' + (process.env[bot.keyVar] ? ', живые ответы включены.' : ', без ' + bot.keyVar + ' сообщения только уходят на разбор.')
      : 'Бот ' + bot.name + ': Telegram не принял webhook (' + (hook.description || 'нет ответа') + '). Проверьте ' + bot.tokenVar + '.');
  }
  res.setHeader('content-type', 'text/plain; charset=utf-8');
  return res.status(200).send(lines.join('\n') + '\nТеперь напишите каждому боту /start.');
}

async function onMessage(bot, token, m) {
  const chatId = m.chat.id;
  const text = String(m.text || m.caption || '').trim();
  if (!allowed(m.from?.id)) {
    return say(token, chatId, 'Бот закрыт. Ваш id: ' + m.from?.id + '\nЕсли это вы, Ольга, впишите его в Vercel в TELEGRAM_ALLOWED_IDS и сделайте Redeploy.');
  }
  if (m.document || m.photo) {
    return say(token, chatId, 'Файлы через бота не принимаю: документы клиентов держим в почте и Seafile. Напишите словами, что сделать.');
  }
  const live = Boolean(process.env[bot.keyVar]);
  const cmd = text.split(/[\s@]/)[0].toLowerCase();
  if (cmd === '/start' || cmd === '/help') {
    return say(token, chatId, 'Здравствуйте! Я ' + bot.name + ' по пульту дня.\n/nuzhno — что нужно от вас\n/dengi — деньги\n/gorit — срочное\n' +
      'Любой другой текст — вопрос или поручение: ' + (live ? 'отвечу сразу и ' : '') + 'передам Claude на разбор в 10:00, 13:00, 18:00.' +
      (bot === BOTS.gpt ? '\nДанных о клиентах у меня нет: их не передаём стороннему ИИ.' : ''));
  }
  if (cmd === '/id') return say(token, chatId, 'Ваш chat id: ' + m.from.id);
  const d = await svodka();
  if (cmd === '/nuzhno') return say(token, chatId, nuzhno(d));
  if (cmd === '/dengi') return say(token, chatId, dengi(d));
  if (cmd === '/gorit') return say(token, chatId, gorit(d));
  if (!text) return;

  let saved = true;
  try {
    await saveInbox([{ client: '', text, at: new Date(m.date * 1000).toISOString() }], bot.source);
  } catch {
    saved = false;
  }
  let answer = '';
  if (live) {
    try {
      answer = await bot.ask(d, text);
    } catch (e) {
      answer = bot.name + ' сейчас не ответил (' + (e.status || e.message) + ').';
    }
  }
  const note = saved ? 'Передано на разбор.' : 'Не удалось сохранить на разбор (нет GITHUB_TOKEN?). Повторите позже.';
  return say(token, chatId, answer ? answer + '\n\n' + note : note + ' Ответ придёт после ближайшего разбора (10:00, 13:00, 18:00).');
}

// Нажатие кнопки под сообщением Claude (из outbox): callback_data = "<ref>|<вариант>"
async function onButton(bot, token, q) {
  if (!allowed(q.from?.id)) return tg(token, 'answerCallbackQuery', { callback_query_id: q.id, text: 'Бот закрыт' });
  const [ref, choice] = String(q.data || '').split('|');
  let ok = true;
  try {
    await saveInbox([{ client: '', taskId: ref || null, text: 'Ответ кнопкой: ' + choice, at: new Date().toISOString() }], bot.source);
  } catch {
    ok = false;
  }
  await tg(token, 'answerCallbackQuery', { callback_query_id: q.id, text: ok ? 'Принято: ' + choice : 'Не сохранилось, нажмите ещё раз' });
  if (ok && q.message) {
    await tg(token, 'editMessageReplyMarkup', { chat_id: q.message.chat.id, message_id: q.message.message_id, reply_markup: { inline_keyboard: [] } });
    await say(token, q.message.chat.id, '✓ ' + choice);
  }
}

export default async function handler(req, res) {
  const q = req.query || {};
  if (req.method === 'GET') return q.setup ? setup(req, res) : res.status(200).send('ok');
  if (req.method !== 'POST') return res.status(405).end();
  const bot = BOTS[q.bot || 'claude'];
  const token = bot && process.env[bot.tokenVar];
  if (!token || req.headers['x-telegram-bot-api-secret-token'] !== (await secretOf(token))) return res.status(401).end();
  const u = typeof req.body === 'string' ? JSON.parse(req.body || '{}') : req.body || {};
  try {
    if (u.message) await onMessage(bot, token, u.message);
    else if (u.callback_query) await onButton(bot, token, u.callback_query);
  } catch (e) {
    console.error('tg', e);
  }
  // Telegram повторяет запрос, если не получил 200, — отвечаем 200 всегда
  return res.status(200).json({ ok: true });
}
