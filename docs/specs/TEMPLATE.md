# <Feature name>
Status: draft | approved | done · Size: S/M/L · Date: YYYY-MM-DD · Owner: lead-engineer

<!-- One spec = the lead's workplan AND the executors' brief. Terse: bullets and identifiers.
     Every line either removes a decision the executor would otherwise make, or is cut. -->

## 1. Objective & interpretation
One tight paragraph: user-visible behaviour after the change + assumptions made.

## 2. Current state
- `file.py` · `symbol` · constraint.md §N — what it does today (quote only the lines that matter,
  so executors don't have to read whole files).

## 3. Behaviour spec
- Inputs → outputs; new settings/flags with key name, type, default.
- Edge cases, each with its required behaviour (empty, missing, boundary, conflicting CSV request, inactive nurse…).
- Must NOT change: …

## 4. Design
- Files to create/modify (exhaustive) and what changes in each.
- New/changed signatures, exact: `def foo(x: int, *, flag: bool = False) -> Bar`
- Data flow (2–5 bullets), following the shiftwork-dev layer checklist.
- Rejected alternative + why (only if a real fork exists).
- Kernel (optional): code written by the lead for the hard part — paste verbatim.

## 5. Work packages
| WP | Owner | Model | Files (disjoint across WPs) | Depends on | Effort |
|----|-------|-------|-----------------------------|-----------|--------|
| 1  | web-specialist | sonnet | … | — | S |

Per WP: ordered steps; for each kind of thing added, a pattern to copy as `path:line`.

## 6. Acceptance tests (must exist and pass)
- `test_name` (file): given <concrete input>, expect <concrete output>. Fails if <rule> is removed.
- Suites that must stay green (run from repo root):
  `python3 -m unittest discover -s webapp/tests -p "test_*.py"` and `python3 test_shiftwork.py`

## 7. Risks
- Known failure modes touched (by name, from CODEBASE.md → "Known failure modes"): …
- What the lead will verify at review: …

## 8. Do NOT
- Edit files outside your WP's list (return proposed diffs instead). Add dependencies. Change signatures not listed.
- Rewrite or weaken a test to make it pass.
- Change solver output semantics, tier/licensing behaviour, or the CSV/Excel input contract (escalations — not in scope unless §3 says so).

## 9. STOP and report instead of guessing if
- A named file/symbol is missing or its signature differs from §2/§4.
- The spec contradicts the code, constraint.md, or itself.
- An acceptance test can't pass without breaking §8.

## 10. Report format
Files changed (paths only) · test commands + pass/fail counts · deviations from spec + why · open questions.
No diffs, no file contents.

## 11. Done / deviations log
- [ ] Acceptance tests + suites green (run by the lead) · [ ] constraint.md / CODEBASE.md updated · [ ] Status → done
- Deviations: …
