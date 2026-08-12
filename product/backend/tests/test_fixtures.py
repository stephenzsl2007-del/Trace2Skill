from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from trace2skill.fixtures import FixtureCatalog, FixtureRunner


ROOT = Path(__file__).resolve().parents[3]


class FixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.catalog = FixtureCatalog(ROOT / "product" / "fixtures").load()
        self.runner = FixtureRunner(Path(self.temporary.name), timeout_seconds=60)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_exact_catalog_and_reproduction(self) -> None:
        self.assertEqual(len(self.catalog), 6)
        for fixture in self.catalog.values():
            with self.subTest(fixture=fixture.fixture_id):
                prepared = self.runner.prepare(fixture)
                self.assertNotEqual(prepared.receipt.exit_code, 0)
                self.assertTrue(prepared.receipt.matched_patterns)
                self.assertEqual(prepared.receipt.snapshot_hash, fixture.manifest["initial_snapshot_hash"])
                self.assertFalse((prepared.workspace / "host.json").exists())

    def test_all_six_known_repairs_pass_host_validator(self) -> None:
        repairs = {
            "train-npm-peer-conflict": self._repair_train_peer,
            "train-npm-lockfile-drift": self._repair_train_lock,
            "train-npm-missing-dev-dependency": self._repair_train_missing,
            "heldout-npm-peer-conflict": self._repair_heldout_peer,
            "heldout-pnpm-frozen-lockfile": self._repair_pnpm_frozen,
            "heldout-pnpm-missing-build-dependency": self._repair_pnpm_missing,
        }
        for fixture_id, repair in repairs.items():
            with self.subTest(fixture=fixture_id):
                prepared = self.runner.prepare(self.catalog[fixture_id])
                repair(prepared.workspace)
                report = self.runner.validate(prepared)
                self.assertTrue(report["passed"], report)
                self.assertTrue(report["original_error_absent"])
                self.assertTrue(report["safety"]["passed"])
                self.assertEqual(
                    [report["stages"][stage]["status"] for stage in ("install", "build", "test")],
                    ["passed", "passed", "passed"],
                )

    def test_disallowed_change_blocks_commands(self) -> None:
        prepared = self.runner.prepare(self.catalog["train-npm-peer-conflict"])
        self._repair_train_peer(prepared.workspace)
        (prepared.workspace / "build.js").write_text("process.exit(0);\n", encoding="utf-8")
        report = self.runner.validate(prepared)
        self.assertFalse(report["passed"])
        self.assertEqual(report["safety"]["disallowed_files"], ["build.js"])
        self.assertEqual(report["stages"]["install"]["status"], "skipped")

    def test_pnpm_lockfile_can_be_safely_synchronized_before_frozen_validation(self) -> None:
        prepared = self.runner.prepare(self.catalog["heldout-pnpm-missing-build-dependency"])
        _, package = self._package(prepared.workspace)
        package["dependencies"] = {"@fixture/compiler": "file:packages/compiler"}
        self._write_package(prepared.workspace, package)
        sync = self.runner.synchronize_pnpm_lockfile(prepared)
        self.assertTrue(sync["passed"], sync)
        self.assertTrue(self.runner.validate(prepared)["passed"])

    def test_agent_diagnostic_reads_are_real_bounded_tool_calls(self) -> None:
        prepared = self.runner.prepare(self.catalog["train-npm-peer-conflict"])
        receipts = self.runner.execute_agent_diagnostics(
            prepared, [["cat", "package.json"], ["read_file", "vendor/react-19/package.json"]]
        )
        self.assertEqual([item["tool"] for item in receipts], ["read_file", "read_file"])
        self.assertTrue(all(item["exit_code"] == 0 for item in receipts))
        self.assertTrue(all(len(item["output_hash"]) == 64 for item in receipts))
        with self.assertRaises(ValueError):
            self.runner.execute_agent_diagnostics(prepared, [["cat", "../host.json"]])
        with self.assertRaises(ValueError):
            self.runner.execute_agent_diagnostics(prepared, [["npm", "exec", "untrusted"]])
        with self.assertRaises(ValueError):
            self.runner.execute_agent_diagnostics(prepared, [["npm", "install"]])

    def test_agent_package_manager_diagnostic_is_executed_with_a_receipt(self) -> None:
        prepared = self.runner.prepare(self.catalog["heldout-pnpm-frozen-lockfile"])
        receipts = self.runner.execute_agent_diagnostics(
            prepared, [["pnpm", "install", "--dry-run", "--frozen-lockfile"]]
        )
        self.assertEqual(receipts[0]["tool"], "pnpm")
        self.assertEqual(receipts[0]["status"], "succeeded" if receipts[0]["exit_code"] == 0 else "failed")
        self.assertEqual(len(receipts[0]["output_hash"]), 64)

    @staticmethod
    def _package(root: Path) -> tuple[Path, dict]:
        path = root / "package.json"
        return path, json.loads(path.read_text(encoding="utf-8"))

    @classmethod
    def _write_package(cls, root: Path, value: dict) -> None:
        path = root / "package.json"
        path.write_text(json.dumps(value, separators=(",", ":")) + "\n", encoding="utf-8")

    @classmethod
    def _repair_train_peer(cls, root: Path) -> None:
        _, package = cls._package(root)
        package["dependencies"]["react"] = "file:vendor/react-18"
        cls._write_package(root, package)

    @classmethod
    def _repair_train_lock(cls, root: Path) -> None:
        _, package = cls._package(root)
        package["dependencies"]["ci-util"] = "file:vendor/ci-util-v1"
        cls._write_package(root, package)

    @classmethod
    def _repair_train_missing(cls, root: Path) -> None:
        _, package = cls._package(root)
        package["devDependencies"] = {"ci-builder": "file:vendor/ci-builder"}
        cls._write_package(root, package)

    @classmethod
    def _repair_heldout_peer(cls, root: Path) -> None:
        _, package = cls._package(root)
        package["dependencies"]["eslint"] = "file:vendor/eslint-8"
        cls._write_package(root, package)

    @classmethod
    def _repair_pnpm_frozen(cls, root: Path) -> None:
        _, package = cls._package(root)
        package["dependencies"]["frozen-lib"] = "file:vendor/frozen-lib-v1"
        cls._write_package(root, package)

    @classmethod
    def _repair_pnpm_missing(cls, root: Path) -> None:
        _, package = cls._package(root)
        package["devDependencies"] = {"@fixture/compiler": "workspace:*"}
        cls._write_package(root, package)
        (root / "pnpm-lock.yaml").write_text(
            """lockfileVersion: '9.0'

settings:
  autoInstallPeers: true
  excludeLinksFromLockfile: false

importers:
  .:
    devDependencies:
      '@fixture/compiler':
        specifier: workspace:*
        version: link:packages/compiler
  packages/compiler: {}
""",
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
