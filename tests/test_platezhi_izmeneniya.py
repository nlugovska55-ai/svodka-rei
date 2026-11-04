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

    def test_empty_writes_no_changes(self):
        until = dt.datetime(2026, 11, 5, 9, 12, tzinfo=MSK)
        with tempfile.TemporaryDirectory() as d:
            items, other = I.changes(self.state(), until - dt.timedelta(hours=24), until)
            I.write(items, other, until - dt.timedelta(hours=24), until, d)
            self.assertIn("Изменений нет.", open(os.path.join(d, "2026-11-05.md")).read())
            self.assertEqual(I.latest(d, "2026-11-05")["items"], [])
            self.assertIsNone(I.latest(d, "2026-11-04"))


if __name__ == "__main__":
    unittest.main()
