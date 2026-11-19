"""Оплаты из «Сводной фин. аналитики» портала (снимок Браузера в .md) → svodka/portal-oplaty.json.
Строка снимка: «ДД.ММ | п/п | сумма | клиент | услуга (договор, прил.) | менеджер». Сумма строк месяца сверяется с итогом заголовка.
Запуск: python3 scripts/portal_oplaty.py <снимок.md> [<выход.json>]"""
import json
import re
import sys
from datetime import datetime, timedelta, timezone

MSK = timezone(timedelta(hours=3))
OUT = "/mnt/project-files/daily-control/svodka/portal-oplaty.json"
MONTHS = {"январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6, "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12}


def num(s):
    s = re.sub(r"[^\d,.-]", "", s or "").replace(",", ".")
    return float(s) if s and s not in "-." else None


def parse(text, year=2026):
    rows, months, cur, snap = [], {}, None, ""
    m = re.search(r"снято[^0-9]*(\d{2}\.\d{2}\.\d{4})\s*(\d{1,2}:\d{2})?", text)
    if m:
        snap = m.group(1) + (" " + m.group(2) if m.group(2) else "")
    for line in text.splitlines():
        h = re.match(r"^##\s*([А-Яа-яё]+)[^,]*,\s*([\d\s ]+)", line)
        if h:
            mon = next((v for k, v in MONTHS.items() if h.group(1).lower().startswith(k)), None)
            cur = f"{year}-{mon:02d}" if mon else None
            if cur:
                months[cur] = {"total": num(h.group(2)), "rows": 0, "sum": 0.0}
            continue
        if line.startswith("##"):
            cur = None
            continue
        if not cur or "|" not in line:
            continue
        p = [x.strip() for x in line.split("|")]
        if len(p) > 6 and "=" in line:  # «15.10 | 5520 + 16.10 | 5538 | 21 799 + 26 201 = 48 000 | …» — две п/п на одну строку
            p = [p[0], p[1] + " " + p[2], p[3].split("=")[-1]] + p[4:]
        if len(p) < 5:
            continue
        a = num(p[2].split("×")[0])
        k = re.search(r"×\s*(\d+)", p[2])
        if a is not None and k:
            a *= int(k.group(1))
        if a is None:
            continue
        dm = re.match(r"(\d{2})\.(\d{2})", p[0])
        doc = re.findall(r"\d{3,4}", p[4])
        pr = re.search(r"прил\.?\s*(\d+)", p[4])
        rows.append({"date": f"{year}-{dm.group(2)}-{dm.group(1)}" if dm else None, "month": cur, "pp": p[1] if p[1] not in ("—", "-") else "",
                     "amount": a, "client": p[3], "service": p[4], "contract": doc[0] if doc else "", "pril": pr.group(1) if pr else "",
                     "mgr": (p[5].split()[0] if len(p) > 5 and p[5] else "")})
        months[cur]["rows"] += 1
        months[cur]["sum"] += a
    for k, v in months.items():
        v["sum"] = round(v["sum"], 2)
        v["ok"] = v["total"] is not None and abs(v["sum"] - v["total"]) < 1
    return {"snapshot": snap, "built": datetime.now(MSK).isoformat(timespec="seconds"), "months": months, "rows": rows}


if __name__ == "__main__":
    d = parse(open(sys.argv[1], encoding="utf-8").read())
    out = sys.argv[2] if len(sys.argv) > 2 else OUT
    json.dump(d, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(out, {k: (v["sum"], v["total"], v["ok"]) for k, v in d["months"].items()}, len(d["rows"]))
