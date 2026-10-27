"""Ежедневные выгрузки «Браузера» -> data/vygruzki.json для пульта (и надзорщик).

Папка дня на ПК: Загрузки\\Контроль\\выгрузки\\<ГГГГ-ММ-ДД>\\ (кладёт «Браузер» до 9:30).
Claude стейджит её в контейнер и запускает:
  python3 -I scripts/collect_vygruzki.py <папка дня> data/vygruzki.json

Что узнаём по имени файла:
  «ОСВ 62 …»            1С: долги покупателей (сальдо на конец, дебет)
  «Битрикс…»/«сделки…»  Битрикс24: заявки и сделки (формат уточняется по первой выгрузке)
  «выписка…»/«банк…»    банк за вчера (формат уточняется по первой выгрузке)
Надзорщик: если ожидаемой выгрузки нет или файл не читается — строка в alerts,
сайт показывает её в «Сегодня от вас».
"""
import json, os, re, sys
from datetime import datetime, timezone, timedelta

MSK = timezone(timedelta(hours=3))
EXPECTED = {
    "osv62": ("1С: долги покупателей (ОСВ 62)", re.compile(r"осв\s*62", re.I)),
    "bitrix": ("Битрикс24: заявки и сделки", re.compile(r"битрикс|bitrix|сделк|лид", re.I)),
    "bank": ("Банк: выписка за вчера", re.compile(r"выписк|банк|альфа|точка", re.I)),
}


def num(v):
    if v in (None, ""):
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    return float(str(v).replace("\xa0", "").replace(" ", "").replace(",", "."))


def client_name(raw):
    """«1/GEA WIEGAND GMBH 2/AM …» -> «GEA WIEGAND GMBH»; «7814… ООО "КОМПАНИЯ СТАНКЕ"» -> «ООО "КОМПАНИЯ СТАНКЕ"»."""
    s = str(raw).strip()
    m = re.match(r"^1/(.+?)\s+2/", s)
    if m:
        return m[1].strip()
    s = re.sub(r"^\d{10,12}\s+", "", s)
    s = re.sub(r"\s+р/с\s+\d{20}.*$", "", s, flags=re.I)
    return s.strip()


def read_osv62(path):
    import openpyxl
    ws = openpyxl.load_workbook(path, data_only=True, read_only=True).worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    start = next(i for i, r in enumerate(rows) if r and str(r[0]).strip() == "62") + 1
    debt = {}
    for r in rows[start:]:
        if not r or not r[0] or str(r[0]).strip().lower().startswith("итого"):
            continue
        dt, kt = num(r[5] if len(r) > 5 else 0), num(r[6] if len(r) > 6 else 0)
        name = client_name(r[0])
        debt[name] = debt.get(name, 0.0) + dt - kt
    out = [{"client": k, "debt": round(v, 2)} for k, v in debt.items() if v > 0.005]
    return sorted(out, key=lambda x: -x["debt"])


def main():
    day_dir, out = sys.argv[1], sys.argv[2]
    files = sorted(f for f in os.listdir(day_dir) if not f.startswith((".", "~$"))) if os.path.isdir(day_dir) else []
    now = datetime.now(MSK)
    res = {"collected": now.isoformat(timespec="minutes"), "dir": os.path.basename(day_dir.rstrip("/")),
           "sources": [], "alerts": [], "debtors": []}
    for key, (label, rx) in EXPECTED.items():
        hit = [f for f in files if rx.search(f)]
        src = {"key": key, "label": label, "file": hit[0] if hit else "", "ok": False}
        if not hit:
            res["alerts"].append(f"Нет выгрузки за {res['dir']}: {label}. Проверить, сделал ли её «Браузер».")
        else:
            path = os.path.join(day_dir, hit[0])
            src["at"] = datetime.fromtimestamp(os.path.getmtime(path), MSK).strftime("%d.%m %H:%M")
            try:
                if key == "osv62":
                    res["debtors"] = read_osv62(path)
                    src["rows"] = len(res["debtors"])
                src["ok"] = True
            except Exception as e:  # надзорщик: файл есть, но не читается
                res["alerts"].append(f"Выгрузка «{hit[0]}» не читается ({type(e).__name__}). Пересохранить в Excel.")
        res["sources"].append(src)
    json.dump(res, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"files {len(files)}, ok {sum(s['ok'] for s in res['sources'])}/{len(EXPECTED)}, "
          f"debtors {len(res['debtors'])}, alerts {len(res['alerts'])} -> {out}")


if __name__ == "__main__":
    main()
