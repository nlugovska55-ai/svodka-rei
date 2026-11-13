// Отправляет одно сообщение Ольге ботом. Запускается из .github/workflows/tg-send.yml.
// Вход (переменные окружения): TEXT — текст; BUTTONS — варианты через «;» (необязательно); REF — id дела или вопроса,
// он вернётся вместе с нажатой кнопкой в ветку inbox («Ответ кнопкой в Telegram: …», taskId = REF).
const token = process.env.TELEGRAM_BOT_TOKEN;
const chats = (process.env.TELEGRAM_CHAT_ID || '').split(/[\s,;]+/).filter(Boolean);
if (!token || !chats.length) { console.error('Нет секретов репозитория TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID'); process.exit(1); }

const cut = (s, n) => { let r = ''; for (const c of s) { if (Buffer.byteLength(r + c) > n) break; r += c; } return r; }; // callback_data ≤ 64 байт
const text = String(process.env.TEXT || '').replace(/\\n/g, '\n').trim();
if (!text) { console.error('Пустой текст'); process.exit(1); }
const ref = cut(String(process.env.REF || ''), 30);
const buttons = String(process.env.BUTTONS || '').split(';').map((b) => b.trim()).filter(Boolean)
  .map((b) => ({ text: b, callback_data: cut(ref + '|' + b, 64) }));

let failed = 0;
for (const chat_id of chats) {
  for (let i = 0; i < text.length; i += 4000) {
    const last = i + 4000 >= text.length;
    const res = await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ chat_id, text: text.slice(i, i + 4000), ...(last && buttons.length ? { reply_markup: { inline_keyboard: [buttons] } } : {}) }),
    });
    const j = await res.json().catch(() => ({}));
    console.log(j.ok ? 'отправлено' : 'ошибка: ' + (j.description || res.status));
    if (!j.ok) failed++;
  }
}
process.exit(failed ? 1 : 0);
