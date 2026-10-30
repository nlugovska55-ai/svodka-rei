"""Данные для tests/pult-kt.mjs: настоящая цепочка «правка менеджера в таблице → сверка → сборка пульта».
Пишет в <out>: ok.json (после правки Лены), fail.json (сбой обмена после этого), none.json (снимка нет).
Запуск: python3 tests/lena_cases.py <out>"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_reestr_garanty import lena_file  # noqa: E402
from test_reestr_google import shown  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(sys.argv[1])
TMP = Path(tempfile.mkdtemp())
STATE = TMP / "state"
ENV = dict(os.environ, GARANTY_DIR=str(STATE), PROGNOZ_BASELINE=str(TMP / "baseline.json"),
           TREBOVANIYA=str(TMP / "no-treb.json"), AUTO_STATUS=str(TMP / "no-auto.json"))
TODAY = os.environ.get("TODAY", "")


def sh(*args, ok=0):
    p = subprocess.run([sys.executable, "-I", *map(str, args)], env=ENV, capture_output=True, text=True)
    if p.returncode != ok:
        sys.exit(f"{args}: {p.returncode}\n{p.stderr}{p.stdout}")
    return p.stdout


def sheet_cycle(sheet):
    path = TMP / "sheet.json"
    path.write_text(json.dumps(sheet, ensure_ascii=False), encoding="utf-8")
    sh(ROOT / "scripts/reestr_google.py", "pull", path, *(["--today", TODAY] if TODAY else []))
    sh(ROOT / "scripts/reestr_google.py", "plan", path, "--out", TMP / "w.json", *(["--today", TODAY] if TODAY else []))
    for w in json.loads((TMP / "w.json").read_text(encoding="utf-8")):
        tab, rng = w["range"].split("!")
        if tab == "Банк":
            continue
        start = int("".join(ch for ch in rng.split(":")[0] if ch.isdigit()))
        rows = sheet[tab]
        for k, vals in enumerate(w["values"]):
            while len(rows) < start + k:
                rows.append([])
            row = rows[start + k - 1]
            row.extend([""] * (len(vals) - len(row)))
            for c, v in enumerate(vals):
                if v is not None:
                    row[c] = shown(tab, c, v)
    path.write_text(json.dumps(sheet, ensure_ascii=False), encoding="utf-8")
    sh(ROOT / "scripts/reestr_google.py", "pull", path, *(["--today", TODAY] if TODAY else []))


def build(name):
    db = TMP / "db" / "tasks"
    db.mkdir(parents=True, exist_ok=True)
    (db / "t1.json").write_text(json.dumps({"id": "t1", "title": "Проверить гарант Эрмаш", "status": "open", "who": "ООО Эрмаш", "mgr": "Лена"}, ensure_ascii=False), encoding="utf-8")
    (TMP / "summary.json").write_text("[]", encoding="utf-8")
    sh(ROOT / "scripts/build_svodka.py", TMP / "db", TMP / "summary.json", OUT / name)


OUT.mkdir(parents=True, exist_ok=True)
f = lena_file(TMP / "октябрь 2026.xlsx", [("ооо эрмаш", "сс", "1379 прил 2", 600000, None), ("ооо укс групп", "сс гостр", "1051 прил 8", None, 50000)])
sh(ROOT / "scripts/reestr_garanty.py", "sync", f)
sheet = {"Реестр": [["ID"] + ["x"] * 18], "Платежи": [["ID"] + ["x"] * 9]}
sheet_cycle(sheet)
# Лена правит в таблице: сумма гаранта, срок оплаты через 7 дней, этап «Гарант»
n = next(i for i, r in enumerate(sheet["Реестр"]) if r[0] == "G-0001")
due = sh("-c", "from datetime import date, timedelta; d = date.today() + timedelta(days=7); print(d.strftime('%d.%m.%Y'))").strip()
sheet["Реестр"][n][6], sheet["Реестр"][n][7], sheet["Реестр"][n][8] = "555 000", "Гарант", due
sheet_cycle(sheet)
sh(ROOT / "scripts/reestr_garanty.py", "sync", f)  # повторный импорт Excel не затирает правку
build("ok.json")
sh(ROOT / "scripts/reestr_garanty.py", "fail", "ПК недоступен")
build("fail.json")
(STATE / "snapshot.json").unlink()
build("none.json")
shutil.rmtree(TMP)
print(json.dumps({"due": due}))
