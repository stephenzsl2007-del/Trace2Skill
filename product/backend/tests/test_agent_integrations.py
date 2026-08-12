from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from trace2skill.agent_specs import load_agent_specs
from trace2skill.agentscope_integration import AgentScopeBridge, AgentScopeUnavailable
from trace2skill.agentteams import AgentTeamsClient, WorkerHandle


ROOT = Path(__file__).resolve().parents[3]


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.created_name: str | None = None

    def run(self, arguments: list[str], timeout: int = 60, input_text: str | None = None) -> str:
        self.calls.append(arguments)
        command = " ".join(arguments)
        if "get managers default" in command:
            return json.dumps(
                {
                    "phase": "Running",
                    "model": "qwen-plus",
                    "runtime": "copaw",
                    "image": "registry/hiclaw-manager-copaw:v1.1.2",
                    "welcomeSent": True,
                }
            )
        if "get workers" in command and self.created_name:
            return json.dumps(
                {
                    "name": self.created_name,
                    "phase": "Running",
                    "containerState": "running",
                    "matrixUserID": f"@{self.created_name}:matrix-local",
                    "roomID": "!room:matrix-local",
                }
            )
        if "get workers" in command:
            return json.dumps({"workers": [], "total": 0})
        if "create worker" in command:
            name = arguments[arguments.index("--name") + 1]
            self.created_name = name
            return json.dumps(
                {
                    "name": name,
                    "phase": "Ready",
                    "matrixUserID": f"@{name}:matrix-local",
                    "roomID": "!room:matrix-local",
                }
            )
        return "{}"


class FakeProvisioner:
    def __init__(self) -> None:
        self.requested: list[str] = []

    def request_worker(self, name: str, runtime: str, model: str, identity: str) -> str:
        self.requested.append(name)
        return "event"

    def verify_human_visible_worker_room(self, room_id: str, worker_name: str) -> bool:
        return room_id == "!room:matrix-local" and worker_name in self.requested

    def send(self, room_id: str, body: str) -> str:
        return "$event"


class AgentIntegrationTests(unittest.TestCase):
    def test_exact_four_specs_and_permission_boundaries(self) -> None:
        specs = load_agent_specs(ROOT / "product" / "agent-specs")
        self.assertEqual(
            set(specs), {"execution", "trace-analyst", "skill-engineer", "evaluator"}
        )
        self.assertTrue(specs["execution"].permissions["fixture_write"])
        self.assertFalse(specs["evaluator"].permissions["validator_private_read"])
        self.assertFalse(specs["skill-engineer"].permissions["registry_write"])
        self.assertEqual(len({item.content_hash for item in specs.values()}), 4)

    def test_worker_is_fresh_spec_bound_and_deleted(self) -> None:
        specs = load_agent_specs(ROOT / "product" / "agent-specs")
        runner = FakeRunner()
        client = AgentTeamsClient(runner)
        worker = client.create_worker(specs["execution"], "run_0123456789")
        self.assertTrue(worker.name.startswith("t2s-execution-"))
        self.assertEqual(worker.spec_hash, specs["execution"].content_hash)
        create = next(call for call in runner.calls if "create" in call)
        self.assertIn(specs["execution"].prompt, create)
        self.assertIn("--wait-timeout", create)
        client.delete_worker(worker)
        self.assertTrue(any("delete worker" in " ".join(call) for call in runner.calls))

    def test_health_pins_version_and_model(self) -> None:
        health = AgentTeamsClient(FakeRunner()).health()
        self.assertTrue(health["passed"])
        self.assertEqual(health["pinned_version"], "v1.1.2")
        self.assertEqual(health["manager"]["model"], "qwen-plus")

    def test_result_parser_is_bounded(self) -> None:
        parsed = AgentTeamsClient._parse_result('TRACE2SKILL_RESULT {"ok":true}')
        self.assertTrue(parsed["ok"])
        with self.assertRaises(ValueError):
            AgentTeamsClient._parse_result('narration TRACE2SKILL_RESULT {"ok":true}')
        with self.assertRaises(ValueError):
            AgentTeamsClient._parse_result('[1,2]')

    def test_inline_spec_is_sent_with_auditable_matrix_event(self) -> None:
        runner = FakeRunner()
        provisioner = FakeProvisioner()
        client = AgentTeamsClient(runner, provisioner)
        worker = WorkerHandle(
            "worker", "execution", "qwen-plus", "copaw",
            "@worker:matrix-local", "!room:matrix-local", "Running", "spec",
        )
        assignment = client.create_finite_task(
            worker, "task-inline", "inline", "EXACT SPEC", inline_spec=True
        )
        self.assertEqual(assignment["request_event_id"], "$event")

    def test_agentscope_disabled_is_explicit_noop(self) -> None:
        bridge = AgentScopeBridge("http://127.0.0.1:3001", enabled=False)
        bridge.initialize()
        with bridge.run_span(run_id="run"):
            pass

    def test_agentscope_missing_is_not_reported_as_success(self) -> None:
        bridge = AgentScopeBridge("http://127.0.0.1:3001", enabled=True)
        try:
            import agentscope  # noqa: F401
        except ImportError:
            with self.assertRaises(AgentScopeUnavailable):
                bridge.initialize()


if __name__ == "__main__":
    unittest.main()
