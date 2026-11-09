---
name: start
description: Start or continue a bugkata challenge. (Test probe in this build.)
argument-hint: "[language] [mode] [tier]"
---

# bugkata start (test probe)

This build is a probe that checks plugin mechanics. Do not start a challenge or do anything else. Output exactly the report below, one line each, and nothing before or after it.

1. `BUGKATA_PROBE`
2. `args: $ARGUMENTS`. If nothing appears after `args:` in this line as you read it, print `args: (none)` instead.
3. `plugin_root (text): ${CLAUDE_PLUGIN_ROOT}`, printed exactly as that text appears to you, even if it looks like an unexpanded variable.
4. `plugin_root (env): ` followed by the output of running `echo "$CLAUDE_PLUGIN_ROOT"` in Bash. If it prints nothing, write `(empty)`.
5. `core_md: ` followed by the first line of `${CLAUDE_PLUGIN_ROOT}/references/core.md`, read with the Read tool. If the read fails, write `NOT FOUND: <the path you tried>`.

Only if the args contain `--probe-bg`: after the report, launch one background subagent (Agent tool, `run_in_background: true`) with this task: "Run `python3 -c 'import time; time.sleep(30)'` in Bash, then create the directory `<cwd>/.bugkata-probe/` and write the current ISO timestamp (from `date -u +%Y-%m-%dT%H:%M:%SZ`) to `<cwd>/.bugkata-probe/bg.txt`. Reply only `done`." Replace `<cwd>` with the absolute current working directory. Then print `bg: launched` and end your turn immediately without waiting for the subagent.
