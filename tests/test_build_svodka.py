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
        for ident, status in (("applied", "done"), ("pending", "new")):
            (comments / (ident + ".json")).write_text(json.dumps({"eventId": ident, "status": status}), encoding="utf-8")
        self.assertEqual(["applied"], self.build()["appliedEvents"])

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
