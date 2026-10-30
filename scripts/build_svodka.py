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
    # длинное слово — по основе без двух последних букв: «Морепласт» в таблице Лены = «Мореплас» в задаче
    return all((k in words) if len(k) <= 3 else (k in flat) or (len(k) >= 7 and k[:-2] in flat) for k in keys)


PAY_RX = re.compile(r"оплат|альф|поступл|п/п", re.I)


def pay_hint(linked):
    """Ориентир срока для строки Лены без даты: ближайший срок связанного дела, где ждём именно оплату.
    Это дата контроля по делу (источник — дело в системе заданий), а не обещанная клиентом дата."""
    got = []
    for t in linked:
        d = iso_date(t.get("due"))
        if d and (t.get("money") in ("ждём", "ждём подпись") or PAY_RX.search(t.get("short") or t.get("title", ""))):
            got.append((d, t))
    if not got:
        return None
    d, t = min(got, key=lambda x: x[0])
    return {"date": d, "task": t["id"], "short": t.get("short") or t["title"][:80], "src": "срок контроля дела «" + (t.get("short") or t["title"][:80]) + "»"}


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


SPISANIYA_JSON = "/mnt/project-files/daily-control/svodka/spisaniya.json"
BUH_PLAN_JSON = "/mnt/project-files/daily-control/svodka/buh-plan.json"
BANK_JSON = "/mnt/project-files/daily-control/svodka/bank-oboroty.json"
KONTROL_DIR = "/mnt/project-files/daily-control/kontrol"
BUH_PLANS = {
    "Реестр обязательных платежей": "/mnt/project-files/daily-control/otchety-claude/reestr-platezhey-chernovik.md",
    "Пакет Ларисе на закрытие квартала": "/mnt/project-files/daily-control/otchety-claude/otchet-buhgalteru.md",
}


def iso_date(v):
    """Дата в ГГГГ-ММ-ДД из «ДД.ММ.ГГГГ» или ISO; иначе пусто (не выдумываем)."""
    v = (v or "").strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", v) or re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})", v)
    if not m:
        return ""
    y, mo, d = (m[1], m[2], m[3]) if len(m[1]) == 4 else (m[3], m[2], m[1])
    return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"


def spisaniya():
    """Предстоящие списания (ведёт «Бухгалтерия», svodka/spisaniya.json). Нет файла — None, пульт пишет «не получено»."""
    if not os.path.exists(SPISANIYA_JSON):
        return None
    with open(SPISANIYA_JSON, encoding="utf-8-sig") as fh:
        p = json.load(fh)
    rows = []
    for r in p.get("rows", []):
        if PON.search(str(r.get("who") or "")):
            continue  # налоги и платежи ИП Пономаренко на пульт не выводим
        d = iso_date(r.get("date"))
        if not d:
            raise ValueError(f"spisaniya.json: строка без даты {r.get('what')!r}; прежняя сводка сохранена")
        rows.append({"date": d, "who": r.get("who") or "", "what": r.get("what") or "", "amount": r.get("amount"),
                     "state": r.get("state") or "", "src": r.get("src") or "", "paid": bool(r.get("paid")),
                     "paidSrc": r.get("paidSrc") or "", "kind": r.get("kind") or "plan",
                     # amount None = сумма неизвестна (диапазон в amountRange), 0 = действительно ноль (zeroMeans)
                     "amountRange": r.get("amountRange") or None, "zeroMeans": r.get("zeroMeans") or "",
                     "bankConfirmed": bool(r.get("bankConfirmed")), "checked": r.get("checked") or "",
                     # кем подтверждена оплата: явное поле confirmedBy или «подтверждено ФНС» в state (не «не подтверждено»)
                     # кем подтверждена оплата: ensConfirmed (выписка ФНС «Операции ЕНП») или явное confirmedBy; слова Ольги — не подтверждение
                     "confirmedBy": r.get("confirmedBy") or ("ФНС" if r.get("paid") and r.get("ensConfirmed") else ""),
                     "paidOn": iso_date(r.get("ensDate") or r.get("paidOn")), "plan": iso_date(r.get("planDate") or r.get("plan")),
                     "basis": r.get("ensBasis") or "", "bankNote": r.get("bankNote") or "", "bankCheck": r.get("bankCheck") or ""})
    rows.sort(key=lambda r: r["date"])
    return {"built": p.get("built", ""), "note": p.get("note", ""), "confirmedNote": p.get("confirmed_note", ""), "rows": rows}


def buh_plan():
    """Регулярные платежи и остаток от «Бухгалтерии» (svodka/buh-plan.json). Её tasks не берём: дела идут из db."""
    if not os.path.exists(BUH_PLAN_JSON):
        return None
    with open(BUH_PLAN_JSON, encoding="utf-8-sig") as fh:
        p = json.load(fh)
    bal = p.get("balance") or {}
    return {"built": p.get("built", ""), "regular": [{k: r.get(k) for k in ("name", "org", "period", "amount", "amountKnown", "note", "kind", "src", "dataAsOf")} for r in p.get("regular", [])],
            # остатки — снимки на дату из выписок/осмотра, не текущий остаток; прогноза по ним не строим
            "balance": {"known": bool(bal.get("known")), "why": bal.get("why") or "", "note": bal.get("note") or "",
                        "accounts": [{"account": a.get("account") or a.get("bank") or "", "date": a.get("date") or a.get("asof") or "", "amount": a.get("amount"), "src": a.get("src") or ""}
                                     for a in bal.get("accounts", []) if a.get("amount") is not None and not PON.search(str(a.get("account") or a.get("bank") or ""))],
                        # полнота: остаток можно продолжать вперёд, только если «Бухгалтерия» явно подтвердила, что видны все счета и потоки
                        "complete": bool(bal.get("complete"))}}


# ИП Пономаренко (правило Ольги через Jonas 29.10): на пульт только поступления на его счёт не от РЭИ.
# Списания, остаток, налоги и переводы от РЭИ этого ИП не публикуются (отрезаются при сборке, а не прячутся на странице).
PON = re.compile(r"пономаренко|точка\s+ип", re.I)
FROM_REI = re.compile(r"рэи|русэко|рус\s*эко", re.I)


def pon_day(d):
    """День выписки счёта ИП Пономаренко → только поступления не от РЭИ; None, если таких нет."""
    items = [x for x in d.get("inItems") or [] if not FROM_REI.search(str(x.get("who") or ""))]
    if not items:
        return None
    return {**d, "open": None, "close": None, "out": None, "outItems": [], "in": round(sum(float(x.get("amount") or 0) for x in items), 2),
            "inItems": items, "partial": "только поступления не от РЭИ", "pon": True}


def bank_oboroty():
    """Обороты по прочитанным выпискам: только охваченные счета и дни. Вход + приход − расход ≠ конец — сборка останавливается."""
    if not os.path.exists(BANK_JSON):
        return None
    with open(BANK_JSON, encoding="utf-8-sig") as fh:
        p = json.load(fh)
    days = []
    for d in p.get("days", []):
        date = iso_date(d.get("date"))
        nums = {k: d.get(k) for k in ("open", "in", "out", "close")}
        if not date or any(v is not None and not isinstance(v, (int, float)) for v in nums.values()) or nums["close"] is None:
            raise ValueError(f"bank-oboroty.json: неполная строка {d.get('account')!r} {d.get('date')!r}; прежняя сводка сохранена")
        if nums["open"] is not None and abs(nums["open"] + (nums["in"] or 0) - (nums["out"] or 0) - nums["close"]) > 0.005:
            raise ValueError(f"bank-oboroty.json: {d.get('account')} {date}: вход + приход − расход не равно концу; прежняя сводка сохранена")
        days.append({"account": d.get("account") or "", "date": date, **nums, "partial": d.get("partial") or "",
                     "inItems": d.get("inItems") or [], "outItems": d.get("outItems") or [], "src": d.get("src") or ""})
    days = [x for x in (pon_day(d) if PON.search(d["account"]) else d for d in days) if x]
    for d in days:  # переводы РЭИ → ИП Пономаренко: сумма остаётся в «ушло» (иначе не сойдётся остаток), получатель не называется
        hide = [x for x in d["outItems"] if PON.search(str(x.get("who") or ""))]
        if hide:
            d["outItems"] = [x for x in d["outItems"] if x not in hide] + [{"who": "прочие перечисления", "amount": round(sum(float(x.get("amount") or 0) for x in hide), 2), "basis": ""}]
    days.sort(key=lambda x: (x["date"], x["account"]))
    return {"built": p.get("built", ""), "note": p.get("note", ""), "days": days}


def buh_svodka():
    """Последняя дневная сводка «Бухгалтерии» (kontrol/buh-svodka-ДАТА.md): разделы и строки как есть."""
    files = sorted(glob.glob(os.path.join(KONTROL_DIR, "buh-svodka-*.md")))
    if not files:
        return None
    sections, cur = [], None
    with open(files[-1], encoding="utf-8-sig") as fh:
        for line in fh:
            line = line.rstrip()
            if line.startswith("## "):
                cur = {"title": line[3:].strip(), "lines": []}
                sections.append(cur)
            elif line.startswith("- ") and cur is not None:
                cur["lines"].append(line[2:].strip())
    head = open(files[-1], encoding="utf-8-sig").readline().lstrip("# ").strip()
    return {"file": os.path.basename(files[-1]), "title": head, "sections": sections}


def buh_sources(sp, bs, bp=None, bank=None):
    """Что получено для раздела «Бухгалтерия», а что нет: пульт показывает это прямо, без нулей."""
    out = [{"name": "Реестр списаний (Бухгалтерия)", "ok": sp is not None, "note": ("от " + sp["built"]) if sp else "файл svodka/spisaniya.json не найден"},
           {"name": "Регулярные платежи (Бухгалтерия)", "ok": bp is not None, "note": ("от " + bp["built"]) if bp else "файл svodka/buh-plan.json не найден"},
           {"name": "Дневная сводка Бухгалтерии", "ok": bs is not None, "note": bs["file"] if bs else "kontrol/buh-svodka-*.md не найдена"}]
    for name, path in BUH_PLANS.items():
        ok = os.path.exists(path)
        out.append({"name": name, "ok": ok, "note": (datetime.fromtimestamp(os.path.getmtime(path), MSK).strftime("обновлён %d.%m") + " · " + os.path.relpath(path, "/mnt/project-files")) if ok else "не найден"})
    if bank and bank["days"]:
        cov = sorted({(d["account"], d["date"][8:10] + "." + d["date"][5:7] + (" " + d["partial"] if d["partial"] else "")) for d in bank["days"]})
        out.append({"name": "Выписки банков", "ok": True, "partial": True, "note": "только " + "; ".join(f"{a} за {dt}" for a, dt in cov) + ". Прочие счета и дни не охвачены"})
    else:
        out.append({"name": "Выписки банков (Альфа, Точка)", "ok": False, "note": "не получены: фактических оборотов по банку нет"})
    return out


GARANTY_DIR = os.environ.get("GARANTY_DIR", "/mnt/project-files/daily-control/garanty-leny")


def garanty(tasks, bank):
    """Гаранты Лены: снимок её таблицы (scripts/import_garanty.py) + связь с задачами db и выписками.
    Таблица только читается. Нет снимка — блок «источник не подключён», без нулей. Сбой импорта — прежние строки с пометкой."""
    sp, stp = os.path.join(GARANTY_DIR, "snapshot.json"), os.path.join(GARANTY_DIR, "import-status.json")
    st = json.load(open(stp, encoding="utf-8-sig")) if os.path.exists(stp) else {}
    if not os.path.exists(sp):
        return {"connected": False, "status": st, "rows": []}
    snap = json.load(open(sp, encoding="utf-8-sig"))
    items = [dict(x, account=d["account"], date=d["date"]) for d in (bank or {}).get("days", []) for x in d.get("inItems", [])]
    used, rows = set(), []
    src = snap.get("rows", [])
    keys_of = [name_keys(r.get("client") or "") for r in src]
    got = [[] for _ in src]
    # 1) платёж той же суммы у того же клиента; 2) одна п/п на несколько строк клиента (сумма строк = платёж). Каждая п/п — один раз.
    reestr = snap.get("format") == "reestr"  # реестр (scripts/reestr_garanty.py) уже сопоставил банк и платежи сам
    if reestr:
        got = [list(r.get("bankReg") or []) for r in src]
        used = set(range(len(items)))
    for k, r in enumerate(src):
        if reestr or r.get("stage") == "paid" or r.get("amount") is None:
            continue
        for i, x in enumerate(items):
            if i not in used and keys_of[k] and matches(keys_of[k], x.get("who") or "") and abs(float(x.get("amount") or 0) - r["amount"]) < 1:
                used.add(i)
                got[k].append({"date": x["date"], "amount": r["amount"], "basis": x.get("basis") or "", "account": x["account"]})
                break
    for i, x in enumerate(items):
        if i in used:
            continue
        idx = [k for k, r in enumerate(src) if r.get("stage") != "paid" and r.get("amount") is not None and not got[k]
               and keys_of[k] and matches(keys_of[k], x.get("who") or "")]
        if len(idx) > 1 and abs(sum(src[k]["amount"] for k in idx) - float(x.get("amount") or 0)) < 1:
            used.add(i)
            for k in idx:
                got[k].append({"date": x["date"], "amount": src[k]["amount"], "basis": (x.get("basis") or "") + ", одна оплата " + f"{float(x.get('amount') or 0):,.0f}".replace(",", " ") + f" ₽ на {len(idx)} строк(и) таблицы", "account": x["account"]})
    for k, r in enumerate(src):
        keys = keys_of[k]
        digits = set(re.findall(r"\d{2,}", r.get("doc") or ""))
        amt = r.get("amount")
        linked = [t for t in tasks if keys and matches(keys, t["client"] + " " + t["title"])
                  and ((amt is not None and t["amount"] is not None and abs(float(t["amount"]) - amt) < 1)
                       or (digits and digits & set(re.findall(r"\d{2,}", t["title"] + " " + t["detail"]))))]
        how = "сумма или договор"
        if not linked and keys:
            same = [t for t in tasks if matches(keys, t["client"] + " " + t["title"]) and t["mgr"] != "Ольга"]
            linked, how = (same, "только по клиенту") if len(same) == 1 else ([], "")
        bank_sum = sum(float(g["amount"] or 0) for g in got[k])
        t0 = linked[0] if linked else None
        rows.append({**{f: r.get(f) for f in ("key", "row", "client", "service", "doc", "amount", "amountRaw", "stage", "date", "dateRaw", "datePaid", "status", "received", "receivedRaw", "who", "note")},
                     "bank": got[k], "bankSum": bank_sum if got[k] else None,
                     "remain": r.get("remainReg") if reestr else ((amt - bank_sum) if (amt is not None and got[k]) else None),
                     "id": r.get("id", ""), "month": r.get("month", ""), "stageReg": r.get("stageReg", ""), "flags": r.get("flags", []), "src": r.get("source", ""), "checked": r.get("checked", ""),
                     "taskIds": [t["id"] for t in linked], "linkBy": how if linked else "",
                     "hint": pay_hint(linked) if not r.get("date") else None,
                     "next": (t0["next"] if t0 else "") or r.get("note") or "",
                     "owner": (t0["waiting"] or t0["mgr"]) if t0 else (r.get("who") or "Лена")})
    acc = os.path.join(GARANTY_DIR, "access.json")  # доступ Лены к таблице: пишем только после решения Ольги
    access = json.load(open(acc, encoding="utf-8-sig")) if os.path.exists(acc) else {}
    return {"connected": True, "live": bool((snap.get("source") or {}).get("live")), "status": st, "access": access, "importedAt": snap.get("importedAt", ""), "source": snap.get("source", {}),
            "format": snap.get("format", ""), "months": snap.get("months", {}),
            "bankCovered": sorted({(d["account"], d["date"]) for d in (bank or {}).get("days", [])}), "rows": rows}


BASELINE_JSON = os.environ.get("PROGNOZ_BASELINE", "/mnt/project-files/daily-control/svodka/prognoz-baseline.json")


def week_start(d):
    return d - timedelta(days=d.weekday())


def forecast_accuracy(gar, today=None):
    """Точность прогноза поступлений по таблице Лены (гарант и прогноз раздельно): только по прогнозу, сохранённому в начале недели (понедельник), и оплатам из банка,
    сопоставленным с теми же ID. Прогноз недели сохраняется первой сборкой этой недели; сохранённый позже понедельника
    помечается partial и в оценку не идёт. Истории нет — «начинаем сбор», задним числом ничего не считаем."""
    today = today or datetime.now(MSK).date()
    wk = week_start(today)
    try:
        with open(BASELINE_JSON, encoding="utf-8-sig") as fh:
            base = json.load(fh)
    except (OSError, ValueError):
        base = {"weeks": {}}
    rows = (gar or {}).get("rows") or []
    key = wk.isoformat()
    if gar and gar.get("connected") and key not in base["weeks"]:
        end = (wk + timedelta(days=6)).isoformat()
        promised = [{"id": r["id"], "amount": r["remain"] if r.get("remain") is not None else r.get("amount"), "date": r["date"],
                     "kind": "прогноз" if r.get("stageReg") == "Обсуждаем" else "гарант"}
                    for r in rows if r.get("id") and r.get("date") and key <= r["date"] <= end and (r.get("remain") or 0) > 0 and r.get("stageReg") != "Отказ"]
        base["weeks"][key] = {"savedAt": datetime.now(MSK).isoformat(timespec="minutes"), "partial": today != wk, "rows": promised}
        atomic_json(BASELINE_JSON, base)
    by_id = {r.get("id"): r for r in rows}
    out = []
    for k, w in sorted(base["weeks"].items()):
        if k >= key or w.get("partial"):
            continue
        end = (date_from(k) + timedelta(days=6)).isoformat()
        for kind in ("гарант", "прогноз"):
            part = [x for x in w["rows"] if x.get("kind", "гарант") == kind]
            if not part:
                continue
            promised = sum(float(x["amount"] or 0) for x in part)
            came = sum(float(b["amount"] or 0) for x in part for b in (by_id.get(x["id"]) or {}).get("bank", []) if k <= b["date"] <= end)
            out.append({"week": k, "kind": kind, "promised": promised, "cameBank": came, "n": len(part)})
    first = min(base["weeks"]) if base["weeks"] else ""
    return {"weeks": out, "first": first, "firstPartial": bool(first and base["weeks"][first].get("partial")),
            "savedAt": base["weeks"].get(first, {}).get("savedAt", "")}


def date_from(y):
    return datetime.strptime(y, "%Y-%m-%d").date()


TREB_JSON = os.environ.get("TREBOVANIYA", "/mnt/project-files/daily-control/svodka/trebovaniya.json")


def trebovaniya(tasks, closed_ids):
    """Требования Ольги → существующее дело db → проверка. Своего списка задач нет: дело только по taskId из базы."""
    if not os.path.exists(TREB_JSON):
        return None
    with open(TREB_JSON, encoding="utf-8-sig") as fh:
        p = json.load(fh)
    open_ids = {t["id"]: t for t in tasks}
    out = []
    for r in p.get("rows", []):
        tid = r.get("taskId") or ""
        link = "open" if tid in open_ids else "closed" if tid in closed_ids else "missing" if tid else "none"
        out.append({"id": r["id"], "text": r.get("text", ""), "source": r.get("source", ""), "taskId": tid, "task": link,
                    "check": r.get("check") or {}, "gap": r.get("gap", ""), "owner": r.get("owner", "")})
    return {"built": p.get("built", ""), "rows": out}


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
    """id событий сайта, которые разбор уже внёс в задачи (db comments: done, или routed для поручений, + eventId).
    Пульт снимает отметку «ждёт синхронизации» только по ним, а не по времени сборки."""
    out = set()
    for f in glob.glob(os.path.join(db_dir, "comments", "*.json")):
        with open(f, encoding="utf-8-sig") as fh:
            c = json.load(fh)
        # routed тоже принято: отметка в базе и у ветки (§16 п.2), «ждёт разбора» снимаем. Отметку «сделано» — только после done
        if c.get("eventId") and (c.get("status") == "done" or (c.get("status") == "routed" and c.get("action") != "done")):
            out.add(c["eventId"])
    return sorted(out)


# Итог должен отвечать на поручение: «отправить акт» не закрывается фразой об оплате (находка Codex 29.10)
FIT = [(re.compile(r"портал|внести", re.I), re.compile(r"внес|портал|заказ", re.I), "внесение в портал"),
       (re.compile(r"отправ|по\s+эдо|диадок", re.I), re.compile(r"отправл|документооборот|доставлен|диадок|эдо", re.I), "отправку"),
       (re.compile(r"подпис", re.I), re.compile(r"подпис", re.I), "подпись"),
       (re.compile(r"оплат|заплат", re.I), re.compile(r"оплач|оплат|п/п|поступ|списан", re.I), "оплату"),
       (re.compile(r"письм|ответить|написать", re.I), re.compile(r"черновик|письм|ответ|отправ", re.I), "письмо")]


def result_fit(title, result):
    """'' — не о чем судить; 'ok' — итог про то же действие; иначе — что ожидалось в итоге."""
    if not (title or "").strip() or not (result or "").strip():
        return ""
    for need, got, what in FIT:
        if need.search(title):
            return "ok" if got.search(result) else what
    return ""


def comment_replies(db_dir, days=3):
    """Ответы Claude на отметки Ольги (db comments: status done + reply) — пульт показывает их в журнале."""
    out = []
    for f in glob.glob(os.path.join(db_dir, "comments", "*.json")):
        with open(f, encoding="utf-8-sig") as fh:
            c = json.load(fh)
        # done без reply тоже показываем: пульт пометит его «итог не подтверждён», а не спрячет
        if c.get("supersededBy"):
            continue  # отметку отменила следующая (например, «вернуть в работу»): показываем только последнюю
        if c.get("status") in ("done", "routed") and (c.get("reply") or c.get("status") == "done") and c.get("at"):
            out.append({"at": c["at"], "atStr": c.get("atStr", ""), "eventId": c.get("eventId", ""),
                        "title": c.get("taskTitle", ""), "text": c.get("text", ""), "reply": c.get("reply") or "",
                        "resultCheck": c.get("resultCheck") or "", "proof": c.get("proof") or "",
                        "action": c.get("action") or "", "appliedTo": c.get("appliedTo") or "",
                        "status": c["status"], "routedTo": c.get("routedTo", ""), "routedAt": c.get("routedAt", ""),
                        "result": c.get("result", ""), "resultAt": c.get("resultAt", ""), "resultBy": c.get("resultBy", ""),
                        "taskId": c.get("taskId") or ""})
            tid = c.get("taskId") or c.get("appliedTo") or ""
            tf = os.path.join(db_dir, "tasks", tid + ".json") if tid else ""
            t = {}
            if tf and os.path.exists(tf):
                with open(tf, encoding="utf-8-sig") as fh:
                    t = json.load(fh)
            # доказательство из карточки дела (proof) — второй кандидат на итог, если итог ответа не про это действие
            out[-1]["taskProof"] = t.get("proof") or ""
            out[-1]["taskStatus"] = t.get("status") or ""
            out[-1]["fit"] = result_fit(out[-1]["title"], out[-1]["result"])
            out[-1]["proofFit"] = result_fit(out[-1]["title"], out[-1]["taskProof"])
    cutoff = (datetime.now(MSK) - timedelta(days=days)).isoformat()
    rows = sorted([r for r in out if r["at"] >= cutoff], key=lambda r: r["at"], reverse=True)
    # 12 последних, но отметку с итогом «требует проверки» не выталкиваем за край, пока её не проверят
    return [r for i, r in enumerate(rows) if i < 12 or r["resultCheck"]]


PORTAL_ORDER = "http://95.84.156.117:3839/projects/orders/{}/"


def portal_orders(t):
    """Номера заказов портала (7 цифр, 239xxxx) из названия, подробностей и шага — по порядку, без повторов, не больше 12."""
    text = " ".join(str(t.get(k) or "") for k in ("title", "detail", "next", "link", "url"))
    return list(dict.fromkeys(re.findall(r"(?<!\d)239\d{4}(?!\d)", text)))[:12]


VYGRUZKI = os.environ.get("VYGRUZKI", "/mnt/project-files/daily-control/svodka/vygruzki.json")


def ru_date(v):
    """Срок в ДД.ММ.ГГГГ: ветки иногда пишут ISO (2026-10-27)."""
    v = (v or "").strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", v)
    return f"{m[3]}.{m[2]}.{m[1]}" if m else v


def vygruzki_row(today):
    """Надзорщик утренних выгрузок «Браузера» (scripts/collect_vygruzki.py → vygruzki.json): чего нет — пишем, нулей не рисуем."""
    base = {"id": "auto-vygruzki", "section": "Автоматика", "client": "", "src": "svodka/vygruzki.json"}
    if not os.path.exists(VYGRUZKI):
        return dict(base, flag="срочно", checked="", text="Утренние выгрузки (ОСВ 62, Битрикс24, банк): сборщик ещё ни разу не запускался, данных нет")
    try:
        with open(VYGRUZKI, encoding="utf-8") as fh:
            v = json.load(fh)
    except (ValueError, OSError) as e:
        return dict(base, flag="срочно", checked="", text=f"Утренние выгрузки: файл не читается ({type(e).__name__})")
    src = v.get("sources") or []
    ok = [x for x in src if x.get("ok")]
    miss = [x.get("label", "?") for x in src if not x.get("ok")]
    fresh = str(v.get("dir") or "") == today
    text = f"Утренние выгрузки за {v.get('dir') or '?'}: получено {len(ok)} из {len(src)}" + (f"; нет: {', '.join(miss)}" if miss else "")
    if not fresh:
        text += "; за сегодня сборки не было"
    return dict(base, flag="ok" if (fresh and not miss) else "срочно", checked=v.get("collected") or "", text=text)


AUTO_STATUS = os.environ.get("AUTO_STATUS", "/mnt/project-files/daily-control/kontrol/avtomatika-status.json")


def automation(summary, gar):
    """Статусы автоматики на пульт берутся прямо из kontrol/avtomatika-status.json (пишет «Регламент» по файлам и коммитам),
    а не из ручной копии в summary. Нет файла или он битый — остаются строки summary как были.
    Реестр гарантов Лены добавляется отдельной строкой по import-status.json: время последней попытки = время проверки."""
    rows = None
    if os.path.exists(AUTO_STATUS):
        try:
            with open(AUTO_STATUS, encoding="utf-8-sig") as fh:
                got = json.load(fh)
            if isinstance(got, list) and all(isinstance(r, dict) and r.get("text") for r in got):
                rows = [dict(r, section="Автоматика", src=r.get("src") or "kontrol/avtomatika-status.json") for r in got]
        except (ValueError, OSError):
            rows = None
    if rows is None:
        rows = [r for r in summary if r.get("section") == "Автоматика"]
    st = (gar or {}).get("status") or {}
    if st:
        ok_at = str(st.get("lastOk") or "")
        text = ("Реестр гарантов Лены: импорт " + ok_at[8:10] + "." + ok_at[5:7] + " " + ok_at[11:16] if ok_at else "Реестр гарантов Лены: успешного импорта нет")
        text += ", запуск по расписанию «Гаранты Лены»" if st.get("scheduled") else ", запуск вручную"
        if not st.get("ok"):
            text += "; последняя попытка — сбой: " + str(st.get("lastError") or "причина не записана")
        rows = [r for r in rows if r.get("id") != "auto-garanty"] + [{
            "id": "auto-garanty", "section": "Автоматика", "client": "", "flag": "ok" if st.get("ok") else "срочно",
            "text": text, "checked": st.get("lastAttempt") or "", "src": "garanty-leny/import-status.json",
            "link": "garanty-leny/vnedrenie-2026-10-29.md"}]
    rows = [r for r in rows if r.get("id") != "auto-vygruzki"] + [vygruzki_row(datetime.now(MSK).strftime("%Y-%m-%d"))]
    return [r for r in summary if r.get("section") != "Автоматика"] + rows


# Правило Ольги (§14 п.8): просьбы войти, ввести ПИН, логин или пароль на пульт не ставим никогда, только в чат
LOGIN_RE = re.compile(r"(^|[^а-яё])пин([^а-яё]|$)|alfa id|войдите|войти в|вход в (банк|альф|точк|личн|кабинет|диадок|почт)|разлогин|залогин|логин|авториз|введите пароль|ввести пароль|сесси[яи] истекл", re.I)


def is_login_ask(row):
    return bool(LOGIN_RE.search(" ".join(str(row.get(k) or "") for k in ("title", "short", "text", "next", "detail"))))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db_dir")
    parser.add_argument("summary_path")
    parser.add_argument("out", nargs="?", default=os.path.join(os.path.dirname(__file__), "..", "data", "svodka.json"))
    parser.add_argument("--allow-at-limit", action="store_true", help="Все страницы tasks выгружены и полнота проверена")
    args = parser.parse_args()
    db_dir, summary_path, out = args.db_dir, args.summary_path, args.out
    tasks, closed_ids = [], set()
    for t in read_export(db_dir, args.allow_at_limit):
        if t["status"] not in ACTIVE_STATUSES:
            closed_ids.add(t["id"])
            continue
        if PON.search(str(t.get("who") or "")):
            continue  # дела самого ИП Пономаренко (налоги, справки) на пульт не идут; поступления ему от клиентов — в банке
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
            "due": ru_date(t.get("due")),
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
            "link": t.get("url") or t.get("link") or portal_orders(t)[:1] and PORTAL_ORDER.format(portal_orders(t)[0]) or "",
            # Правило Ольги 29.10: у любого пункта про портал — ссылки на заказы; номера заказов берём из текста задачи
            "orders": portal_orders(t),
            # Для пульта «Утро»: если ветка их не записала, сайт вычислит сам
            "short": (t.get("short") or "").strip()[:70],
            "waiting": t.get("waiting") or "",  # я / клиент / Лена / Claude
            "delegated": t.get("delegated") or "",  # eid поручения с сайта, внесённого разбором
            "urgency": t.get("urgency") or "",  # сегодня / неделя / фон
            # подпункты сверки (n, text, criterion, src, done, result): пульт показывает, сколько закрыто и что дальше
            "steps": [{k: x.get(k) for k in ("n", "text", "criterion", "src", "done", "result")} for x in (t.get("steps") or []) if isinstance(x, dict)],
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
            "cat": t.get("cat") or "",
            # reference — справочная сумма (погашено/история): пульт не считает её деньгами в пути
            "amountKind": t.get("amountKind") or "",
            "amountNote": (t.get("amountNote") or "").strip(),
        })
    with open(summary_path, encoding="utf-8-sig") as fh:
        summary = json.load(fh)
    if isinstance(summary, dict):
        summary = summary.get("summary", summary.get("rows"))
    if not isinstance(summary, list) or not all(isinstance(row, dict) for row in summary):
        raise ValueError("summary должен быть списком объектов; прежняя сводка сохранена")
    tasks = [t for t in tasks if not is_login_ask(t)]
    summary = [r for r in summary if not is_login_ask(r)]
    data = {
        "updated": datetime.now(MSK).strftime("%Y-%m-%dT%H:%M:%S+03:00"),
        "tasks": tasks,
        "summary": summary,
        "lena_clients": lena_clients(tasks),
        "prognoz": prognoz(),
        "appliedEvents": applied_events(db_dir),
        "replies": [r for r in comment_replies(db_dir) if not is_login_ask(r)],
    }
    sp, bs, bp, bank = spisaniya(), buh_svodka(), buh_plan(), bank_oboroty()
    data.update({"spisaniya": sp, "buh_svodka": bs, "buh_plan": bp, "bank": bank, "buh_sources": buh_sources(sp, bs, bp, bank)})
    data["garanty"] = garanty(tasks, bank)
    data["summary"] = automation(summary, data["garanty"])
    data["accuracy"] = forecast_accuracy(data["garanty"])
    data["trebovaniya"] = trebovaniya(tasks, closed_ids)
    atomic_json(out, data)
    print(f"tasks {len(tasks)}, summary {len(summary)}, lena_clients {len(data['lena_clients'])} -> {out}")


if __name__ == "__main__":
    main()
