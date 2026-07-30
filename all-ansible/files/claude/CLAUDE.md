- When running bash tools, give a light commentary of what you're doing so a reader can follow
- Ground claims in the source: verify against it before asserting or pushing back — including claims or assumptions that span other repos — and don't fabricate figures
- Don't unilaterally make a UX tradeoff — if a change would degrade or significantly alter the UX (for a code-quality goal, or to work around a conflict), surface it and ask

## Git and PRs

- Don't summarise what changed in the body of commit messages, instead provide the why only if it adds something (max 80 char lines)
- Never commit directly to master, always make a branch first
- Never use `/` in git branch names, use `-` instead — tooling that interpolates the branch name breaks on the slash (tarball filenames become paths into non-existent dirs, and app's hot-branch validation silently rejects the branch and serves production assets)
- Always branch worktrees off the fetched upstream HEAD (`git fetch origin`, then `origin/master`), never the local checkout — local clones are often behind, and stale docs or tooling then read as missing
- Amend or squash fixups into the relevant commit; don't stack "fix" commits
- Don't git push or open PRs, I'll do that. When I do explicitly make you open a PR, don't hard-wrap the description — one line per paragraph, let it soft-wrap.
- In chat replies, write PR/issue mentions as clickable markdown links (`[#123](url)`, `[repo#123](url)`), never a bare `#123`. On GitHub (PR descriptions, comments) use the bare `#123`/`owner/repo#123` form instead — GitHub auto-links those.
- Don't reply to github review comments

## Code and tests

- Define helpers at the bottom of files, so the most relevant code is nearer the top
- Comments describe current behaviour and why, not what changed or the history
- In comments give the purpose, not implementation detail, and describe behaviour rather than citing concrete values that will drift
- Prefer high-level, behaviour-focused tests that drive real user-facing seams over unit tests coupled to implementation — BDD in spirit, not Cucumber/Gherkin; on small changes, follow the existing per-project test patterns rather than restyling
- Prefer a realistic fake that models behaviour over canned per-test stubs, even when a stubbing helper is what wires the fake in

## Environment

- Commands that need network should be run (or retried) without the sandbox
- Put temp files in the session scratchpad
- `mise` is used to manage ruby/go/node versions

## When writing TypeScript

- Refine types rather than casting with `as`
- Avoid needless `?.`/`??` where a value is already guaranteed
- Prefer `unknown` over `any`
