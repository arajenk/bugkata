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
