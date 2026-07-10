---
name: circleci-failure-investigation
description: Investigate CircleCI failures with the preview `circleci` CLI. Use when CI is red on CircleCI and you need which job/step/test failed and why.
---

# Investigating CircleCI failures from the CLI

Uses the new **preview, agent-friendly** `circleci` CLI (`circleci 1.x-pre`). It is
Markdown-first (tables by default, `--json` + `--jq` for scripting) and infers the
project and branch from the current git repo's remote and checked-out branch.

Hierarchy: **run** (one trigger firing) → **workflows** → **jobs** → **steps**.
A parallel job (`parallelism > 1`) has multiple **executions** (index 0, 1, …).

Check auth once with `circleci auth me`. Most commands take no project argument —
they read the git remote. Add `--project gh/org/repo` / `--branch <name>` to override.

## The investigation flow

### 1. Find the run and its failed jobs

```bash
# Latest run for the current branch — jobs, outcomes, and their UUIDs
circleci run get

# Or list recent runs first (e.g. to pick an older revision)
circleci run list --current-branch          # -B is shorthand
```

`run get` prints a table of every job with its status and ID — read off the failed
job and copy its ID (every later command needs it). No `jq` required.

To wait for an in-progress run to finish first: `circleci run watch`.

### 2. Find the failed step in each failed job (the backbone)

Not every failure is a parsed test. A lint, typecheck, build, dependency-check, or
compile job fails a **step** with no test result, and even a test job can die
(OOM, setup crash, non-zero exit) before storing results. So always start from the
failed step — it exists for every failure.

```bash
# Per-execution step table: step #, status, exit code, command — find the failed one
circleci job get <job-id>

# Read that step's full output (stdout+stderr, ANSI stripped when piped)
circleci job output get <job-id> --step-num <N> --execution 0
```

`job get` lists every step with its number, status, and exit code, split per
execution — read off the failed step's number (and which execution it's in). For a
single-execution job that's `--execution 0`; for a parallel job see the gotcha below.

`job output list <job-id>` is an alternative that prints the last 200 lines of every
step inline — good for a quick scan of where it broke; `--tail 0` shows all lines.

The step log is the source of truth for **non-test failures** — read it directly to
see the eslint errors, `tsc` diagnostics, webpack/build error, failing shell
command, etc. For those jobs you are done here; step 3 only applies when the job
parsed test results.

### 3. If the job stored test results, read the parsed failures

When the failing step ran a test suite that uploaded JUnit via `store_test_results`,
`testresult` gives you the failures structured — cleaner than grepping the log.
It shows **failures only** by default and aggregates across all parallel executions.

```bash
circleci testresult list <job-id>                      # failed tests, as a table
circleci testresult list <job-id> --json | jq -r '.message'   # full assertion message(s)
```

The rendered table truncates; `--json` carries the complete failure `message`
(the stack trace / expected-vs-received). `--json` emits **JSONL** (one object per
test) — with `--jq`, the expression runs once per record.

`testresult list` returning nothing does **not** mean the job passed — it means no
failures were *parsed* (no results stored, or a crash before the upload step). Trust
the step outcome from step 2, and read the raw log for the real error.

## Parallelism gotcha

For a job with `parallelism > 1`, a failing test lives in exactly one execution.
`testresult list` aggregates them, so it tells you *what* failed — but
`job output get` is **per-execution**, so you must target the right `--execution`
to see the log. `job get <job-id>` prints a step table per execution, so you can see
at a glance which shard has the failed step and pass that `--execution` index.

## Handy extras

- `circleci job artifact list <job-id>` / `download` — screenshots, coverage, Playwright traces.
- `circleci <cmd> <sub> --help` — every command documents its `JSON fields:` for `--jq`.
- Add `--json`/`--jq`/`-q` (quiet) to any command for scripting.
