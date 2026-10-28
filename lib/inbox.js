// Кладёт сообщения Ольги файлом inbox/<время>.json в ветку inbox репозитория (ветка не выкладывается, см. vercel.json).
// Оттуда их забирает Claude на разборе (10:00, 13:00, 18:00): scripts/inbox_to_db.py.
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

// source: 'svodka-rei' (сайт) или 'telegram' (бот)
export async function saveInbox(items, source) {
  const token = process.env.GITHUB_TOKEN;
  if (!token) throw new Error('Не задан GITHUB_TOKEN в настройках Vercel');
  const now = new Date();
  const stamp = now.toISOString().replace(/[:.]/g, '-') + (source === 'svodka-rei' ? '' : '-' + source);
  const file = { sentAt: now.toISOString(), source, status: 'new', items };
  await ensureBranch(token);
  const put = await gh('/contents/inbox/' + stamp + '.json', token, {
    method: 'PUT',
    body: JSON.stringify({
      message: (source === 'telegram' ? 'Из Telegram: ' : 'Комментарии со сводки: ') + items.length,
      branch: BRANCH,
      content: Buffer.from(JSON.stringify(file, null, 2)).toString('base64'),
    }),
  });
  if (!put.ok) throw new Error('save ' + put.status);
  return file;
}
