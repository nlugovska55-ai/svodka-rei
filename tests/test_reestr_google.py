"""Google-таблица ↔ реестр: ввод менеджера не теряется, ID не дублируются, Excel не затирает правки.
Таблица имитируется: записи plan применяются к строкам (null пропускается, как в Sheets), чтение отдаёт
отформатированные значения («окт. 2026», «25 000», «15.11.2026»). Запуск: python3 tests/test_reestr_google.py"""
import json
import os
import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_reestr_garanty import lena_file  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SYNC = ROOT / "scripts/reestr_garanty.py"
GS = ROOT / "scripts/reestr_google.py"
MON = ["янв.", "февр.", "мар.", "апр.", "мая", "июн.", "июл.", "авг.", "сент.", "окт.", "нояб.", "дек."]


def shown(tab, col, v):
    """Как Google показывает записанное значение при чтении."""
    if v in (None, ""):
        return ""
    if tab == "Реестр" and col == 1 and isinstance(v, str) and v[:4].isdigit():
        return f"{MON[int(v[5:7]) - 1]} {v[:4]}"
    if (tab, col) in (("Реестр", 8), ("Реестр", 15), ("Платежи", 2)) and isinstance(v, str) and v[:4].isdigit():
        return f"{v[8:10]}.{v[5:7]}.{v[:4]}"
    if isinstance(v, float) or isinstance(v, int):
        return f"{v:,.0f}".replace(",", " ")
    return v


class Google(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.state = self.dir / "state"
        self.env = dict(os.environ, GARANTY_DIR=str(self.state))
        f = lena_file(self.dir / "октябрь 2026.xlsx", [("ооо эрмаш", "сс", "1379 прил 2", 600000, None),
                                                      ("ооо укс групп", "сс гостр", "1051 прил 8", None, 50000)])
        self.excel = f
        self.sh(SYNC, "sync", f, "--today", "2026-10-29")
        self.sheet = {"Реестр": [["ID"] + ["x"] * 18], "Платежи": [["ID"] + ["x"] * 9]}
        self.round()  # первая выгрузка в таблицу: строки Excel появляются в таблице

    def sh(self, script, *args, ok=0):
        p = subprocess.run([sys.executable, "-I", str(script), *args], env=self.env, capture_output=True, text=True)
        self.assertEqual(p.returncode, ok, p.stderr + p.stdout)
        return p.stdout

    def st(self):
        return json.loads((self.state / "reestr.json").read_text(encoding="utf-8"))

    def save_sheet(self):
        path = self.dir / "sheet.json"
        path.write_text(json.dumps(self.sheet, ensure_ascii=False), encoding="utf-8")
        return str(path)

    def pull(self, today="2026-10-29"):
        return self.sh(GS, "pull", self.save_sheet(), "--today", today)

    def plan(self, today="2026-10-29", apply=True):
        out = self.dir / "w.json"
        self.sh(GS, "plan", self.save_sheet(), "--out", str(out), "--today", today)
        writes = json.loads(out.read_text(encoding="utf-8"))
        if apply:
            for w in writes:
                tab, rng = w["range"].split("!")
                if tab == "Банк":
                    continue
                start = int("".join(ch for ch in rng.split(":")[0] if ch.isdigit()))
                rows = self.sheet[tab]
                for k, vals in enumerate(w["values"]):
                    n = start + k
                    while len(rows) < n:
                        rows.append([])
                    row = rows[n - 1]
                    row.extend([""] * (len(vals) - len(row)))
                    for c, v in enumerate(vals):
                        if v is not None:
                            row[c] = shown(tab, c, v)
        return writes

    def round(self, today="2026-10-29"):
        self.pull(today)
        self.plan(today)
        return self.pull(today)

    def reg(self):
        return {r[0]: r for r in self.sheet["Реестр"][1:] if r and r[0]}

    def new_row(self, client, doc, amount, due="", stage="Гарант"):
        row = [""] * 19
        row[3], row[5], row[6], row[7], row[8] = client, doc, f"{amount:,}".replace(",", " "), stage, due
        self.sheet["Реестр"].append(row)
        return len(self.sheet["Реестр"])

    HEAD = ["ID", "Клиент", "Услуга", "Ссылка на услугу", "Договор, прил.", "Сумма, ₽", "Статус"]

    def month_sheet(self, title, rows, spare=2):
        """Разметка с 02.11 13:35: шапка в 1-й строке, клиенты, запасные пустые строки, внизу блок «ИТОГИ»."""
        self.sheet = {k: v for k, v in self.sheet.items() if k not in ("Реестр", "Платежи")}
        self.sheet[title] = [list(self.HEAD)] + [list(r) for r in rows] + [[""]] * spare + [
            ["ИТОГИ МЕСЯЦА И ЗАРПЛАТА ЛЕНЫ"], ["Пришло по выписке, ₽", "1 434 000"], ["ID", "не строка данных"]]

    def test_month_sheets_are_the_input_from_02_11(self):
        st = {r["id"]: r for r in self.st()["rows"]}
        ermash = next(i for i, r in st.items() if "эрмаш" in r["client"])
        uks = next(i for i, r in st.items() if "укс" in r["client"])
        row = lambda rid, r, status, **kw: [rid, r["client"], r["service"], kw.get("link", ""), r["doc"], f"{r['amount']:,.0f}".replace(",", " "),
                                            status, kw.get("paid", ""), "", "", kw.get("ds", ""), kw.get("lena", ""), kw.get("q", "")]
        self.month_sheet("Октябрь 2026", [row(ermash, st[ermash], "⚪ Гарант"), row(uks, st[uks], "✅ Оплачено", paid="50 000", ds="1", lena="1")])
        oct_ = self.sheet["Октябрь 2026"]
        # перенос в ноябрь: Эрмаш в ноябре, статус меняет Лена; новая строка без ID (Лена вписала в пустую строку над итогами)
        self.month_sheet("Ноябрь 2026", [row(ermash, st[ermash], "⛔ Отказ", link="https://rus-ei.ru/x", q="клиент отказался"),
                                         ["", "ооо новый", "дс тр тс", "", "12 прил 1", "12 000", "🔵 Прогноз"]])
        self.sheet["Октябрь 2026"] = oct_
        out = self.pull("2026-11-02")
        self.assertIn("новых 1", out)
        r = {x["id"]: x for x in self.st()["rows"]}
        self.assertEqual((r[ermash]["stage"], r[ermash]["month"], r[ermash]["link"]), ("Отказ", "2026-11-01", "https://rus-ei.ru/x"))
        self.assertEqual(r[ermash]["question"], "клиент отказался")
        self.assertEqual((r[uks]["stage"], r[uks]["dsLena"]), ("Пришло по Лене", "1"))
        new = next(x for x in r.values() if x["client"] == "ооо новый")
        self.assertEqual((new["stage"], new["month"]), ("Обсуждаем", "2026-11-01"))
        out = self.dir / "w.json"
        self.sh(GS, "plan", self.save_sheet(), "--out", str(out), "--today", "2026-11-02")
        writes = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual([w for w in writes if not w["range"].startswith("Банк")], [{"range": "'Ноябрь 2026'!A3", "values": [[new["id"]]]}])
        self.sheet["Ноябрь 2026"][2][0] = new["id"]
        self.assertIn("правок 0, новых 0, проблем 0", self.pull("2026-11-02"))  # строки блока итогов не читаются как клиенты
        self.assertFalse(any(x.get("sheetGone") for x in self.st()["rows"]))
        # строку, которой нет в листах (например, из Excel Лены), plan дописывает в первую пустую строку над итогами
        del self.sheet["Октябрь 2026"][2]  # УКС (строка 3) пропал; пустые теперь строки 3 и 4, итоги с 5-й
        self.sh(GS, "plan", self.save_sheet(), "--out", str(out), "--today", "2026-11-02")
        writes = [w for w in json.loads(out.read_text(encoding="utf-8")) if not w["range"].startswith("Банк")]
        self.assertEqual(len(writes), 1)
        self.assertTrue(writes[0]["range"].startswith("'Октябрь 2026'!A3:N3"))
        self.assertEqual(writes[0]["values"][0][:2], [uks, st[uks]["client"]])
        # над итогами нет места → строка не пишется поверх итогов
        self.month_sheet("Октябрь 2026", [row(ermash, st[ermash], "⚪ Гарант")], spare=0)
        self.sh(GS, "plan", self.save_sheet(), "--out", str(out), "--today", "2026-11-02")
        self.assertFalse([w for w in json.loads(out.read_text(encoding="utf-8")) if w["range"].startswith("'Октябрь")])

    def test_first_export_then_repeat_pull_is_quiet(self):
        self.assertEqual(set(self.reg()), {"G-0001", "G-0002"})
        self.assertIn("правок 0, новых 0, проблем 0", self.pull())
        self.assertFalse([w for w in self.plan(apply=False) if not w["range"].startswith("Банк") and any(v is not None for r in w["values"] for v in r)])

    def test_new_row_without_id_gets_id_once(self):
        n = self.new_row("ооо новый клиент", "77 прил 1", 120000, "15.11.2026")
        out = self.pull()
        self.assertIn("новых 1", out)
        self.assertIn("новых 1", self.pull())  # ID ещё не записан в таблицу: тот же ID, не новый
        self.assertEqual([r["id"] for r in self.st()["rows"] if r["client"] == "ооо новый клиент"], ["G-0003"])
        self.plan()
        self.assertEqual(self.sheet["Реестр"][n - 1][0], "G-0003")
        self.assertIn("правок 0, новых 0, проблем 0", self.pull())
        r = next(r for r in self.st()["rows"] if r["id"] == "G-0003")
        self.assertEqual((r["amount"], r["due"], r["month"], r["stage"]), (120000.0, "2026-11-15", "2026-11-01", "Гарант"))

    def test_two_identical_new_rows_get_two_ids_and_sorting_keeps_them(self):
        a = self.new_row("ооо клинмаркет", "1201 прил 10", 12000)
        b = self.new_row("ооо клинмаркет", "1201 прил 10", 12000)
        self.round()
        self.assertEqual({self.sheet["Реестр"][a - 1][0], self.sheet["Реестр"][b - 1][0]}, {"G-0003", "G-0004"})
        body = self.sheet["Реестр"][1:]
        random.Random(7).shuffle(body)  # менеджер отсортировал таблицу
        self.sheet["Реестр"][1:] = body
        self.assertIn("правок 0, новых 0, проблем 0", self.pull())
        self.assertEqual(len(self.st()["rows"]), 4)
        # правка после сортировки попадает в свою строку по ID, а не по номеру
        n = next(i for i, r in enumerate(self.sheet["Реестр"]) if r[0] == "G-0004")
        self.sheet["Реестр"][n][8] = "20.11.2026"
        self.pull()
        self.assertEqual(next(r for r in self.st()["rows"] if r["id"] == "G-0004")["due"], "2026-11-20")
        self.assertIsNone(next(r for r in self.st()["rows"] if r["id"] == "G-0003")["due"])

    def test_wrong_amount_input_keeps_amount_and_reports(self):
        n = next(i for i, r in enumerate(self.sheet["Реестр"]) if r[0] == "G-0001")
        self.sheet["Реестр"][n][6] = "12к"  # прогон 29.10: ошибка ввода стирала сумму
        out = self.pull()
        self.assertIn("проблем 1", out)
        self.assertEqual(next(r for r in self.st()["rows"] if r["id"] == "G-0001")["amount"], 600000.0)
        self.assertTrue(any("не число" in j["msg"] for j in self.st()["journal"]))
        self.sheet["Реестр"][n][6] = "12 000"  # исправили — правка принята
        self.pull()
        self.assertEqual(next(r for r in self.st()["rows"] if r["id"] == "G-0001")["amount"], 12000.0)

    def test_manual_edit_survives_excel_reimport_and_is_logged(self):
        n = next(i for i, r in enumerate(self.sheet["Реестр"]) if r[0] == "G-0001")
        self.sheet["Реестр"][n][6] = "550 000"  # менеджер исправил сумму
        self.sheet["Реестр"][n][8] = "30.11.2026"
        self.pull()
        f = lena_file(self.dir / "октябрь 2026.xlsx", [("ооо эрмаш", "сс", "1379 прил 2", 600000, None),
                                                      ("ооо укс групп", "сс гостр", "1051 прил 8", None, 50000)])
        self.sh(SYNC, "sync", f, "--today", "2026-10-30")
        r = next(r for r in self.st()["rows"] if r["id"] == "G-0001")
        self.assertEqual((r["amount"], r["due"]), (550000.0, "2026-11-30"))
        self.assertTrue(any("оставлено значение таблицы" in j["msg"] for j in self.st()["journal"]))
        self.plan()
        self.assertEqual(self.sheet["Реестр"][n][6], "550 000")

    def test_partial_payment_from_sheet_and_ids_kept_after_reimport(self):
        row = ["", "G-0001 · ооо эрмаш · 1379 прил 2 · 600000", "28.10.2026", "250 000", "", "да", "", "", "", ""]
        self.sheet["Платежи"].append(row)
        self.round()
        st = self.st()
        p = next(p for p in st["payments"] if p["id"].startswith("P-M-"))
        self.assertEqual((p["rowId"], p["amount"], p["date"], p["count"]), ("G-0001", 250000.0, "2026-10-28", "да"))
        self.assertEqual(self.sheet["Платежи"][-1][0], p["id"])
        snap = json.loads((self.state / "snapshot.json").read_text(encoding="utf-8"))
        self.assertEqual(next(x for x in snap["rows"] if x["id"] == "G-0001")["remainReg"], 350000.0)
        self.sh(SYNC, "sync", self.excel, "--today", "2026-10-30")
        st2 = self.st()
        self.assertEqual([r["id"] for r in st2["rows"]], [r["id"] for r in st["rows"]])
        self.assertIn(p["id"], [x["id"] for x in st2["payments"]])

    def test_incomplete_payment_is_not_counted(self):
        self.sheet["Платежи"].append(["", "", "28.10.2026", "5 000", "", "да"])
        out = self.pull()
        self.assertIn("не выбран клиент", out)
        self.assertFalse([p for p in self.st()["payments"] if p["id"].startswith("P-M-")])

    def test_id_of_row_not_in_sheets_is_not_reused(self):
        # 06.11: в новую строку ноября вписали ID G-0221 строки Купера (заведена Claude по акту, в листах не было) — Купер не затирается
        st = self.st()
        ermash = next(r for r in st["rows"] if "эрмаш" in r["client"])
        kuper = dict(ermash, id="G-0099", client="ооо купер", ck="ооо купер", month="2026-09-01", seen=["2026-09-01"], exp={}, expPending={}, manual={})
        st["rows"].append(kuper)
        (self.state / "reestr.json").write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
        self.month_sheet("Ноябрь 2026", [[ermash["id"], ermash["client"], ermash["service"], "", ermash["doc"], "600 000", "⚪ Гарант"],
                                         ["G-0099", "ао по севермаш", "дс тр тс 010", "", "941 прил 29", "20 000", "⚪ Гарант"]])
        out = self.pull("2026-11-06")
        self.assertIn("новых 1", out)
        self.assertIn("«Ноябрь 2026», стр. 3: ID G-0099 занят строкой ооо купер", out)
        r = {x["id"]: x for x in self.st()["rows"]}
        self.assertEqual((r["G-0099"]["client"], r["G-0099"]["month"]), ("ооо купер", "2026-09-01"))
        new = next(x for x in r.values() if x["client"] == "ао по севермаш")
        self.assertNotEqual(new["id"], "G-0099")

    def test_month_change_dec_to_jan(self):
        self.new_row("ооо январь", "5 прил 1", 30000, "20.01.2027")
        self.round(today="2026-12-31")
        r = next(r for r in self.st()["rows"] if r["client"] == "ооо январь")
        self.assertEqual(r["month"], "2027-01-01")
        snap = json.loads((self.state / "snapshot.json").read_text(encoding="utf-8"))
        self.assertEqual(snap["months"], {"current": "2026-12-01", "next": "2027-01-01"})

    def test_write_not_landed_is_repeated_not_mistaken_for_edit(self):
        f = lena_file(self.dir / "октябрь 2026.xlsx", [("ооо эрмаш", "сс", "1379 прил 2", 700000, None),
                                                      ("ооо укс групп", "сс гостр", "1051 прил 8", None, 50000)])
        self.sh(SYNC, "sync", f, "--today", "2026-10-30")
        self.plan(apply=False)  # запись в таблицу не дошла
        self.pull()
        self.assertEqual(next(r for r in self.st()["rows"] if r["id"] == "G-0001")["amount"], 700000.0)
        self.plan()
        n = next(i for i, r in enumerate(self.sheet["Реестр"]) if r[0] == "G-0001")
        self.assertEqual(self.sheet["Реестр"][n][6], "700 000")
        self.assertIn("правок 0", self.pull())

    def test_deleted_row_is_kept_and_flagged(self):
        self.sheet["Реестр"] = [r for r in self.sheet["Реестр"] if r[0] != "G-0002"]
        out = self.pull()
        self.assertIn("G-0002: пропала из таблицы", out)
        self.assertIn("G-0002", [r["id"] for r in self.st()["rows"]])

    def test_google_mode_blocks_excel_and_keeps_registry(self):
        self.sh(GS, "mode", "google", ok=1)  # без даты и решения Ольги не переключается
        self.sh(GS, "mode", "google", "--since", "2026-11-01", "--by", "тест")
        before = (self.state / "reestr.json").read_text(encoding="utf-8")
        self.sh(SYNC, "sync", self.excel, "--today", "2026-11-02", ok=3)
        self.assertEqual((self.state / "reestr.json").read_text(encoding="utf-8"), before)
        status = json.loads((self.state / "import-status.json").read_text(encoding="utf-8"))
        self.assertIn("импорт Excel отключён", status["lastError"])
        n = next(i for i, r in enumerate(self.sheet["Реестр"]) if r[0] == "G-0001")
        self.sheet["Реестр"][n][7] = "Отказ"
        self.pull(today="2026-11-02")
        r = next(r for r in self.st()["rows"] if r["id"] == "G-0001")
        self.assertEqual(r["stage"], "Отказ")
        self.assertEqual(self.st()["source"]["legacyExcel"], "архив: Seafile\\boroday\\зарплатуленька, только чтение")


if __name__ == "__main__":
    unittest.main()
