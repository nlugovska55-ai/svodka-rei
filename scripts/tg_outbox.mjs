// Отправляет файлы outbox/*.json ботом Ольге (запускает .github/workflows/tg-outbox.yml).
// Формат файла: {"text": "...", "ref": "<id задачи или вопроса>", "buttons": ["Да", "Нет", "Позже"]}
// Кнопки необязательны; нажатие приходит в api/tg.js и ложится в ветку inbox как «Ответ кнопкой: …» с taskId = ref.
import { readFileSync } from 'node:fs';

const token = process.env.TELEGRAM_BOT_TOKEN;
const chats = (process.env.TELEGRAM_CHAT_ID || '').split(/[\s,;]+/).filter(Boolean);
if (!token || !chats.length) throw new Error('Нет секретов TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID');

// callback_data у Telegram не длиннее 64 байт
const cut = (s, n) => { let r = ''; for (const c of s) { if (Buffer.byteLength(r + c) > n) break; r += c; } return r; };

let failed = 0;
for (const file of process.argv.slice(2)) {
  const m = JSON.parse(readFileSync(file, 'utf8'));
  const ref = cut(String(m.ref || ''), 30);
  const buttons = (m.buttons || []).map((b) => ({ text: String(b), callback_data: cut(ref + '|' + b, 64) }));
  for (const chat_id of chats) {
    const res = await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        chat_id,
        text: String(m.text || '').slice(0, 4000),
        ...(buttons.length ? { reply_markup: { inline_keyboard: [buttons] } } : {}),
      }),
    });
    const j = await res.json();
    console.log(file, chat_id, j.ok ? 'отправлено' : 'ошибка: ' + j.description);
    if (!j.ok) failed++;
  }
}
process.exit(failed ? 1 : 0);
