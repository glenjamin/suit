---
name: aws-access
description: How to run AWS CLI commands in this environment without hanging — pick the right credential path (`aws --profile`, aws-vault-backed vs native SSO, which fail independently), run outside the sandbox, and handle the aws-sso-preflight PreToolUse hook correctly. Use whenever a task runs `aws`, `terraform`, or otherwise resolves AWS credentials (Secrets Manager, S3, ECS, STS, etc.).
---

# Running AWS CLI commands

## Two independent credential paths

Run AWS commands as `aws --profile <name> …`, never `aws-vault exec …` yourself:
aws-vault's own GUI unlock dialog has no remote or device-code route, a dead end
when the user isn't at the Mac.

But know which path a profile takes, because there are two and **they succeed and
fail independently**:

- **`staging`, `prod`, `dev`, `ecr`, `aperture`, `vagrant`** are
  `credential_process = aws-vault export --format=json <name>-sso`, so they go
  *through* the aws-vault keychain. `--profile staging` does not skip aws-vault.
- **The `-sso` suffixed profiles** (`staging-sso`, `prod-sso`, …) are native SSO
  (`sso_session = geckoboard`) and skip aws-vault entirely.

So an unlocked keychain and a live SSO session are different things. A user who
says "AWS is fixed" may have unlocked only one of them, which is why `--profile
staging` can work while `--profile staging-sso` fails with `Token has expired and
refresh failed`, or the reverse.

**Diagnose before asking the user twice.** Read `~/.aws/sso/cache/*.json` — a
plain file read that resolves no credentials — and look for an entry containing an
`accessToken` with an unexpired `expiresAt`. No live token means native SSO is
dead, so try the plain profiles; if `sts get-caller-identity` on one of those
works, get on with the task. Only go back to the user when both paths are shut.

## Run outside the sandbox

AWS commands need the network, so run them with `dangerouslyDisableSandbox: true`
(matches the global "network commands run without the sandbox" rule). A sandboxed
AWS call fails on network egress, not on anything you can fix by retrying inside.

## The aws-sso-preflight hook

A `PreToolUse` (Bash) hook at `~/.claude/scripts/aws-sso-preflight.py` inspects
every command touching `aws`, `aws-vault`, `terraform`, or `AWS_PROFILE=`. It
checks credential state *without side effects* and blocks (exit 2) when a command
would hang or dead-end:

- the SSO OIDC token is expired, so resolving creds would launch a browser login
  and poll forever;
- the aws-vault keychain is locked (see above);
- an `aws` command names no profile and there's no `[default]` — this fails fast
  rather than hangs, and the hook steers you to add `--profile`.

**When it blocks, follow its instructions exactly:**

1. Do **not** retry the command, and do **not** pick a resolution yourself.
2. Check whether the *other* credential path is open first (see above) — the hook
   reports on the path your command took, and the other one may be usable, in
   which case there's nothing to ask about.
3. Otherwise send the user a `PushNotification`, and present **both** routes the
   hook prints for them to choose between:
   - **A) At the Mac** — a command that opens a browser to complete SSO login.
   - **B) Remote / away from the Mac** — the same with `--stdout`, which prints
     the SSO link to the terminal instead of opening a browser; relay the link so
     they can approve on their phone.
4. Re-run the original command only once they confirm it's resolved — and if it
   still fails the same way, diagnose from the token cache rather than relaying
   the same two routes again.

The user resolves it by running the login themselves (often via `!` at the
prompt). After that, sessions are cached and subsequent `aws --profile` calls run
without prompting.

**Give the hook a clean command to parse.** It extracts the profile from the
command string; compound commands — `;`-chained statements, pipes, `2>&1`
redirection — confuse its parser, so it may report the wrong token as the
"profile". Run a single, unredirected `aws --profile <p> …` when you want its
message (and your own reasoning) to be accurate. Confirm access with
`aws --profile <p> sts get-caller-identity`.

**It matches the bare substring, so paths set it off.** A command that merely
mentions `aws` in a filename — `ls ~/.claude/skills/aws-access/` — is treated as a
credential-resolving command and blocked for naming no profile. Nothing is wrong;
reword to avoid the literal (a glob like `~/.claude/skills/*ccess/` works) rather
than adding a meaningless `--profile`.

## Geckoboard profiles

- `staging` — account `538784012500`
- `prod` — account `457679966447`

## Secrets Manager hygiene

Secrets live as a JSON object of `KEY → value` (e.g. `/ecs/config/<app>`). When
adding or changing a key:

- **Read, merge, write** — never `put` a fresh object, or you drop the keys you
  didn't mention. `get-secret-value` → merge → `put-secret-value`.
- **Never echo secret values.** Merge with `jq --arg`, and pass the result to
  `put-secret-value` via `--secret-string file://<tmpfile>` rather than inlining
  it on the command line. Write the tmpfile in the session scratchpad and delete
  it straight after.
- Confirm the result by listing **keys only**: `jq -r 'keys[]'`.
- Record the returned `VersionId` — Terraform (strata) pins the secret by
  version, so a new version means a `secrets_version` bump.

## Don't capture secrets into shell variables

Whatever the shell, a captured secret is a liability: it can land in an error
message, and glob characters (`*`, `?`, `[`) in the value cause trouble depending
on how it's expanded. Pipe straight through `jq` to a file and use
`--secret-string file://…`, or put the logic in a small script file invoked with
explicit arguments.

Note the Bash tool runs **zsh**, not fish, whatever the session's environment
summary claims: `(cmd)` substitution fails with zsh's `no matches found`, and
`$(cmd)` is what works. Worth checking before writing shell that depends on it.
