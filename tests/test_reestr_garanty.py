"""Реестр гарантов и оплат Лены: файлы Лены → реестр с постоянными ID. Запуск: python3 tests/test_reestr_garanty.py"""
import json
import os
from datetime import date, datetime
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/reestr_garanty.py"
HEAD = ["наименование клиента ", "услуга ", "номер договора ", "ожидапемая сумма ", "сумма пришедщая ", "себестоимсость "]


def lena_file(path, rows, total=None):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(HEAD)
    for r in rows:
        ws.append(list(r))
    if total:
        ws.append([])
        ws.append([None, None, "итого "] + list(total))
    wb.save(path)
    return str(path)


class Reestr(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.state = self.dir / "state"
        self.env = dict(os.environ, GARANTY_DIR=str(self.state))

    def run_sync(self, *files, today="2026-10-29", bank=None, ok=True):
        cmd = [sys.executable, "-I", str(SCRIPT), "sync", *files, "--today", today]
        if bank:
            cmd += ["--bank", bank]
        p = subprocess.run(cmd, env=self.env, capture_output=True, text=True)
        self.assertEqual(p.returncode == 0, ok, p.stderr + p.stdout)
        return p

    def st(self):
        return json.loads((self.state / "reestr.json").read_text(encoding="utf-8"))

    def snap(self):
        return json.loads((self.state / "snapshot.json").read_text(encoding="utf-8"))

    def test_rerun_is_idempotent_and_ids_stable(self):
        f = lena_file(self.dir / "октябрь 2026.xlsx", [("ооо эрмаш", "сс тр тс 012", "1379 ЛЕ ТРТС прил 2", 600000, None),
                                                      ("ооо укс групп", "сс гостр", "1051 ЛЕ ГОСТ ПРИЛ 8", None, 50000)])
        self.run_sync(f)
        first = self.st()
        p = self.run_sync(f)
        self.assertIn("изменения: нет", p.stdout)
        second = self.st()
        self.assertEqual(first["rows"], second["rows"])
        self.assertEqual(first["payments"], second["payments"])
        self.assertEqual([r["id"] for r in second["rows"]], ["G-0001", "G-0002"])

    def test_months_dec_to_jan(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import reestr_garanty as g
        self.assertEqual(g.months_to_show(date(2026, 12, 31)), ["2026-12-01", "2027-01-01"])
        self.assertEqual(g.months_to_show(date(2026, 10, 29)), ["2026-10-01", "2026-11-01"])
        self.assertEqual(g.month_of("май 2026  .xlsx"), "2026-05-01")
        self.assertEqual(g.month_of("март 2026.xlsx"), "2026-03-01")
        self.assertEqual(g.month_of("августа 2026.xlsx"), "2026-08-01")
        f = lena_file(self.dir / "декабрь 2026.xlsx", [("ооо квант", "дс", "1365 ле дс", 12000, None)])
        self.run_sync(f, today="2026-12-20")
        self.assertEqual(self.snap()["months"], {"current": "2026-12-01", "next": "2027-01-01"})
        self.assertIn("2027-01-01", self.st()["months"])

    def test_source_failure_keeps_previous(self):
        f = lena_file(self.dir / "октябрь 2026.xlsx", [("ооо эрмаш", "сс", "1379 прил 2", 600000, None)])
        self.run_sync(f)
        before = (self.state / "snapshot.json").read_text(encoding="utf-8")
        bad = self.dir / "ноябрь 2026.xlsx"
        wb = openpyxl.Workbook()
        wb.active.append(["что-то другое"])
        wb.save(bad)
        self.run_sync(str(bad), ok=False)
        self.assertEqual((self.state / "snapshot.json").read_text(encoding="utf-8"), before)
        status = json.loads((self.state / "import-status.json").read_text(encoding="utf-8"))
        self.assertFalse(status["ok"])
        self.assertTrue(status["lastOk"])
        self.assertIn("шапка", status["lastError"])

    def test_scheduled_fail_and_rerun_are_journaled(self):
        # расписание: успешный запуск, повтор без изменений, затем ПК недоступен — реестр не тронут, всё в журнале
        f = lena_file(self.dir / "октябрь 2026.xlsx", [("ооо эрмаш", "сс", "1379 прил 2", 600000, None)])
        for _ in range(2):
            p = subprocess.run([sys.executable, "-I", str(SCRIPT), "sync", f, "--today", "2026-10-29", "--scheduled"], env=self.env, capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
        before = (self.state / "snapshot.json").read_text(encoding="utf-8")
        p = subprocess.run([sys.executable, "-I", str(SCRIPT), "fail", "ПК недоступен", "--scheduled"], env=self.env, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual((self.state / "snapshot.json").read_text(encoding="utf-8"), before)
        status = json.loads((self.state / "import-status.json").read_text(encoding="utf-8"))
        self.assertEqual((status["ok"], status["scheduled"], status["lastError"]), (False, True, "ПК недоступен"))
        self.assertTrue(status["lastOk"])
        log = (self.state / "zhurnal-zapuskov.md").read_text(encoding="utf-8").splitlines()[4:]
        self.assertEqual(len(log), 3)
        self.assertIn("изменения: да", log[0])
        self.assertIn("изменения: нет", log[1])
        self.assertIn("сбой: ПК недоступен", log[2])
        self.assertTrue(all("по расписанию" in l for l in log))

    def test_both_columns_is_question_not_partial(self):
        f = lena_file(self.dir / "октябрь 2026.xlsx", [("ооо электростнадртприбор", "сс сил 2", "689 ле сенйс прил 19", 100000, 12000)])
        self.run_sync(f)
        st = self.st()
        self.assertEqual(st["payments"][0]["count"], "вопрос")
        row = self.snap()["rows"][0]
        self.assertEqual(row["remainReg"], 100000)
        self.assertIn("вопрос", row["flags"])
        self.assertIn("частичная оплата или другой ввод", row["note"])

    def test_partial_payment_and_move_to_paid_keep_id(self):
        f = self.dir / "октябрь 2026.xlsx"
        lena_file(f, [("ооо эрмаш", "сс тр тс 012", "1379 ЛЕ ТРТС прил 2", 600000, None)])
        self.run_sync(str(f))
        lena_file(f, [("ооо эрмаш", "сс тр тс 012", "1379 ЛЕ ТРТС прил 2", None, 600000)])
        self.run_sync(str(f))
        st = self.st()
        self.assertEqual(len(st["rows"]), 1)
        self.assertEqual(st["rows"][0]["id"], "G-0001")
        self.assertEqual(self.snap()["rows"][0]["remainReg"], 0)
        # банк подтвердил 250 000 из 600 000: остаток по банку не меньше Лены, строка та же
        bank = self.dir / "svodka.json"
        bank.write_text(json.dumps({"bank": {"days": [{"account": "Альфа", "date": "2026-10-30", "inItems": [{"who": "ООО ЭРМАШ", "amount": 250000, "basis": "п/п 1"}]}]}}, ensure_ascii=False), encoding="utf-8")
        self.run_sync(str(f), bank=str(bank))
        st = self.st()
        b = [p for p in st["payments"] if p["kind"] == "банк"]
        self.assertEqual(b, [])  # 250 000 не равно ни строке, ни сумме строк — не притягиваем
        bank.write_text(json.dumps({"bank": {"days": [{"account": "Альфа", "date": "2026-10-30", "inItems": [{"who": "ООО ЭРМАШ", "amount": 600000, "basis": "п/п 1"}]}]}}, ensure_ascii=False), encoding="utf-8")
        self.run_sync(str(f), bank=str(bank))
        self.run_sync(str(f), bank=str(bank))
        b = [p for p in self.st()["payments"] if p["kind"] == "банк"]
        self.assertEqual(len(b), 1)
        self.assertEqual(self.snap()["rows"][0]["flags"], ["нет менеджера"])

    def test_carry_over_without_duplicate(self):
        oct_ = lena_file(self.dir / "октябрь 2026.xlsx", [("ооо в2 лаб", "сс промбезопасность", "901 ЛЕ ДС ПРИЛ 14", 165000, None)])
        nov = lena_file(self.dir / "ноябрь 2026.xlsx", [("ооо в 2 лаб", "сс промбезопасность", "901 ле дс прил 14", 165000, None),
                                                       ("ооо квант", "дс тр тс", "1365 ле дс", 12000, None)])
        self.run_sync(oct_, nov, today="2026-11-03")
        st = self.st()
        self.assertEqual(len(st["rows"]), 2)
        self.assertEqual(st["rows"][0]["seen"], ["2026-10-01", "2026-11-01"])
        self.assertEqual(st["rows"][0]["month"], "2026-10-01")
        self.assertTrue(any("не дублируем" in j["msg"] for j in st["journal"]))

    def test_manual_total_mismatch_logged_rows_not_replaced(self):
        f = lena_file(self.dir / "октябрь 2026.xlsx", [("ооо квант", "дс", "1365 ле дс", 12000, None),
                                                      ("ооо чзмэк", "дс", "1240 ле дс прил 7", None, 15000)], total=(1240000, 1200600))
        self.run_sync(f)
        mig = self.st()["migration"][0]
        self.assertEqual((mig["sumD"], mig["sumE"]), (12000, 15000))
        self.assertTrue(any("D" in x and "разница" in x for x in mig["issues"]))
        self.assertEqual(sum(r["amount"] for r in self.snap()["rows"]), 27000)

    def test_row_gone_is_kept_with_flag(self):
        f = self.dir / "октябрь 2026.xlsx"
        lena_file(f, [("ооо квант", "дс", "1365 ле дс", 12000, None), ("ооо купер", "ик", "824 ле гост прил 48", 180000, None)])
        self.run_sync(str(f))
        lena_file(f, [("ооо квант", "дс", "1365 ле дс", 12000, None)])
        self.run_sync(str(f))
        rows = self.snap()["rows"]
        self.assertEqual(len(rows), 2)
        self.assertIn("пропала из файла Лены", rows[1]["flags"])

    def test_salary_one_file_formulas_rates_not_invented(self):
        f = lena_file(self.dir / "июль 2026.xlsx", [("ооо квант", "дс", "1365 ле дс", None, 578500)], total=(None, "578500= 57800 +3900 (13*300)=61700"))
        self.run_sync(f)
        self.assertNotIn("57800", (self.state / "snapshot.json").read_text(encoding="utf-8"))
        # второй книги нет: команда salary отказывает, зарплата — лист «Зарплата» той же книги
        r = subprocess.run([sys.executable, "-I", str(SCRIPT), "salary", "--out", str(self.dir / "z.xlsx")], env=self.env, capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse((self.dir / "z.xlsx").exists())
        out = self.dir / "r.xlsx"
        subprocess.run([sys.executable, "-I", str(SCRIPT), "xlsx", "--out", str(out), "--today", "2026-07-15"], env=self.env, check=True, capture_output=True)
        ws = openpyxl.load_workbook(out)["Зарплата"]
        self.assertEqual(ws["B2"].value, "по выписке")  # Ольга 30.10: 10% от пришедшего по выписке + 500 ₽ за макет ДС
        self.assertEqual([ws["B5"].value, ws["C5"].value], [10, 500])
        self.assertEqual(ws["A5"].value.date().isoformat(), "2026-10-01")  # с октября; сентябрь — «нет ставки», не ноль
        self.assertTrue(all(ws[f"{c}{r}"].value is None for c in "ABC" for r in (6, 7)))
        self.assertEqual(ws["A10"].value, "Месяц")
        self.assertTrue(ws["B10"].value.startswith("Начислено, ₽"))  # ключевая колонка рядом с месяцем
        self.assertIsNone(ws.freeze_panes)  # без закрепления: заголовок и пояснения объединены, не обрезаются
        self.assertIn("A1:G1", [str(x) for x in ws.merged_cells.ranges])
        cur = [r for r in range(12, ws.max_row + 1) if ws[f"A{r}"].value == datetime(2026, 7, 1)]
        self.assertEqual(len(cur), 1)
        r = cur[0]
        self.assertIn("Месяцы!D:D", ws[f"C{r}"].value)  # база по месяцу обязательства — из единого реестра
        self.assertIn("Платежи!C:C", ws[f"D{r}"].value)  # база по дате оплаты — отдельно, только оплаты с датой
        self.assertIn("принцип базы не согласован", ws[f"B{r}"].value)
        self.assertIn('$B$2="по выписке",E', ws[f"B{r}"].value)
        self.assertIn("нет данных: выписок за месяц нет", ws[f"B{r}"].value)  # пусто ≠ 0
        pay = [x for x in range(12, ws.max_row + 1) if str(ws[f"A{x}"].value).startswith("Выплаты Лене")]
        self.assertEqual(len(pay), 1)
        self.assertTrue(any(f"$F${pay[0] + 2}:" in str(ws[f"Q{x}"].value) for x in range(12, pay[0])))  # «Выплачено» ссылается на блок «Выплаты»  # база — пришло по выписке (колонка E)
        self.assertIsNone(ws[f"J{r}"].value)  # архив: «шт.» только из записи Лены
        self.assertIn("MAXIFS", ws[f"B{r}"].value)  # ставка берётся из периода, а не одна на всё прошлое
        self.assertIn("у части оплат нет даты", ws[f"B{r}"].value)
        self.assertTrue(ws[f"B{r}"].value.startswith('=IF(F'))  # закрытый месяц — зафиксированная сумма
        self.assertEqual(ws[f"O{r}"].value, "578500= 57800 +3900 (13*300)=61700")
        self.assertTrue(any('Реестр!W:W,"да"' in str(ws[f"J{x}"].value) for x in range(12, ws.max_row + 1)))  # текущий период: макеты ДС из «Реестра»
        self.assertEqual(ws["R10"].value, "Остаток к выплате, ₽")
        self.assertTrue(ws["Q10"].value.startswith("Выплачено Лене"))
        self.assertTrue(any(str(ws[f"R{x}"].value).startswith('=IF(NOT(ISNUMBER(B') for x in range(12, ws.max_row + 1)))  # остаток = к выплате − выплачено

    def test_workbook_has_views_and_salary_only_on_its_sheet(self):
        f = lena_file(self.dir / "октябрь 2026.xlsx", [("ооо квант", "дс", "1365 ле дс", 12000, None), ("ооо чзмэк", "дс", "1240 ле дс прил 7", None, 15000)],
                      total=(None, "12000=1200"))
        self.run_sync(f)
        out = self.dir / "r.xlsx"
        subprocess.run([sys.executable, "-I", str(SCRIPT), "xlsx", "--out", str(out), "--today", "2026-10-29"], env=self.env, check=True, capture_output=True)
        wb = openpyxl.load_workbook(out)
        self.assertEqual(wb.sheetnames, ["Этот месяц", "Следующий месяц", "Реестр", "Платежи", "Зарплата", "Как пользоваться", "Банк", "Месяцы", "Контроль", "Миграция", "Журнал"])
        self.assertIn("MONTH(TODAY())+1", wb["Следующий месяц"]["B1"].value)
        self.assertIn("FILTER", wb["Этот месяц"]["A7"].value)
        self.assertEqual([ws.title for ws in wb if ws.sheet_state == "visible"], ["Этот месяц", "Следующий месяц", "Реестр", "Платежи", "Зарплата", "Как пользоваться"])
        self.assertIn("🔴 Просрочено", wb["Реестр"]["M1"].value)
        self.assertIn("🔵 Прогноз (обсуждаем)", wb["Реестр"]["M1"].value)  # «Обсуждаем» — прогноз, не «Ждём оплату»
        # 30.10 (Ольга): один статус; отметка менеджера без выписки — приписка к ✅, не отдельный жёлтый статус
        self.assertIn("✅ Деньги пришли · сверяю с выпиской", wb["Реестр"]["M1"].value)
        self.assertNotIn("🟡", wb["Реестр"]["M1"].value)
        self.assertEqual(wb["Реестр"]["W1"].value, "Макет ДС делала Лена")
        self.assertNotIn("не сверен", wb["Реестр"]["M1"].value + wb["Этот месяц"]["A7"].value)
        self.assertEqual([wb["Этот месяц"][c].value for c in ("C3", "D3", "G6")], ["По данным менеджера, ₽", "По выписке, ₽", "Отметка, ₽"])
        self.assertIn("по данным менеджера", wb["Этот месяц"]["G6"].comment.text)
        how = wb["Как пользоваться"]
        self.assertEqual(how["A4"].value, "Главный лист")
        self.assertIn("🟠 пришла часть → ⚪ гарант, ждём оплату → 🔵 прогноз → ✅ деньги пришли", how["B4"].value)
        self.assertEqual(how["A15"].value, "Обещание и банк")
        self.assertIn("Статус (M) один", how["B15"].value)
        self.assertNotIn("не сверен", " ".join(str(c.value) for row in how.iter_rows() for c in row if c.value))
        self.assertIn("LEFT(Платежи!B2:B,6)", wb["Реестр"]["J1"].value)
        self.assertTrue(str(wb["Платежи"]["B2"].value).startswith("G-0002 · ооо чзмэк · 1240 ле дс прил 7 · 15000"))  # в «Платежах» выбирают «ID · клиент · договор · сумма»
        self.assertIn("ARRAYFORMULA", wb["Реестр"]["N1"].value)
        self.assertIn("Банк!", wb["Реестр"]["K1"].value)  # банк считается только из защищённой вкладки
        self.assertIsNone(wb["Реестр"]["J2"].value)  # формула в шапке: сортировка строк её не уносит
        self.assertTrue(any("G2:G" in str(v.sqref) and v.type == "decimal" for v in wb["Реестр"].data_validations.dataValidation))
        self.assertNotIn("банк", [c.value for c in wb["Платежи"]["E"]])
        # запись Лены о зарплате — только на листе «Зарплата», не в реестре и не в служебных вкладках
        text = " ".join(str(c.value) for ws in wb if ws.title != "Зарплата" for row in ws.iter_rows() for c in row if c.value)
        self.assertNotIn("12000=1200", text)
        self.assertIn("12000=1200", [c.value for c in wb["Зарплата"]["O"]])


if __name__ == "__main__":
    unittest.main()
