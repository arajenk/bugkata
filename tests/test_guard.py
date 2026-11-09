import json
import subprocess
import sys
import unittest
from pathlib import Path

GUARD = Path(__file__).resolve().parent.parent / "scripts" / "guard.py"


def run_guard(tool_name: str, tool_input: dict) -> int:
    event = json.dumps({"tool_name": tool_name, "tool_input": tool_input})
    return subprocess.run(
        [sys.executable, str(GUARD)], input=event, text=True, capture_output=True
    ).returncode


class GuardTest(unittest.TestCase):
    def test_blocks_read_of_cache(self):
        self.assertEqual(run_guard("Read", {"file_path": "/g/.bugkata/cache/a3f9.dat"}), 2)

    def test_blocks_bash_cat_of_cache(self):
        self.assertEqual(run_guard("Bash", {"command": "cat .bugkata/cache/a3f9.dat"}), 2)

    def test_blocks_grep_in_bugkata_dir(self):
        self.assertEqual(run_guard("Grep", {"pattern": "ANSWER", "path": ".bugkata"}), 2)

    def test_allows_bugkata_script(self):
        self.assertEqual(
            run_guard("Bash", {"command": "python3 /p/scripts/bugkata.py score --gym /g/.bugkata"}), 0
        )

    def test_blocks_chaining_after_bugkata_script(self):
        self.assertEqual(
            run_guard("Bash", {"command": "python3 /p/scripts/bugkata.py x; cat .bugkata/cache/a"}), 2
        )

    def test_blocks_subshell_in_bugkata_script(self):
        self.assertEqual(
            run_guard("Bash", {"command": "python3 /p/scripts/bugkata.py $(cat .bugkata/cache/a)"}), 2
        )

    def test_allows_similar_named_folder(self):
        self.assertEqual(run_guard("Read", {"file_path": "/g/.bugkata-probe/bg.txt"}), 0)

    def test_allows_challenge_files(self):
        self.assertEqual(run_guard("Read", {"file_path": "/g/go/014/challenge.go"}), 0)


if __name__ == "__main__":
    unittest.main()
