from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from trace2skill.api import create_app
from trace2skill.models import RunStatus
from trace2skill.service import ProductSettings


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.app = create_app(ProductSettings(Path(self.temporary.name)))
        self.client_context = TestClient(self.app)
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.temporary.cleanup()

    def test_health_and_local_cors(self) -> None:
        self.assertEqual(self.client.get("/health").json()["version"], "0.3.0")
        response = self.client.options(
            "/runs",
            headers={
                "Origin": "http://127.0.0.1:3000",
                "Access-Control-Request-Method": "POST",
            },
        )
        self.assertEqual(response.headers["access-control-allow-origin"], "http://127.0.0.1:3000")
        blocked = self.client.options(
            "/runs",
            headers={
                "Origin": "https://example.com",
                "Access-Control-Request-Method": "POST",
            },
        )
        self.assertNotIn("access-control-allow-origin", blocked.headers)

    def test_run_idempotency_redaction_get_and_cancel(self) -> None:
        body = {"kind": "execution", "fixture_ids": ["x"], "config": {"api_key": "secret"}}
        first = self.client.post("/runs", json=body, headers={"Idempotency-Key": "idem"})
        second = self.client.post("/runs", json=body, headers={"Idempotency-Key": "idem"})
        self.assertEqual(first.status_code, 202)
        self.assertTrue(first.json()["created"])
        self.assertFalse(second.json()["created"])
        self.assertEqual(first.json()["id"], second.json()["id"])
        self.assertEqual(first.json()["request"]["config"]["api_key"], "[REDACTED]")
        run_id = first.json()["id"]
        cancelled = self.client.post(f"/runs/{run_id}/cancel")
        self.assertEqual(cancelled.json()["status"], "cancelled")
        self.assertEqual(self.client.get(f"/runs/{run_id}").json()["status"], "cancelled")

    def test_sse_reconnect_uses_event_id(self) -> None:
        run, _ = self.app.state.repository.create_run("execution", {})
        repository = self.app.state.repository
        repository.transition_run(run["id"], RunStatus.DISPATCHING)
        repository.transition_run(run["id"], RunStatus.RUNNING)
        first = repository.append_event(self._event(run["id"], "tool.started"))
        second = repository.append_event(self._event(run["id"], "tool.finished"))
        repository.transition_run(run["id"], RunStatus.VALIDATING)
        repository.transition_run(run["id"], RunStatus.SUCCEEDED)
        response = self.client.get(
            f"/runs/{run['id']}/events", headers={"Last-Event-ID": first.event_id}
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(first.event_id, response.text)
        self.assertIn(second.event_id, response.text)
        self.assertIn("event: tool.finished", response.text)

    def test_publish_challenge_is_single_use_and_manifest_bound(self) -> None:
        repository = self.app.state.repository
        repository.put_skill(
            "diagnose-ci-dependency-failure",
            "2.0.0",
            "qualified",
            "manifest-a",
            "sha256:" + "a" * 64,
            {},
        )
        url = "/skills/diagnose-ci-dependency-failure/2.0.0/publish"
        prepared = self.client.post(url, json={"action": "prepare"})
        self.assertEqual(prepared.status_code, 200)
        values = prepared.json()
        wrong = self.client.post(
            url,
            json={
                "action": "confirm",
                "approval_id": values["approval_id"],
                "challenge": values["challenge"],
                "manifest_hash": "manifest-b",
            },
        )
        self.assertEqual(wrong.status_code, 409)
        payload = {
            "action": "confirm",
            "approval_id": values["approval_id"],
            "challenge": values["challenge"],
            "manifest_hash": values["manifest_hash"],
        }
        self.assertTrue(self.client.post(url, json=payload).json()["approved"])
        self.assertEqual(self.client.post(url, json=payload).status_code, 409)

    def test_full_loop_api_starts_supplied_dispatcher(self) -> None:
        class FakeDispatcher:
            def __init__(self) -> None:
                self.calls = []

            async def execute(self, run_id, kind, request) -> None:
                self.calls.append((run_id, kind, request))

            async def cancel(self, run_id) -> None:
                return None

        dispatcher = FakeDispatcher()
        temporary = tempfile.TemporaryDirectory()
        app = create_app(ProductSettings(Path(temporary.name)), dispatcher=dispatcher)
        with TestClient(app) as client:
            created = client.post("/runs", json={"kind": "full-loop", "config": {}}).json()
            for _ in range(50):
                current = client.get(f"/runs/{created['id']}").json()
                if current["status"] == "succeeded":
                    break
                time.sleep(0.01)
            self.assertEqual(current["status"], "succeeded")
            self.assertEqual(dispatcher.calls[0][1], "full-loop")
        temporary.cleanup()

    @staticmethod
    def _event(run_id: str, event_type: str) -> dict[str, object]:
        return {
            "run_id": run_id,
            "task_id": "task",
            "agent_id": "agent",
            "phase": "training_execution",
            "event_type": event_type,
            "status": "running",
            "evidence_refs": [],
        }


if __name__ == "__main__":
    unittest.main()
