"""Exercise the actual Docker boundary using temporary instructor/student repositories."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from protocol import WORKFLOW, POLICY, canonical, file_at, sha, source_package


def main():
    with tempfile.TemporaryDirectory(prefix="ac-container-test-") as temporary:
        folder = Path(temporary); repo = folder / "submission"; repo.mkdir()
        def git(*args):
            return subprocess.check_output(["git", "-C", str(repo), *args], stderr=subprocess.DEVNULL).decode().strip()
        git("init"); git("config", "user.email", "fixture@example.invalid"); git("config", "user.name", "Fixture")
        for name in [POLICY, "grading/check.sh", "solution.sh"]:
            path = repo / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes((ROOT / name).read_bytes())
        workflow = repo / WORKFLOW; workflow.parent.mkdir(parents=True); workflow.write_text("approved workflow")
        checks = repo / "grading/check.sh"
        checks.write_text(checks.read_text() + '\n[ -z "${ACTIONS_ID_TOKEN_REQUEST_TOKEN:-}" ]\n[ -z "${GITHUB_TOKEN:-}" ]\nif touch /workspace/grading/forged 2>/dev/null; then exit 1; fi\n')
        git("add", "."); git("commit", "-m", "trusted source"); teacher_sha = git("rev-parse", "HEAD")
        snapshot = source_package(repo, teacher_sha, "grading")
        for label, solution, expected in [("passing", '#!/bin/sh\nprintf "%s\\n" "$(( $1 + $2 ))"\n', 0),
                                           ("failing", '#!/bin/sh\nprintf "0\\n"\n', 1),
                                           ("premature-exit", '#!/bin/sh\nexit 0\n', 1)]:
            (repo / "solution.sh").write_text(solution)
            # The student's replacement tests must not become the authoritative checks.
            checks.write_text("exit 0\n"); (repo / "grading/unwanted").write_text("student leftover")
            git("add", "."); git("commit", "-m", label); commit = git("rev-parse", "HEAD")
            package = folder / "source.json"
            package.write_bytes(canonical({"snapshot": snapshot, "policyDigest": sha(canonical(snapshot)),
                "workflowDigest": sha(file_at(repo, commit, WORKFLOW)), "commitSha": commit}))
            result = subprocess.run([sys.executable, str(ROOT / "scripts/grade.py")], env={**os.environ,
                "SOURCE_PACKAGE": str(package), "SUBMISSION_PATH": str(repo), "GITHUB_SHA": commit,
                "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "synthetic-must-not-enter-container", "GITHUB_TOKEN": "synthetic-must-not-enter-container"}, timeout=400)
            if result.returncode != expected:
                raise AssertionError(f"{label}: expected status {expected}, got {result.returncode}")
        print("Container smoke passed: restored checks, readonly inputs, isolated credentials, failure and premature-exit rejection.")


if __name__ == "__main__":
    main()
