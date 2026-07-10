Run `gh pr-feedback` to fetch review comments on the current branch's PR. This command must be run outside the sandbox (with `dangerouslyDisableSandbox: true`) because it needs network access to the GitHub API.

Read through all the feedback carefully. For each comment:

1. If it's a code change request: make the change directly
2. If it's a question: investigate and respond with what you find, then ask me if a change is needed
3. If it's already resolved or outdated: skip it

Work through the feedback one item at a time. After each change, briefly state what you did and move to the next item. Don't ask for confirmation between items unless the feedback is ambiguous.
