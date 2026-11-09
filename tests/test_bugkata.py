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


def read_tree(root: Path) -> dict:
    return {
        p.relative_to(root).as_posix(): p.read_text()
        for p in sorted(root.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
    }


def stage(test: TempDirTest, gym: Path, fixture: str) -> Path:
    """Build a run-ready workdir: visible + hidden + fixed copies of reference."""
    src = FIXTURES / fixture
    lang = json.loads((src / "key.json").read_text())["lang"]
    work = test.tmpdir()
    files = {**read_tree(src / "visible"), **read_tree(src / "hidden")}
    files.update(bk.make_fixed(lang, read_tree(src / "reference")))
    for rel, text in files.items():
        (work / rel).parent.mkdir(parents=True, exist_ok=True)
        (work / rel).write_text(text)
    if lang == "go":
        bk.write_go_mod(gym, work)
    else:
        bk.write_python_harness(work)
    return work


class GoHarnessTest(TempDirTest):
    def test_go_make_fixed_flips_tag_and_name(self):
        out = bk.make_fixed("go", {"challenge_store.go": "//go:build !fixed\n\npackage x\n"})
        self.assertEqual(out, {"fixed_store.go": "//go:build fixed\n\npackage x\n"})

    def test_go_make_fixed_requires_tag(self):
        with self.assertRaises(bk.BugkataError):
            bk.make_fixed("go", {"challenge.go": "package x\n"})

    @unittest.skipUnless(HAS_GO, "go not installed")
    def test_go_run_challenge_and_fixed(self):
        gym = self.make_gym()
        work = stage(self, gym, "go-truncate")
        r = bk.run_tests(gym, "go", work, "challenge")
        self.assertEqual(r["status"], "ok")
        self.assertEqual(
            r["tests"], {"TestTruncateASCII": "pass", "TestHiddenTruncateMultibyte": "fail"}
        )
        r = bk.run_tests(gym, "go", work, "fixed")
        self.assertEqual(
            r["tests"], {"TestTruncateASCII": "pass", "TestHiddenTruncateMultibyte": "pass"}
        )

    @unittest.skipUnless(HAS_GO, "go not installed")
    def test_go_build_error(self):
        gym = self.make_gym()
        work = stage(self, gym, "go-truncate")
        (work / "fixed.go").write_text("//go:build fixed\n\npackage truncate\n\nfunc Truncate(\n")
        r = bk.run_tests(gym, "go", work, "fixed")
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["tests"], {})
        self.assertTrue(r["output"])

    @unittest.skipUnless(HAS_GO, "go not installed")
    def test_go_hang_times_out(self):
        import time

        gym = self.make_gym()
        work = stage(self, gym, "go-truncate")
        (work / "hidden_hang_test.go").write_text(
            'package truncate\n\nimport "testing"\n\n'
            "func hiddenSpin() { for {} }\n\n"
            "func TestHiddenHang(t *testing.T) { hiddenSpin() }\n"
        )
        start = time.monotonic()
        r = bk.run_tests(gym, "go", work, "challenge", timeout=3)
        self.assertEqual(r["status"], "timeout")
        self.assertLess(time.monotonic() - start, 40)


@unittest.skipUnless(HAS_PYTEST, "pytest not installed")
class PythonHarnessTest(TempDirTest):
    def setUp(self):
        import os

        old = os.environ.get("BUGKATA_PYTHON")
        os.environ["BUGKATA_PYTHON"] = sys.executable
        self.addCleanup(
            lambda: os.environ.pop("BUGKATA_PYTHON", None) if old is None
            else os.environ.__setitem__("BUGKATA_PYTHON", old)
        )

    def test_python_make_fixed_moves_package(self):
        out = bk.make_fixed("python", {"challenge/__init__.py": "x = 1\n", "challenge/a.py": "y\n"})
        self.assertEqual(out, {"fixed/__init__.py": "x = 1\n", "fixed/a.py": "y\n"})

    def test_python_run_challenge_and_fixed(self):
        gym = self.make_gym()
        work = stage(self, gym, "python-chunk")
        r = bk.run_tests(gym, "python", work, "challenge")
        self.assertEqual(r["status"], "ok", r["output"])
        self.assertEqual(
            r["tests"],
            {
                "test_chunk_even_split": "pass",
                "test_chunk_empty": "pass",
                "test_hidden_chunk_uneven": "fail",
                "test_hidden_chunk_keeps_every_item": "fail",
            },
        )
        r = bk.run_tests(gym, "python", work, "fixed")
        self.assertEqual(set(r["tests"].values()), {"pass"})
        self.assertEqual(len(r["tests"]), 4)

    def test_python_collection_error(self):
        gym = self.make_gym()
        work = stage(self, gym, "python-chunk")
        (work / "fixed" / "chunking.py").write_text("def chunk(:\n")
        r = bk.run_tests(gym, "python", work, "fixed")
        self.assertEqual(r["status"], "error")
        self.assertTrue(r["output"])

    def test_python_executable_missing(self):
        import os

        os.environ.pop("BUGKATA_PYTHON")
        with self.assertRaises(bk.BugkataError):
            bk.python_executable(self.make_gym())


if __name__ == "__main__":
    unittest.main()
