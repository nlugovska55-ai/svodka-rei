"""Собирает data/svodka.json для дашборда «Сводка дня РЭИ» на Vercel.

Вход:
  1) папка с выгрузкой db системы заданий (ArtifactData list ... out_dir=<dir>):
     <dir>/tasks/<id>.json и <dir>/comments/<id>.json (comments нужны для appliedEvents);
  2) summary.json — блоки «Нужно от вас» и «Проекты» (составляет ветка «Утро и задачи»).
Выход: data/svodka.json = {updated, tasks: [...], summary: [...]}

Запуск: python3 scripts/build_svodka.py <db_dir> <summary.json> [data/svodka.json]
"""
import csv
import argparse
import glob
import json
import math
import re
import os
import sys
import tempfile
from decimal import Decimal, InvalidOperation
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


PROGNOZ_JSON = "/mnt/project-files/daily-control/prodleniya/prognoz.json"


def prognoz():
    """Прогноз продлений СС/ДС по реестру ФСА (готовит ветка «Продления сертификатов и прогноз»)."""
    if not os.path.exists(PROGNOZ_JSON):
        return None
    p = json.load(open(PROGNOZ_JSON, encoding="utf-8"))
    month = datetime.now(MSK).strftime("%Y-%m")
    months = [m for m in p.get("months", []) if m["month"] >= month]
    return {
        "built": p.get("built", ""),
        "source": p.get("source", ""),
        "next6_expected": sum(m["expected"] for m in months[:6]),
        "next6_potential": sum(m["potential"] for m in months[:6]),
        "next6_expected_olga": sum(m.get("expected_olga", 0) for m in months[:6]),
        "total_expected": sum(m["expected"] for m in months),
        "months": months,
    }


ACTIVE_STATUSES = {"open", "waiting", "wait", "expert"}
KNOWN_STATUSES = ACTIVE_STATUSES | {"done", "closed", "handled"}


def read_export(db_dir, allow_at_limit=False):
    paths = sorted(glob.glob(os.path.join(db_dir, "tasks", "*.json")))
    if not paths:
        raise ValueError("Выгрузка tasks отсутствует или пуста; прежняя сводка сохранена")
    if len(paths) >= 1000 and not allow_at_limit:
        raise ValueError("Достигнут лимит 1000: выгрузите все страницы и подтвердите полноту --allow-at-limit")
    rows, ids = [], set()
    for path in paths:
        with open(path, encoding="utf-8-sig") as fh:
            t = json.load(fh)
        if not isinstance(t, dict):
            raise ValueError(f"{os.path.basename(path)}: задача должна быть объектом")
        t.setdefault("id", os.path.splitext(os.path.basename(path))[0])
        if not isinstance(t["id"], str) or not t["id"].strip() or t["id"] in ids:
            raise ValueError(f"{os.path.basename(path)}: отсутствующий или повторный id")
        ids.add(t["id"])
        if t.get("status") not in KNOWN_STATUSES:
            raise ValueError(f"{t['id']}: неизвестный status {t.get('status')!r}; задача не исключена молча")
        if t["status"] in ACTIVE_STATUSES:
            if not isinstance(t.get("title"), str) or not t["title"].strip():
                raise ValueError(f"{t['id']}: у открытой задачи нет заголовка")
            amount = t.get("amount")
            if amount is not None and str(amount).strip():
                try:
                    if isinstance(amount, bool):
                        raise ValueError("boolean amount")
                    normalized = re.sub(r"\s+", "", str(amount)).replace(",", ".")
                    parsed = Decimal(normalized)
                    if not parsed.is_finite() or not math.isfinite(float(parsed)):
                        raise ValueError("nonfinite amount")
                    t["amount"] = float(parsed)
                except (InvalidOperation, ValueError, OverflowError):
                    raise ValueError(f"{t['id']}: неверная сумма, прежняя сводка сохранена") from None
        rows.append(t)
    return rows


def atomic_json(path, data):
    parent = os.path.dirname(os.path.abspath(path))
    fd, temporary = tempfile.mkstemp(prefix=".svodka-", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1, allow_nan=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def applied_events(db_dir):
    """id событий сайта, которые разбор уже внёс в задачи (db comments: status done + eventId).
    Пульт снимает отметку «ждёт синхронизации» только по ним, а не по времени сборки."""
    out = set()
    for f in glob.glob(os.path.join(db_dir, "comments", "*.json")):
        with open(f, encoding="utf-8-sig") as fh:
            c = json.load(fh)
        if c.get("status") == "done" and c.get("eventId"):
            out.add(c["eventId"])
    return sorted(out)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db_dir")
    parser.add_argument("summary_path")
    parser.add_argument("out", nargs="?", default=os.path.join(os.path.dirname(__file__), "..", "data", "svodka.json"))
    parser.add_argument("--allow-at-limit", action="store_true", help="Все страницы tasks выгружены и полнота проверена")
    args = parser.parse_args()
    db_dir, summary_path, out = args.db_dir, args.summary_path, args.out
    tasks = []
    for t in read_export(db_dir, args.allow_at_limit):
        if t["status"] not in ACTIVE_STATUSES:
            continue
        m = money_of(t)
        if m == "ждём" and not t.get("src"):
            m = "проверяется"  # правило 26.10: «ждём» только с источником (src)
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
            "wait": 1 if t.get("status") in ("wait", "expert") else 0,
            "expert": 1 if t.get("status") == "expert" else 0,
            "waitSince": t.get("waitSince") or "",
            "prio": 1 if t.get("prio") else 0,
            "evening": 1 if (t.get("evening") or t.get("when") == "вечер") else 0,
            "link": t.get("url") or t.get("link") or "",
            # Для пульта «Утро»: если ветка их не записала, сайт вычислит сам
            "short": (t.get("short") or "").strip()[:70],
            "waiting": t.get("waiting") or "",  # я / клиент / Лена / Claude
            "urgency": t.get("urgency") or "",  # сегодня / неделя / фон
            # Сверка «Утра» (§14): без checked за сегодня, src и thread/origin пульт не показывает дело как «нужно от вас»
            "checked": t.get("checked") or "",
            "src": t.get("src") or "",
            "thread": t.get("thread") or "",
            "origin": t.get("origin") or "",
            "proof": t.get("proof") or "",
            # Деньги: одна операция только по явному id; без него одинаковые суммы — «возможный дубль»
            "invoice": t.get("invoice") or "",
            "payment": t.get("payment") or "",
            "dedupe": t.get("dedupe") or "",
        })
    with open(summary_path, encoding="utf-8-sig") as fh:
        summary = json.load(fh)
    if isinstance(summary, dict):
        summary = summary.get("summary", summary.get("rows"))
    if not isinstance(summary, list) or not all(isinstance(row, dict) for row in summary):
        raise ValueError("summary должен быть списком объектов; прежняя сводка сохранена")
    data = {
        "updated": datetime.now(MSK).strftime("%Y-%m-%dT%H:%M:%S+03:00"),
        "tasks": tasks,
        "summary": summary,
        "lena_clients": lena_clients(tasks),
        "prognoz": prognoz(),
        "appliedEvents": applied_events(db_dir),
    }
    atomic_json(out, data)
    print(f"tasks {len(tasks)}, summary {len(summary)}, lena_clients {len(data['lena_clients'])} -> {out}")


if __name__ == "__main__":
    main()
