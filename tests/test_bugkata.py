import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "bugkata.py"
FIXTURES = ROOT / "tests" / "fixtures"

_spec = importlib.util.spec_from_file_location("bugkata", SCRIPT)
bk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bk)

HAS_GO = shutil.which("go") is not None
HAS_PYTEST = importlib.util.find_spec("pytest") is not None

# Tests run the gym's Python harness with this interpreter instead of a gym venv.
os.environ.setdefault("BUGKATA_PYTHON", sys.executable)


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
        with mock.patch.dict(os.environ):
            os.environ.pop("BUGKATA_PYTHON", None)
            with self.assertRaises(bk.BugkataError):
                bk.python_executable(self.make_gym())


def copy_fixture(test: TempDirTest, fixture: str) -> Path:
    staging = test.tmpdir() / "staging"
    shutil.copytree(FIXTURES / fixture, staging)
    return staging


def edit_key(staging: Path, **changes) -> None:
    key = json.loads((staging / "key.json").read_text())
    key.update(changes)
    (staging / "key.json").write_text(json.dumps(key))


def assert_problem(test, problems, needle):
    test.assertTrue(any(needle in p for p in problems), f"{needle!r} not in {problems}")


class VerifyStaticTest(TempDirTest):
    def test_bad_key_fields(self):
        staging = copy_fixture(self, "go-truncate")
        key = json.loads((staging / "key.json").read_text())
        key["tier"] = "extreme"
        del key["bugs"][0]["why"]
        (staging / "key.json").write_text(json.dumps(key))
        problems = bk.verify(self.make_gym(), staging)
        assert_problem(self, problems, "key.json: tier")
        assert_problem(self, problems, "key.json: bug b1: why")
        self.assertEqual(len(problems), 2, problems)

    def test_reference_file_set_mismatch(self):
        staging = copy_fixture(self, "go-truncate")
        (staging / "reference" / "challenge_extra.go").write_text("//go:build !fixed\n\npackage truncate\n")
        assert_problem(self, bk.verify(self.make_gym(), staging), "layout:")

    def test_visible_test_with_hidden_prefix_rejected(self):
        staging = copy_fixture(self, "go-truncate")
        with open(staging / "visible" / "truncate_test.go", "a") as f:
            f.write("\nfunc TestHiddenX(t *testing.T) {}\n")
        assert_problem(self, bk.verify(self.make_gym(), staging), "TestHidden")

    def test_python_test_importing_challenge_rejected(self):
        staging = copy_fixture(self, "python-chunk")
        test = staging / "visible" / "test_chunk.py"
        test.write_text(test.read_text().replace("from target import", "from challenge import"))
        assert_problem(self, bk.verify(self.make_gym(), staging), "imports challenge")


@unittest.skipUnless(HAS_GO and HAS_PYTEST, "go and pytest needed")
class VerifyRunTest(TempDirTest):
    def test_verify_go_fixture_ok(self):
        self.assertEqual(bk.verify(self.make_gym(), copy_fixture(self, "go-truncate"), repeat=1), [])

    def test_verify_python_fixture_ok(self):
        staging = copy_fixture(self, "python-chunk")
        self.assertEqual(bk.verify(self.make_gym(), staging, repeat=2), [])

    def test_bug_test_passing_on_challenge(self):
        staging = copy_fixture(self, "go-truncate")
        shutil.copyfile(staging / "reference" / "challenge.go", staging / "visible" / "challenge.go")
        assert_problem(
            self,
            bk.verify(self.make_gym(), staging, repeat=1),
            "bug b1: TestHiddenTruncateMultibyte passes on challenge",
        )

    def test_key_test_typo(self):
        staging = copy_fixture(self, "go-truncate")
        key = json.loads((staging / "key.json").read_text())
        key["bugs"][0]["tests"] = ["TestHiddenTypo"]
        (staging / "key.json").write_text(json.dumps(key))
        problems = bk.verify(self.make_gym(), staging, repeat=1)
        assert_problem(self, problems, "bug b1: TestHiddenTypo not found")
        assert_problem(self, problems, "TestHiddenTruncateMultibyte fails on challenge but is not in the key")

    def test_reference_broken(self):
        staging = copy_fixture(self, "go-truncate")
        ref = staging / "reference" / "challenge.go"
        ref.write_text(ref.read_text().replace("return string(r[:n])", "return string(r[:n]) + \"!\""))
        assert_problem(self, bk.verify(self.make_gym(), staging, repeat=1), "TestTruncateASCII fails on reference")

    def test_tier_easy_needs_failing_visible(self):
        staging = copy_fixture(self, "go-truncate")
        edit_key(staging, tier="easy")
        assert_problem(self, bk.verify(self.make_gym(), staging, repeat=1), "tier easy")

    def test_cli_verify_exit_codes(self):
        gym = self.make_gym()
        r = cli(["verify", str(copy_fixture(self, "go-truncate")), "--repeat", "1"], cwd=gym)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(json.loads(r.stdout)["ok"])
        broken = copy_fixture(self, "go-truncate")
        edit_key(broken, tier="easy")
        r = cli(["verify", str(broken), "--repeat", "1"], cwd=gym)
        self.assertEqual(r.returncode, 1)
        self.assertFalse(json.loads(r.stdout)["ok"])


class EncodeTest(TempDirTest):
    def test_encode_go_installs_challenge(self):
        gym = self.make_gym()
        (gym / "go" / "001-old").mkdir(parents=True)
        (gym / "go" / "notes").mkdir()
        staging = copy_fixture(self, "go-truncate")
        path = bk.encode(gym, staging)
        self.assertEqual(path, "go/002-truncate")
        folder = gym / path
        self.assertEqual(
            sorted(p.name for p in folder.iterdir()),
            ["challenge.go", "fixed.go", "notes.md", "spec.md", "truncate_test.go"],
        )
        self.assertTrue((folder / "fixed.go").read_text().startswith("//go:build fixed\n"))
        self.assertIn("len(s) <= n", (folder / "fixed.go").read_text())  # a copy of challenge, not reference
        self.assertEqual((folder / "notes.md").read_text(), bk.NOTES_TEMPLATE)
        self.assertFalse(staging.exists())
        bundle = bk.load_bundle(gym, path)
        self.assertEqual(bundle["key"]["slug"], "truncate")
        self.assertIn("hidden_truncate_test.go", bundle["hidden"])
        self.assertIn("[]rune(s)", bundle["reference"]["challenge.go"])

    def test_encode_python_installs_challenge(self):
        gym = self.make_gym()
        path = bk.encode(gym, copy_fixture(self, "python-chunk"))
        self.assertEqual(path, "python/001-chunk")
        folder = gym / path
        for rel in ["challenge/chunking.py", "fixed/chunking.py", "fixed/__init__.py",
                    "conftest.py", "test_chunk.py", "spec.md", "notes.md"]:
            self.assertTrue((folder / rel).is_file(), rel)
        self.assertFalse(any(p.name.startswith("test_hidden") for p in folder.rglob("*")))

    def test_encode_refuses_existing_folder(self):
        gym = self.make_gym()
        bk.encode(gym, copy_fixture(self, "go-truncate"))
        with mock.patch.object(bk, "next_challenge_path", return_value="go/001-truncate"):
            with self.assertRaises(bk.BugkataError):
                bk.encode(gym, copy_fixture(self, "go-truncate"))

    def test_encode_rejects_static_problems(self):
        gym = self.make_gym()
        staging = copy_fixture(self, "go-truncate")
        edit_key(staging, tier="extreme")
        with self.assertRaises(bk.BugkataError) as cm:
            bk.encode(gym, staging)
        self.assertIn("tier", str(cm.exception))
        self.assertTrue(staging.exists())
        self.assertFalse((gym / "go").exists())

    def test_decode_writes_bundle(self):
        gym = self.make_gym()
        path = bk.encode(gym, copy_fixture(self, "go-truncate"))
        out = self.tmpdir() / "out"
        bk.decode(gym, path, out)
        self.assertEqual(json.loads((out / "key.json").read_text())["slug"], "truncate")
        self.assertTrue((out / "reference" / "challenge.go").is_file())
        self.assertTrue((out / "hidden" / "hidden_truncate_test.go").is_file())

    def test_decode_refuses_out_inside_gym(self):
        gym = self.make_gym()
        path = bk.encode(gym, copy_fixture(self, "go-truncate"))
        with self.assertRaises(bk.BugkataError):
            bk.decode(gym, path, gym / "go" / "spoilers")


@unittest.skipUnless(HAS_GO and HAS_PYTEST, "go and pytest needed")
class RunChallengeTest(TempDirTest):
    def installed(self, fixture):
        gym = self.make_gym()
        return gym, bk.encode(gym, copy_fixture(self, fixture))

    def test_installed_go_challenge_runs_directly(self):
        gym, path = self.installed("go-truncate")
        bk.write_go_mod(gym, gym / "go")
        rel = "./" + path.split("/", 1)[1] + "/"
        env = {**os.environ, "GOWORK": "off", "GOFLAGS": "-count=1"}
        for extra in ([], ["-tags", "fixed"]):
            r = subprocess.run(["go", "test", *extra, rel], cwd=gym / "go", env=env,
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_installed_python_challenge_runs_directly(self):
        gym, path = self.installed("python-chunk")
        for target in ("challenge", "fixed"):
            r = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
                cwd=gym / path, capture_output=True, text=True,
                env={**os.environ, "BUGKATA_TARGET": target, "PYTHONDONTWRITEBYTECODE": "1"},
            )
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_run_target_fixed_after_player_edit(self):
        gym, path = self.installed("go-truncate")
        fixed = gym / path / "fixed.go"
        fixed.write_text(fixed.read_text().replace("return s[:n]", "return s"))
        self.assertEqual(bk.run_challenge(gym, path, "fixed")["tests"], {"TestTruncateASCII": "fail"})
        self.assertEqual(bk.run_challenge(gym, path, "challenge")["tests"], {"TestTruncateASCII": "pass"})

    def test_run_leaves_gym_untouched(self):
        gym = self.make_gym()
        go_path = bk.encode(gym, copy_fixture(self, "go-truncate"))
        py_path = bk.encode(gym, copy_fixture(self, "python-chunk"))

        def snapshot():
            temps = [p for p in Path(tempfile.gettempdir()).iterdir() if p.name.startswith("bugkata-")]
            return sorted(str(p.relative_to(gym)) for p in gym.rglob("*")), len(temps)

        before = snapshot()
        for path in (go_path, py_path):
            r = cli(["run", path], cwd=gym / path)
            self.assertEqual(r.returncode, 0, r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(out["status"], "ok")
            self.assertEqual(out["target"], "fixed")
            self.assertEqual(out["challenge"], path)
        self.assertEqual(snapshot(), before)


if __name__ == "__main__":
    unittest.main()
