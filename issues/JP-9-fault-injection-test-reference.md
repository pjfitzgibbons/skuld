---
status: open
type: task
guide: §9
opened: 2026-11-05
closed:
---

# Point failure-modes.md at the Python fault-injection tests, and check its claim

`docs/failure-modes.md:17` says every *Fails* cell "gets a row in the command's fault-injection
test ... (`STEPS` in `test/start.test.js`)". That file is gone (the JS port was dropped from the
first commit) and `lib/test_start.py` has no `STEPS`. The fault injection itself was ported in
reduced form: `lib/test_start.py:5-8` and `lib/test_done.py:6-9` say which rows were kept,
merged, or dropped. GUIDE §9 describes the same tests.

Done when line 17 names the real Python test cases, and each *Fails* cell in failure-modes.md
either maps to one of them or is marked as having no fault-injection case.
