from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from trace2skill.nacos_registry import (
    NacosRegistry,
    RegistryError,
    RegistryReviewTimeout,
)
from trace2skill.skill_packages import SKILL_NAME, SkillPackage


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakeRegistryRunner:
    def __init__(self, statuses: list[str]) -> None:
        self.statuses = statuses
        self.calls: list[list[str]] = []
        self.describe_index = 0

    def run(self, arguments: list[str], timeout: int = 60) -> str:
        self.calls.append(arguments)
        if arguments[-1] == "--version" and "skill-release" not in arguments:
            return "nacos-cli version 1.0.5-beta.1"
        if "skill-describe" in arguments:
            status = self.statuses[min(self.describe_index, len(self.statuses) - 1)]
            self.describe_index += 1
            return '{"versions":[{"version":"2.0.0","status":"' + status + '"}]}'
        return "ok"


class NacosRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.package = SkillPackage(
            SKILL_NAME,
            "2.0.0",
            {"SKILL.md": "---\nname: diagnose-ci-dependency-failure\n---\n"},
            b"zip",
            "manifest",
        )

    def test_preflight_and_exact_lifecycle(self) -> None:
        runner = FakeRegistryRunner(["editing", "reviewing", "reviewed", "reviewed", "online"])
        clock = FakeClock()
        registry = NacosRegistry(runner, "worker", "prod", clock.sleep, clock.monotonic)
        self.assertTrue(registry.preflight()["passed"])
        staged = registry.upload_and_submit_review(
            self.package, Path.home() / "skill", timeout_seconds=30
        )
        self.assertEqual(staged["status"], "reviewed")
        released = registry.release(self.package)
        self.assertEqual(released["status"], "online")
        commands = [" ".join(call) for call in runner.calls]
        self.assertTrue(any("skill-upload" in command for command in commands))
        self.assertTrue(any("skill-review" in command for command in commands))
        self.assertTrue(any("skill-release" in command for command in commands))
        self.assertTrue(all("--profile prod" in command for command in commands if "skill-" in command))

    def test_review_timeout_does_not_release(self) -> None:
        runner = FakeRegistryRunner(["editing", "reviewing"])
        clock = FakeClock()
        registry = NacosRegistry(runner, "worker", "prod", clock.sleep, clock.monotonic)
        with self.assertRaises(RegistryReviewTimeout):
            registry.upload_and_submit_review(
                self.package, Path.home() / "skill", timeout_seconds=3
            )
        self.assertFalse(any("skill-release" in call for call in (" ".join(x) for x in runner.calls)))

    def test_release_refuses_unreviewed_version(self) -> None:
        registry = NacosRegistry(FakeRegistryRunner(["reviewing"]), "worker", "prod")
        with self.assertRaises(RegistryError):
            registry.release(self.package)


if __name__ == "__main__":
    unittest.main()
