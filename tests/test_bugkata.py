import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "bugkata.py"
FIXTURES = ROOT / "tests" / "fixtures"

_spec = importlib.util.spec_from_file_location("bugkata", SCRIPT)
bk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bk)

HAS_GO = shutil.which("go") is not None
HAS_PYTEST = importlib.util.find_spec("pytest") is not None


class TempDirTest(unittest.TestCase):
    def tmpdir(self) -> Path:
        d = Path(tempfile.mkdtemp(prefix="bktest-"))
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        return d

    def make_gym(self) -> Path:
        gym = self.tmpdir()
        (gym / ".bugkata").mkdir()
        (gym / ".bugkata" / "state.json").write_text("{}")
        return gym


def cli(args, cwd, env=None):
    import os

    full_env = dict(os.environ)
    full_env.update(env or {})
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], cwd=cwd, text=True, capture_output=True, env=full_env
    )


class GymAndCacheTest(TempDirTest):
    def test_find_gym_from_subdir(self):
        gym = self.make_gym()
        sub = gym / "go" / "001-x"
        sub.mkdir(parents=True)
        self.assertEqual(bk.find_gym(sub).resolve(), gym.resolve())

    def test_global_dir_is_not_a_gym(self):
        home = self.tmpdir()
        (home / ".bugkata").mkdir()
        (home / ".bugkata" / "profile.json").write_text("{}")
        with self.assertRaises(bk.BugkataError):
            bk.find_gym(home)

    def test_bundle_roundtrip(self):
        b = {"key": {"what": "é…"}, "hidden": {"a.go": "x"}}
        self.assertEqual(bk.decode_bundle(bk.encode_bundle(b)), b)

    def test_cache_file_hides_contents(self):
        gym = self.make_gym()
        bundle = {"hidden": {"hidden_a_test.go": "TestHiddenFoo"}, "challenge": "go/001-truncate"}
        bk.save_bundle(gym, "go/001-truncate", bundle)
        path = bk.cache_path(gym, "go/001-truncate")
        self.assertNotIn("001", path.name)
        self.assertNotIn("truncate", path.name)
        raw = path.read_bytes()
        self.assertNotIn(b"TestHiddenFoo", raw)
        self.assertNotIn(b"truncate", raw)
        self.assertEqual(bk.load_bundle(gym, "go/001-truncate"), bundle)

    def test_load_missing_bundle_errors(self):
        gym = self.make_gym()
        with self.assertRaises(bk.BugkataError):
            bk.load_bundle(gym, "go/001-nope")

    def test_cli_outside_gym(self):
        r = cli(["run", "go/001-x"], cwd=self.tmpdir())
        self.assertEqual(r.returncode, 2)
        self.assertIn("bugkata: not inside a bugkata gym", r.stderr)


if __name__ == "__main__":
    unittest.main()
