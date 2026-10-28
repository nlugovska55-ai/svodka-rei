// Telegram-бот сводки: Ольга пишет боту, бот отвечает по данным сводки и передаёт сообщения Claude.
//  POST /api/tg  — webhook Telegram. Пароль сайта не нужен (middleware пропускает), вместо него
//                 секретный заголовок, который Telegram шлёт сам (выводится из токена бота).
//  GET  /api/tg?setup=1 — за паролем сайта: регистрирует webhook и меню команд у бота.
// Переменные Vercel:
//  TELEGRAM_BOT_TOKEN   — токен от @BotFather (обязательно);
//  TELEGRAM_ALLOWED_IDS — chat id Ольги (через запятую, если несколько); пока пусто, бот только называет id;
//  ANTHROPIC_API_KEY    — по желанию: живые ответы Claude. Без него сообщения уходят на разбор 10/13/18;
//  ANTHROPIC_MODEL      — по желанию, по умолчанию claude-opus-5-5;
//  GITHUB_TOKEN         — уже есть для кнопки «Отправить Claude»: сообщения кладутся в ветку inbox.
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import Anthropic from '@anthropic-ai/sdk';
import { saveInbox } from '../lib/inbox.js';

const TG = 'https://api.telegram.org/bot';
const MODEL = process.env.ANTHROPIC_MODEL || 'claude-opus-5-5';
// server-side fallbacks принимают только эти модели (Haiku — нет)
const FALLBACK_MODELS = ['claude-opus-5-5', 'claude-opus-5', 'claude-sonnet-5-5', 'claude-fable-5-1'];

const COMMANDS = [
  { command: 'nuzhno', description: 'Что нужно от меня' },
  { command: 'dengi', description: 'Деньги: ждём, пришло, выставить' },
  { command: 'gorit', description: 'Срочное и просроченное' },
  { command: 'id', description: 'Мой chat id' },
];

async function secretOf(token) {
  const hash = await crypto.subtle.digest('SHA-256', new TextEncoder().encode('svodka-rei-tg:' + token));
  return [...new Uint8Array(hash)].slice(0, 24).map((b) => b.toString(16).padStart(2, '0')).join('');
}

async function tg(method, body) {
  const res = await fetch(TG + process.env.TELEGRAM_BOT_TOKEN + '/' + method, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  });
  return res.json().catch(() => ({ ok: false }));
}

// Telegram режет сообщения на 4096 знаках
async function say(chatId, text) {
  for (let i = 0; i < text.length; i += 4000) await tg('sendMessage', { chat_id: chatId, text: text.slice(i, i + 4000) });
}

async function svodka() {
  return JSON.parse(await readFile(path.join(process.cwd(), 'data', 'svodka.json'), 'utf8'));
}

const rub = (n) => Math.round(Number(n) || 0).toLocaleString('ru-RU') + ' ₽';
const ddmm = (s) => { const m = /^(\d\d)\.(\d\d)\.(\d{4})$/.exec(s || ''); return m ? new Date(+m[3], m[2] - 1, +m[1]) : null; };
const line = (t) => '• ' + (t.client ? t.client + ': ' : '') + (t.title || t.text) + (t.amount ? ' — ' + rub(t.amount) : '') + (t.due ? ' (до ' + t.due + ')' : '');

function nuzhno(d) {
  const ask = d.summary.filter((s) => s.section === 'Нужно от вас');
  const mine = d.tasks.filter((t) => t.owner === 'Ольга' && !ask.some((s) => s.id === t.id));
  const out = ['Нужно от вас (' + ask.length + '):', ...ask.map(line)];
  if (mine.length) out.push('', 'Ваши задачи (' + mine.length + '):', ...mine.slice(0, 15).map(line));
  return out.join('\n');
}

function dengi(d) {
  const by = {};
  for (const t of d.tasks) if (t.money) (by[t.money] ||= []).push(t);
  const order = ['выставить счёт', 'ждём подпись', 'ждём', 'проверяется', 'пришло'];
  const keys = [...order.filter((k) => by[k]), ...Object.keys(by).filter((k) => !order.includes(k))];
  return keys.map((k) => {
    const sum = by[k].reduce((s, t) => s + (Number(t.amount) || 0), 0);
    return k[0].toUpperCase() + k.slice(1) + ': ' + by[k].length + (sum ? ', ' + rub(sum) : '') + '\n' + by[k].map(line).join('\n');
  }).join('\n\n') || 'Денежных задач нет.';
}

function gorit(d) {
  const today = new Date(); today.setHours(23, 59, 59);
  const hot = d.tasks.filter((t) => t.prio || (ddmm(t.due) && ddmm(t.due) <= today));
  return hot.length ? 'Срочное и просроченное (' + hot.length + '):\n' + hot.slice(0, 25).map(line).join('\n') : 'Срочного нет.';
}

// Компактная выжимка сводки для Claude: только поля, нужные для ответа
function context(d) {
  const keep = ['section', 'client', 'title', 'next', 'amount', 'due', 'money', 'mgr', 'owner', 'prio', 'evening', 'wait', 'waitSince'];
  const tasks = d.tasks.map((t) => Object.fromEntries(keep.filter((k) => t[k]).map((k) => [k, t[k]])));
  return JSON.stringify({ updated: d.updated, summary: d.summary, tasks, lena_clients: d.lena_clients, prognoz: d.prognoz });
}

const SYSTEM = `Ты Claude, помощник Ольги Луговской (ООО «Русэкоинновации», сертификация) в Telegram.
Отвечай по-русски, коротко: 1–6 строк, без таблиц и markdown-разметки, суммы с пробелами и «₽».
Источник фактов — только выжимка сводки ниже (поле updated — когда собрана). Если ответа в ней нет, так и скажи.
Ты ничего не делаешь сам: не отправляешь письма, не платишь, не подписываешь. Просьбы и поручения Ольги
сохраняются автоматически и попадают ветке Claude на разбор в 10:00, 13:00 или 18:00 — так и отвечай на них.
Не проси прислать договоры, акты, сканы или персональные данные: документы клиентов через бота не передаются.`;

async function askClaude(d, text) {
  const client = new Anthropic();
  const params = {
    model: MODEL,
    max_tokens: 2000,
    output_config: { effort: 'low' },
    system: [
      { type: 'text', text: SYSTEM },
      { type: 'text', text: 'Сводка:\n' + context(d), cache_control: { type: 'ephemeral' } },
    ],
    messages: [{ role: 'user', content: text }],
  };
  const res = FALLBACK_MODELS.includes(MODEL)
    ? await client.beta.messages.create({ ...params, betas: ['server-side-fallback-2026-07-01'], fallbacks: 'default' })
    : await client.messages.create(params);
  if (res.stop_reason === 'refusal') return 'Claude не стал отвечать на этот вопрос. Сообщение сохранено на разбор.';
  return res.content.filter((b) => b.type === 'text').map((b) => b.text).join('\n').trim() || 'Пустой ответ.';
}

function allowed(id) {
  return (process.env.TELEGRAM_ALLOWED_IDS || '').split(/[\s,;]+/).filter(Boolean).includes(String(id));
}

async function setup(req, res) {
  const token = process.env.TELEGRAM_BOT_TOKEN;
  if (!token) return res.status(503).send('Не задан TELEGRAM_BOT_TOKEN в настройках Vercel.');
  const url = 'https://' + req.headers.host + '/api/tg';
  const hook = await tg('setWebhook', {
    url,
    secret_token: await secretOf(token),
    allowed_updates: ['message', 'callback_query'],
    drop_pending_updates: true,
  });
  const cmds = await tg('setMyCommands', { commands: COMMANDS });
  const me = await tg('getMe', {});
  res.setHeader('content-type', 'text/plain; charset=utf-8');
  return res.status(hook.ok ? 200 : 502).send(hook.ok
    ? 'Готово: бот @' + (me.result?.username || '?') + ' подключён к ' + url + (cmds.ok ? ', меню команд записано.' : '.') +
      '\nТеперь напишите боту /start.'
    : 'Telegram не принял webhook: ' + (hook.description || 'нет ответа') + '. Проверьте TELEGRAM_BOT_TOKEN.');
}

async function onMessage(m) {
  const chatId = m.chat.id;
  const text = String(m.text || m.caption || '').trim();
  if (!allowed(m.from?.id)) {
    return say(chatId, 'Бот закрыт. Ваш id: ' + m.from?.id + '\nЕсли это вы, Ольга, впишите его в Vercel в TELEGRAM_ALLOWED_IDS и сделайте Redeploy.');
  }
  if (m.document || m.photo) {
    return say(chatId, 'Файлы через бота не принимаю: документы клиентов держим в почте и Seafile. Напишите словами, что сделать.');
  }
  const cmd = text.split(/[\s@]/)[0].toLowerCase();
  if (cmd === '/start' || cmd === '/help') {
    return say(chatId, 'Здравствуйте! Я Claude по сводке дня.\n/nuzhno — что нужно от вас\n/dengi — деньги\n/gorit — срочное\n' +
      'Любой другой текст — вопрос или поручение: ' + (process.env.ANTHROPIC_API_KEY ? 'отвечу сразу и ' : '') + 'передам на разбор в 10:00, 13:00, 18:00.');
  }
  if (cmd === '/id') return say(chatId, 'Ваш chat id: ' + m.from.id);
  const d = await svodka();
  if (cmd === '/nuzhno') return say(chatId, nuzhno(d));
  if (cmd === '/dengi') return say(chatId, dengi(d));
  if (cmd === '/gorit') return say(chatId, gorit(d));
  if (!text) return;

  let saved = true;
  try {
    await saveInbox([{ client: '', text, at: new Date(m.date * 1000).toISOString() }], 'telegram');
  } catch {
    saved = false;
  }
  let answer = '';
  if (process.env.ANTHROPIC_API_KEY) {
    try {
      answer = await askClaude(d, text);
    } catch (e) {
      answer = 'Claude сейчас не ответил (' + (e.status || e.message) + ').';
    }
  }
  const note = saved ? 'Передано на разбор.' : 'Не удалось сохранить на разбор (нет GITHUB_TOKEN?). Повторите позже.';
  return say(chatId, answer ? answer + '\n\n' + note : note + ' Ответ придёт после ближайшего разбора (10:00, 13:00, 18:00).');
}

// Нажатие кнопки под сообщением Claude (из outbox): callback_data = "<ref>|<вариант>"
async function onButton(q) {
  if (!allowed(q.from?.id)) return tg('answerCallbackQuery', { callback_query_id: q.id, text: 'Бот закрыт' });
  const [ref, choice] = String(q.data || '').split('|');
  let ok = true;
  try {
    await saveInbox([{ client: '', taskId: ref || null, text: 'Ответ кнопкой: ' + choice, at: new Date().toISOString() }], 'telegram');
  } catch {
    ok = false;
  }
  await tg('answerCallbackQuery', { callback_query_id: q.id, text: ok ? 'Принято: ' + choice : 'Не сохранилось, нажмите ещё раз' });
  if (ok && q.message) {
    await tg('editMessageReplyMarkup', { chat_id: q.message.chat.id, message_id: q.message.message_id, reply_markup: { inline_keyboard: [] } });
    await say(q.message.chat.id, '✓ ' + choice);
  }
}

export default async function handler(req, res) {
  if (req.method === 'GET') return req.query.setup ? setup(req, res) : res.status(200).send('ok');
  if (req.method !== 'POST') return res.status(405).end();
  const token = process.env.TELEGRAM_BOT_TOKEN;
  if (!token || req.headers['x-telegram-bot-api-secret-token'] !== (await secretOf(token))) return res.status(401).end();
  const u = typeof req.body === 'string' ? JSON.parse(req.body || '{}') : req.body || {};
  try {
    if (u.message) await onMessage(u.message);
    else if (u.callback_query) await onButton(u.callback_query);
  } catch (e) {
    console.error('tg', e);
  }
  // Telegram повторяет запрос, если не получил 200, — отвечаем 200 всегда
  return res.status(200).json({ ok: true });
}
