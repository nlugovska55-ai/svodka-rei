"""Собирает data/svodka.json для дашборда «Сводка дня РЭИ» на Vercel.

Вход:
  1) папка с выгрузкой db "tasks" системы заданий (ArtifactData list ... out_dir=<dir>),
     внутри <dir>/tasks/<id>.json;
  2) summary.json — блоки «Нужно от вас» и «Проекты» (составляет ветка «Утро и задачи»).
Выход: data/svodka.json = {updated, tasks: [...], summary: [...]}

Запуск: python3 scripts/build_svodka.py <db_dir> <summary.json> [data/svodka.json]
"""
import glob
import json
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
    }
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    print(f"tasks {len(tasks)}, summary {len(summary)} -> {out}")


if __name__ == "__main__":
    main()
