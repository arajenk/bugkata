"""PreToolUse hook: keep Claude out of bugkata's answer cache.

Blocks any Read/Grep/Glob/Bash call whose input mentions a `.bugkata` folder,
unless it's a Bash call to bugkata's own script. Exit 2 tells Claude Code to
refuse the tool call and show the message to Claude.
"""

import json
import re
import sys

PROTECTED = re.compile(r"\.bugkata(?![-\w])")
ALLOWED_COMMAND = re.compile(r"^\s*python3\s+\S*scripts/bugkata\.py(\s|$)")
SHELL_CHAINING = re.compile(r"[;&|`<>\n]|\$\(")


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    tool_input = event.get("tool_input") or {}
    command = tool_input.get("command", "") if event.get("tool_name") == "Bash" else ""
    if command and ALLOWED_COMMAND.match(command) and not SHELL_CHAINING.search(command):
        return 0
    if PROTECTED.search(json.dumps(tool_input)):
        print("bugkata: the answer cache is off limits", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
