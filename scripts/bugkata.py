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
    raise BugkataError(f"unknown language {lang}")


# ------------------------------------------------------------------------ CLI

def _not_built(args) -> int:
    raise BugkataError(f"{args.command} is not built yet")


def _cmd_run(args) -> int:
    find_gym(Path.cwd())
    return _not_built(args)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bugkata.py")
    sub = p.add_subparsers(dest="command", required=True)

    v = sub.add_parser("verify")
    v.add_argument("staging")
    v.set_defaults(func=_not_built)

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
