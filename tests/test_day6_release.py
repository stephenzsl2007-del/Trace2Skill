import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).parents[1]


def load_builder():
    path = ROOT / "scripts" / "build_day6_release.py"
    spec = importlib.util.spec_from_file_location("test_day6_builder", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Day6ReleaseTests(unittest.TestCase):
    def test_release_is_validated_but_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = load_builder().build_release(Path(directory))
        self.assertEqual("validated", manifest["skill"]["status"])
        self.assertEqual("observed-on-one-new-held-out-task", manifest["claims"]["independent_transfer"])
        self.assertEqual("not-claimed", manifest["claims"]["efficiency_improvement"])
        self.assertIn("yarn", manifest["skill"]["unvalidated_scope"])
        self.assertFalse(manifest["registry"]["external_write_performed"])

    def test_release_zip_is_deterministic_and_rooted_at_skill(self):
        builder = load_builder()
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            one = builder.build_release(Path(first))
            two = builder.build_release(Path(second))
            self.assertEqual(one["package"]["sha256"], two["package"]["sha256"])
            archive = Path(first) / one["package"]["file"]
            self.assertEqual(one["package"]["sha256"], hashlib.sha256(archive.read_bytes()).hexdigest())
            with zipfile.ZipFile(archive) as value:
                self.assertEqual(list(builder.PACKAGE_FILES), value.namelist())


if __name__ == "__main__":
    unittest.main()
