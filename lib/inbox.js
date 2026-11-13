// Кладёт сообщения из Telegram-бота файлом inbox/<время>.json в ветку inbox (как комментарии с пульта, api/comments.js).
// Оттуда их забирает Claude на разборе (10:00, 13:00, 18:00): scripts/inbox_to_db.py.
// Нужна уже заданная переменная Vercel GITHUB_TOKEN (та же, что у кнопки «Отправить Claude»).
const API = 'https://api.github.com/repos/nlugovska55-ai/svodka-rei';
const BRANCH = 'inbox';

async function gh(path, token, init = {}) {
  return fetch(API + path, {
    ...init,
    headers: { authorization: 'Bearer ' + token, accept: 'application/vnd.github+json', 'user-agent': 'svodka-rei', ...(init.headers || {}) },
  });
}

async function ensureBranch(token) {
  if ((await gh('/git/ref/heads/' + BRANCH, token)).ok) return;
  const main = await gh('/git/ref/heads/main', token);
  if (!main.ok) throw new Error('main ' + main.status);
  const sha = (await main.json()).object.sha;
  const made = await gh('/git/refs', token, { method: 'POST', body: JSON.stringify({ ref: 'refs/heads/' + BRANCH, sha }) });
  if (!made.ok && made.status !== 422) throw new Error('branch ' + made.status);
}

export async function saveInbox(items, source = 'telegram') {
  const token = process.env.GITHUB_TOKEN;
  if (!token) throw new Error('Не задан GITHUB_TOKEN в настройках Vercel');
  const now = new Date();
  const id = now.toISOString().replace(/[:.]/g, '-') + '-' + Math.random().toString(36).slice(2, 10);
  const file = { sentAt: now.toISOString(), batchId: id, source, status: 'new', items };
  await ensureBranch(token);
  const put = await gh('/contents/inbox/' + id + '.json', token, {
    method: 'PUT',
    body: JSON.stringify({ message: 'Из Telegram: ' + items.length, branch: BRANCH, content: Buffer.from(JSON.stringify(file, null, 2)).toString('base64') }),
  });
  if (!put.ok) throw new Error('save ' + put.status);
  return file;
}
