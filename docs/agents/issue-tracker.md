# Issue tracker: GitHub

Issues and PRDs for this repository live as GitHub Issues. Use the `gh` CLI for
all operations and infer the repository from `git remote -v`.

## Conventions

- Create: `gh issue create --title "..." --body "..."`
- Read: `gh issue view <number> --comments`, including labels
- List: `gh issue list --state open --json number,title,body,labels,comments`
- Comment: `gh issue comment <number> --body "..."`
- Label: `gh issue edit <number> --add-label "..."`
- Close: `gh issue close <number> --comment "..."`

PRs are not a request surface. External PRs do not automatically enter the
issue triage workflow.

When a skill says “publish to the issue tracker”, create a GitHub Issue. When a
skill says “fetch the relevant ticket”, read the Issue with comments and labels.

## Wayfinding

A future wayfinder map is one Issue labelled `wayfinder:map`. Child tickets use
GitHub sub-issues and native issue dependencies when available, falling back to
task lists and a `Blocked by:` line. Claim work by assigning the Issue before the
first write; resolve it by commenting the result and closing it.
