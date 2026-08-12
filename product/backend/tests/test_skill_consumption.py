from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from trace2skill.skill_consumption import install_verified_skill
from trace2skill.skill_packages import REQUIRED_FILES, SKILL_NAME, V2_CANDIDATE


class SkillConsumptionTests(unittest.TestCase):
    def test_verified_package_is_hash_identical_after_install(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            for relative in REQUIRED_FILES:
                path = source / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                content = "placeholder\n"
                if relative == "trace2skill.json":
                    content = json.dumps({"name": SKILL_NAME, "version": V2_CANDIDATE})
                path.write_text(content, encoding="utf-8")
            receipt = install_verified_skill(source, root / "installed", V2_CANDIDATE)
            self.assertEqual(receipt["source_digest"], receipt["installed_digest"])
            self.assertEqual(set(receipt["files"]), REQUIRED_FILES)

    def test_install_rejects_extra_files_and_wrong_version(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            for relative in REQUIRED_FILES:
                path = source / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}" if relative == "trace2skill.json" else "x", encoding="utf-8")
            (source / "unexpected.txt").write_text("x", encoding="utf-8")
            with self.assertRaises(ValueError):
                install_verified_skill(source, root / "installed", V2_CANDIDATE)


if __name__ == "__main__":
    unittest.main()
