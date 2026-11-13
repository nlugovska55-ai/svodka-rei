// Telegram-бот пульта: уведомления Ольге и её короткие ответы.
//  POST /api/tg          — webhook Telegram. Пароль сайта не нужен (middleware пропускает только этот адрес),
//                          вместо него секретный заголовок, который Telegram шлёт сам (выводится из токена бота).
//  GET  /api/tg?setup=1  — за паролем сайта: подключает webhook и меню команд.
// Команды /nuzhno /dengi /gorit отвечают по data/svodka.json без ИИ. Любой другой текст и нажатия кнопок
// ложатся в ветку inbox и попадают на разбор в 10:00, 13:00, 18:00. Файлы бот не принимает.
// Сообщения Ольге (сводки, «Нужно ваше нажатие», перечень) шлёт .github/workflows/tg-send.yml, см. scripts/tg_send.mjs.
// Переменные Vercel: TELEGRAM_BOT_TOKEN (от @BotFather), TELEGRAM_ALLOWED_IDS (chat id Ольги; пока пусто — бот только называет id),
// GITHUB_TOKEN (уже есть).
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { saveInbox } from '../lib/inbox.js';

const TG = 'https://api.telegram.org/bot';
const COMMANDS = [
  { command: 'gorit', description: 'Срочное на сегодня' },
  { command: 'nuzhno', description: 'Что ждёт меня' },
  { command: 'dengi', description: 'Деньги: ждём, выставить, проверяется' },
  { command: 'id', description: 'Мой chat id' },
];

export async function secretOf(token) {
  const hash = await crypto.subtle.digest('SHA-256', new TextEncoder().encode('svodka-rei-tg:' + token));
  return [...new Uint8Array(hash)].slice(0, 24).map((b) => b.toString(16).padStart(2, '0')).join('');
}

async function tg(token, method, body) {
  const res = await fetch(TG + token + '/' + method, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
  return res.json().catch(() => ({ ok: false }));
}

async function say(token, chatId, text) {
  for (let i = 0; i < text.length; i += 4000) await tg(token, 'sendMessage', { chat_id: chatId, text: text.slice(i, i + 4000) });
}

async function svodka() {
  return JSON.parse(await readFile(path.join(process.cwd(), 'data', 'svodka.json'), 'utf8'));
}

const rub = (n) => Math.round(Number(n) || 0).toLocaleString('ru-RU') + ' ₽';
const line = (t) => '• ' + (t.client ? t.client + ': ' : '') + (t.short || t.title || t.text) + (t.amount ? ' — ' + rub(t.amount) : '') + (t.due ? ' (до ' + t.due + ')' : '');
const tasks = (d) => (d.tasks || []).filter((t) => !t.done && t.status !== 'done');
const stamp = (d) => (d.updated ? '\n\nПульт от ' + d.updated : '');

export function gorit(d) {
  const hot = tasks(d).filter((t) => t.urgency === 'сегодня' || t.prio);
  return (hot.length ? 'Срочное на сегодня (' + hot.length + '):\n' + hot.slice(0, 25).map(line).join('\n') : 'Срочного нет.') + stamp(d);
}

export function nuzhno(d) {
  const mine = tasks(d).filter((t) => ['я', 'ольга'].includes(String(t.waiting || '').toLowerCase()));
  return (mine.length ? 'Ждёт вас (' + mine.length + '):\n' + mine.slice(0, 25).map(line).join('\n') : 'На пульте сейчас ничего не ждёт вас.') + stamp(d);
}

export function dengi(d) {
  const by = {};
  for (const t of tasks(d)) if (t.money) (by[t.money] ||= []).push(t);
  const out = Object.entries(by).map(([k, list]) => {
    const sum = list.reduce((s, t) => s + (Number(t.amount) || 0), 0);
    return k[0].toUpperCase() + k.slice(1) + ': ' + list.length + (sum ? ', ' + rub(sum) : '') + '\n' + list.map(line).join('\n');
  });
  return (out.join('\n\n') || 'Денежных дел на пульте нет.') + stamp(d);
}

export function allowed(id) {
  return (process.env.TELEGRAM_ALLOWED_IDS || '').split(/[\s,;]+/).filter(Boolean).includes(String(id));
}

async function setup(req, res, token) {
  res.setHeader('content-type', 'text/plain; charset=utf-8');
  if (!token) return res.status(200).send('Не задан TELEGRAM_BOT_TOKEN в настройках Vercel.');
  const hook = await tg(token, 'setWebhook', {
    url: 'https://' + req.headers.host + '/api/tg',
    secret_token: await secretOf(token),
    allowed_updates: ['message', 'callback_query'],
    drop_pending_updates: true,
  });
  await tg(token, 'setMyCommands', { commands: COMMANDS });
  const me = await tg(token, 'getMe', {});
  return res.status(200).send(hook.ok
    ? 'Бот @' + (me.result?.username || '?') + ' подключён. Напишите ему /start.'
    : 'Telegram не принял подключение (' + (hook.description || 'нет ответа') + '). Проверьте TELEGRAM_BOT_TOKEN.');
}

async function onMessage(token, m) {
  const chatId = m.chat.id;
  const text = String(m.text || m.caption || '').trim();
  if (!allowed(m.from?.id)) {
    return say(token, chatId, 'Бот закрыт. Ваш id: ' + m.from?.id + '\nОльга, пришлите это число в чат Claude — он скажет, куда его вписать.');
  }
  if (m.document || m.photo || m.voice || m.video) {
    return say(token, chatId, 'Файлы через бота не принимаю: документы клиентов держим в почте и Seafile. Напишите словами.');
  }
  const cmd = text.split(/[\s@]/)[0].toLowerCase();
  if (cmd === '/start' || cmd === '/help') {
    return say(token, chatId, 'Здравствуйте! Я бот пульта.\nСюда приходят сводки и вопросы «Нужно ваше нажатие».\n/gorit — срочное\n/nuzhno — что ждёт вас\n/dengi — деньги\nЛюбой другой текст — поручение Claude, разбор в 10:00, 13:00, 18:00.');
  }
  if (cmd === '/id') return say(token, chatId, 'Ваш chat id: ' + m.from.id);
  if (cmd === '/gorit') return say(token, chatId, gorit(await svodka()));
  if (cmd === '/nuzhno') return say(token, chatId, nuzhno(await svodka()));
  if (cmd === '/dengi') return say(token, chatId, dengi(await svodka()));
  if (!text) return;
  try {
    await saveInbox([{ client: '', taskId: null, text: text.slice(0, 4000), action: '', at: new Date(m.date * 1000).toISOString() }]);
    return say(token, chatId, 'Принято, передано Claude на разбор (10:00, 13:00, 18:00).');
  } catch {
    return say(token, chatId, 'Не сохранилось. Повторите через минуту или напишите в чат Claude.');
  }
}

// Кнопка под сообщением (из tg-send): callback_data = "<ref>|<вариант>"
async function onButton(token, q) {
  if (!allowed(q.from?.id)) return tg(token, 'answerCallbackQuery', { callback_query_id: q.id, text: 'Бот закрыт' });
  const [ref, choice] = String(q.data || '').split('|');
  let ok = true;
  try {
    await saveInbox([{ client: '', taskId: ref || null, text: 'Ответ кнопкой в Telegram: ' + choice, action: '', at: new Date().toISOString() }]);
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
  const token = process.env.TELEGRAM_BOT_TOKEN;
  const q = req.query || {};
  if (req.method === 'GET') return q.setup ? setup(req, res, token) : res.status(200).send('ok');
  if (req.method !== 'POST') return res.status(405).end();
  if (!token || req.headers['x-telegram-bot-api-secret-token'] !== (await secretOf(token))) return res.status(401).end();
  const u = typeof req.body === 'string' ? JSON.parse(req.body || '{}') : req.body || {};
  try {
    if (u.message) await onMessage(token, u.message);
    else if (u.callback_query) await onButton(token, u.callback_query);
  } catch (e) {
    console.error('tg', e);
  }
  return res.status(200).json({ ok: true }); // иначе Telegram повторяет запрос
}
