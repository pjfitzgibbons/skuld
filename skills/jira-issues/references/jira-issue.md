---
project: CIAM
type: Task
summary: An imperative outcome, with no estimate in it
status: To Do
compliance: Not Required
duedate:
parent:
blocked_by:
relates:
---

## Task

What to create or solve, and why, in one to three sentences. Link the design section by anchor.

- In scope: … (bullets, where the Task needs them)

## Guardrails

- A condition the work must hold: a constraint, an invariant, what must not change.
- Out of scope: … (only when someone might assume it's in)

## Lenses

- [ ] L1 Security
- [ ] L2 Privacy and data
- [ ] L3 Reliability and failure
- [ ] L4 Performance and limits
- [ ] L5 Observability
- [ ] L6 Compatibility and migration
- [ ] L7 Usability and accessibility
- [ ] L8 Testability
- [ ] L9 Operations and cost
- [ ] L10 Docs impact
- [ ] L11 Invariants
- [ ] L12 Offline and concurrency

## Done

- [ ] A pass/fail condition: a reader can check it and say yes or no.
- [ ] Another, until every part of the Task has one.

<!--
Fields, by the names Jira shows: project (defaults to CIAM), type (Task, Story, Bug, Sub-task, ...,
as the project has them), summary, status (To Do by default), compliance (CIAM's required
"Compliance Tags" field — defaults to "Not Required"; see
~/.claude/skills/jira-ciam-create/SKILL.md for the allowed values), duedate (CIAM requires one;
defaults to today + 14 days if left blank), parent (an issue key, for a Sub-task or an Epic child),
blocked_by and relates (issue keys separated by commas — ported from Redmine's numeric #<n>,
dropped: points, lane, ordinal, version, tracker).
-->
