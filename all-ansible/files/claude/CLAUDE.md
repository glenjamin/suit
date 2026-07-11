- Don't edit `package.json` by hand for dependency changes, use `npm` cli commands
- Don't summarise what changed in the body of commit messages, instead provide the why only if it adds something (max 80 char lines)
- Never commit directly to master, always make a branch first
- Amend or squash fixups into the relevant commit; don't stack "fix" commits
- Don't git push or open PRs, I'll do that
- Don't reply to github review comments
- Define helpers at the bottom of files, so the most relevant code is nearer the top
- Comments describe current behaviour and why, not what changed or the history
- Prefer high-level, behaviour-focused tests that drive real user-facing seams over unit tests coupled to implementation — BDD in spirit, not Cucumber/Gherkin; on small changes, follow the existing per-project test patterns rather than restyling
- Ground claims in the source: verify against it before asserting or pushing back, and don't estimate or fabricate figures
- Don't unilaterally make a UX tradeoff — if a change would degrade or significantly alter the UX (for a code-quality goal, or to work around a conflict), surface it and ask
- The Bash tool actually runs fish, not bash
- Put temp files in the session scratchpad, creating and using them in the same shell command
- `mise` is used to manage ruby/go/node versions

## When writing TypeScript

- Refine types rather than casting with `as`
- Avoid needless `?.`/`??` where a value is already guaranteed
- Prefer `unknown` over `any`
