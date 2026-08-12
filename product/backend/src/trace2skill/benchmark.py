from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any


HELD_OUT = {
    "heldout-npm-peer-conflict",
    "heldout-pnpm-frozen-lockfile",
    "heldout-pnpm-missing-build-dependency",
}
QUALIFICATION_CONDITIONS = {"baseline", "skill-v2"}


class QualificationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class LoopQualification:
    loop_id: str
    v1_probe_count: int
    qualification_count: int
    v2_passed: int
    v2_total: int
    baseline_passed: int
    baseline_total: int
    qualified: bool


class BenchmarkGate:
    def qualify_loop(self, loop_id: str, records: list[dict[str, Any]]) -> LoopQualification:
        selected = [record for record in records if record.get("loop_id") == loop_id]
        probes = [record for record in selected if record.get("stage") == "v1-probe"]
        qualifications = [record for record in selected if record.get("stage") == "qualification"]
        probe_keys = Counter((item.get("fixture_id"), item.get("condition")) for item in probes)
        expected_probes = {(fixture, "skill-v1"): 1 for fixture in HELD_OUT}
        if probe_keys != Counter(expected_probes):
            raise QualificationError(f"v1 probe matrix is incomplete or duplicated: {probe_keys}")
        if probes_by_fixture := {item["fixture_id"]: bool(item.get("passed")) for item in probes}:
            if not probes_by_fixture["heldout-npm-peer-conflict"]:
                raise QualificationError("v1 must pass the npm held-out probe")
            if probes_by_fixture["heldout-pnpm-frozen-lockfile"]:
                raise QualificationError("v1 did not expose the frozen pnpm assumption")
            if probes_by_fixture["heldout-pnpm-missing-build-dependency"]:
                raise QualificationError("v1 did not expose the pnpm build-dependency assumption")
        expected = Counter(
            (fixture, condition, repeat)
            for fixture in HELD_OUT
            for condition in QUALIFICATION_CONDITIONS
            for repeat in (1, 2, 3)
        )
        actual = Counter(
            (item.get("fixture_id"), item.get("condition"), item.get("repeat_index"))
            for item in qualifications
        )
        if actual != expected:
            raise QualificationError("qualification matrix must be exact 3x2x3 without duplicates")
        config_hashes = {item.get("config_hash") for item in selected}
        if len(config_hashes) != 1 or None in config_hashes:
            raise QualificationError("all trials in a loop require one non-null config hash")
        for item in selected:
            if not item.get("fixture_hash"):
                raise QualificationError("trial is missing immutable fixture hash")
            if item.get("answer_leak") or item.get("unauthorized_change"):
                raise QualificationError("answer leakage or unauthorized modification detected")
            metrics = item.get("metrics") or {}
            for metric in ("token_usage", "tool_calls", "duration_ms", "invalid_attempts"):
                if metrics.get(metric) is None and not metrics.get(f"{metric}_missing_reason"):
                    raise QualificationError(f"missing telemetry has no reason: {metric}")
        v2 = [item for item in qualifications if item["condition"] == "skill-v2"]
        baseline = [item for item in qualifications if item["condition"] == "baseline"]
        v2_passed = sum(bool(item["passed"]) for item in v2)
        baseline_passed = sum(bool(item["passed"]) for item in baseline)
        if v2_passed != 9:
            raise QualificationError(f"v2 qualification is {v2_passed}/9, expected 9/9")
        if v2_passed / len(v2) < baseline_passed / len(baseline):
            raise QualificationError("v2 success rate is below baseline")
        return LoopQualification(
            loop_id=loop_id,
            v1_probe_count=len(probes),
            qualification_count=len(qualifications),
            v2_passed=v2_passed,
            v2_total=len(v2),
            baseline_passed=baseline_passed,
            baseline_total=len(baseline),
            qualified=True,
        )

    def qualify_release(
        self,
        loops: list[dict[str, Any]],
        records: list[dict[str, Any]],
    ) -> list[LoopQualification]:
        if len(loops) != 2 or len({item.get("loop_id") for item in loops}) != 2:
            raise QualificationError("release requires exactly two independent loops")
        if len({item.get("config_hash") for item in loops}) != 1:
            raise QualificationError("the two loops used different configurations")
        fixture_sets = [item.get("fixture_hashes") for item in loops]
        if fixture_sets[0] != fixture_sets[1]:
            raise QualificationError("the two loops used different fixture snapshots")
        if any(item.get("worker_instance_overlap") for item in loops):
            raise QualificationError("fresh Worker isolation was not preserved")
        return [self.qualify_loop(str(item["loop_id"]), records) for item in loops]
