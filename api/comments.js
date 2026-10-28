// Принимает комментарии Ольги со страницы и кладёт их файлом в ветку inbox репозитория.
// Оттуда их забирает Claude на разборе (10:00, 13:00, 18:00) и переносит в базу комментариев страницы задач.
// Вход проверяет middleware.js (тот же пароль, что у страницы).
// Сохранение в ветку inbox — lib/inbox.js (нужна переменная Vercel GITHUB_TOKEN).
import { saveInbox } from '../lib/inbox.js';

export default async function handler(req, res) {
  if (req.method !== 'POST') return res.status(405).json({ ok: false, error: 'Только POST' });
  const token = process.env.GITHUB_TOKEN;
  if (!token) return res.status(503).json({ ok: false, error: 'Не задан GITHUB_TOKEN в настройках Vercel' });

  const body = typeof req.body === 'string' ? JSON.parse(req.body || '{}') : req.body || {};
  const items = (Array.isArray(body.items) ? body.items : [])
    .map((x) => ({
      client: String(x.client || '').slice(0, 200),
      taskId: x.taskId ? String(x.taskId).slice(0, 100) : null,
      taskTitle: x.taskTitle ? String(x.taskTitle).slice(0, 300) : null,
      text: String(x.text || '').trim().slice(0, 4000),
      action: ['eve', 'prio', 'wait', 'done'].includes(x.action) ? x.action : '',
      actionLabel: String(x.actionLabel || '').slice(0, 40),
      at: String(x.at || '').slice(0, 40),
    }))
    .filter((x) => x.text || x.action);
  if (!items.length) return res.status(400).json({ ok: false, error: 'Пустые комментарии' });
  if (items.length > 100) return res.status(400).json({ ok: false, error: 'Больше 100 комментариев за раз' });

  try {
    const file = await saveInbox(items, 'svodka-rei');
    return res.status(200).json({ ok: true, count: items.length, sentAt: file.sentAt });
  } catch (e) {
    return res.status(502).json({ ok: false, error: 'Не удалось сохранить: ' + e.message });
  }
}
