from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Protocol

from .security import sanitize
from .skill_packages import SKILL_NAME, SkillPackage


NACOS_CLI_VERSION = "1.0.5-beta.1"


class RegistryCommandRunner(Protocol):
    def run(self, arguments: list[str], timeout: int = 60) -> str: ...


class RegistryError(RuntimeError):
    pass


class RegistryReviewTimeout(RegistryError):
    pass


class NacosRegistry:
    def __init__(
        self,
        runner: RegistryCommandRunner,
        worker_container: str,
        profile: str,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not profile or any(character.isspace() for character in profile):
            raise ValueError("Nacos profile name is required")
        self.runner = runner
        self.worker_container = worker_container
        self.profile = profile
        self.sleep = sleep
        self.monotonic = monotonic

    def preflight(self) -> dict[str, Any]:
        output = self.runner.run(
            ["docker", "exec", self.worker_container, "nacos-cli", "--version"], timeout=30
        )
        passed = NACOS_CLI_VERSION in output
        if not passed:
            raise RegistryError(f"Nacos CLI version mismatch; expected {NACOS_CLI_VERSION}")
        return {"passed": True, "cli_version": NACOS_CLI_VERSION, "profile": self.profile}

    def upload_and_submit_review(
        self, package: SkillPackage, host_skill_path: Path, timeout_seconds: int = 300
    ) -> dict[str, Any]:
        if package.name != SKILL_NAME:
            raise RegistryError("unexpected Skill identity")
        container_path = self._container_path(host_skill_path)
        self._run_cli(["skill-upload", container_path], timeout=120)
        editing = self.describe(package.name)
        if self._version_status(editing, package.version) != "editing":
            raise RegistryError("uploaded Skill did not enter editing state")
        self._run_cli(["skill-review", package.name, "--version", package.version], timeout=60)
        deadline = self.monotonic() + timeout_seconds
        delay = 1.0
        last_status = "reviewing"
        while self.monotonic() < deadline:
            description = self.describe(package.name)
            last_status = self._version_status(description, package.version)
            if last_status in {"reviewed", "approved"}:
                return {
                    "status": "reviewed",
                    "remote_status": last_status,
                    "skill_name": package.name,
                    "version": package.version,
                    "manifest_hash": package.manifest_hash,
                }
            if last_status not in {"editing", "reviewing"}:
                raise RegistryError(f"Nacos review entered unexpected state: {last_status}")
            self.sleep(delay)
            delay = min(delay * 2, 15.0)
        raise RegistryReviewTimeout(
            f"Nacos review remained {last_status} after {timeout_seconds} seconds"
        )

    def release(self, package: SkillPackage) -> dict[str, Any]:
        before = self.describe(package.name)
        if self._version_status(before, package.version) not in {"reviewed", "approved"}:
            raise RegistryError("Skill is not approved for release")
        self._run_cli(
            ["skill-release", package.name, "--version", package.version, "--update-latest=true"],
            timeout=90,
        )
        after = self.describe(package.name)
        if self._version_status(after, package.version) != "online":
            raise RegistryError("Nacos did not confirm the released version as online")
        return {
            "status": "online",
            "skill_name": package.name,
            "version": package.version,
            "manifest_hash": package.manifest_hash,
        }

    def get_and_verify(self, package: SkillPackage, host_output: Path) -> dict[str, Any]:
        host_output = Path(host_output)
        if host_output.exists():
            if any(host_output.iterdir()):
                raise RegistryError("skill-get destination must be a fresh empty directory")
        else:
            host_output.mkdir(parents=True)
        container_output = self._container_path(host_output)
        self._run_cli(
            [
                "skill-get", package.name, "--version", package.version,
                "--output", container_output,
            ],
            timeout=120,
        )
        candidates = [host_output / package.name, host_output]
        skill_root = next((path for path in candidates if (path / "SKILL.md").is_file()), None)
        if not skill_root:
            raise RegistryError("skill-get did not produce a Skill directory")
        actual_files = {
            path.relative_to(skill_root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in skill_root.rglob("*")
            if path.is_file()
        }
        expected_files = {
            path: hashlib.sha256(content.encode()).hexdigest()
            for path, content in package.files.items()
        }
        if actual_files != expected_files:
            raise RegistryError("downloaded Skill content does not match the released package")
        return {
            "status": "verified",
            "skill_name": package.name,
            "version": package.version,
            "manifest_hash": package.manifest_hash,
            "file_hashes": actual_files,
        }

    def describe(self, skill_name: str) -> dict[str, Any]:
        output = self._run_cli(["skill-describe", skill_name, "--output", "json"], timeout=60)
        try:
            value = json.loads(output)
        except json.JSONDecodeError as exc:
            raise RegistryError("Nacos describe did not return JSON") from exc
        if not isinstance(value, dict):
            raise RegistryError("Nacos describe returned an invalid object")
        return sanitize(value)

    def _run_cli(self, arguments: list[str], timeout: int) -> str:
        return self.runner.run(
            [
                "docker", "exec", self.worker_container,
                "nacos-cli", "--profile", self.profile, *arguments,
            ],
            timeout=timeout,
        )

    @staticmethod
    def _version_status(description: dict[str, Any], version: str) -> str:
        versions = description.get("versions")
        if not isinstance(versions, list):
            data = description.get("data")
            versions = data.get("versions") if isinstance(data, dict) else None
        if not isinstance(versions, list):
            raise RegistryError("Nacos describe response has no versions list")
        matches = [item for item in versions if str(item.get("version")) == version]
        if len(matches) != 1:
            raise RegistryError(f"Nacos describe expected one version {version}, found {len(matches)}")
        return str(matches[0].get("status", "")).lower()

    @staticmethod
    def _container_path(host_path: Path) -> str:
        host = Path(host_path).resolve()
        host_share_root = Path(
            os.environ.get("TRACE2SKILL_HOST_SHARE_ROOT", str(Path.home()))
        ).resolve()
        try:
            relative = host.relative_to(host_share_root)
        except ValueError as exc:
            raise RegistryError("Nacos files must live under the AgentTeams host-share root") from exc
        return "/host-share/" + relative.as_posix()
