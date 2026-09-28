# Guardian

Guardian mechanically enforces static import dependencies and module boundaries.
It does NOT check logic, security, style, runtime DI, reflection or string-based lookups.

## Before coding
- Get minimal architecture context: `python3 .guardian/guardian.py context <module|path>`
- Do not load other project docs or rules unless the task needs them.

## Results
- exit 0 PASS: silent. Continue.
- exit 1 BLOCK: fix the application code.
- exit 2 ERROR: stop. Show the message to the human. Do not retry. Do not modify `.guardian/`.
- Exception: onboarding. Only when the human asks for it, run
  `python3 .guardian/guardian.py onboard` and follow it (source: PROJECT.md, read once);
  it may write `.guardian/guardian.toml` after the human approves.

## When the fix needs a new dependency
If the correct fix needs a module dependency not in `depends`, or a forbidden import:
- Do not work around it: no copying code, no dynamic import, no string-based lookup,
  no moving code to unmapped paths, no re-export tricks.
- Stop and propose the rule change to the human.
  Rule changes land in their own PR first; code follows in a later PR.

## Never
- Edit `.guardian/` to pass a BLOCK.
- Look for inline ignore comments; none exist.
- Push to main. Work on a feature branch and open a PR.

## Commands
- `python3 .guardian/guardian.py check`                     changed files (L1)
- `python3 .guardian/guardian.py check --all`               full scan
- `python3 .guardian/guardian.py check --base origin/main`  CI (L3, base rules)
