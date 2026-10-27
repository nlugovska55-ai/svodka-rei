// Принимает комментарии Ольги со страницы и кладёт их файлом в ветку inbox репозитория.
// Оттуда их забирает Claude на разборе (10:00, 13:00, 18:00) и переносит в базу комментариев страницы задач.
// Вход проверяет middleware.js (тот же пароль, что у страницы).
// Нужна переменная Vercel GITHUB_TOKEN: fine-grained токен только на svodka-rei, Contents: Read and write.
const REPO = 'nlugovska55-ai/svodka-rei';
const BRANCH = 'inbox';
const API = 'https://api.github.com/repos/' + REPO;

async function gh(path, token, init = {}) {
  const res = await fetch(API + path, {
    ...init,
    headers: { authorization: 'Bearer ' + token, accept: 'application/vnd.github+json', 'user-agent': 'svodka-rei', ...(init.headers || {}) },
  });
  return res;
}

async function ensureBranch(token) {
  if ((await gh('/git/ref/heads/' + BRANCH, token)).ok) return;
  const main = await gh('/git/ref/heads/main', token);
  if (!main.ok) throw new Error('main ' + main.status);
  const sha = (await main.json()).object.sha;
  const made = await gh('/git/refs', token, { method: 'POST', body: JSON.stringify({ ref: 'refs/heads/' + BRANCH, sha }) });
  if (!made.ok && made.status !== 422) throw new Error('branch ' + made.status);
}

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
      action: ['eve', 'prio', 'l1', 'l2', 'l3', 'l4', 'wait', 'expert', 'done'].includes(x.action) ? x.action : '',
      actionLabel: String(x.actionLabel || '').slice(0, 40),
      at: String(x.at || '').slice(0, 40),
    }))
    .filter((x) => x.text || x.action);
  if (!items.length) return res.status(400).json({ ok: false, error: 'Пустые комментарии' });
  if (items.length > 100) return res.status(400).json({ ok: false, error: 'Больше 100 комментариев за раз' });

  const now = new Date();
  const stamp = now.toISOString().replace(/[:.]/g, '-');
  const file = { sentAt: now.toISOString(), source: 'svodka-rei', status: 'new', items };
  try {
    await ensureBranch(token);
    const put = await gh('/contents/inbox/' + stamp + '.json', token, {
      method: 'PUT',
      body: JSON.stringify({
        message: 'Комментарии со сводки: ' + items.length,
        branch: BRANCH,
        content: Buffer.from(JSON.stringify(file, null, 2)).toString('base64'),
      }),
    });
    if (!put.ok) throw new Error('save ' + put.status);
    return res.status(200).json({ ok: true, count: items.length, sentAt: file.sentAt });
  } catch (e) {
    return res.status(502).json({ ok: false, error: 'Не удалось сохранить: ' + e.message });
  }
}
