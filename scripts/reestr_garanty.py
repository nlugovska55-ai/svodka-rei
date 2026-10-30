#!/usr/bin/env python3
"""Реестр гарантов и оплат Лены: файлы Лены (Seafile, только чтение) → реестр с постоянными ID → Google-таблица и пульт.

    python3 -I scripts/reestr_garanty.py sync <файл.xlsx>... [--mtime ISO] [--bank data/svodka.json] [--today ГГГГ-ММ-ДД]
    python3 -I scripts/reestr_garanty.py xlsx --out реестр.xlsx [--today ...]      # книга для Google Диска (формулы, без зарплаты)
    (salary — отключено с 29.10: зарплата — лист «Зарплата» единой Google-таблицы, второй книги нет)
    python3 -I scripts/reestr_garanty.py fail "ПК недоступен" [--scheduled]        # запуск не состоялся: записать сбой, реестр не трогать

--scheduled ставит в import-status.json scheduled=true: пульт пишет «по расписанию», а не «вручную».
Каждый sync и fail дописывает строку в zhurnal-zapuskov.md (время, итог, изменения, sha файла) — это журнал для проверки.

Где лежит состояние: $GARANTY_DIR (по умолчанию /mnt/project-files/daily-control/garanty-leny):
  reestr.json        строки (ID G-0001…), платежи (P-…), протокол миграции, журнал запусков;
  snapshot.json      строки для пульта (build_svodka.py читает его как раньше), без зарплаты;
  import-status.json последний запуск, последний успешный, ошибка, дата файла Лены;
  zhurnal-zapuskov.md по строке на каждый запуск (и успешный, и сбой).

Правила:
- Файлы Лены не меняются. Ошибка чтения не трогает прежний реестр и снимок (только status ok=false).
- Повторный запуск с тем же файлом ничего не меняет (идемпотентно), ID не перевыдаются.
- Сумма строки одна («Сумма»); оплаты — отдельные записи. Частичная оплата = платёж меньше суммы, остаток остаётся на той же строке.
- Строка, повторённая Леной в файле следующего месяца, не дублируется: находится открытая строка того же клиента/договора/услуги.
- Строка, где у Лены заполнены и «ожидаемая», и «пришедшая», не считается оплаченной: платёж «вопрос», остаток не уменьшается.
- Ручные «итого» Лены не подменяют строки: расхождение пишется в протокол миграции.
- Зарплата Лены — лист «Зарплата» той же книги (с 29.10 единый файл): база и банк формулами из «Месяцев», запись Лены как есть,
  ставки пустые = «не рассчитано». В пульт не попадает; ставки из записей не выводятся.
"""
import argparse
import hashlib
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone

MSK = timezone(timedelta(hours=3))
DIR = os.environ.get("GARANTY_DIR", "/mnt/project-files/daily-control/garanty-leny")
MONTHS = ["январ", "феврал", "март", "апрел", "ма[йя]", "июн", "июл", "август", "сентябр", "октябр", "ноябр", "декабр"]
STAGES = ["Обсуждаем", "Гарант", "Пришло по Лене", "Отказ"]


# Текст листа «Как пользоваться» — как в живой таблице 1SfK4y… на 30.10.2026 (строка, A, B); правки делать здесь и в таблице одновременно
HOW_TO = [
    (1, 'Лена: гаранты, оплаты и зарплата — единый реестр (с 29.10.2026 один файл)', None),
    (2, 'Дата перехода на этот файл', 'Не назначена. Её ставит Ольга. До неё действует режим сверки: Claude читает файлы Лены в Seafile (boroday\\зарплатуленька) и эту таблицу. Если в таблице поправили сумму, этап, срок или другое поле, импорт Excel его больше не перезаписывает, а расхождение записывается в журнал.'),
    (3, 'После перехода', 'Источник ввода — только эта таблица. Claude переключает режим, и импорт Excel перестаёт работать: файлы Лены остаются архивом, Claude их не читает и в них не пишет. Новые обещания, сроки и оплаты вносятся только сюда.'),
    (4, 'Главный лист', '«Этот месяц»: итоги сверху, ниже клиенты по важности: 🔴 просрочено → ❓ вопрос → 🟠 частично → ⚪ ждём → 🟡 оплата отмечена менеджером → 🟢 оплачено по выписке. Колонка «Что сделать» — ваше действие.'),
    (5, 'Что меняет Лена', '«Реестр»: Клиент, Услуга, Договор/прил., Сумма, Этап, Срок оплаты, Вопрос. «Платежи»: новая строка на каждую оплату — выбрать клиента из списка, дата, сумма, «да». Сумма — только цифрами (12000), срок — датой (15.11.2026): «12к» или «в ноябре» таблица не примет. «Чей факт» и «Источник» заполнит Claude.'),
    (6, 'Что делает Claude', 'Номера строк, сверка с банком, статусы и скрытые служебные вкладки (Банк, Месяцы, Контроль, Миграция, Журнал). Ольга открывает их: меню «Вид» → «Скрытые листы».'),
    (8, 'Пример: новое обещание (гарант)', 'Новая строка в «Реестре» без номера: клиент, договор/прил., Сумма 120000, Этап «Гарант», Срок 15.11.2026. Номер Claude присвоит при сверке.'),
    (9, 'Пример: частичная оплата', 'Сумму в «Реестре» НЕ меняем. В «Платежи»: «За что» — выбрать «G-0199 · ооо эрмаш … 600000», дата, Сумма 250000, Учитывать «да». Остаток 350000 и статус 🟠 появятся сами.'),
    (10, 'Пример: оплата в несколько дней', 'Каждая оплата — отдельная строка в «Платежи» с тем же клиентом из списка. Дубль строки в «Реестре» не делаем.'),
    (11, 'Пример: перенос срока', 'Меняем только «Срок оплаты» в той же строке (ID остаётся тем же). Старый месяц не копируем: открытый остаток сам появится на листе «Этот месяц».'),
    (12, 'Пример: отмена', 'Этап «Отказ», в «Вопрос» коротко причину. Строку не удаляем — история и ID сохраняются.'),
    (13, 'Пример: не уверена в сумме', 'Учитывать «вопрос» в «Платежи» или текст в колонке «Вопрос». Такой платёж не уменьшает остаток, пока не подтвердят.'),
    (15, 'Обещание и банк', 'Прогноз («Обсуждаем») и гарант — этапы сделки в колонке H «Этап». «Оплачено по Лене» (J) — сумма, отмеченная менеджером; это ещё не подтверждение по выписке. «Подтверждено банком» (K) — сумма из выписки. 🟡 «Оплата отмечена менеджером»: по отметке менеджера сумма закрыта, но по выписке ещё не подтверждена полностью; причина может быть в неполной сверке. 🟢 «Оплачено по выписке»: вся сумма подтверждена. Жёлтый статус не означает, что деньги не пришли.'),
    (16, 'Месяцы', 'Листы «Этот месяц» и «Следующий месяц» собираются сами по сегодняшней дате, в том числе при переходе с декабря на январь. Создавать вкладку на каждый месяц не нужно. Если месяц у новой строки не указан, он берётся из срока оплаты, а без срока — текущий.'),
    (17, 'Флаги (колонка N, вкладка «Контроль»)', 'просрочено; нет срока / менеджера / договора / суммы; Лена ≠ банк; банк не подтвердил; возможный дубль; давно не проверяли (>7 дн.); вопрос; платёж под вопросом; новая строка без ID.'),
    (18, 'Зарплата', 'Лист «Зарплата» в этом же файле, только данные Лены. Сверху — текущий период (с сент. 2026), текущий месяц выделен; ниже серым — архив её записей дек. 2025 – авг. 2026. «К выплате» — второй столбец. Баз две и обе подписаны: по месяцу обязательства («Реестр», «Месяц») и по дате оплаты («Платежи», дата). Какая идёт в расчёт — решает Ольга (B2), пока «не согласован» — «не рассчитано». Ставки — периодами с месяца начала (A5:C7): новая ставка не меняет прошлое; закрытый месяц (F, G) показывает зафиксированную сумму. Пусто = «не рассчитано», не ноль. Лист защищён, но не скрыт. Старый файл «Зарплата Лены» — АРХИВ, не удалён. Расхождения — в задаче sverka-leny-2910.'),
    (20, 'Как обновляется', 'Каждый запуск идёт в таком порядке. 1) Claude читает эту таблицу: правки сохраняются, новые строки без ID получают ID. 2) До перехода — файлы Лены; правленные в таблице поля не трогаются. После перехода этот шаг выключен. 3) Сверка с банком. 4) Запись обратно сюда (только изменившиеся ячейки, формулы не трогаются) и в пульт. При сбое прежние данные не затираются, сбой пишется в журнал.'),
    (21, 'Расписание', 'По будням в 9:12 и 15:12 МСК, включено 29.10.2026. Каждый запуск, удачный или со сбоем, записан в журнале (garanty-leny/zhurnal-zapuskov.md), его время видно на пульте в разделе «Автоматика».'),
    (22, 'Ручные итоги в старых файлах', 'Не переносятся и не исправляются. Где итог Лены не равен сумме строк — записано во вкладке «Миграция».'),
    (23, 'Источник строки', 'Колонка O — файл, строка и колонка у Лены; P — когда проверено; Q — ответственный; R — следующее действие.'),
    (24, 'Владелец файла', 'lugovchanka86 (Ольга). Доступы выдаёт только Ольга.'),
    (25, 'Режим сейчас', 'Режим сверки с файлами Лены, ввод в таблице уже сохраняется. Переход на «только таблица» — после «да» Ольги и назначенной даты.'),
    (26, 'Сортировка и поиск', 'Пользуйтесь меню «Данные» → «Создать представление фильтра»: так данные не меняются, а другие видят таблицу как раньше. Строки связаны по ID, поэтому сортировка их не путает.'),
    (27, 'Защита', 'Колонки ID, формулы (J–N, T–V), «Источник», «Проверено», «Что сделать», шапки, вкладки «Банк», «Этот/Следующий месяц» и служебные защищены: менять их может только владелец. Менеджер вводит данные в «Реестре» (B–I, Q, S) и в «Платежах» (B, C, D, F, J).'),
]


def now():
    return datetime.now(MSK).strftime("%Y-%m-%dT%H:%M:%S+03:00")


def compact(s):
    return "".join(re.findall(r"[a-zа-я0-9]+", str(s or "").lower().replace("ё", "е")))


def client_key(s):
    s = re.sub(r"\b(ооо|оао|ао|зао|пао|ип|кфх)\b", " ", str(s or "").lower().replace("ё", "е"))
    return compact(s)


def doc_key(s):
    """«689 ле сенйс прил 19» и «689-ЛЕ-СЕЙС ПРИЛ 19» → «689|19»: номер договора и приложения, без опечаток в буквах."""
    s = str(s or "").lower()
    n = re.findall(r"\d+", s)
    app = re.search(r"прил\D*(\d+)", s)
    return f"{n[0] if n else ''}|{app.group(1) if app else ''}"


def month_of(name):
    """«октябрь 2026.xlsx» → '2026-10-01'; файл без года не берётся (старые 2024 года)."""
    low = os.path.basename(name).lower()
    y = re.search(r"20\d\d", low)
    if not y:
        return None
    for i, m in reversed(list(enumerate(MONTHS))):
        if re.search(r"\b" + m, low):
            return f"{y.group(0)}-{i + 1:02d}-01"
    return None


def add_month(m, k=1):
    y, mo = int(m[:4]), int(m[5:7]) - 1 + k
    return f"{y + mo // 12}-{mo % 12 + 1:02d}-01"


def months_to_show(today):
    """Текущий и следующий месяц: декабрь → январь следующего года."""
    cur = today.strftime("%Y-%m-01")
    return [cur, add_month(cur)]


def num(v):
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v or "").replace("\xa0", " ").replace("₽", "").strip()
    if not re.fullmatch(r"-?[\d ]+([.,]\d+)?", s):
        return None
    return float(s.replace(" ", "").replace(",", "."))


def read_lena(path):
    """Строки файла Лены как есть: (номер строки, клиент, услуга, договор, ожидаемая, пришедшая, себестоимость, сырые значения) + итог."""
    import openpyxl
    ws = openpyxl.load_workbook(path, data_only=True, read_only=True).worksheets[0]
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    head = [str(c or "").lower() for c in (rows[0] if rows else [])]
    if not (head and "клиент" in head[0] and "ожида" in " ".join(head) and "пришед" in " ".join(head)):
        raise ValueError(f"{os.path.basename(path)}: шапка не как у Лены (наименование клиента / ожидаемая / пришедшая): {head[:6]}")
    out, total = [], None
    for n, r in enumerate(rows[1:], start=2):
        r = (r + [None] * 6)[:6]
        if any(isinstance(c, str) and c.strip().lower().startswith("итог") for c in r[:3]):
            total = {"row": n, "D": r[3], "E": r[4]}
            continue
        if not str(r[0] or "").strip():
            continue
        out.append({"row": n, "client": str(r[0]).strip(), "service": str(r[1] or "").strip(), "doc": str(r[2] or "").strip(),
                    "D": num(r[3]), "E": num(r[4]), "F": num(r[5]), "rawD": r[3], "rawE": r[4]})
    return out, total


def load_state():
    p = os.path.join(DIR, "reestr.json")
    if os.path.exists(p):
        return json.load(open(p, encoding="utf-8"))
    return {"rows": [], "payments": [], "migration": [], "journal": [], "salaryNotes": [], "seq": 0}


def save(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def find_row(st, month, src, used):
    """Та же строка того же месяца (по ключу и порядку повторов); иначе открытая строка прошлых месяцев (перенос остатка)."""
    ck, dk, sk = client_key(src["client"]), doc_key(src["doc"]), compact(src["service"])
    same = [r for r in st["rows"] if r["month"] == month and r["ck"] == ck and r["dk"] == dk and r["sk"] == sk and r["id"] not in used]
    if same:
        return same[0], "тот же месяц"
    if dk != "|":
        older = [r for r in st["rows"] if r["month"] < month and r["ck"] == ck and r["dk"] == dk and r["sk"] == sk
                 and r["id"] not in used and remain(st, r) > 0 and r["stage"] != "Отказ"]
        if older:
            return older[-1], "перенос с " + older[-1]["month"][:7]
    return None, ""


def paid(st, rid, kind):
    return sum(p["amount"] for p in st["payments"] if p["rowId"] == rid and p["count"] == "да" and p["kind"] == kind)


def remain(st, r):
    if r.get("amount") is None or r.get("stage") == "Отказ":  # отказ — не ждём (прогон 29.10: отказ попадал в «Ждём»)
        return 0
    return max(0.0, r["amount"] - max(paid(st, r["id"], "Лена"), paid(st, r["id"], "банк")))


def log(st, msg):
    st["journal"].append({"at": now(), "msg": msg})


def upsert_lena_payment(st, r, src, fname):
    pid = "P-L-" + r["id"]
    E, D = src["E"], src["D"]
    p = next((x for x in st["payments"] if x["id"] == pid), None)
    if not E:
        if p and p["count"] != "нет":
            p.update(count="нет", note=f"в файле Лены сумма пришедшая пропала ({fname}, стр. {src['row']}); запись сохранена")
            log(st, f"{r['id']}: пришедшая сумма {p['amount']:.0f} пропала в файле Лены — платёж не учитывается, не удалён")
        return
    count, note = "да", f"{fname}, стр. {src['row']}, колонка «сумма пришедшая»"
    if D:
        count = "вопрос"
        note = (f"в одной строке и ожидаемая {D:_.0f}, и пришедшая {E:_.0f}: частичная оплата или другой ввод — не подтверждено, "
                f"остаток не уменьшен").replace("_", " ")
    if p is not None and p.get("manual"):
        if p["amount"] != E or p["count"] != count:
            log(st, f"{pid}: в Excel {E:.0f} ({count}), в таблице поправлено на {p['amount']:.0f} ({p['count']}) — оставлено значение таблицы")
        return
    if p is None:
        st["payments"].append({"id": pid, "rowId": r["id"], "date": None, "amount": E, "kind": "Лена", "count": count,
                               "account": "", "basis": "", "source": note, "note": ""})
    elif p["amount"] != E or p["count"] != count:
        log(st, f"{r['id']}: оплата по Лене {p['amount']:.0f} → {E:.0f} ({count})")
        p.update(amount=E, count=count, source=note)


def sync(paths, mtime, bank_path, today):
    st = load_state()
    st["journal"] = st.get("journal", [])
    before = json.dumps({k: st[k] for k in ("rows", "payments")}, sort_keys=True)
    files = sorted(((month_of(p), p) for p in paths if month_of(p)), key=lambda x: x[0])
    if not files:
        raise ValueError("нет файлов вида «<месяц> <год>.xlsx»")
    for month, path in files:
        fname = os.path.basename(path)
        recs, total = read_lena(path)
        used = set()
        for src in recs:
            r, how = find_row(st, month, src, used)
            amount = src["D"] if src["D"] else src["E"]
            if r is None:
                st["seq"] += 1
                r = {"id": f"G-{st['seq']:04d}", "month": month, "manager": "", "client": src["client"], "service": src["service"],
                     "doc": src["doc"], "amount": amount, "stage": "Гарант" if src["D"] else "Пришло по Лене", "due": None,
                     "ck": client_key(src["client"]), "dk": doc_key(src["doc"]), "sk": compact(src["service"]),
                     "cost": src["F"], "source": f"{fname}, стр. {src['row']}", "checked": today.isoformat(),
                     "owner": "Лена", "next": "", "question": "", "seen": [month], "gone": ""}
                st["rows"].append(r)
                log(st, f"{r['id']}: новая строка {src['client']} / {src['doc']} / {amount if amount is None else round(amount)} из {fname}")
            else:
                if month not in r["seen"]:
                    r["seen"].append(month)
                    log(st, f"{r['id']}: та же строка в {fname} ({how}) — не дублируем")
                new_stage = "Гарант" if src["D"] else "Пришло по Лене"
                if r["month"] == month and (r["amount"] != amount or r["stage"] != new_stage):
                    # поле, поправленное в Google-таблице (manual), Excel не перезаписывает: правка человека важнее старого файла
                    man, new = r.get("manual") or {}, {"amount": amount, "stage": new_stage}
                    upd = {k: v for k, v in new.items() if k not in man and r[k] != v}
                    for k in (k for k in new if k in man and new[k] != r[k]):
                        log(st, f"{r['id']}: в Excel {k} = {new[k]!r}, в таблице поправлено на {r[k]!r} — оставлено значение таблицы")
                    if upd:
                        log(st, f"{r['id']}: у Лены изменилось {r['stage']} {r['amount']} → {upd.get('stage', r['stage'])} {upd.get('amount', r['amount'])}")
                        r.update(upd)
                r.update(source=f"{fname}, стр. {src['row']}", checked=today.isoformat(), gone="")
            used.add(r["id"])
            upsert_lena_payment(st, r, src, fname)
            if src["D"] and src["E"]:
                r["question"] = (f"В строке {src['row']} файла Лены и ожидаемая {src['D']:_.0f}, и пришедшая {src['E']:_.0f}. "
                                 f"Это частичная оплата или другой ввод? Остаток не уменьшен до подтверждения.").replace("_", " ")
        for r in st["rows"]:
            if r["month"] == month and r["id"] not in used and not r["gone"]:
                r["gone"] = today.isoformat()
                log(st, f"{r['id']}: строки больше нет в {fname} — оставлена в реестре с пометкой")
        D = sum(x["D"] or 0 for x in recs)
        E = sum(x["E"] or 0 for x in recs)
        mig = {"file": fname, "month": month, "rows": len(recs), "sumD": D, "sumE": E, "totalRow": None, "issues": []}
        if total:
            mig["totalRow"] = total["row"]
            for col, s in (("D", D), ("E", E)):
                v = total[col]
                if isinstance(v, (int, float)):
                    if abs(v - s) >= 1:
                        mig["issues"].append(f"{col}{total['row']} вручную {v:_.0f}, сумма строк {s:_.0f}, разница {v - s:_.0f}".replace("_", " "))
                elif isinstance(v, str) and v.strip():
                    lead = re.match(r"\s*(\d[\d ]*\d)", v)
                    if col == "E" and lead and abs(float(lead.group(1).replace(" ", "")) - s) >= 1:
                        mig["issues"].append(f"{col}{total['row']}: запись Лены начинается с {float(lead.group(1).replace(' ', '')):_.0f}, сумма строк {s:_.0f}".replace("_", " "))
                    # разбор записи (base, pct, …) сохраняем, пока её текст тот же; изменилась запись — разбор сбрасывается
                    was = next((n for n in st.get("salaryNotes", []) if n["file"] == fname and n.get("text") == v.strip()), {})
                    st["salaryNotes"] = [n for n in st.get("salaryNotes", []) if n["file"] != fname] + [
                        {**was, "file": fname, "month": month, "cell": f"{col}{total['row']}", "text": v.strip(), "sumE": E}]
        for x in recs:
            if x["D"] is None and x["rawD"] not in (None, "") or x["E"] is None and x["rawE"] not in (None, ""):
                mig["issues"].append(f"стр. {x['row']}: сумма не число ({x['rawD'] or x['rawE']!r}) — не учтена")
            if x["D"] and x["E"]:
                mig["issues"].append(f"стр. {x['row']} {x['client']} {x['doc']}: и ожидаемая {x['D']:_.0f}, и пришедшая {x['E']:_.0f} — вопрос".replace("_", " "))
        st["migration"] = [m for m in st["migration"] if m["file"] != fname] + [mig]
    if bank_path:
        match_bank(st, bank_path)
    st["migration"].sort(key=lambda m: m["month"])
    changed = json.dumps({k: st[k] for k in ("rows", "payments")}, sort_keys=True) != before
    log(st, f"запуск: файлов {len(files)}, строк {len(st['rows'])}, изменений {'есть' if changed else 'нет'}")
    st["journal"] = st["journal"][-500:]
    st["sourceMtime"] = mtime or st.get("sourceMtime", "")
    st["months"] = sorted(set(r["month"] for r in st["rows"]) | set(months_to_show(today)))
    return st, changed


def match_bank(st, bank_path):
    """Поступления из выписок пульта (data/svodka.json → bank.days.inItems) к строкам того же клиента.
    Точная сумма к одной строке, иначе одна п/п на несколько строк клиента, если сумма строк равна платежу. Каждая п/п один раз."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from build_svodka import name_keys, matches
    bank = json.load(open(bank_path, encoding="utf-8")).get("bank") or {}
    have = {(p["account"], p["date"], p["basis"]) for p in st["payments"] if p["kind"] == "банк"}
    for d in bank.get("days", []):
        for x in d.get("inItems", []):
            if (d["account"], d["date"], x.get("basis") or "") in have:
                continue
            m = d["date"][:7] + "-01"
            cand = [r for r in st["rows"] if r["month"] in (m, add_month(m, -1)) and r.get("amount")
                    and paid(st, r["id"], "банк") < r["amount"] and name_keys(r["client"]) and matches(name_keys(r["client"]), x.get("who") or "")]
            amt = float(x.get("amount") or 0)
            one = [r for r in cand if abs(r["amount"] - amt) < 1]
            pick = one[:1] or (cand if len(cand) > 1 and abs(sum(r["amount"] for r in cand) - amt) < 1 else [])
            for r in pick:
                st["payments"].append({"id": f"P-B-{r['id']}-{d['date']}-{compact(x.get('basis'))}", "rowId": r["id"], "date": d["date"],
                                       "amount": r["amount"] if len(pick) > 1 else amt, "kind": "банк", "count": "да", "account": d["account"],
                                       "basis": x.get("basis") or "", "source": f"выписка {d['account']} за {d['date']}" +
                                       (f", одна оплата {amt:_.0f} ₽ на {len(pick)} строк(и)".replace("_", " ") if len(pick) > 1 else ""), "note": ""})
                log(st, f"{r['id']}: банк {d['account']} {d['date']} {x.get('basis')} подтвердил {r['amount']:.0f}")


def flags(st, r, today):
    out = []
    rem, lena, bank = remain(st, r), paid(st, r["id"], "Лена"), paid(st, r["id"], "банк")
    if not r["manager"]:
        out.append("нет менеджера")
    if r.get("amount") is None:
        out.append("нет суммы")
    if rem > 0 and not r["due"]:
        out.append("нет срока")
    if not re.search(r"\d", r["doc"]):
        out.append("нет договора")
    if rem > 0 and r["due"] and r["due"] < today.isoformat():
        out.append("просрочено")
    if lena and bank and abs(lena - bank) >= 1:
        out.append("Лена ≠ банк")
    if lena and not bank and r["month"] >= add_month(today.strftime("%Y-%m-01"), -1):
        out.append("банк не подтвердил")
    same = [x for x in st["rows"] if x["id"] != r["id"] and x["month"] == r["month"] and x["ck"] == r["ck"] and x["dk"] == r["dk"] and x["dk"] != "|" and x["amount"] == r["amount"]]
    if same:
        out.append("возможный дубль: " + ", ".join(x["id"] for x in same))
    if rem > 0 and (today - date.fromisoformat(r["checked"])).days > 7:
        out.append("давно не проверяли")
    if r["gone"]:
        out.append("пропала из файла Лены")
    if r["question"]:
        out.append("вопрос")
    return out


# Рабочая таблица менеджера (Google) и начало истории: Лена ведёт реестр с сентября 2026, эти месяцы не выпадают из снимка
SHEET_URL = "https://docs.google.com/spreadsheets/d/1SfK4y5Iy1hvzLPVxjHZQ3xMhQo7qLf1goQK0DKIY0wE/edit"
HISTORY_FROM = "2026-09-01"


def snapshot(st, today):
    """Строки для пульта в формате import_garanty (build_svodka.garanty читает как раньше) + ID, месяц, остаток, флаги. Без зарплаты."""
    rows = []
    for r in st["rows"]:
        lena, bank = paid(st, r["id"], "Лена"), paid(st, r["id"], "банк")
        stage = "paid" if lena >= (r["amount"] or 0) and lena else "guarantee"
        rows.append({"id": r["id"], "key": r["id"], "row": int(re.search(r"стр\. (\d+)", r["source"]).group(1)), "month": r["month"],
                     "client": r["client"], "service": r["service"], "doc": r["doc"], "amount": r["amount"], "amountRaw": "",
                     "stage": stage, # статусы — теми же словами, что в таблице (колонка M, 30.10): «оплата отмечена менеджером» ≠ «оплачено по выписке»
                     "status": "отказ клиента" if r["stage"] == "Отказ" else "оплачено по выписке" if stage == "paid" and bank >= (r["amount"] or 0)
                     else {"paid": "оплата отмечена менеджером", "guarantee": "клиент подтвердил (гарант)"}[stage],
                     "date": r["due"], "dateRaw": "", "datePaid": None, "received": lena or None, "receivedRaw": "",
                     "who": r["manager"], "note": r["question"], "remainReg": remain(st, r), "flags": flags(st, r, today),
                     "bankReg": [{"date": p["date"], "amount": p["amount"], "basis": p["basis"] + (", " + p["source"].split(", ", 1)[1] if ", одна" in p["source"] else ""),
                                  "account": p["account"]} for p in st["payments"] if p["rowId"] == r["id"] and p["kind"] == "банк"],
                     "source": r["source"], "checked": r["checked"],
                     # этап из таблицы Лены: «Гарант» — гарант поступления, «Обсуждаем» — прогноз; только из него пульт строит прогноз денег
                     "stageReg": r["stage"]})
    cur, nxt = months_to_show(today)
    return {"importedAt": now(), "format": "reestr", "months": {"current": cur, "next": nxt},
            "source": {"url": SHEET_URL, "tab": "Реестр", "live": True, "title": "Лена: гаранты, оплаты и зарплата — единый реестр",
                       "file": "Seafile\\boroday\\зарплатуленька", "mtime": st.get("sourceMtime", ""),
                       "mode": (st.get("source") or {}).get("mode", "excel"), "since": (st.get("source") or {}).get("since", "")},
            "header": [], "rows": [x for x in rows if x["month"] >= min(HISTORY_FROM, add_month(cur, -1)) or x["remainReg"] > 0]}


# ---------- книга для Google Диска ----------
REG_COLS = ["ID", "Месяц", "Менеджер", "Клиент", "Услуга", "Договор, прил.", "Сумма, ₽", "Этап", "Срок оплаты",
            "Оплачено по Лене", "Подтверждено банком", "Остаток", "Статус", "Флаги", "Источник", "Проверено",
            "Ответственный", "Что сделать", "Вопрос", "Ключ дубля", "Ключ миграции", "Для выбора в «Платежах»"]
PAY_COLS = ["ID платежа", "За что (выбрать из списка)", "Дата оплаты", "Сумма, ₽", "Чей факт", "Учитывать", "Счёт", "П/п", "Источник", "Примечание"]


def ru_formula(f):
    """Формула для таблицы с русской локалью: разделитель аргументов «;», запятые внутри кавычек не трогаем."""
    if not isinstance(f, str) or not f.startswith("="):
        return f
    out, q = [], False
    for ch in f:
        if ch == '"':
            q = not q
        out.append(";" if ch == "," and not q else ch)
    return "".join(out)


def next_action(st, r, today):
    """Что сделать менеджеру. Сверку с банком делает Claude, менеджеру её не пишем."""
    if r["question"]:
        return "Ответить на вопрос в колонке «Вопрос»"
    if remain(st, r) <= 0:
        return ""
    if not r["due"]:
        return "Поставить срок оплаты"
    return "Напомнить клиенту: срок прошёл" if r["due"] < today.isoformat() else "Проверить оплату к сроку"


def build_xlsx(st, out, today, since=""):
    """since='2026-09': в книгу идут строки с этого месяца и все открытые старые; история целиком остаётся в reestr.json и во вкладке «Месяцы»."""
    if since:
        keep = {r["id"] for r in st["rows"] if r["month"] >= since + "-01" or remain(st, r) > 0}
        st = dict(st, rows=[r for r in st["rows"] if r["id"] in keep], payments=[p for p in st["payments"] if p["rowId"] in keep],
                  allRows=st["rows"], allPayments=st["payments"])
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.comments import Comment
    from openpyxl.worksheet.datavalidation import DataValidation
    from openpyxl.formatting.rule import FormulaRule
    wb = openpyxl.Workbook()
    bold, head_fill = Font(bold=True), PatternFill("solid", fgColor="E4EFDD")
    N = len(st["rows"]) + 1
    LAST = 2000  # запас строк под формулы и ввод

    def header(ws, cols, widths=None):
        ws.append(cols)
        for c in ws[1]:
            c.font, c.fill = bold, head_fill
        ws.freeze_panes = "B2"
        for i, w in enumerate(widths or [], start=1):
            ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w

    ws = wb.active
    ws.title = "Как пользоваться"
    for n, a, b in HOW_TO:
        ws.cell(n, 1, a).font = bold
        ws.cell(n, 2, b).alignment = Alignment(wrap_text=True, vertical="top")
    ws.cell(1, 1).font = Font(bold=True, size=14)
    ws.cell(HOW_TO[-1][0] + 2, 1, f"Собрано {now()} из файлов Seafile\\boroday\\зарплатуленька (дата файла Лены {st.get('sourceMtime') or 'неизвестна'}).")
    ws.column_dimensions["A"].width, ws.column_dimensions["B"].width = 34, 140

    rg = wb.create_sheet("Реестр")
    header(rg, REG_COLS, [8, 10, 11, 30, 22, 24, 11, 14, 11, 12, 12, 11, 24, 40, 30, 11, 12, 34, 40, 18, 18])
    # Формулы J–N и T — одна ARRAYFORMULA на колонку: новые строки, которые вписывает Лена, считаются сами.
    for r in st["rows"]:
        rg.append([r["id"], date.fromisoformat(r["month"]), r["manager"] or None, r["client"], r["service"], r["doc"], r["amount"], r["stage"],
                   date.fromisoformat(r["due"]) if r["due"] else None, None, None, None, None, None,
                   r["source"] + (f"; пропала из файла {r['gone']}" if r["gone"] else ""), date.fromisoformat(r["checked"]), r["owner"],
                   next_action(st, r, today), r["question"] or None, None,
                   f"{r['month'][:7]}|{r['ck']}|{r['dk']}|{r['amount'] or 0:.0f}" if r["dk"] != "|" else None])
    big = 'IF(J2:J>K2:K,J2:J,K2:K)'
    has = '(L2:L<>"")*(L2:L>0)'
    rg["J2"] = '=ARRAYFORMULA(IF(A2:A="","",SUMIF(LEFT(Платежи!B2:B,6)&"|"&Платежи!F2:F,A2:A&"|да",Платежи!D2:D)))'
    rg["K2"] = '=ARRAYFORMULA(IF(A2:A="","",SUMIF(Банк!B2:B,A2:A,Банк!D2:D)))'
    rg["L2"] = f'=ARRAYFORMULA(IF((A2:A="")+(G2:G=""),"",IF(H2:H="Отказ",0,IF(G2:G-{big}>0,G2:G-{big},0))))'
    q = '(S2:S<>"")+(COUNTIF(LEFT(Платежи!B2:B,6)&"|"&Платежи!F2:F,A2:A&"|вопрос")>0)'
    rg["M2"] = (f'=ARRAYFORMULA(IF(A2:A="","",IF(H2:H="Отказ","⛔ Отказ",IF(G2:G="","❓ Нет суммы",IF({q},"❓ Вопрос",IF(L2:L=0,'
                f'IF(K2:K>=G2:G,"🟢 Оплачено по выписке","🟡 Оплата отмечена менеджером"),IF({big}>0,"🟠 Частично",'
                f'IF(H2:H="Обсуждаем","🔵 Прогноз (обсуждаем)",IF((I2:I<>"")*(I2:I<TODAY()),"🔴 Просрочено","⚪ Ждём оплату")))))))))')
    rg["N2"] = (f'=ARRAYFORMULA(IF(A2:A="",IF(D2:D="","","новая строка: ID присвоит Claude при сверке; "),IF(C2:C="","нет менеджера; ","")&IF(G2:G="","нет суммы; ","")&IF({has}*(I2:I=""),"нет срока; ","")'
                f'&IF(F2:F="","нет договора; ","")&IF({has}*(I2:I<>"")*(I2:I<TODAY()),"просрочено; ","")'
                f'&IF((J2:J>0)*(K2:K>0)*(J2:J<>K2:K),"Лена ≠ банк; ","")'
                f'&IF((J2:J>0)*(K2:K=0)*(B2:B>=EDATE(DATE(YEAR(TODAY()),MONTH(TODAY()),1),-1)),"банк не подтвердил; ","")'
                f'&IF((T2:T<>"")*(COUNTIF(T2:T,T2:T)>1),"возможный дубль; ","")&IF({has}*(P2:P<TODAY()-7),"давно не проверяли; ","")'
                f'&IF(S2:S<>"","вопрос; ","")&IF(COUNTIF(LEFT(Платежи!B2:B,6)&"|"&Платежи!F2:F,A2:A&"|вопрос")>0,"платёж под вопросом; ","")))')
    rg["T2"] = ('=ARRAYFORMULA(IF((A2:A="")+(F2:F=""),"",IF(U2:U<>"",U2:U,TEXT(B2:B,"yyyy-mm")&"|"&LOWER(TRIM(D2:D))&"|"&LOWER(TRIM(F2:F))&"|"&G2:G)))')
    rg["V2"] = '=ARRAYFORMULA(IF(A2:A="","",A2:A&" · "&D2:D&" · "&F2:F&" · "&G2:G))'
    for col in "JKLMNTV":  # формула живёт в шапке: сортировка строк 2+ её не уносит (прогон 29.10: сортировка «Реестра» стёрла J–N у 17 строк)
        rg[f"{col}1"], rg[f"{col}2"] = head_formula(rg[f"{col}1"].value, rg[f"{col}2"].value), None
    for col in "KNOPQTUV":  # техническое: видно Ольге и Claude по «Показать», менеджеру не мешает
        rg.column_dimensions[col].hidden = True
    for row in rg.iter_rows(min_row=2, max_row=N):
        row[1].number_format, row[8].number_format, row[15].number_format, row[6].number_format = "MMM YYYY", "DD.MM.YYYY", "DD.MM.YYYY", "#,##0"
    for col in "JKL":
        rg.column_dimensions[col].number_format = "#,##0"
    dv = DataValidation(type="list", formula1='"' + ",".join(STAGES) + '"', allow_blank=True)
    rg.add_data_validation(dv)
    dv.add(f"H2:H{LAST}")
    for rng, kind, hint in ((f"G2:G{LAST}", "decimal", "Сумма числом, без «к» и «руб»: 12000"), (f"I2:I{LAST}", "date", "Дата: 15.11.2026")):
        v = DataValidation(type=kind, operator="greaterThanOrEqual", formula1="0", allow_blank=True, showErrorMessage=True,
                           promptTitle="Ввод", prompt=hint, error=hint)  # прогон 29.10: «12к» ломал остаток и статус
        rg.add_data_validation(v)
        v.add(rng)
    rg.conditional_formatting.add(f"A2:T{LAST}", FormulaRule(formula=['ISNUMBER(SEARCH("просрочено",$N2))'], fill=PatternFill("solid", fgColor="F8D7D3")))
    rg.conditional_formatting.add(f"A2:T{LAST}", FormulaRule(formula=['ISNUMBER(SEARCH("вопрос",$N2))'], fill=PatternFill("solid", fgColor="FFF1C9")))
    rg.auto_filter.ref = f"A1:T{N}"

    pg = wb.create_sheet("Платежи")
    header(pg, PAY_COLS, [26, 10, 11, 12, 9, 10, 18, 12, 50, 40])
    label = {r["id"]: f'{r["id"]} · {r["client"]} · {r["doc"]} · {r["amount"] or 0:.0f}' for r in st["rows"]}
    for p in (p for p in st["payments"] if p["kind"] == "Лена"):
        pg.append([p["id"], label.get(p["rowId"], p["rowId"]), date.fromisoformat(p["date"]) if p["date"] else None, p["amount"], p["kind"], p["count"],
                   p["account"] or None, p["basis"] or None, p["source"], p["note"] or None])
    for row in pg.iter_rows(min_row=2):
        row[2].number_format, row[3].number_format = "DD.MM.YYYY", "#,##0"
    for col in "AEGHI":  # служебное; Лена заполняет «За что», дату, сумму, «Учитывать»
        pg.column_dimensions[col].hidden = True
    for col, vals in (("F", "да,нет,вопрос"),):
        v = DataValidation(type="list", formula1=f'"{vals}"', allow_blank=False)
        pg.add_data_validation(v)
        v.add(f"{col}2:{col}{LAST}")
    v = DataValidation(type="list", formula1=f"=Реестр!$V$2:$V${LAST}", allow_blank=False)
    pg.add_data_validation(v)
    v.add(f"B2:B{LAST}")

    # Банк отдельно: его заполняет только Claude по выписке, вкладка защищена; Лена ставит факты только в «Платежи».
    bk = wb.create_sheet("Банк")
    header(bk, ["ID записи", "ID строки", "Дата", "Сумма, ₽", "Счёт", "П/п", "Источник", "Кто внёс"], [11, 10, 11, 12, 12, 10, 50, 22])
    for i, p in enumerate((p for p in st["payments"] if p["kind"] == "банк" and p["count"] == "да"), start=1):
        bk.append([f"B-{i:04d}", p["rowId"], date.fromisoformat(p["date"]) if p["date"] else None, p["amount"], p["account"] or None,
                   p["basis"] or None, p["source"], "Claude по выписке"])
    for row in bk.iter_rows(min_row=2):
        row[2].number_format, row[3].number_format = "DD.MM.YYYY", "#,##0"

    # Листы менеджера: одна таблица месяца, итоги сверху, сначала то, что требует действий.
    VIEW = ["Статус", "Клиент", "Договор, прил.", "Услуга", "Сумма, ₽", "Срок оплаты", "Отметка, ₽", "Остаток, ₽", "Что сделать", "№"]
    for title, k, carry in (("Этот месяц", 0, True), ("Следующий месяц", 1, False)):
        ws = wb.create_sheet(title, index=k)
        ws["A1"], ws["B1"] = "Оплаты клиентов", f"=DATE(YEAR(TODAY()),MONTH(TODAY())+{k},1)"
        ws["B1"].number_format = "MMMM YYYY"
        ws["A1"].font = ws["B1"].font = Font(bold=True, size=16)
        cards = [("Ждём, ₽", "=SUM(H7:H)"), ("Просрочено, ₽", '=SUMIF(A7:A,"*Просроч*",H7:H)'),
                 ("По данным менеджера, ₽", "=SUMIFS(Реестр!J:J,Реестр!B:B,B1)"), ("По выписке, ₽", "=SUMIFS(Реестр!K:K,Реестр!B:B,B1)"),
                 ("Вопросов", '=COUNTIF(A7:A,"*Вопрос*")+COUNTIF(A7:A,"*Нет суммы*")')]
        for n, (lab, f) in enumerate(cards, start=1):
            c = openpyxl.utils.get_column_letter(n)
            ws[f"{c}3"], ws[f"{c}4"] = lab, f
            ws[f"{c}4"].font, ws[f"{c}4"].number_format = Font(bold=True, size=14), "#,##0"
        ws["A5"] = ("Этот месяц и неоплаченное из прошлых. " if carry else "") + "Лист собирается сам: менять — во вкладке «Реестр», оплаты — в «Платежи»."
        for n, h in enumerate(VIEW, start=1):
            c = ws.cell(6, n, h)
            c.font, c.fill = bold, head_fill
        ws["G6"].comment = Comment("Сумма оплат по данным менеджера (Реестр, колонка J). Подтверждение по выписке учитывается отдельно.", "Claude")
        cond = "(Реестр!B2:B=B1)" + ('+(Реестр!B2:B<B1)*(Реестр!L2:L>0)*(Реестр!H2:H<>"Отказ")' if carry else "")
        prio = ('IF(REGEXMATCH(s,"Просроч"),1,IF(REGEXMATCH(s,"Вопрос|Нет суммы"),2,IF(REGEXMATCH(s,"Частич"),3,'
                'IF(REGEXMATCH(s,"Ждём"),4,IF(REGEXMATCH(s,"отмечена менеджером"),5,IF(REGEXMATCH(s,"Оплачено"),6,7))))))')
        ws["A7"] = (f'=IFERROR(ARRAYFORMULA(LET(f,FILTER(Реестр!A2:S,{cond}),s,CHOOSECOLS(f,13),'
                    f'SORT(CHOOSECOLS(f,13,4,6,5,7,9,10,12,18,1),{prio},TRUE,CHOOSECOLS(f,4),TRUE))),"строк нет")')
        ws.freeze_panes = "A7"
        for i, w in enumerate([24, 30, 24, 20, 12, 12, 12, 12, 34, 8], start=1):
            ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w

    ms = wb.create_sheet("Месяцы")
    ms.append(["Месяц", "Строк", "Сумма, ₽", "Пришло по Лене, ₽", "Подтв. банком, ₽", "Остаток, ₽", "Флагов", "Сумма строк у Лены, ожидаемая", "Сумма строк у Лены, пришедшая", "Замечания по файлу Лены (её итоги не правим)"])
    for c in ms[1]:
        c.font, c.fill = bold, head_fill
    mig = {m["month"]: m for m in st["migration"]}
    months = sorted(set(st["months"]))
    first = months[0]
    for i in range(2, 2 + 24):
        ms[f"A{i}"] = f'=EDATE(DATE({first[:4]},{int(first[5:7])},1),{i - 2})' if i > 2 else f"=DATE({first[:4]},{int(first[5:7])},1)"
        ms[f"A{i}"].number_format = "MMM YYYY"
        ms[f"B{i}"] = f"=COUNTIF(Реестр!B:B,A{i})"
        for col, src in (("C", "G"), ("D", "J"), ("E", "K"), ("F", "L")):
            ms[f"{col}{i}"] = f"=SUMIFS(Реестр!{src}:{src},Реестр!B:B,A{i})"
            ms[f"{col}{i}"].number_format = "#,##0"
        ms[f"G{i}"] = f'=COUNTIFS(Реестр!B:B,A{i},Реестр!N:N,"?*")'
    full = dict(st, rows=st.get("allRows", st["rows"]), payments=st.get("allPayments", st["payments"]))
    for m in sorted({r["month"] for r in full["rows"]} - {r["month"] for r in st["rows"]}):
        row = 2 + (int(m[:4]) - int(first[:4])) * 12 + int(m[5:7]) - int(first[5:7])
        rs = [r for r in full["rows"] if r["month"] == m]
        ms[f"B{row}"] = len(rs)
        ms[f"C{row}"] = sum(r["amount"] or 0 for r in rs)
        ms[f"D{row}"] = sum(paid(full, r["id"], "Лена") for r in rs)
        ms[f"E{row}"] = sum(paid(full, r["id"], "банк") for r in rs)
        ms[f"F{row}"] = sum(remain(full, r) for r in rs)
        ms[f"G{row}"] = "история: строки в reestr.json"
    for i, m in enumerate(months, start=2):
        x = mig.get(m)
        if not x:
            continue
        row = 2 + (int(m[:4]) - int(first[:4])) * 12 + int(m[5:7]) - int(first[5:7])
        ms[f"H{row}"], ms[f"I{row}"] = x["sumD"], x["sumE"]
        ms[f"J{row}"] = "; ".join(x["issues"]) or "сходится"
    for col, w in zip("ABCDEFGHIJ", [10, 7, 12, 14, 14, 12, 8, 14, 14, 90]):
        ms.column_dimensions[col].width = w
    ms["L1"] = "Месяцы идут формулой от первого на 24 месяца вперёд, новые создавать не нужно. До сентября 2026 итоги взяты из файлов Лены, строки лежат в reestr.json."

    ct = wb.create_sheet("Контроль")
    ct.append(["Флаг", "Строк", "Что делать"])
    for c in ct[1]:
        c.font, c.fill = bold, head_fill
    for f, todo in (("нет суммы", "В строке Лены нет суммы"), ("просрочено", "Срок прошёл, остаток есть: узнать у Лены статус оплаты"), ("нет срока", "Открытая сумма без срока: попросить у Лены дату"),
                    ("нет менеджера", "В файлах Лены менеджера нет; не выдумываем, заполняет Лена/Ольга"), ("нет договора", "Нет номера договора/приложения"),
                    ("Лена ≠ банк", "Сумма по Лене и по выписке разная"), ("банк не подтвердил", "Лена отметила оплату, в выписке пока нет"),
                    ("возможный дубль", "Одинаковые клиент, договор, сумма в одном месяце: проверить, не задвоено ли"),
                    ("давно не проверяли", "Открытая строка не проверялась больше 7 дней"), ("вопрос", "Есть вопрос в колонке «Вопрос»"),
                    ("платёж под вопросом", "Платёж с «Учитывать = вопрос» не уменьшает остаток")):
        ct.append([f, f'=COUNTIF(Реестр!N:N,"*{f}*")', todo])
    k = ct.max_row + 2
    ct[f"A{k}"] = "Файл Лены изменён"
    ct[f"B{k}"] = st.get("sourceMtime") or "неизвестно"
    ct[f"C{k}"] = f'=IF(B{k}="неизвестно","",IF(TODAY()-DATEVALUE(LEFT(B{k},10))>7,"файл Лены не обновлялся больше 7 дней",""))'
    ct[f"A{k + 2}"] = "Строки с флагами (кроме «нет менеджера»):"
    ct[f"A{k + 3}"] = '=IFERROR(CHOOSECOLS(FILTER(Реестр!A2:N,REGEXREPLACE(Реестр!N2:N,"нет менеджера; ","")<>""),1,4,6,12,14),"нет")'
    for col, w in zip("ABCDE", [26, 30, 70, 12, 60]):
        ct.column_dimensions[col].width = w

    mg = wb.create_sheet("Миграция")
    mg.append(["Файл Лены", "Месяц", "Строк", "Сумма строк D (ожидаемая)", "Сумма строк E (пришедшая)", "Строка итога", "Замечания (оригинал не правим)"])
    for c in mg[1]:
        c.font, c.fill = bold, head_fill
    for m in st["migration"]:
        mg.append([m["file"], date.fromisoformat(m["month"]), m["rows"], m["sumD"], m["sumE"], m["totalRow"], "; ".join(m["issues"]) or "сходится"])
    for col, w in zip("ABCDEFG", [26, 10, 7, 14, 14, 9, 110]):
        mg.column_dimensions[col].width = w

    jr = wb.create_sheet("Журнал")
    jr.append(["Когда", "Что"])
    for c in jr[1]:
        c.font, c.fill = bold, head_fill
    for j in st["journal"][-60:]:
        jr.append([j["at"], j["msg"]])
    jr.column_dimensions["A"].width, jr.column_dimensions["B"].width = 26, 110
    # Менеджеру видны пять вкладок; служебные скрыты (Ольга открывает: правый клик по ярлыку → «Показать»).
    wb.move_sheet("Как пользоваться", offset=len(wb.sheetnames) - 1 - wb.sheetnames.index("Как пользоваться"))
    wb.move_sheet("Как пользоваться", offset=wb.sheetnames.index("Платежи") - wb.sheetnames.index("Как пользоваться") + 1)
    salary_sheet(wb, st, today)  # после «Платежей», перед «Как пользоваться» — как в Google-таблице
    wb.move_sheet("Зарплата", offset=wb.sheetnames.index("Как пользоваться") - wb.sheetnames.index("Зарплата"))
    for t in ("Банк", "Месяцы", "Контроль", "Миграция", "Журнал"):
        wb[t].sheet_state = "hidden"
    wb.active = 0
    wb.save(out)


SALARY_COLS = ["Месяц", "К выплате, ₽", "База по месяцу обязательства, ₽", "База по дате оплаты, ₽ (только оплаты с датой)",
               "Банк по дате п/п, ₽", "Месяц закрыт (дата)", "Зафиксировано при закрытии, ₽",
               "База в записи Лены, ₽", "% в записи Лены, ₽", "Шт.", "Доплата в записи, ₽", "Удержания в записи, ₽",
               "Итог в записи Лены, ₽", "Проверка записи", "Запись Лены как есть", "Где запись"]
SAL_HEAD = 10      # строка шапки таблицы месяцев
SAL_RATES = (5, 7)  # строки периодов ставок
PRINCIPLES = ["не согласован", "месяц обязательства", "дата оплаты"]


def head_formula(title, f):
    """ARRAYFORMULA из строки 2 → в шапку: строка 1 — заголовок, ниже — те же формулы. Ссылки других листов (Платежи!B2:B) не трогаем."""
    inner = re.sub(r"(?<![!\w])([A-Z])2:\1(?![\w])", r"\g<1>1:\1", f[len("=ARRAYFORMULA("):-1])
    return f'=ARRAYFORMULA(IF(ROW(A1:A)=1,"{title}",{inner}))'


def salary_row(i, m, n):
    """Строка месяца. К выплате (B) — только при выбранном принципе базы и ставке периода; закрытый месяц — зафиксированная сумма."""
    r0, r1 = SAL_RATES
    rs, rp, ru = (f"$A${r0}:$A${r1}", f"$B${r0}:$B${r1}", f"$C${r0}:$C${r1}")
    return [date.fromisoformat(m),
            f'=IF(F{i}<>"",G{i},LET(s,MAXIFS({rs},{rs},"<="&A{i}),p,IF(s=0,"",INDEX({rp},MATCH(s,{rs},0))),u,IF(s=0,"",INDEX({ru},MATCH(s,{rs},0))),'
            f'b,IF($B$2="месяц обязательства",C{i},IF($B$2="дата оплаты",D{i},"")),'
            f'IF(b="","не рассчитано: принцип базы не согласован",'
            # «по дате оплаты» без дат у части оплат — не считаем: даты не придумываем
            f'IF(AND($B$2="дата оплаты",COUNTIFS(Платежи!B:B,"<>",Платежи!C:C,"",Платежи!F:F,"да")>0),"не рассчитано: у части оплат нет даты",'
            f'IF(OR(p="",u=""),"не рассчитано: нет ставки на этот месяц",'
            f'IF(ISNUMBER(b),b*p/100+N(J{i})*u-N(L{i}),"нет базы"))))))',
            f'=IFERROR(INDEX(Месяцы!D:D,MATCH(A{i},Месяцы!A:A,0)),"нет в «Месяцах»")',
            f'=SUMIFS(Платежи!D:D,Платежи!C:C,">="&A{i},Платежи!C:C,"<"&EDATE(A{i},1),Платежи!F:F,"да")',
            f'=SUMIFS(Банк!D:D,Банк!C:C,">="&A{i},Банк!C:C,"<"&EDATE(A{i},1))',
            None, None,
            *[n.get(k) for k in ("base", "pct", "pcs", "extra", "hold", "total")],
            f'=IF(M{i}="","записи нет",IF(I{i}="","% в записи не выписан",IF(I{i}+K{i}-L{i}=M{i},"арифметика сходится","не сходится на "&TEXT(I{i}+K{i}-L{i}-M{i},"#,##0"))))'
            f'&IF(AND(H{i}<>"",ISNUMBER(C{i})),IF(H{i}<>C{i}," · база у Лены ≠ реестру на "&TEXT(H{i}-C{i},"#,##0"),""),"")',
            n.get("text"), (n.get("file", "") + " " + n.get("cell", "")).strip() or None]


def salary_sheet(wb, st, today):
    """Лист «Зарплата» в той же книге (решение Ольги 29.10: один файл, один расчётный источник).
    Сверху: принцип базы и периоды ставок (вводит только Ольга; пусто — «не рассчитано», не ноль). Ниже текущий период
    (с HISTORY_FROM), под ним архив записей Лены. «К выплате» — второй столбец, без горизонтальной прокрутки.
    Базы две и подписаны: по месяцу обязательства («Реестр» B) и по дате оплаты («Платежи» C) — даты не придумываем.
    Новая ставка — новая строка периода с месяца начала; закрытый месяц показывает зафиксированную сумму."""
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.worksheet.datavalidation import DataValidation
    from openpyxl.formatting.rule import FormulaRule
    ws = wb.create_sheet("Зарплата")
    ws.append(["Зарплата Лены — расчёт по месяцам (только данные Лены)"])
    ws.append(["Принцип базы", "не согласован", "← выбирает Ольга: «месяц обязательства» (оплата сентябрьского долга в октябре меняет сентябрь) "
               "или «дата оплаты» (только оплаты с датой). Пока «не согласован» — к выплате не считается."])
    ws.append(['="Оплат по Лене без даты: "&TEXT(SUMIFS(Платежи!D:D,Платежи!C:C,"",Платежи!F:F,"да"),"#,##0")&" ₽ — по дате оплаты их не разложить, даты не придумываем. Пока дат нет, база «по дате оплаты» неполная."'])
    ws.append(["Периоды ставок: с месяца", "% от базы", "₽ за шт.", "Кто и когда согласовал"])
    for _ in range(SAL_RATES[1] - SAL_RATES[0] + 1):
        ws.append([])
    ws.append(["Новая ставка — новой строкой с месяца начала: прошлые периоды не меняются. Закрытый месяц (F) показывает зафиксированную сумму (G). "
               "Ставки, принцип и закрытие вводит только Ольга. Лист защищён, но НЕ скрыт: его видит каждый, кому открыт файл."])
    ws.append([])
    ws.append(SALARY_COLS)
    notes = {n["month"]: n for n in st.get("salaryNotes", [])}
    first = min([r["month"] for r in st["rows"]] + list(notes) or [today.replace(day=1).isoformat()])
    end = add_month(today.replace(day=1).isoformat(), 2)
    cur_from = max(min(HISTORY_FROM, end), first)
    i = SAL_HEAD + 1
    ws.append([f"Текущий период (с {month_label(cur_from)})"])
    sec = [i]
    i += 1
    m = cur_from
    while m <= end:
        ws.append(salary_row(i, m, notes.get(m, {})))
        m, i = add_month(m, 1), i + 1
    cur_rows = (sec[0] + 1, i - 1)
    ws.append(["Итого текущий период", None, f"=SUM(C{cur_rows[0]}:C{cur_rows[1]})", f"=SUM(D{cur_rows[0]}:D{cur_rows[1]})", f"=SUM(E{cur_rows[0]}:E{cur_rows[1]})"])
    i += 1
    ws.append([])
    i += 1
    if first < cur_from:
        ws.append([f"Архив: записи Лены {month_label(first)} – {month_label(add_month(cur_from, -1))} (как в её файлах, не пересчитываются)"])
        sec.append(i)
        i += 1
        m = first
        while m < cur_from:
            ws.append(salary_row(i, m, notes.get(m, {})))
            m, i = add_month(m, 1), i + 1
    # вид: заголовок не обрезается, «К выплате» рядом с месяцем, текущий месяц выделен
    ws.merge_cells("A1:G1")
    ws["A1"].font = Font(bold=True, size=15)
    for r in (2, 3, 8):
        ws.merge_cells(f"C{r}:P{r}" if r == 2 else f"A{r}:P{r}")
        ws[f"{'C' if r == 2 else 'A'}{r}"].alignment = Alignment(wrap_text=True, vertical="top")
    dv = DataValidation(type="list", formula1='"' + ",".join(PRINCIPLES) + '"', allow_blank=False)
    ws.add_data_validation(dv)
    dv.add("B2")
    for c in ("A2", "A4", "B4", "C4", "D4", f"A{SAL_HEAD}") + tuple(f"{chr(65 + k)}{SAL_HEAD}" for k in range(len(SALARY_COLS))):
        ws[c].font = Font(bold=True)
    for r in sec:
        ws[f"A{r}"].font = Font(bold=True, size=13)
        ws.merge_cells(f"A{r}:J{r}")
    cf = FormulaRule(formula=[f"$A{cur_rows[0]}=DATE(YEAR(TODAY()),MONTH(TODAY()),1)"], fill=PatternFill("solid", fgColor="FAEBC0"), font=Font(bold=True))
    ws.conditional_formatting.add(f"A{cur_rows[0]}:P{cur_rows[1]}", cf)  # текущий месяц выделен
    widths = [12, 30, 16, 18, 14, 12, 14, 13, 11, 6, 11, 11, 12, 34, 40, 22]
    for k, w in enumerate(widths):
        ws.column_dimensions[chr(65 + k)].width = w
    for row in ws.iter_rows(min_row=SAL_HEAD + 1, max_row=i):
        row[0].number_format = "MMM YYYY"
        for c in row[1:13]:
            c.number_format = "#,##0"
    return ws


def month_label(m):
    return f"{['янв.', 'февр.', 'мар.', 'апр.', 'мая', 'июн.', 'июл.', 'авг.', 'сент.', 'окт.', 'нояб.', 'дек.'][int(m[5:7]) - 1]} {m[:4]}"


def journal_line(status, what):
    """Строка журнала запусков: проверяющий видит каждый запуск, а не только последний."""
    path = os.path.join(DIR, "zhurnal-zapuskov.md")
    new = not os.path.exists(path)
    with open(path, "a", encoding="utf-8") as fh:
        if new:
            fh.write("# Журнал запусков реестра гарантов Лены\n\n| Время МСК | Как | Итог | Файл Лены (sha, дата) |\n|---|---|---|---|\n")
        how = "по расписанию" if status.get("scheduled") else "вручную"
        fh.write(f"| {status['lastAttempt'][:16].replace('T', ' ')} | {how} | {what} | {status.get('sha', '')} {status.get('sourceMtime', '')[:16]} |\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["sync", "fail", "xlsx", "salary", "sheet-json"])
    ap.add_argument("files", nargs="*")
    ap.add_argument("--mtime", default="")
    ap.add_argument("--bank", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--today", default="")
    ap.add_argument("--since", default="", help="xlsx: строки с этого месяца ГГГГ-ММ (плюс открытые старые)")
    ap.add_argument("--scheduled", action="store_true", help="запуск по расписанию (пишется в import-status)")
    a = ap.parse_args()
    today = date.fromisoformat(a.today) if a.today else datetime.now(MSK).date()
    os.makedirs(DIR, exist_ok=True)
    stp = os.path.join(DIR, "import-status.json")
    if a.cmd in ("sync", "fail"):
        status = json.load(open(stp, encoding="utf-8")) if os.path.exists(stp) else {}
        status["lastAttempt"] = now()
        status["scheduled"] = a.scheduled
        if a.cmd == "fail":  # запуск не состоялся (ПК выключен, файла нет): реестр и снимок не трогаем
            status.update(ok=False, lastError=" ".join(a.files) or "причина не указана")
            save(stp, status)
            journal_line(status, "сбой: " + status["lastError"])
            print("записан сбой, прежний реестр сохранён:", status["lastError"])
            return
        if (load_state().get("source") or {}).get("mode") == "google":
            # после перехода ввод только в Google-таблице; Excel Лены — архив, импорт затёр бы правки менеджера
            status.update(ok=False, lastError="источник ввода — Google-таблица, импорт Excel отключён (архив)")
            save(stp, status)
            journal_line(status, "отказ: " + status["lastError"])
            print("ОТКАЗ:", status["lastError"], file=sys.stderr)
            sys.exit(3)
        try:
            st, changed = sync(a.files, a.mtime, a.bank, today)
        except Exception as e:  # прежние реестр и снимок не трогаем
            status.update(ok=False, lastError=f"{type(e).__name__}: {e}")
            save(stp, status)
            journal_line(status, "сбой: " + status["lastError"])
            print("ОШИБКА, прежний реестр сохранён:", status["lastError"], file=sys.stderr)
            sys.exit(1)
        save(os.path.join(DIR, "reestr.json"), st)
        snap = snapshot(st, today)
        save(os.path.join(DIR, "snapshot.json"), snap)
        digest = hashlib.sha256()
        for f in a.files:
            with open(f, "rb") as fh:
                digest.update(fh.read())
        status.update(ok=True, lastOk=status["lastAttempt"], lastError="", rows=len(snap["rows"]), live=True, format="reestr",
                      sha=digest.hexdigest()[:12], files=[os.path.basename(f) for f in a.files],
                      sourceMtime=st.get("sourceMtime", ""), changed=changed)
        save(stp, status)
        journal_line(status, f"ок, строк в пульт {len(snap['rows'])}, изменения: {'да' if changed else 'нет'}")
        print(f"реестр: {len(st['rows'])} строк, платежей {len(st['payments'])}, в пульт {len(snap['rows'])}, изменения: {'да' if changed else 'нет'}")
    elif a.cmd == "sheet-json":
        # книга xlsx → {вкладка: строки} для записи в Google-таблицу коннектором Таблиц (update_values, USER_ENTERED)
        import openpyxl
        wb = openpyxl.load_workbook(a.files[0])
        out = {ws.title: [[(c.isoformat()[:10] if isinstance(c, (date, datetime)) else ru_formula(c)) for c in row]
                          for row in ws.iter_rows(values_only=True)] for ws in wb.worksheets}
        save(a.out, out)
        print("записано", a.out, {k: len(v) for k, v in out.items()})
    else:
        st = load_state()
        if not st["rows"]:
            sys.exit("реестр пуст: сначала sync")
        if a.cmd == "salary":  # 29.10: второй книги зарплаты нет — один расчётный источник
            sys.exit("отключено: зарплата — лист «Зарплата» единой Google-таблицы (xlsx собирает его вместе с реестром)")
        build_xlsx(st, a.out, today, a.since)
        print("записано", a.out)


if __name__ == "__main__":
    main()
