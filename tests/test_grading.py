import base64
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from grade import completed_tests, materialize, ordinary_file, run_bounded
from protocol import WORKFLOW, POLICY, canonical, safe_path, sha, source_package


class GradingTests(unittest.TestCase):
    def test_tap_requires_complete_plan_and_passes_without_skips(self):
        good = b"TAP version 13\n1..2\nok 1 - first\nok 2 - second\n"
        self.assertEqual(2, completed_tests(good, "tap", 2, ["second"]))
        for value in [b"", b"1..0\n", b"1..2\nok 1 - first\n", good.replace(b"ok 2", b"not ok 2"),
                      good + b"Bail out!\n", good.replace(b"second", b"second # SKIP"), good.replace(b"second", b"first")]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                completed_tests(value, "tap", 2)
        with self.assertRaises(ValueError):
            completed_tests(good, "tap", 2, ["missing"])

    def test_junit_counts_real_cases_and_rejects_incomplete_or_hostile_xml(self):
        good = b'<testsuite tests="1" failures="0"><testcase classname="Math" name="sum"/></testsuite>'
        self.assertEqual(1, completed_tests(good, "junit", 1, ["Math.sum"]))
        for value in [b'<testsuite tests="1"/>', good.replace(b'tests="1"', b'tests="2"'),
                      good.replace(b'failures="0"', b'failures="1"'), good.replace(b'/></testsuite>', b'><skipped/></testcase></testsuite>'),
                      b'<!DOCTYPE x [<!ENTITY data SYSTEM "file:///etc/passwd">]><testsuite/>']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                completed_tests(value, "junit", 1)

    def test_paths_and_report_symlinks_cannot_escape(self):
        for value in ["../grading", "/tmp/x", "a//b", ".", ".git/config", "a/../b", "a\\b"]:
            with self.assertRaises(ValueError):
                safe_path(value)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / "outside").write_text("secret")
            results = root / "results"; results.mkdir()
            (results / "report").symlink_to(root / "outside")
            with self.assertRaises(ValueError):
                ordinary_file(results / "report", results)
            (results / "report").unlink(); os.mkfifo(results / "report")
            with self.assertRaises(ValueError):
                ordinary_file(results / "report", results)

    def test_restore_deletes_leftovers_but_keeps_student_tests_and_checks_workflow_first(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); repo = root / "repo"; repo.mkdir()
            def git(*args):
                return subprocess.check_output(["git", "-C", str(repo), *args], stderr=subprocess.DEVNULL).decode().strip()
            git("init"); git("config", "user.name", "Test"); git("config", "user.email", "test@example.invalid")
            files = {WORKFLOW: "approved", POLICY: json.dumps({"writePaths": ["build"]}), "grading/good": "correct", "student-tests/test": "student"}
            for name, content in files.items():
                path = repo / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(content)
            git("add", "."); git("commit", "-m", "source"); teacher = git("rev-parse", "HEAD")
            snapshot = source_package(repo, teacher, "grading")
            (repo / "grading" / "good").write_text("wrong")
            (repo / "grading" / "extra").write_text("must disappear")
            git("add", "."); git("commit", "-m", "student"); student = git("rev-parse", "HEAD")
            package = {"snapshot": snapshot, "policyDigest": sha(canonical(snapshot)), "workflowDigest": sha(b"approved"), "commitSha": student}
            output = root / "output"; output.mkdir()
            materialize(repo, student, output, package)
            self.assertEqual("correct", (output / "grading" / "good").read_text())
            self.assertFalse((output / "grading" / "extra").exists())
            self.assertEqual("student", (output / "student-tests" / "test").read_text())
            (repo / WORKFLOW).write_text("altered"); git("add", "."); git("commit", "-m", "tamper")
            changed = git("rev-parse", "HEAD"); package["commitSha"] = changed
            empty = root / "empty"; empty.mkdir()
            with self.assertRaises(ValueError):
                materialize(repo, changed, empty, package)
            self.assertEqual([], list(empty.iterdir()))

    def test_premature_success_does_not_supply_completion_evidence(self):
        run = subprocess.run([sys.executable, "-c", "import os; os._exit(0)"], capture_output=True)
        self.assertEqual(0, run.returncode)
        with self.assertRaises(ValueError):
            completed_tests(run.stdout, "tap", 1)

    def test_timeout_fails(self):
        import time
        with self.assertRaises(TimeoutError):
            run_bounded([sys.executable, "-c", "import time; time.sleep(5)"], time.monotonic() + 0.1)


if __name__ == "__main__":
    unittest.main()
