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


if __name__ == "__main__":
    unittest.main(verbosity=2)
