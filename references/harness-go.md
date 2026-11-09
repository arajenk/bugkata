# Go harness

How a Go challenge is laid out, written, staged, and run. `bugkata.py` does the mechanical parts (fixed copies, numbering, cache, temp-dir runs); this file is what the generator must get right.

## Challenge folder

What the player sees after `encode`:
```
go/014-rate-limiter/
├── spec.md            # intended behavior (Debug adds bug-report.md)
├── challenge*.go      # original code, never edited
├── fixed*.go          # copy of challenge*.go the player edits (made by bugkata)
├── *_test.go          # visible tests; the player adds proof tests here
└── notes.md           # findings template (made by bugkata unless staged)
```
The gym's `go/` folder is one module. Number and slug come from `encode`.

## Writing code

- Stdlib only. No `go.sum`, no third-party imports.
- Every non-test file is `challenge*.go` at the top level, and its first line is exactly `//go:build !fixed`. bugkata flips it to `//go:build fixed` in the `fixed*.go` copy, so `go test` runs challenge code and `go test -tags fixed` runs the player's fix.
- One package per challenge. The package name must be a valid identifier (the slug may have hyphens; the package can't).
- Tests have no build tag, so the same tests run against both targets.

## Tests

- Test names are unique within the challenge. Results are tracked by top-level test name; subtests roll up into their parent.
- Hidden test files are named `hidden_*_test.go` and every test in them is `TestHidden*`. Helpers in hidden files also start with `hidden`. Hidden tests share the package namespace with the player's tests, so a clash would break grading.
- Visible tests never use the `TestHidden` prefix.
- Concurrency bugs are forced through a seam (injectable clock, scheduler, or hook), so the hidden test fails every run. Give such tests their own deadline (`select` with `time.After`) so a deadlock fails the test instead of hanging the run.

## Staging bundle

Write the bundle in a temp dir outside the gym (`mktemp -d`), never inside it:
```
<staging>/
├── key.json      # answer key
├── visible/      # the challenge folder minus fixed*.go (notes.md optional)
├── reference/    # fixed versions of every challenge*.go, same names, same `//go:build !fixed` line
└── hidden/       # hidden_*_test.go
```
`key.json`:
```json
{
  "lang": "go",
  "slug": "truncate",
  "mode": "audit",
  "tier": "medium",
  "bugs": [
    {
      "id": "b1",
      "category": "edge-cases",
      "location": "challenge.go:Truncate",
      "what": "cuts multi-byte strings in the middle of a character",
      "why": "len and slicing count bytes, not runes",
      "tests": ["TestHiddenTruncateMultibyte"]
    }
  ]
}
```
`mode`: `audit`, `debug`, `review`, `test-review`. `tier`: `easy`, `medium`, `hard`. Each bug owns its hidden tests; no test is listed under two bugs. A full example lives in `tests/fixtures/go-truncate/` in the plugin repo.

## Commands

Run from inside the gym. `<bk>` is `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bugkata.py"`.

- `<bk> verify <staging>`: prints `{"ok", "problems", "seconds"}`, exit 0 if ok. Checks:
  - `key.json` fields and the hidden naming rules above
  - layout: `spec.md`, the tag line, `reference/` matching the code files exactly, no `fixed*` files staged
  - reference (with `-race`): every visible and hidden test passes
  - challenge: every key test fails; hidden tests not in the key pass
  - all of the above holds 3 runs in a row (anything else is reported as flaky)
  - visible tests follow the tier: easy has at least one failing, medium has at least one and all pass, hard all pass (or none)
- `<bk> encode <staging>`: installs the bundle as the next `go/NNN-<slug>`, caches the key, deletes the staging dir. Prints `{"challenge": "go/NNN-slug"}`.
- `<bk> run <challenge> [--target challenge|fixed]`: runs visible and player tests (never hidden) in a temp dir. Default target `fixed`.

The player can also run tests directly from the gym's `go/` folder:
```
go test ./014-rate-limiter/              # challenge code
go test -tags fixed ./014-rate-limiter/  # your fix
```
