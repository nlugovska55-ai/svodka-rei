# Сводка дня РЭИ на Vercel

Закрытая страница для Ольги: обычная ссылка в браузере, вход по паролю.
Репозиторий GitHub `nlugovska55-ai/svodka-rei` (private) → Vercel выкладывает каждый push сам.

## Из чего состоит
- `index.html` — страница (без сборки, без внешних библиотек).
- `data/svodka.json` — данные: `{updated, tasks[], summary[]}`. Меняется только этот файл.
- `middleware.js` — страница входа по паролю, вход помнится 30 дней (cookie); принимает пароль и в другой раскладке. Пароль в Vercel: Settings → Environment Variables → `DASH_PASSWORD`. Без него сайт закрыт полностью.
- `scripts/build_svodka.py` — собирает `data/svodka.json` из выгрузки db `tasks` и файла `summary.json`.

## Откуда данные
| Что на странице | Источник | Кто пишет |
|---|---|---|
| Задачи (все таблицы, деньги, цифры) | db `tasks` системы заданий https://claude.ai/artifact/3FMn9JyWFXrL6ZzgAx5JAY | все ветки, каждая свои задачи |
| «Нужно от вас» | `/mnt/project-files/daily-control/svodka/summary.json`, section «Нужно от вас» | «Утро и задачи» |
| «Проекты» | тот же файл, section «Проекты» | «Утро и задачи» по отчётам «Управления проектами» |

## Кто что делает
- **«Утро и задачи» (Sonnet, low)** — единственный, кто выкладывает сайт. Будни 9:30 МСК: утренний сбор, `summary.json`, выгрузка. 13:00 и 17:00: только выгрузка свежей db (без чтения почты).
- **«Бухгалтерия» (Opus, medium)** — ведёт свои задачи в db (cat `tax`, `supplier`, owner «Лариса»); закрытое отмечает `status: done`. Сайт сама не выкладывает.
- **«Браузер» (Sonnet, low)** — задачи Диадока (cat `edo`) и оплат портала/банка (cat `pay`, поле `money`: ждём | пришло | выставить счёт) в db.
- **«Управление проектами» (Opus, medium)** — раз в день пишет «Утру» 1–2 строки состояния проектов для блока «Проекты».
- Срочное днём (оплата пришла, подписано): ветка правит db и пишет «Утру» `message_thread` «обнови сводку» — та выгружает вне расписания.

Одна выкладывающая ветка — нет конфликтов в git и лишних токенов у других веток.

## Как выгрузить (ветка «Утро и задачи»)
1. `ArtifactData list` url=https://claude.ai/artifact/3FMn9JyWFXrL6ZzgAx5JAY collection=`tasks` query.limit=1000 out_dir=`<tmp>/db`
2. `python3 -I scripts/build_svodka.py <tmp>/db /mnt/project-files/daily-control/svodka/summary.json data/svodka.json`
3. `git add data/svodka.json && git commit -m "Сводка <дата время>" && git push` → через ~30 сек. на сайте.

Правила раздела и денег (`section`, `money`) — в `build_svodka.py`; если в задаче db есть поле `money` или `section`, берётся оно.
Страница сама перечитывает данные каждые 10 минут; если данные старше 30 часов, вверху красным «данные устарели».

## Клиенты Лены
Блок «Активные клиенты Лены» строится сам из `/mnt/project-files/daily-control/svodka/klienty-leny.csv` (выгрузка «Клиенты Лены — сводка.xlsx» с ПК, разделитель «;»): активные 2025–2026 + их открытые задачи из db по названию. Обновить файл — положить новый CSV туда же.

## Комментарии с сайта (кнопка 💬 и «Отправить Claude»)
- У каждой задачи клиента и у активных клиентов Лены кнопка 💬: комментарий и быстрый статус (на вечер, в приоритет, ждём клиента, сделано). Черновики хранятся в браузере, «Отправить Claude» наверху шлёт все разом.
- `api/comments.js` (Vercel, за тем же паролем) кладёт их файлом `inbox/<время>.json` в ветку `inbox` этого репозитория (ветка не выкладывается, см. `vercel.json`). Нужна переменная Vercel `GITHUB_TOKEN` — fine-grained токен только на `svodka-rei`, Contents: Read and write. Без неё кнопка честно пишет «Не задан GITHUB_TOKEN», черновики не теряются.
- На разборе комментариев (10:00, 13:00, 18:00): `git fetch origin inbox` → `python3 -I scripts/inbox_to_db.py . /mnt/project-files/daily-control/svodka-site/inbox-imported.txt <tmp>/batch.json` → ArtifactData batch в `comments` страницы задач → обычный разбор. Статусы из кнопок ставятся в задаче db: на вечер → `evening: true`, в приоритет → `prio: true`, ждём клиента → `status: "wait"` + `waitSince`, сделано → `status: "done"`.
- На сайте статусы видны в колонке «Статус»; фильтры «На вечер» и «Ждём клиента» на вкладке «Клиенты».

## Telegram-бот (api/tg.js)
Ольга пишет боту — бот отвечает по данным сводки и передаёт сообщения Claude; Claude пишет Ольге в бот с кнопками «да/нет».
- Команды: `/nuzhno` (нужно от вас + задачи Ольги), `/dengi` (деньги по этапам), `/gorit` (приоритет и срок сегодня или раньше), `/id`.
- Любой другой текст ложится в ветку `inbox` (`source: "telegram"`) и разбирается вместе с комментариями сайта в 10:00, 13:00, 18:00 (`inbox_to_db.py` ставит источник «Telegram»). Если задан `ANTHROPIC_API_KEY`, бот ещё и отвечает сразу по выжимке сводки (модель `ANTHROPIC_MODEL`, по умолчанию `claude-opus-5-5`).
- Файлы и фото бот не принимает: документы клиентов через Telegram не передаём.
- Пишут только chat id из `TELEGRAM_ALLOWED_IDS`; остальным бот называет их id и больше ничего не делает. Webhook защищён секретным заголовком, выведенным из токена; `middleware.js` пропускает без пароля только `POST /api/tg`.
- Настройка: в Vercel `TELEGRAM_BOT_TOKEN` (от @BotFather) → Redeploy → открыть `https://svodka-rei.vercel.app/api/tg?setup=1` (за паролем сайта) → написать боту `/start`, он назовёт id → `TELEGRAM_ALLOWED_IDS` в Vercel → Redeploy.

### Claude → Ольге (ветка outbox)
Ветка Claude создаёт в ветке `outbox` файл `outbox/<время>.json` (GitHub MCP `create_or_update_file`):
`{"text": "ГЕА: отправлять письмо Маркову?", "ref": "gea-ik2710", "buttons": ["Да", "Нет", "Позже"]}`.
`.github/workflows/tg-outbox.yml` отправляет его ботом (секреты репозитория `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`). Нажатая кнопка приходит в `inbox` как «Ответ кнопкой: Да» с `taskId = ref`. Ветка `outbox` не выкладывается (`vercel.json`). Ольге в бот пишет только «Утро и задачи» (§13 регламента).
