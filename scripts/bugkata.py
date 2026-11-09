"""bugkata's mechanical work, so skills spend no tokens on it. Stdlib only.

Subcommands (run from anywhere inside the gym; the gym is found automatically):
  verify <staging>              check a generated challenge bundle by running it
  encode <staging>              install a verified bundle as the next challenge
  decode <challenge> --out DIR  unpack a challenge's cached key outside the gym
  run <challenge> [--target challenge|fixed]
                                run the visible and player tests

Output is JSON on stdout. Errors print `bugkata: <message>` and exit 2.
This script never prints answer keys, reference code, or hidden test code.
"""

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path


class BugkataError(Exception):
    pass


# ---------------------------------------------------------------- gym + cache

def find_gym(start: Path) -> Path:
    """Walk up from `start` to the gym: the dir holding `.bugkata/state.json`.

    The marker is state.json, not just `.bugkata/`, because the global
    `~/.bugkata/` would otherwise make the home dir look like a gym.
    """
    for d in [start.resolve(), *start.resolve().parents]:
        if (d / ".bugkata" / "state.json").is_file():
            return d
    raise BugkataError("not inside a bugkata gym (run /bugkata:start)")


def cache_path(gym: Path, challenge: str) -> Path:
    name = hashlib.sha256(challenge.encode()).hexdigest()[:16]
    return gym / ".bugkata" / "cache" / f"{name}.dat"


def encode_bundle(bundle: dict) -> str:
    return base64.b64encode(json.dumps(bundle).encode()).decode()


def decode_bundle(text: str) -> dict:
    return json.loads(base64.b64decode(text).decode())


def save_bundle(gym: Path, challenge: str, bundle: dict) -> None:
    path = cache_path(gym, challenge)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(encode_bundle(bundle))


def load_bundle(gym: Path, challenge: str) -> dict:
    path = cache_path(gym, challenge)
    if not path.is_file():
        raise BugkataError(f"no cached key for {challenge}")
    return decode_bundle(path.read_text())


# ------------------------------------------------------------------- harness
#
# A "workdir" is a temp dir outside the gym holding one challenge's files,
# ready to run: challenge code, fixed copies, tests, and the language harness
# (go.mod, or conftest.py + pytest.ini). run_tests never copies; it only runs.

GO_TAG_CHALLENGE = "//go:build !fixed"
GO_TAG_FIXED = "//go:build fixed"
OUTPUT_TAIL_LINES = 40


def is_code_file(lang: str, relpath: str) -> bool:
    """True for files the player fixes: the ones that get a fixed copy."""
    if lang == "go":
        return (
            "/" not in relpath
            and relpath.startswith("challenge")
            and relpath.endswith(".go")
            and not relpath.endswith("_test.go")
        )
    if lang == "python":
        return relpath.startswith("challenge/")
    return False


def make_fixed(lang: str, code: dict[str, str]) -> dict[str, str]:
    """Map challenge code files to their fixed copies (relpath -> text)."""
    out = {}
    for rel, text in code.items():
        if lang == "go":
            first, _, rest = text.partition("\n")
            if first.strip() != GO_TAG_CHALLENGE:
                raise BugkataError(f"{rel}: first line must be `{GO_TAG_CHALLENGE}`")
            out["fixed" + rel[len("challenge"):]] = GO_TAG_FIXED + "\n" + rest
        elif lang == "python":
            out["fixed/" + rel[len("challenge/"):]] = text
        else:
            raise BugkataError(f"unknown language {lang}")
    return out


def _local_go_version() -> str:
    out = subprocess.run(["go", "env", "GOVERSION"], capture_output=True, text=True).stdout
    m = re.match(r"go(\d+\.\d+)", out.strip())
    if not m:
        raise BugkataError("could not read the local Go version (is go installed?)")
    return m.group(1)


def write_go_mod(gym: Path, workdir: Path) -> None:
    gym_mod = gym / "go" / "go.mod"
    if gym_mod.is_file():
        shutil.copyfile(gym_mod, workdir / "go.mod")
    else:
        (workdir / "go.mod").write_text(f"module bugkata-gym\n\ngo {_local_go_version()}\n")


def _tail(text: str) -> str:
    return "\n".join(text.strip().splitlines()[-OUTPUT_TAIL_LINES:])


def _run_go(workdir: Path, target: str, race: bool, timeout: int) -> dict:
    cmd = ["go", "test", "-json", "-count=1", f"-timeout={timeout}s"]
    if target == "fixed":
        cmd.append("-tags=fixed")
    if race:
        cmd.append("-race")
    cmd.append(".")
    env = {**os.environ, "GOWORK": "off", "GOFLAGS": "", "GOTOOLCHAIN": "local"}
    try:
        proc = subprocess.run(
            cmd, cwd=workdir, env=env, capture_output=True, text=True, timeout=timeout + 30
        )
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout or ""
        if isinstance(out, bytes):
            out = out.decode(errors="replace")
        return {"status": "timeout", "tests": {}, "output": _tail(out)}

    tests: dict[str, str] = {}
    text_out = []
    for line in proc.stdout.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            text_out.append(line)
            continue
        if ev.get("Output"):
            text_out.append(ev["Output"].rstrip("\n"))
        name = ev.get("Test")
        if not name or "/" in name:
            continue
        action = ev.get("Action")
        if action == "run":
            tests.setdefault(name, "fail")  # started but never finished counts as fail
        elif action in ("pass", "fail", "skip"):
            tests[name] = action
    output = "\n".join(text_out) + "\n" + proc.stderr

    if race and "requires cgo" in output:
        return _run_go(workdir, target, False, timeout)
    if "panic: test timed out" in output:
        return {"status": "timeout", "tests": tests, "output": _tail(output)}
    if proc.returncode != 0 and not tests:
        return {"status": "error", "tests": {}, "output": _tail(output)}
    return {"status": "ok", "tests": tests, "output": ""}


CONFTEST = '''\
"""Written by bugkata. Picks which package the tests import as `target`.

BUGKATA_TARGET=challenge (default) tests challenge/, BUGKATA_TARGET=fixed tests fixed/.
"""
import importlib
import os
import sys

_name = os.environ.get("BUGKATA_TARGET", "challenge")
if _name not in ("challenge", "fixed"):
    raise RuntimeError("BUGKATA_TARGET must be 'challenge' or 'fixed', not %r" % _name)
sys.modules["target"] = importlib.import_module(_name)
'''


def write_python_harness(workdir: Path) -> None:
    (workdir / "conftest.py").write_text(CONFTEST)
    # An inifile here stops pytest from searching parent dirs for config.
    (workdir / "pytest.ini").write_text("[pytest]\n")


def python_executable(gym: Path) -> str:
    override = os.environ.get("BUGKATA_PYTHON")
    if override:
        return override
    venv = gym / "python" / ".venv" / "bin" / "python"
    if not venv.exists():
        raise BugkataError("python venv missing (run /bugkata:start python)")
    return str(venv)


_RESULT_RANK = {"pass": 0, "skip": 1, "fail": 2}


def _run_python(gym: Path, workdir: Path, target: str, timeout: int) -> dict:
    report_dir = Path(tempfile.mkdtemp(prefix="bugkata-"))
    try:
        junit = report_dir / "junit.xml"
        cmd = [
            python_executable(gym), "-m", "pytest", "-q", "-p", "no:cacheprovider",
            f"--junitxml={junit}",
        ]
        env = {**os.environ, "BUGKATA_TARGET": target, "PYTHONDONTWRITEBYTECODE": "1"}
        try:
            proc = subprocess.run(
                cmd, cwd=workdir, env=env, capture_output=True, text=True, timeout=timeout
            )
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout or ""
            if isinstance(out, bytes):
                out = out.decode(errors="replace")
            return {"status": "timeout", "tests": {}, "output": _tail(out)}
        output = proc.stdout + "\n" + proc.stderr
        if proc.returncode not in (0, 1, 5):
            return {"status": "error", "tests": {}, "output": _tail(output)}

        tests: dict[str, str] = {}
        if junit.is_file():
            for case in ET.parse(junit).iter("testcase"):
                name = case.get("name", "").split("[", 1)[0]
                if case.find("failure") is not None or case.find("error") is not None:
                    result = "fail"
                elif case.find("skipped") is not None:
                    result = "skip"
                else:
                    result = "pass"
                if _RESULT_RANK[result] >= _RESULT_RANK.get(tests.get(name, "pass"), 0):
                    tests[name] = result
        return {"status": "ok", "tests": tests, "output": ""}
    finally:
        shutil.rmtree(report_dir, ignore_errors=True)


def run_tests(
    gym: Path, lang: str, workdir: Path, target: str, *, race: bool = False, timeout: int = 120
) -> dict:
    """Run every test in `workdir` against `target` ("challenge" or "fixed").

    Returns {"status": "ok"|"error"|"timeout", "tests": {name: "pass"|"fail"|"skip"},
    "output": last lines of output when status isn't ok}.
    """
    if target not in ("challenge", "fixed"):
        raise BugkataError(f"unknown target {target}")
    if lang == "go":
        return _run_go(workdir, target, race, timeout)
    if lang == "python":
        return _run_python(gym, workdir, target, timeout)
    raise BugkataError(f"unknown language {lang}")


# --------------------------------------------------------------------- verify
#
# A staging bundle, written by the generator in a temp dir outside the gym:
#   key.json    answer key (lang, slug, mode, tier, bugs)
#   visible/    what the player gets
#   reference/  fixed versions of the code files in visible/, same paths
#   hidden/     grading tests

LANGS = ("go", "python")
MODES = ("audit", "debug", "review", "test-review")
TIERS = ("easy", "medium", "hard")
SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
BUG_TEXT_FIELDS = ("category", "location", "what", "why")
HIDDEN_TEST_PREFIX = {"go": "TestHidden", "python": "test_hidden_"}
HIDDEN_FILE = {"go": re.compile(r"^hidden_\w+_test\.go$"), "python": re.compile(r"^test_hidden_\w+\.py$")}
TEST_FUNC = {"go": re.compile(r"^func\s+(Test\w+)", re.M), "python": re.compile(r"^\s*def\s+(test_\w+)", re.M)}
IMPORTS_CHALLENGE = re.compile(r"^\s*(from|import)\s+challenge\b", re.M)


def _read_tree(root: Path) -> dict[str, str]:
    if not root.is_dir():
        return {}
    return {
        p.relative_to(root).as_posix(): p.read_text()
        for p in sorted(root.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts and ".pytest_cache" not in p.parts
    }


def read_staging(staging: Path) -> dict:
    key_file = staging / "key.json"
    if not key_file.is_file():
        raise BugkataError(f"{staging} has no key.json")
    try:
        key = json.loads(key_file.read_text())
    except json.JSONDecodeError as err:
        raise BugkataError(f"key.json is not valid JSON: {err}")
    return {
        "key": key,
        "visible": _read_tree(staging / "visible"),
        "reference": _read_tree(staging / "reference"),
        "hidden": _read_tree(staging / "hidden"),
    }


def stage_workdir(gym: Path, lang: str, files: dict[str, str]) -> Path:
    """Write `files` plus the language harness into a new temp dir. Caller deletes it."""
    workdir = Path(tempfile.mkdtemp(prefix="bugkata-"))
    for rel, text in files.items():
        dest = workdir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text)
    if lang == "go":
        write_go_mod(gym, workdir)
    else:
        write_python_harness(workdir)
    return workdir


def _key_problems(key: dict) -> list[str]:
    problems = []
    if not isinstance(key, dict):
        return ["key.json: must be an object"]
    lang = key.get("lang")
    if lang not in LANGS:
        problems.append(f"key.json: lang must be one of {', '.join(LANGS)}")
    if not isinstance(key.get("slug"), str) or not SLUG.match(key["slug"]):
        problems.append("key.json: slug must be lowercase words joined by hyphens")
    if key.get("mode") not in MODES:
        problems.append(f"key.json: mode must be one of {', '.join(MODES)}")
    if key.get("tier") not in TIERS:
        problems.append(f"key.json: tier must be one of {', '.join(TIERS)}")
    bugs = key.get("bugs")
    if not isinstance(bugs, list) or not bugs:
        problems.append("key.json: bugs must be a nonempty list")
        return problems
    seen_ids, seen_tests = set(), set()
    for i, bug in enumerate(bugs):
        if not isinstance(bug, dict):
            problems.append(f"key.json: bug {i} must be an object")
            continue
        bid = bug.get("id")
        if not isinstance(bid, str) or not bid:
            problems.append(f"key.json: bug {i}: id missing")
            bid = str(i)
        elif bid in seen_ids:
            problems.append(f"key.json: bug {bid}: duplicate id")
        seen_ids.add(bid)
        for field in BUG_TEXT_FIELDS:
            if not isinstance(bug.get(field), str) or not bug[field].strip():
                problems.append(f"key.json: bug {bid}: {field} missing")
        tests = bug.get("tests")
        if not isinstance(tests, list) or not tests or not all(isinstance(t, str) for t in tests):
            problems.append(f"key.json: bug {bid}: tests must be a nonempty list of names")
            continue
        for t in tests:
            if t in seen_tests:
                problems.append(f"key.json: bug {bid}: {t} is listed under another bug")
            seen_tests.add(t)
            if lang in LANGS and not t.startswith(HIDDEN_TEST_PREFIX[lang]):
                problems.append(f"key.json: bug {bid}: {t} must start with {HIDDEN_TEST_PREFIX[lang]}")
    return problems


def _layout_problems(lang: str, staged: dict) -> list[str]:
    visible, reference, hidden = staged["visible"], staged["reference"], staged["hidden"]
    problems = []
    if "spec.md" not in visible:
        problems.append("layout: visible/spec.md missing")
    code = {rel for rel in visible if is_code_file(lang, rel)}
    if not code:
        problems.append("layout: visible/ has no challenge code files")
    if set(reference) != code:
        problems.append(
            "layout: reference/ must hold exactly the code files in visible/ "
            f"(visible: {sorted(code)}, reference: {sorted(reference)})"
        )
    if not hidden:
        problems.append("layout: hidden/ has no test files")
    prefix = HIDDEN_TEST_PREFIX[lang]
    for rel, text in hidden.items():
        if not HIDDEN_FILE[lang].match(rel):
            problems.append(f"layout: hidden/{rel} must be named like {HIDDEN_FILE[lang].pattern}")
        if rel in visible:
            problems.append(f"layout: hidden/{rel} clashes with a visible file")
        for name in TEST_FUNC[lang].findall(text):
            if not name.startswith(prefix):
                problems.append(f"layout: hidden test {name} must start with {prefix}")
    for rel, text in visible.items():
        if rel.endswith((".go", ".py")):
            for name in TEST_FUNC[lang].findall(text):
                if name.startswith(prefix):
                    problems.append(f"layout: visible test {name} uses the hidden prefix {prefix}")
    if "conftest.py" in visible:
        problems.append("layout: visible/conftest.py is written by bugkata, remove it")

    if lang == "go":
        for rel in visible:
            if not rel.endswith(".go"):
                continue
            if "/" in rel:
                problems.append(f"layout: visible/{rel}: Go files must sit at the top level")
            elif rel.startswith("fixed"):
                problems.append(f"layout: visible/{rel}: fixed copies are made by bugkata")
            elif not rel.endswith("_test.go") and rel not in code:
                problems.append(f"layout: visible/{rel}: non-test Go files must be named challenge*.go")
        for side, files in (("visible", visible), ("reference", reference)):
            for rel in sorted(code & set(files)):
                if files[rel].partition("\n")[0].strip() != GO_TAG_CHALLENGE:
                    problems.append(f"layout: {side}/{rel}: first line must be `{GO_TAG_CHALLENGE}`")
    else:
        if "challenge/__init__.py" not in visible:
            problems.append("layout: visible/challenge/__init__.py missing")
        if any(rel.startswith("fixed/") for rel in visible):
            problems.append("layout: visible/fixed/ is made by bugkata, remove it")
        for side, files in (("visible", visible), ("reference", reference), ("hidden", hidden)):
            for rel, text in files.items():
                if rel.endswith(".py") and IMPORTS_CHALLENGE.search(text):
                    problems.append(
                        f"layout: {side}/{rel} imports challenge "
                        "(tests import target; package code uses relative imports)"
                    )
    return problems


def static_problems(staged: dict) -> list[str]:
    problems = _key_problems(staged["key"])
    if problems:
        return problems
    return _layout_problems(staged["key"]["lang"], staged)


def _tier_problems(tier: str, visible_results: dict[str, str]) -> list[str]:
    failing = sorted(t for t, r in visible_results.items() if r != "pass")
    if tier == "easy":
        return [] if failing else ["tier easy: no visible test fails on challenge"]
    problems = [f"tier {tier}: visible {t} fails on challenge" for t in failing]
    if tier == "medium" and not visible_results:
        problems.append("tier medium: no visible tests")
    return problems


def verify(gym: Path, staging: Path, repeat: int = 3, timeout: int = 120) -> list[str]:
    """Check a staging bundle. Returns problems; an empty list means it's sound."""
    staged = read_staging(staging)
    problems = static_problems(staged)
    if problems:
        return problems
    key = staged["key"]
    lang = key["lang"]
    prefix = HIDDEN_TEST_PREFIX[lang]
    files = {**staged["visible"], **staged["hidden"], **make_fixed(lang, staged["reference"])}

    workdir = stage_workdir(gym, lang, files)
    try:
        runs = {"fixed": [], "challenge": []}
        for _ in range(max(1, repeat)):
            for target in ("fixed", "challenge"):
                result = run_tests(
                    gym, lang, workdir, target, race=(lang == "go" and target == "fixed"),
                    timeout=timeout,
                )
                if result["status"] != "ok":
                    label = "reference" if target == "fixed" else "challenge"
                    return [f"{label}: {result['status']}: {result['output']}"]
                runs[target].append(result["tests"])
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    for target, results in runs.items():
        for name in sorted(set().union(*results)):
            if len({r.get(name) for r in results}) > 1:
                problems.append(f"flaky: {name} ({'reference' if target == 'fixed' else 'challenge'})")
    ref, chal = runs["fixed"][0], runs["challenge"][0]

    for name, result in sorted(ref.items()):
        if result != "pass":
            problems.append(f"{name} fails on reference")
    key_tests = set()
    for bug in key["bugs"]:
        for t in bug["tests"]:
            key_tests.add(t)
            if t not in chal:
                problems.append(f"bug {bug['id']}: {t} not found")
            elif chal[t] != "fail":
                problems.append(f"bug {bug['id']}: {t} passes on challenge")
    for name, result in sorted(chal.items()):
        if name.startswith(prefix) and name not in key_tests and result != "pass":
            problems.append(f"hidden {name} fails on challenge but is not in the key")
    visible_results = {n: r for n, r in chal.items() if not n.startswith(prefix)}
    problems += _tier_problems(key["tier"], visible_results)
    return problems


# ------------------------------------------------------------------------ CLI

def _not_built(args) -> int:
    raise BugkataError(f"{args.command} is not built yet")


def _print(obj: dict) -> None:
    print(json.dumps(obj, indent=2))


def _cmd_verify(args) -> int:
    gym = find_gym(Path.cwd())
    start = time.monotonic()
    problems = verify(gym, Path(args.staging), repeat=args.repeat, timeout=args.timeout)
    _print({"ok": not problems, "problems": problems, "seconds": round(time.monotonic() - start, 1)})
    return 0 if not problems else 1


def _cmd_run(args) -> int:
    find_gym(Path.cwd())
    return _not_built(args)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bugkata.py")
    sub = p.add_subparsers(dest="command", required=True)

    v = sub.add_parser("verify")
    v.add_argument("staging")
    v.add_argument("--repeat", type=int, default=3)
    v.add_argument("--timeout", type=int, default=120)
    v.set_defaults(func=_cmd_verify)

    e = sub.add_parser("encode")
    e.add_argument("staging")
    e.set_defaults(func=_not_built)

    d = sub.add_parser("decode")
    d.add_argument("challenge")
    d.add_argument("--out", required=True)
    d.set_defaults(func=_not_built)

    r = sub.add_parser("run")
    r.add_argument("challenge")
    r.set_defaults(func=_cmd_run)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except BugkataError as err:
        print(f"bugkata: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
