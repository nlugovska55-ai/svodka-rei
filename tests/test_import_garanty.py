"""Гаранты Лены: импорт таблицы (только чтение) → снимок → блок пульта. Запуск: python3 tests/test_import_garanty.py"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_svodka", ROOT / "scripts/build_svodka.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)

# Шаблон Лены «Оплаты и зарплата — <месяц>»: сумма в одной из колонок Прогноз / Гарант / Оплачено
TEMPLATE = '''Оплаты — октябрь 2026,,,,,,,,,,,
"Сумма живёт в одной из трёх колонок",,,,,,,,,,,
,,,,,ИТОГО:,"44,000 ₽",-,"25,000 ₽",,,
№,Менеджер,Клиент,Услуга,Договор,Прил.,"Прогноз, ₽","Гарант, ₽","Оплачено, ₽",Дата план.,Дата оплаты,Комментарий
1,Лена,ЧЗМЭК,ДС ТР ТС,1240 ЛЕ,5,,,"25,000 ₽",,15.10.2026,
2,Лена,ООО КВАНТ,ДС ТР ТС,1364 ЛЕ,,,"12,000 ₽",,30.10.2026,,ждём п/п
3,,ооо спб схм,отрицательное решение,,,"32,000 ₽",,,,,
,,,,,,,,,,,итого 25 000
'''
# Вложение «октябрь 2026»: ожидаемая и пришедшая суммы, сроков нет
OCT = '''наименование клиента ,услуга ,номер договора ,ожидапемая сумма ,сумма пришедщая
ооо укс групп,сс гостр ,1051 ЛЕ ГОСТ ПРИЛ 8,50000,
ооо укс групп,сс гостр ,1051 ЛЕ ГОСТ ПРИЛ 9,50000,
ооо эрмаш,сс тр тс 012,1379 ЛЕ ТРТС прил 2,"600,000",
ООО ЧЗМЭК,ДСТРТС,1240 ле дс прил 7,,15000
,,Итого ,"700,000","15,000"
'''


class Garanty(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.env = {**os.environ, "GARANTY_DIR": str(self.dir / "g")}

    def imp(self, text, *args, name="t.csv"):
        f = self.dir / name
        f.write_text(text, encoding="utf-8")
        return subprocess.run([sys.executable, "-I", str(ROOT / "scripts/import_garanty.py"), str(f), *args], env=self.env, capture_output=True, text=True)

    def snap(self):
        return json.loads((self.dir / "g/snapshot.json").read_text(encoding="utf-8"))

    def status(self):
        return json.loads((self.dir / "g/import-status.json").read_text(encoding="utf-8"))

    def test_template_stages_and_dates(self):
        r = self.imp(TEMPLATE, "--live", "--url", "https://docs.google.com/spreadsheets/d/X/edit", "--gid", "7")
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = {x["client"]: x for x in self.snap()["rows"]}
        self.assertEqual(len(rows), 3, "строка ИТОГО и пустые не попадают")
        self.assertEqual((rows["ЧЗМЭК"]["stage"], rows["ЧЗМЭК"]["received"], rows["ЧЗМЭК"]["datePaid"]), ("paid", 25000.0, "2026-10-15"))
        self.assertEqual((rows["ООО КВАНТ"]["stage"], rows["ООО КВАНТ"]["amount"], rows["ООО КВАНТ"]["date"]), ("guarantee", 12000.0, "2026-10-30"))
        self.assertEqual(rows["ооо спб схм"]["stage"], "forecast")
        self.assertIsNone(rows["ооо спб схм"]["date"], "нет срока — None, не 0 и не выдумка")
        self.assertIsNone(rows["ООО КВАНТ"]["received"])
        self.assertTrue(self.status()["live"])

    def test_attachment_format(self):
        r = self.imp(OCT)
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = self.snap()["rows"]
        self.assertEqual(sum(x["amount"] for x in rows if x["stage"] == "expected"), 700000)
        self.assertEqual(sum(x["received"] or 0 for x in rows), 15000)
        self.assertFalse(self.status()["live"], "без --live источник не объявляется живой таблицей")

    def test_failure_keeps_previous_snapshot(self):
        self.assertEqual(self.imp(OCT).returncode, 0)
        before = (self.dir / "g/snapshot.json").read_bytes()
        r = self.imp("что-то,не то\n1,2\n", name="bad.csv")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual((self.dir / "g/snapshot.json").read_bytes(), before, "сбой не затирает прежний снимок")
        st = self.status()
        self.assertFalse(st["ok"])
        self.assertTrue(st["lastError"])
        self.assertTrue(st["lastOk"] < st["lastAttempt"] or st["lastOk"] <= st["lastAttempt"])

    def test_bank_one_payment_split_once_and_tasks(self):
        self.assertEqual(self.imp(OCT).returncode, 0)
        bank = {"days": [{"account": "Альфа", "date": "2026-10-28", "inItems": [{"who": "ООО «УКС ГРУП»", "amount": 100000, "basis": "п/п 4866"}]}]}
        tasks = [{"id": "erm", "client": "Эр-Маш", "title": "Эр-Маш: договор 1379", "detail": "", "amount": 1200000, "mgr": "Лена", "next": "запросить договор", "waiting": "Лена"}]
        builder.GARANTY_DIR = str(self.dir / "g")
        g = builder.garanty(tasks, bank)
        uks = [x for x in g["rows"] if "укс" in x["client"]]
        self.assertEqual([x["bankSum"] for x in uks], [50000.0, 50000.0], "одна п/п на две строки делится, не удваивается")
        self.assertEqual([x["remain"] for x in uks], [0.0, 0.0])
        erm = next(x for x in g["rows"] if "эрмаш" in x["client"])
        self.assertEqual(erm["taskIds"], ["erm"])
        self.assertIsNone(erm["bankSum"], "нет в выписках — не 0, а «не подтверждено»")
        self.assertFalse(g["live"])

    def test_no_snapshot_means_not_connected(self):
        builder.GARANTY_DIR = str(self.dir / "none")
        g = builder.garanty([], None)
        self.assertFalse(g["connected"])
        self.assertEqual(g["rows"], [])


if __name__ == "__main__":
    unittest.main(verbosity=1)
