"""Собирает data/svodka.json для дашборда «Сводка дня РЭИ» на Vercel.

Вход:
  1) папка с выгрузкой db "tasks" системы заданий (ArtifactData list ... out_dir=<dir>),
     внутри <dir>/tasks/<id>.json;
  2) summary.json — блоки «Нужно от вас» и «Проекты» (составляет ветка «Утро и задачи»).
Выход: data/svodka.json = {updated, tasks: [...], summary: [...]}

Запуск: python3 scripts/build_svodka.py <db_dir> <summary.json> [data/svodka.json]
"""
import csv
import glob
import json
import re
import os
import sys
from datetime import datetime, timedelta, timezone

MSK = timezone(timedelta(hours=3))

# Денежный статус, если в задаче нет поля money (ветки могут записать его в db сами).
MONEY_OVERRIDE = {
    "m09": "ждём", "m10": "ждём", "v2110": "ждём",
}


def money_of(t):
    if t.get("money"):
        return t["money"]
    if t["id"] in MONEY_OVERRIDE:
        return MONEY_OVERRIDE[t["id"]]
    if t.get("cat") != "pay" or not t.get("mgr"):
        return ""
    title = (t.get("title") or "").lower()
    if "выставить" in title:
        return "выставить счёт"
    if "пришл" in title or "разнест" in title or "внест" in title:
        return "пришло"
    return "ждём"


SECTIONS = ("Мои клиенты", "Клиенты Лены", "Бухгалтерия", "Диадок", "Прочее")


def section_of(t):
    if t.get("section") in SECTIONS:
        return t["section"]
    cat, mgr = t.get("cat"), t.get("mgr")
    if cat in ("tax", "supplier") or t.get("owner") == "Лариса" or (cat == "pay" and not mgr):
        return "Бухгалтерия"
    if cat == "edo":
        return "Диадок"
    if cat in ("client", "pay") and mgr == "Ольга":
        return "Мои клиенты"
    if cat in ("client", "pay"):
        return "Клиенты Лены"
    return "Прочее"


LENA_CSV = "/mnt/project-files/daily-control/svodka/klienty-leny.csv"
STOP = {"ооо", "ао", "пао", "зао", "оао", "ип", "нпп", "пф", "тд", "гк", "завод", "группа", "групп", "компания"}


def compact(text):
    return "".join(re.findall(r"[a-zа-яё0-9]+", text.lower().replace("ё", "е")))


def name_keys(name):
    words = [w.replace("ё", "е") for w in re.findall(r"[a-zа-яё0-9]+", name.lower())]
    return [w for w in words if len(w) >= 3 and w not in STOP]


def matches(keys, text):
    # короткое слово (СПБ, СХМ, АВК) — только целым словом, длинное — как часть текста
    words = set(re.findall(r"[a-zа-я0-9]+", text.lower().replace("ё", "е")))
    flat = compact(text)
    return all((k in words) if len(k) <= 3 else (k in flat) for k in keys)


def lena_clients(tasks):
    """Активные клиенты Лены из сводки по файлам (klienty-leny.csv) + их открытые задачи из db."""
    if not os.path.exists(LENA_CSV):
        return []
    rows = list(csv.DictReader(open(LENA_CSV, encoding="utf-8-sig"), delimiter=";"))
    lena = [t for t in tasks if t["mgr"] != "Ольга" and t["section"] != "Мои клиенты"]
    out = []
    for r in rows:
        if (r.get("Активен 2025–2026") or "").strip().lower() != "да":
            continue
        name = r["Клиент"].strip()
        keys = name_keys(name)
        mine = [t for t in lena if keys and matches(keys, t["client"] + " " + t["title"])]
        out.append({
            "client": name[:1].upper() + name[1:],
            "years": r.get("Годы", ""),
            "services": r.get("Услуги (по названиям файлов)", ""),
            "open": len(mine),
            "wait": sum(float(t["amount"] or 0) for t in mine if t["money"] == "ждём"),
            "todo": "; ".join(t["title"] for t in mine[:3]),
            "due": min((t["due"] for t in mine if t["due"]), key=lambda d: d[6:] + d[3:5] + d[:2], default=""),
        })
    out.sort(key=lambda x: (-x["wait"], -x["open"], x["client"]))
    return out


def main():
    db_dir, summary_path = sys.argv[1], sys.argv[2]
    out = sys.argv[3] if len(sys.argv) > 3 else os.path.join(os.path.dirname(__file__), "..", "data", "svodka.json")
    tasks = []
    for f in sorted(glob.glob(os.path.join(db_dir, "tasks", "*.json"))):
        t = json.load(open(f, encoding="utf-8"))
        t.setdefault("id", os.path.splitext(os.path.basename(f))[0])
        if t.get("status") != "open":
            continue
        m = money_of(t)
        tasks.append({
            "id": t["id"],
            "section": section_of(t),
            "client": t.get("who") or "",
            "title": t.get("title") or "",
            "detail": (t.get("detail") or "").strip(),
            "next": (t.get("next") or "").strip(),
            "lastMove": (t.get("lastMove") or "").strip(),
            "amount": t.get("amount"),
            "due": t.get("due") or "",
            "mgr": t.get("mgr") or "",
            "owner": t.get("owner") or "",
            "added": t.get("added") or "",
            "money": m,
            "pay": 1 if (m or t.get("cat") == "pay") else 0,
            "link": t.get("url") or t.get("link") or "",
        })
    summary = json.load(open(summary_path, encoding="utf-8"))
    if isinstance(summary, dict):
        summary = summary.get("summary") or summary.get("rows") or []
    data = {
        "updated": datetime.now(MSK).strftime("%Y-%m-%dT%H:%M:%S+03:00"),
        "tasks": tasks,
        "summary": summary,
        "lena_clients": lena_clients(tasks),
    }
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    print(f"tasks {len(tasks)}, summary {len(summary)}, lena_clients {len(data['lena_clients'])} -> {out}")


if __name__ == "__main__":
    main()
