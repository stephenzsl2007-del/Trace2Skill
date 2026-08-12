from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from trace2skill.models import RunStatus
from trace2skill.objects import ObjectStore
from trace2skill.repository import Repository
from trace2skill.security import REDACTED, sanitize


class FoundationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.repository = Repository(root / "state.sqlite3")
        self.objects = ObjectStore(root / "objects")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_objects_are_content_addressed_and_sanitized(self) -> None:
        fake_key = "sk-" + "test-value-" * 2
        first = self.objects.put_json({"api_key": fake_key, "value": 3})
        second = self.objects.put_json({"value": 3, "api_key": "different"})
        self.assertEqual(first, second)
        self.assertTrue(self.objects.verify(first))
        self.assertEqual(self.objects.get_json(first)["api_key"], REDACTED)

    def test_text_and_nested_secrets_are_redacted(self) -> None:
        result = sanitize(
            {
                "message": "Authorization: Bearer abcdefghijklmnop and api_key=top-secret",
                "nested": [{"password": "never-store-this"}],
            }
        )
        serialized = json.dumps(result)
        self.assertNotIn("abcdefghijklmnop", serialized)
        self.assertNotIn("top-secret", serialized)
        self.assertNotIn("never-store-this", serialized)

    def test_idempotent_run_creation(self) -> None:
        first, created_first = self.repository.create_run("execution", {"x": 1}, "same")
        second, created_second = self.repository.create_run("execution", {"x": 2}, "same")
        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(second["request"], {"x": 1})

    def test_state_machine_rejects_skips_and_terminal_mutation(self) -> None:
        run, _ = self.repository.create_run("execution", {})
        with self.assertRaises(ValueError):
            self.repository.transition_run(run["id"], RunStatus.SUCCEEDED)
        self.repository.transition_run(run["id"], RunStatus.DISPATCHING)
        self.repository.transition_run(run["id"], RunStatus.RUNNING)
        self.repository.transition_run(run["id"], RunStatus.VALIDATING)
        self.repository.transition_run(run["id"], RunStatus.SUCCEEDED)
        with self.assertRaises(ValueError):
            self.repository.transition_run(run["id"], RunStatus.FAILED)

    def test_events_are_ordered_and_resume_after_event_id(self) -> None:
        run, _ = self.repository.create_run("execution", {})
        first = self.repository.append_event(self._event(run["id"], "one"))
        second = self.repository.append_event(self._event(run["id"], "two"))
        self.assertEqual((first.sequence, second.sequence), (1, 2))
        resumed = self.repository.list_events(run["id"], first.event_id)
        self.assertEqual([item["event_type"] for item in resumed], ["two"])
        with self.assertRaises(KeyError):
            self.repository.list_events(run["id"], "evt_unknown")

    def test_cancel_is_idempotent(self) -> None:
        run, _ = self.repository.create_run("execution", {})
        first = self.repository.cancel_run(run["id"])
        second = self.repository.cancel_run(run["id"])
        self.assertEqual(first["status"], "cancelled")
        self.assertEqual(second["status"], "cancelled")

    def test_restart_safely_terminates_incomplete_runs(self) -> None:
        run, _ = self.repository.create_run("full-loop", {})
        recovered = self.repository.recover_incomplete_runs()
        self.assertEqual(recovered, [run["id"]])
        after = self.repository.get_run(run["id"])
        self.assertEqual(after["status"], "failed")
        self.assertEqual(after["failure_reason"], "service_restarted_before_safe_resume")
        self.assertEqual(len(self.repository.list_events(run["id"])), 1)

    def test_approval_is_hash_bound_and_single_use(self) -> None:
        approval_id, challenge, _ = self.repository.create_approval(
            "diagnose-ci-dependency-failure", "2.0.0", "abc"
        )
        self.assertFalse(self.repository.consume_approval(approval_id, challenge, "changed"))
        self.assertTrue(self.repository.consume_approval(approval_id, challenge, "abc"))
        self.assertFalse(self.repository.consume_approval(approval_id, challenge, "abc"))

    def test_expired_approval_is_rejected(self) -> None:
        approval_id, challenge, _ = self.repository.create_approval("skill", "2.0.0", "abc")
        expired = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        with self.repository.connection() as connection:
            connection.execute(
                "UPDATE approvals SET expires_at=? WHERE approval_id=?", (expired, approval_id)
            )
        self.assertFalse(self.repository.consume_approval(approval_id, challenge, "abc"))

    @staticmethod
    def _event(run_id: str, event_type: str) -> dict[str, object]:
        return {
            "run_id": run_id,
            "task_id": "fixture",
            "agent_id": "worker",
            "phase": "training_execution",
            "event_type": event_type,
            "status": "running",
            "evidence_refs": [],
        }


if __name__ == "__main__":
    unittest.main()
