# bugkata

A Claude Code plugin: **LeetCode for the AI age.** AI writes code fast, and most devs can't confidently review or debug code they didn't write, AI-generated or not. bugkata generates realistic code with realistic bugs, grades you by running real tests, and adapts difficulty to you.

Open source (MIT), no server, no accounts. Markdown skills, small Python scripts, and dot-folders.

## Principles
- **Realistic bugs.** Bugs look like real-world failures (spec mismatch, wrong API assumptions, edge cases, error handling), not random one-character edits.
- **Deterministic.** Any bug that needs a proof test can be triggered on demand. A bug that can't is only allowed in Review mode.
- **Fair.** Every bug is findable from the code plus `spec.md`, without guessing intent.
- **Encouraging.** Every challenge has at least one bug, scores never go negative, and reveals explain where the player went wrong, not just the answer.
- **Token-frugal.** Accept a little more latency to save a lot of tokens. Scripts do the mechanical work.

## Install
```
/plugin marketplace add <owner>/bugkata
/plugin install bugkata@bugkata
```
First launch checks for `python3` (and `go` for Go) and names anything missing. Python challenges run in a gym venv with pytest.

## Plugin layout
```
.claude-plugin/
├── plugin.json
└── marketplace.json
skills/                      # each folder is one command: /bugkata:<name>
├── start/SKILL.md           # set up gym, launch, serve challenge
├── submit/SKILL.md
├── hint/SKILL.md
├── skip/SKILL.md
├── reset/SKILL.md
├── stats/SKILL.md
├── config/SKILL.md
└── help/SKILL.md
references/                  # shared by all skills
├── core.md                  # shared rules: no spoilers, state handling
├── modes.md                 # per-mode generation and grading
├── bug-catalog.md
├── difficulty.md            # tiers, levers, Elo
├── harness-go.md            # layout, targeting, run commands
├── harness-python.md
└── tutorial.md
scripts/
└── bugkata.py               # stdlib only: encode/decode, verify, run, score, rate
agents/
└── challenge-reviewer.md
hooks/
└── hooks.json               # blocks direct access to the answer cache
README.md
LICENSE
```
`hint`, `skip`, `submit`, and `reset` set `disable-model-invocation: true` so they only run when typed; an auto-fired hint or reveal would spoil a challenge or cost points. `start`, `help`, `stats`, and `config` can trigger from plain English ("give me a go challenge", "how am I doing?"). Skills stay thin and point to `references/` and `scripts/`.

Plugin mechanics (verified in build step 1, on a real GitHub install):
- `${CLAUDE_PLUGIN_ROOT}` and `$ARGUMENTS` are substituted in SKILL.md text. `CLAUDE_PLUGIN_ROOT` is empty in Bash, so skills write paths with the text form (`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bugkata.py"`). Hooks do get it as an env var.
- Args arrive raw (spacing and quotes kept); skills normalize them.
- `disable-model-invocation` works, but Claude may route a plain-English "give me a hint" to `start` instead. `start` must redirect hint/skip/submit/reset requests to the typed command and never act on them.

## The gym
Users practice in a dedicated gym repo, never a real project. One gym, one folder per language:
```
bugkata-gym/
├── go/           # go module
├── python/       # venv + pytest
└── .bugkata/     # gitignored
```
`/bugkata:start` in an empty directory runs `git init`, writes `.gitignore` (with `.bugkata/`), and scaffolds the first language. Other language folders are created on first use. Challenges are committed, so git history doubles as a progress log.

Launch languages: Go and Python.

## State
Machine-read state is JSON, written only by `bugkata.py`.

Global `~/.bugkata/`:
- `profile.json`: ratings per mode × language, rating history, tendencies (false-positive rate, skipped proofs, hint usage, per-category miss rates), time stats
- `retry-queue.json`: missed patterns with date and language, challenges since last retry
- `config.json`: tutorial flag, prefetch on/off, reviewer model
- `patterns.md`: personal "how to spot it" cheat sheet, one entry per pattern

Gym `.bugkata/`:
- `state.json`: current focus, current challenge, per-language counters
- `cache/`: answer keys, reference fixes, hidden tests, prefetched challenge. One file per challenge, named by a hash of its path, base64-encoded JSON; light obfuscation only.

The gym root is the folder holding `.bugkata/state.json`. A bare `.bugkata/` folder isn't enough, since `~/.bugkata/` would make the home dir look like a gym.

Spoiler protection is layered, and aims at accidental spoilers, not determined cheaters (cheating only cheats yourself):
1. Generation runs in a separate context, so the player's session never sees the key.
2. A `PreToolUse` hook (`scripts/guard.py`) blocks any Read, Grep, Glob, or Bash call that mentions `.bugkata`, except a plain call to `bugkata.py`. Claude Code enforces this, so it doesn't depend on Claude following instructions. `bugkata.py` finds the gym and `~/.bugkata/` itself, so skills never put `.bugkata` paths in commands. The guard fails open (malformed input is allowed), since it only targets accidental spoilers.
3. `bugkata.py` never prints answers outside `hint`, `skip`, and `submit` output.
4. A rule in `references/core.md` as a backup.

Claude can still read the challenge code and find the bugs itself if asked. That's solving the puzzle for the player, not a leak.

## Modes
Mode rotates by default toward weak spots. Args override (`/bugkata:start debug`, `/bugkata:start go review hard`). Rotation and args only offer modes that are built; `references/modes.md` lists which ones are live.

| Mode | Player gets | Player produces |
|---|---|---|
| Audit | component + `spec.md`, 1–5 bugs (count hidden) | findings in `notes.md`, `fixed.*`, proof tests |
| Debug | small runnable program + `bug-report.md` (symptom, logs or trace) | repro test, root cause in `notes.md`, fix |
| Review | `pr.md` + `change.diff`, with the PR already applied to the code | `review.md` with line-referenced comments; proof tests on Medium/Hard |
| Test review | code + a passing test suite that misses bugs | tests that catch what the suite missed, findings in `notes.md` |

`references/modes.md` defines each mode's files and how the grading rubric applies to it.

## Challenge folder (Go)
```
go/014-rate-limiter/
├── spec.md          # intended behavior (Debug adds bug-report.md)
├── challenge*.go    # original code, never edited
├── fixed*.go        # starts as a copy of challenge; the user edits this
├── *_test.go        # visible tests + user's proof tests
└── notes.md         # template: location / claim / why / proof
```
Numbering is per language. Go selects the target with build tags (`go test` vs `go test -tags fixed`); challenge files carry `//go:build !fixed`, and the `fixed*.go` copies get the tag flipped to `fixed`. Python uses `challenge/` and `fixed/` packages selected by an env var in `conftest.py`. Full details live in the harness refs. To grade, the code and hidden tests are copied into a temp dir outside the gym, run, and deleted.

## Bug catalog
`references/bug-catalog.md` has Go and Python examples for each category. The AI-specific categories follow Tambon et al., "Bugs in Large Language Models Generated Code" (arXiv 2403.08937).
- Spec mismatch: solves a nearby problem instead of the one asked
- Overfit to the examples: handles the cases shown in the spec's examples but not the general requirement
- Unrequested behavior: does something the spec never asked for, and the extra part breaks something
- Incomplete wiring: a piece exists but isn't connected (config read but unused, error path stubbed out)
- Wrong or made-up API assumptions: in-place vs copy, nil/None returns, thread safety, mutability, wrong attribute. In Python, a method that doesn't exist, which only fails on the path that calls it.
- Edge cases: empty input, boundaries, unicode, time zones/DST, floats, overflow
- Error handling: swallowed errors, partial failure leaving bad state, non-idempotent retries
- State and lifecycle: stale caches, init order, leaks, double close
- Concurrency: races, lock ordering, missed cancellation. Always built with seams (injectable clock, scheduler, hooks) so the bug can be forced.
- Security: injection, missing authz, trusting input, secrets in logs
- Performance: accidental O(n²), N+1 queries, unbounded growth
- Tests that lie: assertions that can't fail, over-mocking, happy-path-only
- Slips: classic one-liners, used sparingly

## Difficulty
Tiers measure effort, not length. A Hard challenge shouldn't take much longer than a Medium, but it should make you work harder. Each challenge mixes whichever levers fit the problem:
- multiple files, with bugs in the interactions between them
- visible tests that pass or mislead
- more and better decoys
- a terser spec (still unambiguous)
- symptoms far from the cause (Debug)
- trivial bug categories banned

Visible tests by tier. They may be incomplete, but they never contradict `spec.md`:

| Tier | Visible tests |
|---|---|
| Easy | real tests, at least one failing, pointing at a bug |
| Medium | real happy-path tests that all pass; bugs live in what they don't cover |
| Hard | weak passing tests or none; the player writes their own |

Hidden tests always do the grading.

Selection targets a challenge rating D slightly above the player's rating R (about R + 50). Tier is derived from D using bands in `references/difficulty.md`; a tier arg sets D to that band's midpoint. Solve quality drives difficulty. Time is a secondary nudge: if a tier's median time falls well under target, the next challenge uses more levers.

Elo, tracked per mode × language:
- Expected E = 1 / (1 + 10^((D − R)/400)); R ← R + K(S − E), with S the score in [0, 1].
- K = 40 for the first 10 challenges in a mode × language, then 20.
- The reviewer's estimate becomes the challenge's final D.
- A new mode × language starts from the player's other ratings in that language, or the tutorial result if there are none.

## Generation pipeline
Generation runs in a separate subagent (spoiler layer 1, see State). It returns only the challenge path and a one-line status, never code, bugs, or test contents.

1. Pick mode, target D, and bug categories weighted toward weak spots, or serve a due retry.
2. Write correct, idiomatic code and `spec.md`, then inject bugs and decoys. No tells: no hint comments, odd names, or style shifts.
3. Script verification (no tokens): the generator writes a staging bundle (`key.json`, `visible/`, `reference/`, `hidden/`) in a temp dir outside the gym, and `bugkata.py verify` runs it. Each bug has its own hidden tests, which must fail on challenge and pass on the reference fix. Visible tests must behave as the tier requires. Concurrency bugs are forced through seams, with `-race` and repeated runs as a backstop. The harness refs define the bundle and the naming rules.
4. Reviewer subagent (`agents/challenge-reviewer.md`): a teacher checking the exam before students see it. One fresh subagent, two passes in the same context:
   - Blind pass, without the key: looks for tells and accidental bugs. The share of real bugs it finds feeds the difficulty estimate.
   - Keyed pass, with the key given after the blind report: checks fairness, spec clarity, decoy quality, and difficulty against target D, and notes why it missed what it missed.
5. Patch anything flagged instead of regenerating, rerun script verification, and re-check only the flagged items. After 2 fix rounds, discard and start over.
6. Write files: `bugkata.py encode` installs the bundle as the next numbered folder, makes the `fixed*` copies, and moves the key, reference, and hidden tests into the cache. Output nothing that explains, runs, or hints at the bugs.

## Token budget
- Scripts handle verify, run, score, rate, and encode.
- The reviewer uses a cheaper model by default (set in `config.json`).
- Prefetch at most one challenge, never a pool. Generate it in the background after submit or skip, cache it, and serve it on the next matching launch, including in a later session. `prefetch: false` turns this off. A background job finishes if the session stays open but dies if the user quits (verified in build step 1), so a prefetch is written to a temp name and only marked ready once complete; anything unfinished is discarded and `start` generates fresh.
- Code size grows with tier only as needed; difficulty comes from levers, not volume.
- Generation latency is logged per challenge so the user can judge the cost/latency tradeoff.

## Commands
Commands are namespaced as `/bugkata:<skill>`, with no space.
- `/bugkata:start [language] [mode] [tier]`: set up the gym, or continue (`Still doing <focus>? yes / switch`)
- `/bugkata:submit`: grade. Grading only happens on explicit submit.
- `/bugkata:hint`: up to 3 hints per challenge: where to look, then what to think about, then a near-answer
- `/bugkata:skip`: give up. Teaching reveal, log the miss, queue the pattern, then stop without serving a new challenge.
- `/bugkata:reset <n>`: restore `fixed.*` and `notes.md` for a challenge
- `/bugkata:stats`: ratings, history, average time per tier and mode, tendencies, generation latency
- `/bugkata:config`: change focus or prefs
- `/bugkata:help`: one-screen cheat sheet

## Grading
`bugkata.py score` does the math; the LLM only judges claims. Each bug is worth 100 points, and the total is normalized to [0, 1]:
- Found (30): correct location and what goes wrong ("Get returns a stale entry after Set"). A location alone earns nothing, so listing everything doesn't pay.
- Fixed (30): that bug's hidden tests pass on `fixed.*`.
- Proved (25, Medium/Hard only; redistributed on Easy): the user's own test fails on challenge and passes on fixed.
- Explained (15): the mechanism, i.e. why it goes wrong ("Set updates the map but not the LRU list, so eviction picks the wrong key").

Adjustments:
- Hints: −10% for the first, −10% more for the second, −15% more for the third (−35% max).
- False positives: −10% each, capped at −30%.
- A real bug not in the key earns +10% only if proven with a failing test. Score is still capped at 1.0.
- Score floor is 0. Skip scores 0.

Time is recorded for stats only. It's never shown during play and never affects the score.

## Teaching reveal
After submit or skip, use `notes.md`, the `fixed.*` diff, hints used, and gym git history to explain where the player actually got to:
- per bug: what it is, why it breaks, the fix and proving test
- what they got close to or suspected correctly ("you suspected the lock, and that was right")
- where they got pulled off course, e.g. by a decoy
- one "how to spot it next time" line per pattern, added to `patterns.md`

Keep it short, stay open for follow-up questions, and update `profile.json`.

## Retries
Retries are silent.
- A missed pattern comes due 7–14 days after the miss, in the same language.
- It is served as a new challenge with the same pattern and fresh code, with no announcement.
- At least 5 regular challenges separate retries.
- After grading, say so: "this was the same pattern you missed in `go/007`."

## Tutorial
One short Easy Audit in the user's first language. Hints are free and offered up front, and grading is explained step by step. Completing it sets the tutorial flag and the starting rating.

## Branding
- Plain dev-tool look. No dojo, samurai, belt, or zen theming.
- Working taglines (placeholders, not final): "bugkata: LeetCode for the AI age." and "AI can write your code. Can you own it?" Check the comparative use of "LeetCode" before a wide launch.

## Build plan
Small diffs, each reviewed.
1. ✅ Done. Plugin skeleton with `start`, `help`, and a `hint` stub. Confirm install, that `start` triggers from plain English while `hint` doesn't, `/bugkata:<skill>` naming, args passing, shared-file references, that a plugin `PreToolUse` hook can block reads of a folder, and whether a background subagent can finish work after the turn ends (for prefetch).
2. ✅ Done. Harness refs (Go, Python, including the Go build-tag flip) and `bugkata.py` encode/decode/verify/run.
3. `references/core.md`, `start` (gym setup, launch, Audit generation), and `submit`.
4. `references/bug-catalog.md`.
5. Reviewer subagent: blind and keyed passes, patch loop.
6. Scoring, Elo, `profile.json`.
7. Teaching reveal, `hint`, `skip`, `reset`, `stats`.
8. `references/tutorial.md`.
9. Debug mode, then Test review, then Review.
10. Silent retries and prefetch.
11. Eval: batch-generate per tier and mode, have a fresh subagent play them, and check solve rates, fairness, and token cost per challenge.
