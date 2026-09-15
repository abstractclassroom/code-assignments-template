"""Restore teacher files and supervise credential-free containers on GitHub-hosted Linux."""
import base64
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from protocol import POLICY, WORKFLOW, blob, canonical, file_at, safe_path, sha, tree


def ordinary_file(path, root, maximum=1024 * 1024):
    relative = path.relative_to(root)
    root = root.resolve()
    path = root / relative
    ancestors = [path, *list(path.parents)[:len(relative.parts) - 1]]
    if not path.resolve().is_relative_to(root) or any(part.is_symlink() for part in ancestors):
        raise ValueError("Test report must be a regular file inside its result directory")
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
            raise ValueError("Test report is missing, oversized, or not a regular file")
        return stream.read(maximum + 1)


def completed_tests(data, format_name, minimum, required=()):
    """Validate completion evidence; reports are not cryptographic proof of assertions."""
    if not data or len(data) > 1024 * 1024:
        raise ValueError("A complete test report is required")
    names = []
    if format_name == "junit":
        if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
            raise ValueError("XML declarations and external entities are not supported")
        root = ET.fromstring(data)
        if root.tag not in ("testsuite", "testsuites"):
            raise ValueError("Expected a JUnit XML test report")
        for suite in root.iter():
            if suite.tag in ("failure", "error", "skipped"):
                raise ValueError("Required tests failed or were skipped")
            if suite.tag in ("testsuite", "testsuites"):
                for attribute in ("failures", "errors", "skipped", "disabled"):
                    if int(suite.get(attribute, "0")) != 0:
                        raise ValueError("Required tests did not pass")
                if "tests" in suite.attrib and int(suite.attrib["tests"]) != len(list(suite.iter("testcase"))):
                    raise ValueError("Test completion count does not match the report")
            if suite.tag == "testcase":
                if suite.get("status", "run") in ("notrun", "disabled") or suite.get("result", "completed") in ("skipped", "suppressed"):
                    raise ValueError("A required test did not execute")
                name = suite.get("name", "")
                if not name:
                    raise ValueError("Every test must have a name")
                names.append((suite.get("classname", "") + "." + name).lstrip("."))
    elif format_name == "tap":
        plan = None
        numbers = []
        for line in data.decode("utf-8").splitlines():
            # Initial contract is flat TAP. Nested subtests need a JUnit adapter.
            if not line.strip() or line.startswith("#") or line == "TAP version 13":
                continue
            match = re.fullmatch(r"1\.\.([1-9][0-9]*)", line)
            if match:
                if plan is not None:
                    raise ValueError("Duplicate TAP plan")
                plan = int(match[1]); continue
            match = re.fullmatch(r"ok ([1-9][0-9]*)(?: -)? ([^#\r\n]+)", line)
            if not match:
                raise ValueError("TAP contains failed, skipped, incomplete, or unsupported test output")
            numbers.append(int(match[1])); names.append(match[2].strip())
        if plan is None or numbers != list(range(1, plan + 1)):
            raise ValueError("TAP did not complete every planned test")
    else:
        raise ValueError("Unsupported report format")
    if len(names) < minimum or len(set(names)) != len(names) or not set(required).issubset(names):
        raise ValueError("Required tests are missing, duplicated, or incomplete")
    return len(names)


def remove_path(path, root):
    if not path.parent.resolve().is_relative_to(root.resolve()):
        raise ValueError("Protected path escapes the workspace")
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def materialize(repo, commit, destination, source):
    """Delete protected paths completely, then install registered source bytes."""
    snapshot = source["snapshot"]
    if sha(canonical(snapshot)) != source["policyDigest"]:
        raise ValueError("Source package digest mismatch")
    # This check deliberately precedes any restoration of workflow/config files.
    if source["commitSha"] != commit or sha(file_at(repo, commit, WORKFLOW)) != source["workflowDigest"]:
        raise ValueError("The executed workflow was altered")
    rows = tree(repo, commit)
    if len(rows) > 5000:
        raise ValueError("Submission exceeds 5,000 tracked files")
    total = 0
    for name, mode, kind, oid in rows:
        if kind != "blob" or mode not in ("100644", "100755"):
            raise ValueError("Submission symlinks and submodules are not supported")
        content = blob(repo, oid); total += len(content)
        if total > 100 * 1024 * 1024:
            raise ValueError("Submission exceeds 100 MiB")
        path = destination / safe_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content); path.chmod(0o755 if mode == "100755" else 0o644)
    for name in [*snapshot["replacePaths"], ".abstractclassroom", ".github", ".ac-results"]:
        remove_path(destination / safe_path(name), destination)
    for entry in snapshot["files"]:
        path = destination / safe_path(entry["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(entry["content"], validate=True))
        path.chmod(0o755 if entry["mode"] == "100755" else 0o644)
    policy = json.loads((destination / POLICY).read_bytes())
    writable = [".ac-results", *policy.get("writePaths", [".ac-work"])]
    protected = [".github", ".abstractclassroom", *snapshot["replacePaths"]]
    for name in writable:
        safe_path(name)
        if any(name == root or name.startswith(root + "/") or root.startswith(name + "/") for root in protected):
            raise ValueError("Writable build paths overlap protected paths")
        target = destination / name
        remove_path(target, destination)
        target.mkdir(parents=True)
    return policy, writable


def run_bounded(command, deadline, container_name=None):
    # Output is a bounded host file, never interpreted as workflow commands.
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        try:
            while process.poll() is None:
                if time.monotonic() >= deadline or log.tell() > 1024 * 1024:
                    raise TimeoutError("Grading exceeded its timeout or output limit")
                time.sleep(0.1)
            log.seek(0)
            for line in log.read(1024 * 1024).decode("utf-8", errors="replace").splitlines():
                print(json.dumps(line))
            if process.returncode != 0:
                raise ValueError("An instructor-configured command failed")
        finally:
            if process.poll() is None:
                process.kill(); process.wait(timeout=10)
            if container_name:
                subprocess.run(["docker", "rm", "-f", container_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20, check=False)


def main():
    source = json.loads(Path(os.environ["SOURCE_PACKAGE"]).read_bytes())
    with tempfile.TemporaryDirectory(prefix="ac-grading-") as temporary:
        workspace = Path(temporary) / "workspace"; workspace.mkdir()
        policy, writable = materialize(Path(os.environ["SUBMISSION_PATH"]), os.environ["GITHUB_SHA"], workspace, source)
        deadline = time.monotonic() + policy["timeoutSeconds"]
        run_bounded(["docker", "pull", policy["image"]], deadline)
        for check in policy["checks"]:
            results = workspace / ".ac-results"
            remove_path(results, workspace); results.mkdir()
            name = "ac-grade-" + uuid.uuid4().hex
            command = ["docker", "run", "--rm", "--name", name, "--init", "--read-only", "--cap-drop=ALL",
                       "--security-opt=no-new-privileges", f"--user={os.getuid()}:{os.getgid()}", "--pids-limit=256", "--memory=2g", "--cpus=2",
                       "--network=" + ("bridge" if policy.get("network", False) else "none"),
                       "--tmpfs=/tmp:rw,nosuid,nodev,size=536870912,mode=1777", "--workdir=/workspace",
                       "--mount", f"type=bind,src={workspace},dst=/workspace,readonly", "--env=HOME=/tmp"]
            for path in writable:
                command += ["--mount", f"type=bind,src={workspace / path},dst=/workspace/{path}"]
            command += [policy["image"], "/bin/sh", "-eu", "-c", check["command"]]
            run_bounded(command, deadline, name)
            report_path = workspace / safe_path(check["report"])
            count = completed_tests(ordinary_file(report_path, results), check["format"], check["minimumTests"], check.get("requiredTests", []))
            if time.monotonic() >= deadline:
                raise TimeoutError("Grading exceeded the instructor timeout")
            print(f"Required check {check['id']}: {count} completed tests.")
        print("Every required command and test-completion contract passed.")


if __name__ == "__main__":
    import sys
    try:
        main()
    except Exception as error:
        print("Grading stopped: " + json.dumps(str(error)))
        sys.exit(1)
