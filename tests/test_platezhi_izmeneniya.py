import datetime as dt
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import build_svodka as B  # noqa: E402
import izmeneniya_garanty as I  # noqa: E402

MSK = dt.timezone(dt.timedelta(hours=3))


class Platezhi(unittest.TestCase):
    def test_months_and_check(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "p.json")
            json.dump({"accepted": "02.11", "months": {"2026-12": 300, "2026-11": 100},
                       "rows": [{"month": "Ноябрь", "period": "A", "item": "x", "amount": 100},
                                {"month": "Декабрь", "period": "A", "item": "y", "amount": 200}],
                       "excluded": ["ГлавУпДК"], "personal": []}, open(p, "w"))
            B.PLATEZHI_JSON = p
            r = B.platezhi()
        self.assertEqual([m["ym"] for m in r["months"]], ["2026-11", "2026-12"])
        self.assertEqual(r["months"][0]["note"], "")
        self.assertIn("не равна", r["months"][1]["note"])  # 200 по строкам против 300 в итоге

    def test_no_file(self):
        B.PLATEZHI_JSON = "/nonexistent.json"
        self.assertIsNone(B.platezhi())


class Izmeneniya(unittest.TestCase):
    def state(self):
        return {"rows": [{"id": "G-0001", "client": "ооо эрмаш", "service": "дс", "amount": 600000, "stage": "Гарант"}],
                "journal": [{"at": "2026-11-03T10:00:00+03:00", "msg": "G-0001: stage 'Гарант' → 'Оплачено' (правка в таблице)"},
                            {"at": "2026-11-03T10:00:00+03:00", "msg": "G-0001: month '2026-10-01' → '2026-11-01' (правка в таблице)"},
                            {"at": "2026-11-01T10:00:00+03:00", "msg": "G-0001: старое"}]}

    def test_window_and_text(self):
        until = dt.datetime(2026, 11, 4, 9, 12, tzinfo=MSK)
        items, other = I.changes(self.state(), until - dt.timedelta(hours=24), until)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["text"], "Этап: Гарант → Оплачено (правка в таблице); Месяц: окт. 2026 → ноя. 2026 (правка в таблице)")
        self.assertEqual(other, [])

    def test_unquoted_amount_and_stage_words(self):
        self.assertEqual(I.human("amount 58000.0 → 29000.0 (правка в таблице)"), "Сумма: 58 000 → 29 000 (правка в таблице)")
        self.assertEqual(I.human("stage 'Гарант' → 'Пришло по Лене' (правка в таблице)"), "Этап: Гарант → Оплачено (отметил менеджер) (правка в таблице)")

    def test_empty_writes_no_changes(self):
        until = dt.datetime(2026, 11, 5, 9, 12, tzinfo=MSK)
        with tempfile.TemporaryDirectory() as d:
            items, other = I.changes(self.state(), until - dt.timedelta(hours=24), until)
            I.write(items, other, until - dt.timedelta(hours=24), until, d)
            self.assertIn("Изменений нет.", open(os.path.join(d, "2026-11-05.md")).read())
            self.assertEqual(I.latest(d, "2026-11-05")["items"], [])
            self.assertIsNone(I.latest(d, "2026-11-04"))


class Ozhidaem(unittest.TestCase):
    def test_guarantees_and_forecasts_split_paid_only_by_bank(self):
        row = lambda i, client, amount, stage, bank=None, month="2026-11-01": {"id": i, "client": client, "amount": amount, "stageReg": stage,
                                                                                "bankSum": bank, "month": month, "service": "дс", "doc": "", "src": ""}
        gar = {"importedAt": "2026-11-06T09:17", "rows": [
            row("G-1", "ооо эрмаш", 600000.0, "Пришло по Лене", 600000.0),
            row("G-2", "ип барсуков", 29000.0, "Пришло по Лене"),  # отметка менеджера без выписки — ждём
            row("G-3", "ооо морепласт", 12000.0, "Гарант"),
            row("G-4", "ооо отказ", 50000.0, "Отказ"),
            row("G-5", "ооо прошлый", 70000.0, "Гарант", month="2026-10-01"),
            row("G-6", "ооо новый", 15000.0, "Обсуждаем")]}
        tasks = [{"id": "m1", "cat": "pay", "client": "Мореплас", "amount": 24000, "waiting": "Лена", "due": "07.11.2026"},
                 {"id": "m2", "cat": "client", "client": "ООО «Виртекс» (Москва)", "amount": 175500, "waiting": "клиент", "due": "13.11.2026"},
                 {"id": "m3", "cat": "pay", "client": "Позже", "amount": 1000, "waiting": "клиент", "due": "05.12.2026"},
                 {"id": "pr-x-202611", "cat": "client", "client": "Икс", "amount": 5000, "waiting": "", "due": "10.11.2026"}]
        with tempfile.TemporaryDirectory() as d:
            plan = os.path.join(d, "plan.json")
            with open(plan, "w", encoding="utf-8") as fh:
                json.dump([{"id": "pr-emkabel-202611", "client": "эм кабель", "month": "2026-11", "n": 3, "potential": 360000, "expected": 180000, "first_end": "08.11.2026"},
                           {"id": "pr-y-202612", "client": "игрек", "month": "2026-12", "expected": 9000}], fh)
            old, B.PLAN_JSON = B.PLAN_JSON, plan
            try:
                o = B.ozhidaem(gar, tasks, dt.date(2026, 11, 6))
            finally:
                B.PLAN_JSON = old
        g, f = o["garanty"], o["prognoz"]
        self.assertEqual(g["total"], {"amount": 641000.0, "paid": 600000.0, "remain": 41000.0, "marked": 29000.0, "firms": 3})
        bars = next(x for x in g["firms"] if "барсуков" in x["client"])
        self.assertEqual(bars["rows"][0]["status"], "менеджер отметил оплату, по выписке нет")
        self.assertEqual(sorted(x["client"] for x in f["firms"]), ["ООО «Виртекс» (Москва)", "ооо новый", "эм кабель"])
        self.assertEqual(f["total"]["amount"], 15000 + 175500 + 180000)


if __name__ == "__main__":
    unittest.main()


class MonthHeader(unittest.TestCase):
    def test_header_by_client_column_and_missing_header_stops(self):
        import reestr_google as G
        rows = [[" ", "Клиент", "Услуга"], ["G-0001", "ооо а", "дс"]]
        self.assertEqual([n for n, _ in G.month_rows({"Октябрь 2026": rows}, "Октябрь 2026")], [2])
        with self.assertRaises(SystemExit):
            G.month_rows({"Октябрь 2026": [["", "", ""], ["G-0001", "ооо а"]]}, "Октябрь 2026")