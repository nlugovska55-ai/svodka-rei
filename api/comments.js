// Принимает комментарии Ольги со страницы и кладёт их файлом в ветку inbox репозитория.
// Оттуда их забирает Claude на разборе (10:00, 13:00, 18:00) и переносит в базу комментариев страницы задач.
// Вход проверяет middleware.js (тот же пароль, что у страницы).
// Нужна переменная Vercel GITHUB_TOKEN: fine-grained токен только на svodka-rei, Contents: Read and write.
import { gh, ensureBranch, BRANCH } from '../lib/inbox.js';

// Имя файла партии: время + случайный хвост. Клиент присылает его как batchId и повторяет при сбое — файл один.
const BATCH = /^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-\d{3}Z(?:-[a-z0-9]{6,16})?$/;
const timeOf = (id) => Date.parse(id.slice(0, 24).replace(/^(\d{4}-\d{2}-\d{2}T\d{2})-(\d{2})-(\d{2})-(\d{3})Z$/, '$1:$2:$3.$4Z'));
const decode = (b64) => JSON.parse(Buffer.from(b64, 'base64').toString('utf8'));

// Полный список inbox через git trees (у Contents API предел 1000 файлов); обрезанный список — ошибка, не тишина.
async function inboxFiles(token) {
  const ref = await gh('/git/ref/heads/' + BRANCH, token);
  if (ref.status === 404) return [];
  if (!ref.ok) throw new Error('ref ' + ref.status);
  const commit = await gh('/git/commits/' + (await ref.json()).object.sha, token);
  if (!commit.ok) throw new Error('commit ' + commit.status);
  const root = await gh('/git/trees/' + (await commit.json()).tree.sha, token);
  if (!root.ok) throw new Error('tree ' + root.status);
  const rj = await root.json();
  if (rj.truncated) throw new Error('список ветки неполный');
  const dir = (rj.tree || []).find((e) => e.path === 'inbox' && e.type === 'tree');
  if (!dir) return [];
  const t = await gh('/git/trees/' + dir.sha, token);
  if (!t.ok) throw new Error('inbox tree ' + t.status);
  const tj = await t.json();
  if (tj.truncated) throw new Error('список inbox неполный');
  return (tj.tree || []).filter((e) => e.type === 'blob' && e.path.endsWith('.json')).map((e) => ({ name: e.path, sha: e.sha }));
}

// GET отдаёт отметки за 48 часов страницами по PAGE файлов; nextCursor есть, пока файлы не кончились.
const PAGE = 40;
async function recent(token, cursor) {
  const since = Date.now() - 48 * 3600e3;
  const files = (await inboxFiles(token)).filter((f) => { const t = timeOf(f.name.slice(0, -5)); return !isNaN(t) && t >= since; })
    .sort((a, b) => (a.name < b.name ? -1 : 1)).filter((f) => !cursor || f.name > cursor);
  const page = files.slice(0, PAGE);
  const items = [];
  for (const f of page) {
    const b = await gh('/git/blobs/' + f.sha, token);
    if (!b.ok) throw new Error('file ' + b.status);
    const j = decode((await b.json()).content);
    for (const x of j.items || []) items.push({ taskId: x.taskId, action: x.action, at: x.at || j.sentAt, eid: x.eid || '', text: x.text ? 1 : 0 });
  }
  return { items, nextCursor: files.length > PAGE ? page[page.length - 1].name : null };
}

async function existing(path, token) {
  const r = await gh(path + '?ref=' + BRANCH, token);
  if (r.status === 404) return null;
  if (!r.ok) throw new Error('check ' + r.status);
  return decode((await r.json()).content);
}

export default async function handler(req, res) {
  const token = process.env.GITHUB_TOKEN;
  if (req.method === 'GET') {
    if (!token) return res.status(503).json({ ok: false, error: 'Не задан GITHUB_TOKEN в настройках Vercel' });
    const cursor = (req.query && req.query.cursor) || new URL(req.url || '/', 'http://x').searchParams.get('cursor') || '';
    try { return res.status(200).json({ ok: true, ...(await recent(token, String(cursor))) }); } catch (e) { return res.status(502).json({ ok: false, error: 'Не удалось прочитать отметки: ' + e.message }); }
  }
  if (req.method !== 'POST') return res.status(405).json({ ok: false, error: 'Только GET и POST' });
  if (!token) return res.status(503).json({ ok: false, error: 'Не задан GITHUB_TOKEN в настройках Vercel' });

  const body = typeof req.body === 'string' ? JSON.parse(req.body || '{}') : req.body || {};
  const items = (Array.isArray(body.items) ? body.items : [])
    .map((x) => ({
      client: String(x.client || '').slice(0, 200),
      taskId: x.taskId ? String(x.taskId).slice(0, 100) : null,
      taskTitle: x.taskTitle ? String(x.taskTitle).slice(0, 300) : null,
      text: String(x.text || '').trim().slice(0, 4000),
      action: ['eve', 'prio', 'wait', 'done', 'later', 'reopen', 'claude'].includes(x.action) ? x.action : '',
      actionLabel: String(x.actionLabel || '').slice(0, 40),
      at: String(x.at || '').slice(0, 40),
      eid: /^[a-z0-9]{6,40}$/i.test(x.eid || '') ? x.eid : '',
    }))
    .filter((x) => x.text || x.action);
  if (!items.length) return res.status(400).json({ ok: false, error: 'Пустые комментарии' });
  if (items.length > 100) return res.status(400).json({ ok: false, error: 'Больше 100 комментариев за раз' });

  const now = new Date();
  const id = BATCH.test(body.batchId || '') ? body.batchId : now.toISOString().replace(/[:.]/g, '-') + '-' + Math.random().toString(36).slice(2, 10);
  const path = '/contents/inbox/' + id + '.json';
  const file = { sentAt: now.toISOString(), batchId: id, source: 'svodka-rei', status: 'new', items };
  // Повтор той же партии (ответ на прошлую отправку потерялся) — тот же файл, «ок»; то же имя с другим содержимым — ошибка.
  const same = (f) => JSON.stringify(f.items) === JSON.stringify(items);
  try {
    await ensureBranch(token);
    let ex = await existing(path, token);
    if (!ex) {
      const put = await gh(path, token, {
        method: 'PUT',
        body: JSON.stringify({ message: 'Комментарии со сводки: ' + items.length, branch: BRANCH, content: Buffer.from(JSON.stringify(file, null, 2)).toString('base64') }),
      });
      if (put.ok) return res.status(200).json({ ok: true, count: items.length, sentAt: file.sentAt, batchId: id });
      if (put.status !== 409 && put.status !== 422) throw new Error('save ' + put.status);
      ex = await existing(path, token);
      if (!ex) throw new Error('save ' + put.status);
    }
    if (!same(ex)) return res.status(409).json({ ok: false, error: 'Партия ' + id + ' уже сохранена с другим содержимым' });
    return res.status(200).json({ ok: true, count: items.length, sentAt: ex.sentAt, batchId: id, duplicate: true });
  } catch (e) {
    return res.status(502).json({ ok: false, error: 'Не удалось сохранить: ' + e.message });
  }
}
