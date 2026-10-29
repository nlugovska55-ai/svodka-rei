#!/usr/bin/env python3
"""Google-таблица реестра гарантов ↔ reestr.json: ввод менеджера не теряется, ID присваивает Claude.

    python3 -I scripts/reestr_google.py pull <лист.json> [--today ГГГГ-ММ-ДД]   # ввод из таблицы → реестр (новые строки получают ID)
    python3 -I scripts/reestr_google.py plan <лист.json> --out <записи.json>     # что записать в таблицу (ID, правки Excel, новые строки, «Банк»)
    python3 -I scripts/reestr_google.py mode [excel|google] [--since ГГГГ-ММ-ДД --by "чьё решение"]

<лист.json> — значения вкладок, как их отдаёт чтение таблицы: {"Реестр": [[шапка], [строка]...], "Платежи": [...]}.
Числа и даты могут прийти отформатированными («25 000», «15.11.2026», «сент. 2026») — приводятся к одному виду.

Источник ввода (st["source"]["mode"]):
- excel (до перехода, режим сверки): файлы Лены читаются (reestr_garanty.py sync), но поля, которые кто-то поправил в таблице,
  помечаются как ручные (row["manual"]) и импорт Excel их больше не перезаписывает: расхождение пишется в журнал.
- google (после перехода): таблица — единственный ввод. sync по Excel отказывает (код 3); файлы Лены — только архив (legacyExcel).

Как отличить правку человека от нашей записи: у строки хранятся exp (последнее прочитанное из таблицы) и expPending
(что мы записали, но ещё не прочитали обратно). Значение в таблице ≠ exp и ≠ expPending → это правка человека.
Наша запись не дошла (в таблице всё ещё exp) → в реестре остаётся новое значение, plan запишет его снова.

Новая строка без ID (в «Реестре» или «Платежах») получает следующий ID. Пока ID не записан в таблицу, он закреплён за
содержимым строки (pendingIds), поэтому повторный pull, сортировка или две одинаковые строки не дают дублей и лишних ID.
Строки не удаляются никогда: строка, пропавшая из таблицы, получает пометку и запись в журнале.
"""
import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reestr_garanty as R  # noqa: E402

# поле реестра → колонка вкладки «Реестр» (с 0). Остальные колонки — формулы или поля Claude.
FIELDS = {"month": 1, "manager": 2, "client": 3, "service": 4, "doc": 5, "amount": 6, "stage": 7, "due": 8, "owner": 16, "question": 18}
CLAUDE_COLS = {"source": 14, "checked": 15, "next": 17}  # пишет только Claude (защищены)
PFIELDS = {"rowId": 1, "date": 2, "amount": 3, "count": 5, "note": 9}
ABBR = [("янв", 1), ("фев", 2), ("мар", 3), ("апр", 4), ("ма", 5), ("июн", 6), ("июл", 7), ("авг", 8), ("сен", 9), ("окт", 10), ("ноя", 11), ("дек", 12)]


def text(v):
    return re.sub(r"\s+", " ", str(v if v is not None else "")).strip()


def amount(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[\s  ₽]", "", str(v))
    return R.num(s) if s else None


def serial(v):
    return (date(1899, 12, 30) + timedelta(days=int(v))).isoformat()


def day(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return serial(v)
    s = text(v)
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return m.group(0)
    m = re.match(r"(\d{1,2})\.(\d{1,2})\.(\d{4})$", s)
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    raise ValueError(f"дата не распознана: {s!r}")


def month(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return serial(v)[:8] + "01"
    s = text(v).lower()
    m = re.match(r"(\d{4})-(\d{2})", s) or None
    if m:
        return f"{m.group(1)}-{m.group(2)}-01"
    m = re.match(r"(?:\d{1,2}\.)?(\d{1,2})\.(\d{4})$", s)
    if m:
        return f"{m.group(2)}-{int(m.group(1)):02d}-01"
    y = re.search(r"20\d\d", s)
    for a, n in ABBR:
        if y and s.startswith(a):
            return f"{y.group(0)}-{n:02d}-01"
    raise ValueError(f"месяц не распознан: {s!r}")


def count(v):
    s = text(v).lower()
    return s if s in ("да", "нет", "вопрос") else ("да" if not s else "вопрос")


NORM = {"month": month, "manager": text, "client": text, "service": text, "doc": text, "amount": amount, "stage": text,
        "due": day, "owner": text, "question": text, "rowId": lambda v: (re.match(r"\s*(G-\d{4})", text(v)) or [None, None])[1],
        "date": day, "count": count, "note": text}


def cell(row, i):
    return row[i] if i < len(row) else ""


def read(row, fields, n, problems):
    out = {}
    for f, i in fields.items():
        try:
            out[f] = NORM[f](cell(row, i))
        except ValueError as e:
            problems.append(f"стр. {n}, {f}: {e} — поле не изменено")
            out[f] = KEEP
    return out


KEEP = object()  # значение не распознано: поле не трогаем


def apply(st, obj, vals, what, mode):
    """Слить значения из таблицы в объект реестра по правилу exp / expPending. Возвращает список правок человека."""
    exp, pend = obj.setdefault("exp", {}), obj.setdefault("expPending", {})
    manual, out = obj.setdefault("manual", {}), []
    for f, v in vals.items():
        if v is KEEP:
            continue
        if f in pend and v == pend[f]:
            exp[f] = v
            del pend[f]
            continue
        if f in exp and v == exp[f]:
            continue  # не менялось (или наша запись ещё не дошла — plan повторит)
        if f not in exp and f not in pend and same(f, v, obj.get(f)):
            exp[f] = v
            continue
        old, first = obj.get(f), not exp and not pend
        obj[f], exp[f] = v, v
        pend.pop(f, None)
        if mode == "excel" and not first:
            manual[f] = v  # первая сверка (таблицу до этого писал только Claude) — не «ручная правка», дальше — да
        if f in ("client", "doc", "service"):
            obj.update(ck=R.client_key(obj["client"]), dk=R.doc_key(obj["doc"]), sk=R.compact(obj["service"]))
        out.append(f"{what}: {f} {old!r} → {v!r} (правка в таблице)")
    return out


def same(f, sheet_value, value):
    """Сравнение значения из таблицы с реестром: текст без лишних пробелов, числа и даты как есть."""
    if NORM.get(f) is text:
        return sheet_value == text(value)
    return sheet_value == value


def content_key(vals):
    return json.dumps({k: v for k, v in vals.items() if v is not KEEP}, ensure_ascii=False, sort_keys=True)


def pull(st, sheet, today):
    mode = st.setdefault("source", {"mode": "excel"})["mode"]
    st.setdefault("pendingIds", {})
    st.setdefault("pseq", 0)
    problems, edits, new = [], [], []
    rows = {r["id"]: r for r in st["rows"]}
    seen = set()
    for n, row in enumerate(sheet.get("Реестр", [])[1:], start=2):
        rid = text(cell(row, 0))
        vals = read(row, FIELDS, n, problems)
        if not rid:
            if not (vals.get("client") or vals.get("amount") not in (None, KEEP)):
                continue
            key = content_key(vals)
            rid = next((i for i in st["pendingIds"].get(key, []) if i not in seen and i in rows), None)
            if rid is None:
                st["seq"] += 1
                rid = f"G-{st['seq']:04d}"
                m = vals.get("month") if vals.get("month") not in (None, KEEP) else None
                due = vals.get("due") if vals.get("due") not in (None, KEEP) else None
                r = {"id": rid, "month": m or (due[:8] + "01" if due else today.strftime("%Y-%m-01")), "manager": "", "client": "", "service": "",
                     "doc": "", "amount": None, "stage": "Гарант", "due": None, "cost": None, "source": f"Google «Реестр», стр. {n}, ввод в таблице",
                     "checked": today.isoformat(), "owner": "Лена", "next": "", "question": "", "seen": [], "gone": "", "manual": {}}
                for f, v in vals.items():
                    if v not in (None, KEEP, ""):
                        r[f] = v
                r.update(ck=R.client_key(r["client"]), dk=R.doc_key(r["doc"]), sk=R.compact(r["service"]))
                r["seen"] = [r["month"]]
                if mode == "excel":
                    r["manual"] = {f: r[f] for f in FIELDS}  # строку завела таблица: Excel её поля не перезаписывает
                r["exp"], r["expPending"] = {}, {}
                st["rows"].append(r)
                rows[rid] = r
                st["pendingIds"].setdefault(key, []).append(rid)
                R.log(st, f"{rid}: новая строка из таблицы, стр. {n}: {r['client']} / {r['doc']} / {r['amount']}")
            new.append((n, rid))
            seen.add(rid)
            continue
        r = rows.get(rid)
        if r is None:
            problems.append(f"«Реестр», стр. {n}: ID {rid} нет в реестре — строку не трогаю, нужен разбор")
            continue
        if rid in seen:
            problems.append(f"«Реестр», стр. {n}: ID {rid} повторяется — вторая строка не учтена, нужен разбор")
            continue
        seen.add(rid)
        r.pop("sheetGone", None)
        edits += apply(st, r, vals, rid, mode)
    for r in st["rows"]:
        if r["id"] not in seen and r.get("exp") and not r.get("sheetGone"):
            r["sheetGone"] = today.isoformat()
            R.log(st, f"{r['id']}: строки нет в таблице (удалили или вырезали) — в реестре оставлена, нужен разбор")
            problems.append(f"{r['id']}: пропала из таблицы")

    pays = {p["id"]: p for p in st["payments"]}
    pseen = set()
    for n, row in enumerate(sheet.get("Платежи", [])[1:], start=2):
        pid = text(cell(row, 0))
        vals = read(row, PFIELDS, n, problems)
        if not pid:
            if not any(text(c) for c in row[1:10]):
                continue
            if not vals.get("rowId") or vals.get("amount") in (None, KEEP) or vals["rowId"] not in rows:
                problems.append(f"«Платежи», стр. {n}: не выбран клиент из списка или нет суммы — оплата не учтена, пока не дополнят")
                continue
            key = "P" + content_key(vals)
            pid = next((i for i in st["pendingIds"].get(key, []) if i not in pseen and i in pays), None)
            if pid is None:
                st["pseq"] += 1
                pid = f"P-M-{st['pseq']:04d}"
                p = {"id": pid, "rowId": vals["rowId"], "date": vals.get("date") if vals.get("date") is not KEEP else None,
                     "amount": vals["amount"], "kind": "Лена", "count": vals.get("count") or "да", "account": "", "basis": "",
                     "source": f"Google «Платежи», стр. {n}, ввод в таблице", "note": vals.get("note") or "", "exp": {}, "expPending": {}, "manual": {}}
                st["payments"].append(p)
                pays[pid] = p
                st["pendingIds"].setdefault(key, []).append(pid)
                R.log(st, f"{pid}: оплата из таблицы к {p['rowId']}: {p['amount']:.0f}, учитывать «{p['count']}»")
            new.append(("P", n, pid))
            pseen.add(pid)
            continue
        p = pays.get(pid)
        if p is None:
            problems.append(f"«Платежи», стр. {n}: ID {pid} нет в реестре — не трогаю")
            continue
        pseen.add(pid)
        edits += apply(st, p, vals, pid, mode)
    for e in edits:
        R.log(st, e)
    for e in problems:
        R.log(st, "таблица: " + e)
    # pendingIds держим, пока ID не прочитан обратно из таблицы
    landed = {text(cell(x, 0)) for tab in ("Реестр", "Платежи") for x in sheet.get(tab, [])[1:]}
    st["pendingIds"] = {k: [i for i in v if i not in landed] for k, v in st["pendingIds"].items()}
    st["pendingIds"] = {k: v for k, v in st["pendingIds"].items() if v}
    return {"edits": edits, "new": new, "problems": problems}


def to_cell(f, v):
    if v is None:
        return ""
    if f == "amount":
        return v
    return v


def label(r):
    return f"{r['id']} · {r['client']} · {r['doc']} · {r['amount']:.0f}" if r.get("amount") is not None else f"{r['id']} · {r['client']} · {r['doc']} · "


def plan(st, sheet, today, since="2026-09"):
    """Минимальные записи в таблицу. Ячейки, которые менять не нужно, — null (чтение таблицы их пропускает, формулы целы)."""
    reg = sheet.get("Реестр", [])
    where = {text(cell(x, 0)): n for n, x in enumerate(reg[1:], start=2) if text(cell(x, 0))}
    rows_by_content = {}
    for n, x in enumerate(reg[1:], start=2):
        if not text(cell(x, 0)):
            rows_by_content.setdefault(content_key(read(x, FIELDS, n, [])), []).append(n)
    width, block, nxt = 19, {}, len(reg) + 1
    for r in st["rows"]:
        n = where.get(r["id"])
        if n is None:
            keyed = [i for k, ids in st["pendingIds"].items() for i in ids if i == r["id"]]
            if keyed:  # новая строка из таблицы: ID ещё не записан — пишем ID в её строку
                key = next(k for k, ids in st["pendingIds"].items() if r["id"] in ids)
                free = rows_by_content.get(key, [])
                if free:
                    n = free.pop(0)
                    block.setdefault(n, [None] * width)[0] = r["id"]
            if n is None:
                if r.get("sheetGone") or not (r["month"] >= since + "-01" or R.remain(st, r) > 0):
                    continue
                n, nxt = nxt, nxt + 1
                line = [None] * width
                line[0] = r["id"]
                for f, i in FIELDS.items():
                    line[i] = to_cell(f, r.get(f))
                block[n] = line
                r["expPending"] = {f: r.get(f) for f in FIELDS}
        line = block.setdefault(n, [None] * width)
        cur = reg[n - 1] if n - 1 < len(reg) else []
        for f, i in FIELDS.items():
            try:
                sv = NORM[f](cell(cur, i))
            except ValueError:
                sv = KEEP
            if n <= len(reg) and sv is not KEEP and not same(f, sv, r.get(f)) and line[i] is None:
                line[i] = to_cell(f, r.get(f))
                r.setdefault("expPending", {})[f] = r.get(f)
        want = {"source": r["source"], "checked": r["checked"], "next": R.next_action(st, r, today)}
        for f, i in CLAUDE_COLS.items():
            try:
                sv = day(cell(cur, i)) if f == "checked" else text(cell(cur, i))
            except ValueError:
                sv = None
            if sv != text(want[f]):
                line[i] = want[f]
    writes = []
    if block:
        lo, hi = min(block), max(block)
        writes.append({"range": f"Реестр!A{lo}:S{hi}", "values": [block.get(n, [None] * width) for n in range(lo, hi + 1)]})

    pay = sheet.get("Платежи", [])
    pwhere = {text(cell(x, 0)): n for n, x in enumerate(pay[1:], start=2) if text(cell(x, 0))}
    pfree = {}
    for n, x in enumerate(pay[1:], start=2):
        if not text(cell(x, 0)) and any(text(c) for c in x[1:10]):
            pfree.setdefault("P" + content_key(read(x, PFIELDS, n, [])), []).append(n)
    rows = {r["id"]: r for r in st["rows"]}
    pblock, pnext = {}, len(pay) + 1
    shown = {r["id"] for r in st["rows"] if (r["id"] in where or r["month"] >= since + "-01" or R.remain(st, r) > 0) and not r.get("sheetGone")}
    for p in st["payments"]:
        if p["kind"] == "банк" or p["rowId"] not in shown:
            continue
        n = pwhere.get(p["id"])
        if n is None:
            key = next((k for k, ids in st["pendingIds"].items() if p["id"] in ids), None)
            if key and pfree.get(key):
                n = pfree[key].pop(0)
                pblock.setdefault(n, [None] * 10)[0] = p["id"]
                continue
            n, pnext = pnext, pnext + 1
            pblock[n] = [p["id"], label(rows[p["rowId"]]), p["date"] or "", p["amount"], p["kind"], p["count"], p["account"], p["basis"], p["source"], p["note"]]
            p["expPending"] = {"rowId": p["rowId"], "date": p["date"], "amount": p["amount"], "count": p["count"], "note": p["note"]}
            continue
        cur = pay[n - 1]
        line = [None] * 10
        for f, i in PFIELDS.items():
            if f == "rowId":
                continue
            try:
                sv = NORM[f](cell(cur, i))
            except ValueError:
                sv = KEEP
            if sv is not KEEP and not same(f, sv, p.get(f)):
                line[i] = p.get(f) if p.get(f) is not None else ""
                p.setdefault("expPending", {})[f] = p.get(f)
        if any(v is not None for v in line):
            pblock[n] = line
    if pblock:
        lo, hi = min(pblock), max(pblock)
        writes.append({"range": f"Платежи!A{lo}:J{hi}", "values": [pblock.get(n, [None] * 10) for n in range(lo, hi + 1)]})

    bank = [p for p in st["payments"] if p["kind"] == "банк" and p["rowId"] in shown]
    bank.sort(key=lambda p: (p["date"] or "", p["rowId"]))
    bvals = [[f"B-{k:04d}", p["rowId"], p["date"], p["amount"], p["account"], (p["basis"] or "").split(",")[0], p["source"], "Claude по выписке"]
             for k, p in enumerate(bank, start=1)]
    writes.append({"range": f"Банк!A2:H{len(bvals) + 1}", "values": bvals, "clearAfter": f"Банк!A{len(bvals) + 2}:H"})
    return writes


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["pull", "plan", "mode"])
    ap.add_argument("arg", nargs="?", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--today", default="")
    ap.add_argument("--since", default="")
    ap.add_argument("--by", default="")
    a = ap.parse_args()
    today = date.fromisoformat(a.today) if a.today else datetime.now(R.MSK).date()
    path = os.path.join(R.DIR, "reestr.json")
    st = R.load_state()
    st.setdefault("source", {"mode": "excel"})
    if a.cmd == "mode":
        if a.arg:
            if a.arg not in ("excel", "google"):
                sys.exit("режим: excel или google")
            if a.arg == "google" and not (a.since and a.by):
                sys.exit("переход на Google: нужны --since (дата) и --by (чьё решение — только Ольга)")
            st["source"] = {"mode": a.arg, "since": a.since, "by": a.by, "at": R.now(),
                            "legacyExcel": "архив: Seafile\\boroday\\зарплатуленька, только чтение" if a.arg == "google" else ""}
            R.log(st, f"источник ввода: {a.arg} с {a.since or 'сейчас'} ({a.by or 'без указания'})")
            R.save(path, st)
        print(json.dumps(st["source"], ensure_ascii=False))
        return
    sheet = json.load(open(a.arg, encoding="utf-8"))
    if a.cmd == "pull":
        res = pull(st, sheet, today)
        R.save(path, st)
        R.save(os.path.join(R.DIR, "snapshot.json"), R.snapshot(st, today))
        print(f"таблица → реестр: правок {len(res['edits'])}, новых {len(res['new'])}, проблем {len(res['problems'])}")
        for x in res["edits"] + res["problems"]:
            print(" -", x)
    else:
        writes = plan(st, sheet, today)
        R.save(path, st)
        R.save(a.out, writes)
        cells = sum(1 for w in writes for row in w["values"] for v in row if v is not None)
        print(f"записей в таблицу: диапазонов {len(writes)}, ячеек {cells} -> {a.out}")


if __name__ == "__main__":
    main()
