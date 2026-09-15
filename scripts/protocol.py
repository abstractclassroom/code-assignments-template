"""Trusted GitHub runner control code. Never import files from a submission."""
import base64
import hashlib
import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://preview.abstractclassroom.com/api/code-assignments/github/workflow"
AUDIENCE = "abstractclassroom-code-assignments"
WORKFLOW = ".github/workflows/abstractclassroom.yml"
POLICY = ".abstractclassroom/assignment.json"
MAX_BYTES = 2 * 1024 * 1024


def sha(value):
    return hashlib.sha256(value).hexdigest()


def canonical(value):
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()


def safe_path(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.\-/]{1,240}", value):
        raise ValueError("Invalid repository path")
    if any(part in ("", ".", "..", ".git") for part in value.split("/")):
        raise ValueError("Repository path escapes its workspace")
    return value


def request(url, headers, body=None):
    req = urllib.request.Request(url, data=canonical(body) if body is not None else None,
                                 headers={**headers, "Content-Type": "application/json"})
    # Tokens must never follow redirects to another origin.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_args, **_kwargs):
            return None
    try:
        with urllib.request.build_opener(NoRedirect).open(req, timeout=45) as response:
            data = response.read(4 * 1024 * 1024 + 1)
            if len(data) > 4 * 1024 * 1024:
                raise ValueError("Response too large")
            return json.loads(data)
    except urllib.error.HTTPError as error:
        # Do not print response bodies: source pairing secrets and JWTs stay out of logs.
        raise RuntimeError(f"AbstractClassroom request failed (HTTP {error.code}); check the assignment and course subscription.") from None


def oidc():
    raw = os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"]
    url = urllib.parse.urlsplit(raw)
    if url.scheme != "https" or not url.hostname or not url.hostname.endswith(".actions.githubusercontent.com"):
        raise ValueError("Unexpected GitHub OIDC endpoint")
    query = urllib.parse.parse_qsl(url.query)
    query.append(("audience", AUDIENCE))
    response = request(urllib.parse.urlunsplit(url._replace(query=urllib.parse.urlencode(query))),
                       {"Authorization": "Bearer " + os.environ["ACTIONS_ID_TOKEN_REQUEST_TOKEN"]})
    token = response["value"]
    # Local claims select the exact files; the API independently verifies GitHub's signature.
    payload = token.split(".")[1]
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    return token, claims


def call(body):
    token, claims = oidc()
    return request(API, {"Authorization": "Bearer " + token}, body), claims


def git(repo, *arguments):
    return subprocess.check_output(["git", "-C", str(repo), *arguments], stderr=subprocess.DEVNULL)


def tree(repo, commit):
    if not re.fullmatch(r"[a-f0-9]{40}", commit):
        raise ValueError("Invalid commit SHA")
    result = []
    for row in git(repo, "ls-tree", "-rz", commit).split(b"\0"):
        if not row:
            continue
        meta, raw_path = row.split(b"\t", 1)
        mode, kind, blob = meta.decode().split(" ")
        path = safe_path(raw_path.decode())
        result.append((path, mode, kind, blob))
    return result


def blob(repo, oid):
    if not re.fullmatch(r"[a-f0-9]{40}", oid):
        raise ValueError("Invalid Git object")
    size = int(git(repo, "cat-file", "-s", oid))
    if size > 10 * 1024 * 1024:
        raise ValueError("Individual submission files must be at most 10 MiB")
    return git(repo, "cat-file", "blob", oid)


def file_at(repo, commit, path):
    entries = [entry for entry in tree(repo, commit) if entry[0] == path]
    if len(entries) != 1 or entries[0][1] not in ("100644", "100755") or entries[0][2] != "blob":
        raise ValueError("Required source file is missing or is not a regular file")
    return blob(repo, entries[0][3])


def source_package(repo, commit, replace_paths):
    roots = sorted(safe_path(path.strip()) for path in replace_paths.splitlines() if path.strip())
    files = []
    total = 0
    for path, mode, kind, oid in tree(repo, commit):
        if path not in (WORKFLOW, POLICY) and not any(path == root or path.startswith(root + "/") for root in roots):
            continue
        if mode not in ("100644", "100755") or kind != "blob":
            raise ValueError("Protected source paths cannot contain symlinks or submodules")
        content = blob(repo, oid)
        total += len(content)
        if total > MAX_BYTES or len(files) >= 500:
            raise ValueError("Protected files exceed the 2 MiB / 500 file limit")
        files.append({"path": path, "mode": mode, "content": base64.b64encode(content).decode()})
    return {"schemaVersion": 1, "replacePaths": roots, "files": sorted(files, key=lambda item: item["path"])}


def output(name, value):
    if not re.fullmatch(r"[a-zA-Z0-9_.-]+", name) or not re.fullmatch(r"[a-zA-Z0-9_.-]+", str(value)):
        raise ValueError("Invalid workflow output")
    with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
        stream.write(f"{name}={value}\n")


def prepare():
    repo = Path(os.environ["SUBMISSION_PATH"])
    assignment = os.environ["ASSIGNMENT_ID"]
    if not re.fullmatch(r"aca_[A-Za-z0-9_-]{32}", assignment):
        raise ValueError("Set assignment-id to the public ID from your course dashboard")
    token, claims = oidc()
    commit = claims["workflow_sha"]
    if commit != os.environ["GITHUB_SHA"] or claims["sha"] != commit:
        raise ValueError("The checkout and executed workflow revision do not match")
    workflow = file_at(repo, commit, WORKFLOW)
    result = request(API, {"Authorization": "Bearer " + token}, {"action": "prepare", "assignmentId": assignment,
        "workflow": base64.b64encode(workflow).decode(), "pairingToken": os.environ.get("SOURCE_PAIRING_TOKEN", "")})
    if result["mode"] == "source":
        snapshot = source_package(repo, commit, os.environ["REPLACE_PATHS"])
        call({"action": "publish", "assignmentId": assignment, "snapshot": snapshot})
        print("Instructor source published. Students can now submit this assignment.")
    elif result["mode"] == "grade":
        if result["workflowDigest"] != sha(workflow) or result["commitSha"] != commit:
            raise ValueError("The source workflow or submission revision does not match")
        package = Path(os.environ["PACKAGE_PATH"])
        package.mkdir(parents=True, exist_ok=False)
        (package / "source.json").write_bytes(canonical(result))
    else:
        raise ValueError("Unexpected assignment mode")
    output("mode", result["mode"])


def receipt():
    result, _ = call({"action": "receipt", "assignmentId": os.environ["ASSIGNMENT_ID"]})
    filename = result.get("filename", "")
    if not re.fullmatch(r"[a-f0-9-]{36}\.ast", filename) or not re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", result.get("token", "")):
        raise ValueError("Invalid completion token response")
    folder = Path(os.environ["RECEIPT_PATH"])
    folder.mkdir(parents=True, exist_ok=False)
    (folder / filename).write_text(result["token"] + "\n")
    print("Completion token created. Download the artifact and submit the .ast file through your LMS.")


if __name__ == "__main__":
    import sys
    try:
        {"prepare": prepare, "receipt": receipt}[sys.argv[1]]()
    except Exception as error:
        # Do not include variable data or command output in workflow annotations.
        print(f"AbstractClassroom stopped: {type(error).__name__}. See the template troubleshooting guide.")
        sys.exit(1)
