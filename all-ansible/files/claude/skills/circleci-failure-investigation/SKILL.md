---
name: circleci-failure-investigation
description: Investigate CircleCI failures with the preview `circleci` CLI. Use when CI is red on CircleCI and you need which job/step/test failed and why.
---

# Investigating CircleCI failures

Uses the preview, agent-friendly `circleci` CLI. Output is markdown tables — read
them directly. Hierarchy: **run** → **workflows** → **jobs** → **steps**; a parallel
job (`parallelism > 1`) has multiple **executions** (0, 1, …).

`circleci` infers the project *and* the branch from the current git repo — a plain
`circleci run get` targets your checked-out branch. Override with `--branch <name>`,
or `--project gh/org/repo` for another repo. Check auth with `circleci auth me`.

## 1. Find the failed job

```bash
circleci run get              # latest run for the current branch: jobs, status, IDs
circleci run list -B          # recent runs, if you need an older one (-B = current branch)
```

Read the job table, copy the failed job's ID.

## 2. Find the failed step and read its log

```bash
circleci job get <job-id>                              # step table per execution: #, status, exit code
circleci job output get <job-id> --step-num <N> --execution <X>   # that step's full log
```

`job get` shows every step's number, status, and exit code, split per execution —
read off the failed step and which execution it's in (single-execution job → `0`).
`circleci job output list <job-id>` instead prints the tail of every step inline for
a quick scan (`--tail 0` for the whole thing).

The step log is the source of truth for **any** failure — lint, typecheck, build,
crash, or test. For non-test jobs you're done here.

## 3. Test jobs: list the parsed failures

If the job stored test results (`store_test_results`), `testresult` names the failing
tests without scanning the log. Failures only by default; aggregates all executions.

```bash
circleci testresult list <job-id>
```

Empty output means nothing was *parsed* (no results stored, or a crash before the
upload step) — not that it passed. Trust the step outcome from step 2 and read the
log for the real error.

## Notes

- Parallel jobs: `testresult` aggregates executions but `job output get` is
  per-execution — `job get` shows which execution has the failed step; pass that `--execution`.
- Step `status` is `succeeded`/`failed` (run/job level uses `success`).
- Add `--json` (optionally with `--jq '<expr>'`) to any command for structured output.
- Commands all provide detailed `--help`, use to discover more flags and commands.
- `circleci job artifact list <job-id>` / `download` — screenshots, coverage, traces.
- `circleci api '<path>'` — REST fallback for anything the typed commands don't expose.
