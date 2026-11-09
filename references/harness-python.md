# Python harness

How a Python challenge is laid out, written, staged, and run. `bugkata.py` does the mechanical parts (fixed copy, `conftest.py`, numbering, cache, temp-dir runs); this file is what the generator must get right.

## Challenge folder

What the player sees after `encode`:
```
python/014-rate-limiter/
├── spec.md            # intended behavior (Debug adds bug-report.md)
├── challenge/         # original package, never edited
│   ├── __init__.py
│   └── *.py
├── fixed/             # copy of challenge/ the player edits (made by bugkata)
├── conftest.py        # picks the target package (made by bugkata)
├── test_*.py          # visible tests; the player adds proof tests here
└── notes.md           # findings template (made by bugkata unless staged)
```
The gym's `python/` folder holds the venv (`python/.venv`, with pytest). Number and slug come from `encode`.

## Writing code

- Stdlib only. pytest is the only dependency, and only tests use it.
- All code lives in the `challenge/` package. Inside it, use relative imports only (`from .store import Store`), so the `fixed/` copy imports its own modules.
- `challenge/__init__.py` exports the public API.
- Tests import only from `target`: `from target import RateLimiter`. `conftest.py` makes `target` the `challenge` package, or `fixed` when `BUGKATA_TARGET=fixed`. Nothing may import `challenge` directly, or the fixed run would silently test challenge code.

## Tests

- Test function names are unique within the challenge. Parametrized cases roll up into one result per function (any failing case fails it).
- Hidden test files are named `test_hidden_*.py` and every test in them is `test_hidden_*`.
- Visible tests never use the `test_hidden_` prefix.
- Concurrency bugs are forced through a seam (injectable clock, scheduler, or hook), so the hidden test fails every run. Join threads with a timeout so a deadlock fails the test instead of hanging the run.

## Staging bundle

Write the bundle in a temp dir outside the gym (`mktemp -d`), never inside it:
```
<staging>/
├── key.json      # answer key
├── visible/      # the challenge folder minus fixed/ and conftest.py (notes.md optional)
├── reference/    # challenge/ with the bugs fixed: every file in visible/challenge/, same paths
└── hidden/       # test_hidden_*.py
```
`key.json`:
```json
{
  "lang": "python",
  "slug": "chunk",
  "mode": "audit",
  "tier": "medium",
  "bugs": [
    {
      "id": "b1",
      "category": "edge-cases",
      "location": "challenge/chunking.py:chunk",
      "what": "drops the last chunk when the list doesn't divide evenly",
      "why": "the loop runs len(items) // size times, which rounds the partial chunk away",
      "tests": ["test_hidden_chunk_uneven", "test_hidden_chunk_keeps_every_item"]
    }
  ]
}
```
`mode`: `audit`, `debug`, `review`, `test-review`. `tier`: `easy`, `medium`, `hard`. Each bug owns its hidden tests; no test is listed under two bugs. A full example lives in `tests/fixtures/python-chunk/` in the plugin repo.

## Commands

Run from inside the gym. `<bk>` is `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bugkata.py"`.

- `<bk> verify <staging>`: prints `{"ok", "problems", "seconds"}`, exit 0 if ok. Checks:
  - `key.json` fields and the hidden naming rules above
  - layout: `spec.md`, `challenge/__init__.py`, `reference/` matching `challenge/` exactly, no `import challenge` anywhere, no `fixed/` or `conftest.py` staged
  - reference: every visible and hidden test passes
  - challenge: every key test fails; hidden tests not in the key pass
  - all of the above holds 3 runs in a row (anything else is reported as flaky)
  - visible tests follow the tier: easy has at least one failing, medium has at least one and all pass, hard all pass (or none)
- `<bk> encode <staging>`: installs the bundle as the next `python/NNN-<slug>`, caches the key, deletes the staging dir. Prints `{"challenge": "python/NNN-slug"}`.
- `<bk> run <challenge> [--target challenge|fixed]`: runs visible and player tests (never hidden) in a temp dir. Default target `fixed`.

The player can also run tests directly from the challenge folder:
```
../.venv/bin/python -m pytest                        # challenge code
BUGKATA_TARGET=fixed ../.venv/bin/python -m pytest   # your fix
```
