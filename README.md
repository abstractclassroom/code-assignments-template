# Code Assignments for AbstractClassroom™

Share starter code, grade pushes in GitHub Actions, and give students an
assignment-specific `.ast` completion token to submit through your existing LMS.
Students use their own GitHub repositories; no AbstractClassroom student account,
roster upload, or GitHub App installation is required.

## Instructor setup

1. Register a **Code Assignments** course at
   <https://preview.abstractclassroom.com/code-assignments/register/> and subscribe.
   The initial price is **$5 USD per course per month**, with **no free trial**.
   The amount reviewed at checkout controls your subscription. GitHub Actions
   usage is separate and belongs to the repository owner.
2. Create an assignment in the course dashboard. It gives you a **public assignment
   ID** and a **private, one-time source pairing token**. The token expires after
   two hours; generate a replacement from the dashboard if needed.
3. Use this template to create your instructor repository. Replace
   `REPLACE_WITH_ASSIGNMENT_ID` in `.github/workflows/abstractclassroom.yml` with
   the public ID. Commit that ID; it is a routing identifier, not a password.
4. Add your starter code and tests. In the workflow, set `replace-paths` to the
   directories or files that must always come from your instructor source.
   Each specified path is **deleted completely and restored**, not merged with
   student files. Other paths, including student-written tests, are preserved.
5. Configure `.abstractclassroom/assignment.json` as described below. Save the
   private token as the Actions repository secret `ABSTRACTCLASSROOM_SOURCE_TOKEN`.
   **Never commit the private token.** It is not needed in student repositories.
6. Run **AbstractClassroom assignment** on `main`, or push to `main`. The first run
   binds this repository's immutable GitHub ID and publishes the grading source.
   Later pushes authenticate with short-lived GitHub OIDC automatically.
7. Enable **Template repository** in your instructor repository's GitHub settings.
   Share its template link with students. Once your source is published, ordinary
   course work and updates happen in GitHub. Return to the dashboard only for new
   assignments, billing, pairing recovery, or validator links.

Use a separate instructor source repository per assignment. A source cannot be
silently replaced; create a new assignment to register a different source or a
repository that moved to a different owner. Source renames within an owner retain
the immutable connection. Treat pairing tokens like passwords until consumed.
You may remove the GitHub secret after the first successful pairing.

## Student workflow

Create a repository from the **instructor's** template. Work on `main`, write code
and your own tests as instructed, and push. Keep the assignment workflow unchanged.
When grading succeeds, download the **assignment-completion-token-<attempt>** artifact from
the Actions run, unzip it, and submit the `.ast` file to your LMS. No student secret
or AbstractClassroom registration is needed. The instructor's source publish and
the student's grading run are selected automatically from repository identity.

The public assignment ID is not enrollment proof. Anyone with it and the approved
workflow can attempt the assignment. A copied token does not prove that its LMS
submitter authored the code; instructors handle identity through the LMS.

## Configure any language and build tool

The shell example is a small demonstration, not a required language. Replace it
with your own code, tests, commands, and public Linux container image. Neither
Maven, CMake, nor any other dependency manager is required. The image must contain
`/bin/sh` and your tools and must be **pinned by `@sha256:` digest**. Private image
registry credentials are not supported in this first version.

The grading policy has:

- `image`: public Linux image pinned by digest.
- `timeoutSeconds`: 10–1800 seconds across image pulling and all commands.
- `network`: false by default; set true if your commands need dependency downloads.
- `writePaths`: initially `[".ac-work"]`; add distinct build output directories such
  as `build`, `target`, or `node_modules` as your tools require. These directories
  start empty. They cannot overlap protected paths. `.ac-results` is always writable.
- `checks`: one or more required checks, each with a unique `id`, a shell `command`,
  a `report` path under `.ac-results/`, a `format` of `junit` or `tap`, and a positive
  `minimumTests`. Optional `requiredTests` lists the exact tests that must appear.

The whole source tree is mounted read-only. Build directories and `/tmp` are writable;
run build tools with outputs directed there. Each check runs in a fresh unprivileged
container with no GitHub/OIDC/receipt credentials, Docker socket, or host home directory.
Containers have a 2 GiB memory limit, two CPUs, and at most 256 processes. Build output
directories persist between checks; processes and `/tmp` do not. Output is bounded
to 1 MiB per command. Commands and timeout values come from the registered source,
regardless of student edits to their local policy file.

Use your unit test framework's JUnit XML exporter, or a flat TAP version 13 report.
JUnit XML requires named `<testcase>` entries; test counts must match, and failures,
errors, disabled tests, and skipped tests are rejected. `requiredTests` uses
`classname.name` (or just `name` if the class is absent). Flat TAP requires a positive
plan, sequential numbered `ok` lines, and unique descriptions. Skips, TODOs, nested
subtests, incomplete plans, and bailouts fail; use a JUnit exporter for those frameworks.
The supplied `grading/check.sh` shows flat TAP with assertions on a separate process.

Every check must exit successfully **and** produce a fresh, complete, passing report.
An empty report, missing required test, premature exit, failure, or timeout produces
no completion token. Student-only tests, instructor-only tests, and combined tests
are all possible through your choice of commands, protected paths, and report rules.

## What is verified

The approved, commit-pinned AbstractClassroom reusable workflow reads the actual
caller YAML at GitHub's signed `workflow_sha` and compares its SHA-256 hash with
the registered source **before restoring files**. Git commit IDs need not match
between teacher and student repositories. A matching YAML hash proves content
equality, not fork ancestry.

Only the paired instructor repository can publish source snapshots. Protected
files are uploaded through its OIDC-authenticated workflow, including from a private
repository; no teacher GitHub read token or app is required. Snapshots are immutable
and checksummed. The source package is limited to 500 files / 2 MiB; the submitted
tree is limited to 5,000 files / 100 MiB / 10 MiB per file. Symlinks and submodules
are rejected. No grading modifications are committed back to a student repository.

A separate trusted receipt workflow runs only after grading succeeds. The API
checks its OIDC identity, approved workflow version, exact repository/run/attempt,
submission commit, current instructor policy, and paid course access. No student
`passed: true` request, earlier development job, or supplied JWT is accepted as
receipt authorization. Retries return the same receipt. Instructor policy changes
during grading require a fresh run.

**Security limit:** conventional unit tests execute arbitrary submitted code within
or alongside the test runner. Such code may forge a report, monkey-patch a framework,
or otherwise interfere with assertions. Workflow hashes, read-only test files, and
test-completion reports do **not** make arbitrary unit testing impossible to bypass.
Receipts explicitly attest `instructor_configured_checks`; they do not claim
cryptographic proof of individual assertions, authorship, or identity. For stronger
assurance, design tests around isolated process inputs/outputs and keep assertions
outside the student's execution process. A future independent grading protocol is
needed for a stronger general-purpose guarantee.

**Tests delivered to a student-owned runner are not secret.** A private instructor
repository protects repository access, but the published grading files must reach
the student's runner. Do not use this initial service for hidden assessments.

## Verify submissions

Each assignment has a shareable TA validator link in the course dashboard. It
requires both the expected course ID and assignment ID. It accepts `.ast` files,
verifies the server signature, and displays the exact commit, grading source,
timestamp, and run. A valid token for a different assignment is rejected.
Existing tokens remain verifiable after cancellation and signing-key rotation.
The backend retains old verification keys. There is no automatic LMS grade sync.

## Troubleshooting

- HTTP 402: the course has no active paid subscription or operator waiver.
- HTTP 403 before the instructor's first publication: check the public assignment ID
  and private pairing secret; replace an expired pairing token from the dashboard.
- Workflow mismatch: restore `.github/workflows/abstractclassroom.yml` byte-for-byte
  from the instructor source, including its approved reusable-workflow commit pin.
- Source updated while grading: start a new run. If the instructor changes the
  workflow itself, students must pull its updated YAML into their repositories.
- Missing reports: check output paths, exporter configuration, minimum test counts,
  required names, permissions, skipped tests, and timeouts. A zero exit code alone
  is insufficient.
- Build cannot write: add its output directory to `writePaths` in the instructor
  policy and publish again. Source files remain read-only during grading.
- Actions are disabled: enable Actions for the student repository and allow the
  pinned public AbstractClassroom reusable workflow in the organization's policy.
- API unavailable: keep the failed run and retry later. A failed or unavailable
  service never issues a passing token.
- To retry, choose **Re-run all jobs** so the new run attempt gets its own source
  verification and receipt authorization. Re-running only a failed job cannot reuse
  an earlier attempt's authorization. Download tokens within the 30-day artifact
  retention period and keep the `.ast` file with your LMS submission.

## Development

Run `python3 -m unittest discover -s tests -p 'test_*.py'`. On Linux with Docker,
also run `python3 tests/container_smoke.py`. The CI workflow runs both without
calling deployed APIs or creating payments. The server and UI live in separate
AbstractClassroom repositories. Reusable workflows pin a reviewed runtime commit;
server trust and instructor caller pins must be updated deliberately for releases.

GitHub's [OIDC reference](https://docs.github.com/en/actions/reference/security/oidc)
documents the signed workflow and run claims. See also
[reusing workflows](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows).
