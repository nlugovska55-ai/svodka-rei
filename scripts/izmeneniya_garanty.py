#!/usr/bin/env python3
"""Что изменилось в гарантах Лены за сутки (raspisanie.md, шаг 6a).

Берёт журнал reestr.json за окно (по умолчанию 24 ч до «сейчас»), переводит записи в строки
«Клиент: Этап Гарант → Оплачено» и сохраняет garanty-leny/izmeneniya/ГГГГ-ММ-ДД.md и .json.
Сборка сводки (build_svodka.py) берёт последний .json и показывает его на пульте.

  python3 scripts/izmeneniya_garanty.py [--now 2026-11-04T09:12+03:00] [--hours 24] [--dry]
"""
import argparse
import datetime as dt
import json
import os
import re
import sys

DIR = os.environ.get("GARANTY_DIR", "/mnt/project-files/daily-control/garanty-leny")
MSK = dt.timezone(dt.timedelta(hours=3))
FIELDS = {"stage": "Этап", "month": "Месяц", "question": "Вопрос", "amount": "Сумма", "due": "Срок оплаты",
          "client": "Клиент", "service": "Услуга", "doc": "Договор", "manager": "Менеджер", "owner": "Ответственный",
          "next": "Что сделать", "maket": "Макет ДС", "received": "По данным менеджера"}
EDIT_RX = re.compile(r"^(\w+) '(.*)' → '(.*)'(?: \((.+)\))?$", re.S)
ID_RX = re.compile(r"^(?:таблица: )?(G-\d+): (.*)$", re.S)


def short(v, n=90):
    v = str(v).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        return v[8:10] + "." + v[5:7] + "." + v[:4]
    if re.fullmatch(r"-?\d+(\.\d+)?", v):
        return f"{float(v):,.0f}".replace(",", " ")
    return v if len(v) <= n else v[:n - 1] + "…"


MON = ["янв.", "фев.", "мар.", "апр.", "мая", "июн.", "июл.", "авг.", "сен.", "окт.", "ноя.", "дек."]


def human(msg, row=None):
    if msg.startswith("новая строка") and row:
        bits = [x for x in (row.get("service"), f"{short(row['amount'])} ₽" if row.get("amount") else "", row.get("stage")) if x]
        return "новая строка: " + ", ".join(bits)
    m = EDIT_RX.match(msg)
    if not m:
        return msg
    f, a, b, how = m.groups()
    if f == "month":
        a, b = [MON[int(x[5:7]) - 1] + " " + x[:4] if re.match(r"\d{4}-\d{2}", x) else x for x in (a, b)]
    name = FIELDS.get(f, f)
    tail = f" ({how})" if how else ""
    if not a:
        return f"{name}: {short(b)}{tail}"
    return f"{name}: {short(a)} → {short(b)}{tail}"


def changes(state, since, until):
    rows = {r["id"]: r for r in state.get("rows", [])}
    by_id, other = {}, []
    for e in state.get("journal", []):
        at = dt.datetime.fromisoformat(e["at"])
        if not (since <= at <= until):
            continue
        m = ID_RX.match(e["msg"])
        if not m:
            other.append(e["msg"])
            continue
        gid, msg = m.groups()
        if e["msg"].startswith("таблица: ") and by_id.get(gid):
            continue  # дубль записи о той же строке
        by_id.setdefault(gid, []).append(human(msg, rows.get(gid)))
    items = []
    for gid, texts in by_id.items():
        r = rows.get(gid, {})
        items.append({"id": gid, "client": r.get("client", ""), "amount": r.get("amount"), "stage": r.get("stage", ""),
                      "text": "; ".join(dict.fromkeys(texts))})
    items.sort(key=lambda x: (x["client"], x["id"]))
    return items, other


def write(items, other, since, until, out_dir):
    day = until.strftime("%Y-%m-%d")
    os.makedirs(out_dir, exist_ok=True)
    data = {"date": day, "since": since.isoformat(timespec="minutes"), "until": until.isoformat(timespec="minutes"),
            "items": items, "other": other}
    with open(os.path.join(out_dir, day + ".json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    lines = [f"# Гаранты Лены: что изменилось {until:%d.%m.%Y}", "",
             f"Окно: {since:%d.%m %H:%M} – {until:%d.%m %H:%M} МСК, по журналу reestr.json.", ""]
    if not items and not other:
        lines.append("Изменений нет.")
    for x in items:
        lines.append(f"- {x['client'] or '?'} ({x['id']}): {x['text']}")
    for o in other:
        lines.append(f"- {o}")
    with open(os.path.join(out_dir, day + ".md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return data


def latest(out_dir, today=None):
    """Последняя сводка изменений не позже today (ГГГГ-ММ-ДД); нет — None."""
    if not os.path.isdir(out_dir):
        return None
    days = sorted(f[:-5] for f in os.listdir(out_dir) if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.json", f) and (not today or f[:10] <= today))
    if not days:
        return None
    with open(os.path.join(out_dir, days[-1] + ".json"), encoding="utf-8") as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--now")
    ap.add_argument("--hours", type=float, default=24)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    until = dt.datetime.fromisoformat(a.now) if a.now else dt.datetime.now(MSK)
    if until.tzinfo is None:
        until = until.replace(tzinfo=MSK)
    since = until - dt.timedelta(hours=a.hours)
    with open(os.path.join(DIR, "reestr.json"), encoding="utf-8") as f:
        state = json.load(f)
    items, other = changes(state, since, until)
    if a.dry:
        print(json.dumps({"items": items, "other": other}, ensure_ascii=False, indent=1))
        return
    write(items, other, since, until, os.path.join(DIR, "izmeneniya"))
    print(f"изменений: строк {len(items)}, прочих записей {len(other)} -> izmeneniya/{until:%Y-%m-%d}.md")


if __name__ == "__main__":
    sys.exit(main())
