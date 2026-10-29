"""Offline acceptance tests. Fixtures and outputs stay under work/test-runs."""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_svodka", ROOT / "scripts/build_svodka.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class BuilderAcceptance(unittest.TestCase):
    def setUp(self):
        scratch = ROOT.parent / "test-runs"
        scratch.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="builder-", dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tasks = self.root / "tasks"
        self.tasks.mkdir()
        self.summary = self.root / "summary.json"
        self.summary.write_text("[]", encoding="utf-8")
        self.out = self.root / "svodka.json"
        self.previous = b'{"previous":"verified snapshot"}'
        self.out.write_bytes(self.previous)
        self.addCleanup(patch.stopall)
        patch.object(builder, "LENA_CSV", str(self.root / "missing.csv")).start()
        patch.object(builder, "PROGNOZ_JSON", str(self.root / "missing.json")).start()
        patch.object(builder, "GARANTY_DIR", str(self.root / "no-garanty")).start()
        patch.object(builder, "AUTO_STATUS", str(self.root / "no-auto.json")).start()
        patch.object(builder, "BASELINE_JSON", str(self.root / "baseline.json")).start()
        patch.object(builder, "TREB_JSON", str(self.root / "no-treb.json")).start()

    def task(self, ident="one", **fields):
        row = {"id": ident, "title": "Synthetic task", "status": "open", **fields}
        path = self.tasks / (ident + ".json")
        path.write_text(json.dumps(row), encoding="utf-8")
        return path

    def build(self, *flags):
        argv = ["build_svodka.py", str(self.root), str(self.summary), str(self.out), *flags]
        with patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()):
            builder.main()
        return json.loads(self.out.read_text(encoding="utf-8"))

    def rejected(self):
        with self.assertRaises((ValueError, OSError)):
            self.build()
        self.assertEqual(self.previous, self.out.read_bytes())

    def test_all_active_statuses_remain_visible(self):
        for state in ("open", "waiting", "wait", "expert", "done", "closed", "handled"):
            self.task(state, status=state)
        data = self.build()
        self.assertEqual({"open", "waiting", "wait", "expert"}, {t["id"] for t in data["tasks"]})

    def test_empty_export_preserves_snapshot(self):
        self.rejected()

    def test_unknown_status_preserves_snapshot(self):
        self.task(status="unexpected")
        self.rejected()

    def test_non_object_task_preserves_snapshot(self):
        (self.tasks / "one.json").write_text("[]", encoding="utf-8")
        self.rejected()

    def test_active_task_without_title_preserves_snapshot(self):
        self.task(title="")
        self.rejected()

    def test_bad_amounts_never_become_zero(self):
        for value in ("not money", "NaN", "Infinity", True, "1e10000"):
            with self.subTest(value=value):
                self.task(amount=value)
                self.rejected()

    def test_locale_amount_and_source_files(self):
        source = self.task(amount="29 925,42", status="waiting")
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        data = self.build()
        self.assertEqual(29925.42, data["tasks"][0]["amount"])
        self.assertEqual(before, hashlib.sha256(source.read_bytes()).hexdigest())

    def test_invalid_summary_preserves_snapshot(self):
        self.task()
        self.summary.write_text('{"summary":"not a list"}', encoding="utf-8")
        self.rejected()

    def test_complete_export_with_only_closed_tasks_is_valid(self):
        self.task(status="done")
        self.assertEqual([], self.build()["tasks"])

    def test_limit_requires_confirmed_complete_export(self):
        for i in range(1000):
            self.task(str(i))
        self.rejected()
        self.assertEqual(1000, len(self.build("--allow-at-limit")["tasks"]))

    def test_failed_serialization_preserves_previous_file(self):
        with self.assertRaises(ValueError):
            builder.atomic_json(str(self.out), {"invalid": float("nan")})
        self.assertEqual(self.previous, self.out.read_bytes())
        self.assertEqual([], list(self.root.glob(".svodka-*.tmp")))

    def test_duplicate_id_preserves_snapshot(self):
        self.task()
        (self.tasks / "second.json").write_bytes((self.tasks / "one.json").read_bytes())
        self.rejected()

    def test_only_confirmed_comment_events_are_acknowledged(self):
        self.task()
        comments = self.root / "comments"
        comments.mkdir()
        for ident, status, action in (("applied", "done", "done"), ("pending", "new", "claude"), ("routed", "routed", "claude"), ("routed-done", "routed", "done")):
            (comments / (ident + ".json")).write_text(json.dumps({"eventId": ident, "status": status, "action": action}), encoding="utf-8")
        self.assertEqual(["applied", "routed"], self.build()["appliedEvents"])

    def test_reference_amount_kind_is_passed(self):
        self.task(amount="176103.19", amountKind="reference", amountNote="погашено")
        row = self.build()["tasks"][0]
        self.assertEqual(("reference", "погашено", 176103.19), (row["amountKind"], row["amountNote"], row["amount"]))

    def test_missing_buh_sources_are_named_not_zero(self):
        self.task()
        patch.object(builder, "SPISANIYA_JSON", str(self.root / "net.json")).start()
        patch.object(builder, "KONTROL_DIR", str(self.root / "net")).start()
        patch.object(builder, "BUH_PLAN_JSON", str(self.root / "net-plan.json")).start()
        patch.object(builder, "BANK_JSON", str(self.root / "net-bank.json")).start()
        data = self.build()
        self.assertIsNone(data["buh_plan"])
        names = {x["name"]: x for x in data["buh_sources"]}
        self.assertIsNone(data["spisaniya"])
        self.assertFalse(names["Реестр списаний (Бухгалтерия)"]["ok"])
        self.assertIn("не найден", names["Реестр списаний (Бухгалтерия)"]["note"])
        self.assertFalse(names["Выписки банков (Альфа, Точка)"]["ok"])

    def test_spisaniya_rows_keep_plan_paid_reference(self):
        self.task()
        sp = self.root / "sp.json"
        sp.write_text(json.dumps({"built": "28.10.2026", "rows": [
            {"date": "31.10.2026", "who": "РЭИ", "what": "ориентир", "amount": 10000, "kind": "reference"},
            {"date": "2026-10-28", "who": "РЭИ", "what": "ЕНП", "amount": 28314, "paid": True, "paidSrc": "fakty"}]}), encoding="utf-8")
        patch.object(builder, "SPISANIYA_JSON", str(sp)).start()
        rows = self.build()["spisaniya"]["rows"]
        self.assertEqual([("2026-10-28", "plan", True), ("2026-10-31", "reference", False)], [(r["date"], r["kind"], r["paid"]) for r in rows])

    def test_ens_confirmation_is_separate_from_olga_words(self):
        self.task()
        sp = self.root / "sp.json"
        sp.write_text(json.dumps({"built": "29.10.2026", "rows": [
            {"date": "2026-10-28", "what": "ЕНП", "amount": 28314, "paid": True, "ensConfirmed": True, "ensDate": "26.10.2026", "ensBasis": "п/п 39", "planDate": "2026-10-28"},
            {"date": "2026-10-28", "what": "ЕНП ИП", "amount": 101095.42, "paid": True, "state": "не подтверждено ФНС"}]}), encoding="utf-8")
        patch.object(builder, "SPISANIYA_JSON", str(sp)).start()
        rows = self.build()["spisaniya"]["rows"]
        self.assertEqual([("ФНС", "2026-10-26", "2026-10-28", "п/п 39"), ("", "", "", "")], [(r["confirmedBy"], r["paidOn"], r["plan"], r["basis"]) for r in rows])

    def test_bank_turnover_only_covered_days_and_balanced(self):
        self.task()
        bk = self.root / "bank.json"
        bk.write_text(json.dumps({"days": [{"account": "Альфа", "date": "2026-10-28", "open": 320421.48, "in": 260000, "out": 137001.16, "close": 443420.32}]}), encoding="utf-8")
        patch.object(builder, "BANK_JSON", str(bk)).start()
        data = self.build()
        src = {x["name"]: x for x in data["buh_sources"]}["Выписки банков"]
        self.assertEqual((True, True), (src["ok"], src["partial"]))
        self.assertIn("Прочие счета и дни не охвачены", src["note"])
        self.assertEqual(443420.32, data["bank"]["days"][0]["close"])

    def test_bank_turnover_that_does_not_add_up_preserves_snapshot(self):
        self.task()
        bk = self.root / "bank.json"
        bk.write_text(json.dumps({"days": [{"account": "Альфа", "date": "2026-10-28", "open": 1, "in": 2, "out": 0, "close": 5}]}), encoding="utf-8")
        patch.object(builder, "BANK_JSON", str(bk)).start()
        self.rejected()

    def test_spisaniya_row_without_date_preserves_snapshot(self):
        self.task()
        sp = self.root / "sp.json"
        sp.write_text(json.dumps({"rows": [{"what": "без даты", "amount": 1}]}), encoding="utf-8")
        patch.object(builder, "SPISANIYA_JSON", str(sp)).start()
        self.rejected()

    def test_delegated_task_stays_active_with_claude(self):
        self.task(status="open", waiting="Claude", delegated="edlg0001")
        row = self.build()["tasks"][0]
        self.assertEqual(("Claude", "edlg0001"), (row["waiting"], row["delegated"]))


    def test_automation_comes_from_status_file_and_registry(self):
        auto = self.root / "auto.json"
        auto.write_text(json.dumps([{"id": "auto-utro", "flag": "ok", "text": "Сводка выложена", "checked": "2026-10-29T19:20:18+03:00"}]), encoding="utf-8")
        gd = self.root / "gar"
        gd.mkdir()
        (gd / "import-status.json").write_text(json.dumps({"ok": False, "lastAttempt": "2026-10-29T09:15:00+03:00", "lastOk": "2026-10-28T15:08:46+03:00", "lastError": "ПК недоступен"}), encoding="utf-8")
        self.summary.write_text(json.dumps([{"section": "Автоматика", "text": "старая ручная копия", "checked": "2026-10-28"}, {"section": "Нужно от вас", "text": "x"}]), encoding="utf-8")
        self.task()
        with patch.object(builder, "AUTO_STATUS", str(auto)), patch.object(builder, "GARANTY_DIR", str(gd)):
            self.build()
        rows = [r for r in json.loads(self.out.read_text(encoding="utf-8"))["summary"] if r["section"] == "Автоматика"]
        self.assertEqual([r["id"] for r in rows], ["auto-utro", "auto-garanty"])
        g = rows[1]
        self.assertEqual(g["flag"], "срочно")
        self.assertIn("28.10 15:08", g["text"])
        self.assertIn("ПК недоступен", g["text"])
        self.assertIn("вручную", g["text"])
        self.assertEqual(g["checked"], "2026-10-29T09:15:00+03:00")

    def test_done_comment_without_result_is_shown_with_check_flag(self):
        cdir = self.root / "comments"
        cdir.mkdir(exist_ok=True)
        (cdir / "c1.json").write_text(json.dumps({"status": "done", "at": "2099-01-01T10:00:00+03:00", "text": "[сделано] ", "reply": "Закрыл задачу по письмам Лазаревой.", "resultCheck": "требует проверки"}), encoding="utf-8")
        (cdir / "c2.json").write_text(json.dumps({"status": "done", "at": "2099-01-01T11:00:00+03:00", "text": "[сделано] "}), encoding="utf-8")
        self.task()
        self.build()
        rep = json.loads(self.out.read_text(encoding="utf-8"))["replies"]
        self.assertEqual(len(rep), 2)
        self.assertEqual({r["resultCheck"] for r in rep}, {"требует проверки", ""})


    def test_superseded_mark_hidden_reopen_shown(self):
        cdir = self.root / "comments"
        cdir.mkdir(exist_ok=True)
        (cdir / "a.json").write_text(json.dumps({"status": "done", "action": "done", "at": "2099-01-01T10:00:00+03:00", "text": "[отменено позже]", "supersededBy": "e2"}), encoding="utf-8")
        (cdir / "b.json").write_text(json.dumps({"status": "done", "action": "reopen", "appliedTo": "x", "eventId": "e2", "at": "2099-01-01T10:03:00+03:00", "text": "[вернуть в работу] "}), encoding="utf-8")
        self.task()
        rep = self.build()["replies"]
        self.assertEqual([(r["eventId"], r["action"], r["appliedTo"]) for r in rep], [("e2", "reopen", "x")])


    def test_portal_orders_become_links(self):
        self.task("a", title="ЧЗМЭК: внести оплаты в заказы 2394166 и 2394163", next="сверить 2394166")
        self.task("b", title="Портал: заказ не указан", link="https://example.org/x")
        self.task("c", title="Телефон 82390757123 не заказ")
        t = {x["id"]: x for x in self.build()["tasks"]}
        self.assertEqual(t["a"]["orders"], ["2394166", "2394163"])
        self.assertEqual(t["a"]["link"], "http://95.84.156.117:3839/projects/orders/2394166/")
        self.assertEqual((t["b"]["orders"], t["b"]["link"]), ([], "https://example.org/x"))
        self.assertEqual(t["c"]["orders"], [])

    def test_ponomarenko_only_incoming_not_from_rei(self):
        bk = self.root / "bank.json"
        bk.write_text(json.dumps({"built": "t", "days": [
            {"account": "Точка ИП *3698", "date": "2026-10-28", "open": 10, "in": 96300 + 12000, "out": 14316.13, "close": 10 + 108300 - 14316.13,
             "inItems": [{"who": "РЭИ", "amount": 96300}, {"who": "АЗС Спецснаб", "amount": 12000}], "outItems": [{"who": "Дракон Чай", "amount": 14316.13}]},
            {"account": "Точка ИП *3698", "date": "2026-10-29", "open": 1, "in": 5, "out": 0, "close": 6, "inItems": [{"who": "ООО РусЭкоИнновации", "amount": 5}], "outItems": []},
            {"account": "Альфа РЭИ *7370", "date": "2026-10-28", "open": 100, "in": 0, "out": 96300, "close": 100 - 96300,
             "inItems": [], "outItems": [{"who": "Пономаренко", "amount": 96300, "basis": "п/п 258"}]}]}, ensure_ascii=False), encoding="utf-8")
        patch.object(builder, "BANK_JSON", str(bk)).start()
        days = builder.bank_oboroty()["days"]
        pon = [d for d in days if "ИП" in d["account"]]
        self.assertEqual(len(pon), 1, "день только с поступлением от РЭИ не показывается")
        self.assertEqual((pon[0]["in"], pon[0]["out"], pon[0]["close"], pon[0]["outItems"]), (12000, None, None, []))
        self.assertEqual([x["who"] for x in pon[0]["inItems"]], ["АЗС Спецснаб"])
        alfa = next(d for d in days if "Альфа" in d["account"])
        self.assertEqual((alfa["out"], [x["who"] for x in alfa["outItems"]]), (96300, ["прочие перечисления"]))
        self.assertNotIn("Пономаренко", json.dumps(days, ensure_ascii=False))

    def test_forecast_accuracy_starts_collecting_without_backdating(self):
        gar = {"connected": True, "rows": [{"id": "G-1", "date": "2026-10-30", "remain": 1000.0, "amount": 1000.0, "stageReg": "Гарант", "bank": []}]}
        from datetime import date
        first = builder.forecast_accuracy(gar, date(2026, 10, 29))  # четверг: неделя сохранена не с начала
        self.assertEqual((first["weeks"], first["firstPartial"]), ([], True))
        gar["rows"].append({"id": "G-2", "date": "2026-11-04", "remain": 500.0, "amount": 500.0, "stageReg": "Обсуждаем", "bank": [{"date": "2026-11-05", "amount": 500.0}]})
        builder.forecast_accuracy(gar, date(2026, 11, 2))  # понедельник: прогноз недели сохранён вовремя
        acc = builder.forecast_accuracy(gar, date(2026, 11, 9))
        self.assertEqual([(w["week"], w["kind"], w["promised"], w["cameBank"]) for w in acc["weeks"]], [("2026-11-02", "прогноз", 500.0, 500.0)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
