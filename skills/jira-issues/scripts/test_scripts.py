#!/usr/bin/env python3
"""Tests for the jira-issues scripts, against fakes. No network, no Jira writes, no GitHub writes.

Run: python3 -m unittest test_scripts -v
"""

import copy
import os
import re
import subprocess
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

import checklist
import new_issue
import outline
import pr_threads as threads_mod

SCRIPTS = Path(__file__).parent


# ---------------------------------------------------------------------------
# outline.py
# ---------------------------------------------------------------------------

GOOD = """## Task
Do the thing.

## Guardrails
Don't do the other thing.

## Lenses
- [x] L1: applies because the thing touches X
- [ ] L2

## Done
- [x] L1: covered by the X check
- [ ] tests pass
"""


class Outline(unittest.TestCase):
    def test_conforms(self):
        self.assertEqual(outline.check(GOOD, ["L1", "L2"], []), [])

    def test_missing_section(self):
        body = GOOD.replace("## Guardrails\nDon't do the other thing.\n\n", "")
        findings = outline.check(body, ["L1", "L2"], [])
        self.assertIn("missing section: ## Guardrails", findings)

    def test_outline_section_one_level_deep_flagged(self):
        body = GOOD.replace("## Guardrails", "### Guardrails")
        findings = outline.check(body, ["L1", "L2"], [])
        self.assertIn("heading level: 'Guardrails' is an outline section; write it as ## Guardrails", findings)

    def test_other_subsection_allowed(self):
        body = GOOD.replace("Do the thing.\n", "Do the thing.\n\n### Background\nWhy.\n")
        self.assertEqual(outline.check(body, ["L1", "L2"], []), [])

    def test_log_section_allowed(self):
        body = GOOD + "\n## Log\n- 2026-11-08: Started.\n"
        self.assertEqual(outline.check(body, ["L1", "L2"], []), [])

    def test_old_outline_sections_flagged(self):
        body = GOOD + "\n## Summary\nold stuff\n"
        findings = outline.check(body, ["L1", "L2"], [])
        self.assertTrue(any("old outline" in f for f in findings))

    def test_too_few_done_conditions(self):
        body = GOOD.replace("- [x] L1: covered by the X check\n- [ ] tests pass\n", "- [x] L1: covered by the X check\n")
        findings = outline.check(body, ["L1", "L2"], [])
        self.assertTrue(any("pass/fail condition" in f for f in findings))

    def test_ticked_lens_without_note_flagged(self):
        body = GOOD.replace("- [x] L1: applies because the thing touches X", "- [x] L1")
        findings = outline.check(body, ["L1", "L2"], [])
        self.assertTrue(any("needs a note" in f for f in findings))

    def test_missing_lens_flagged(self):
        findings = outline.check(GOOD, ["L1", "L2", "L3"], [])
        self.assertIn("L3: lens missing", findings)

    def test_ticked_lens_without_done_condition_flagged(self):
        body = GOOD.replace("- [ ] L2", "- [x] L2: also applies")
        findings = outline.check(body, ["L1", "L2"], [])
        self.assertTrue(any("L2: [x] but no Done condition names it" in f for f in findings))

    def test_extra_section_required(self):
        findings = outline.check(GOOD, ["L1", "L2"], ["Risks"])
        self.assertIn("missing section: ## Risks", findings)

    def test_expected_extracts_lenses_and_sections(self):
        global_text = "| L1 Security | ... |\n| L2 Performance | ... |\n"
        repo_text = "Also required: `## Rollout`\n| L3 Repo-only | ... |\n"
        lenses, extra = outline.expected(global_text, repo_text)
        self.assertEqual(lenses, ["L1", "L2", "L3"])
        self.assertEqual(extra, ["Rollout"])


# ---------------------------------------------------------------------------
# pr_threads.py
# ---------------------------------------------------------------------------

class Threads(unittest.TestCase):
    def test_read_replies_parses_tsv(self):
        text = "123\tLooks good\n456\tFixed, thanks\n"
        self.assertEqual(threads_mod.read_replies(text), [(123, "Looks good"), (456, "Fixed, thanks")])

    def test_read_replies_rejects_bad_line(self):
        with self.assertRaises(ValueError):
            threads_mod.read_replies("not-a-number\tfoo\n")

    def test_emit_builds_script(self):
        found = [{"comment": 123, "thread": "T_1", "path": "a.py", "line": 10}]
        script = threads_mod.emit("o/r", 9, found, [(123, "ok")], pause=0)
        self.assertIn("gh api repos/o/r/pulls/9/comments/123/replies", script)
        self.assertIn("resolveReviewThread", script)
        self.assertIn("id=T_1", script)

    def test_emit_rejects_unknown_comment(self):
        with self.assertRaises(ValueError):
            threads_mod.emit("o/r", 9, [], [(999, "x")], pause=0)


class PostReplies(unittest.TestCase):
    def test_write_drafts_then_post_replies_and_resolves(self):
        found = [{"thread": "T1", "resolved": False, "comment": 5, "path": "a.py", "line": 3,
                  "author": "bob", "body": "fix this", "hunk": "x\ny", "replies": []}]
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d) / "replies"
            written = threads_mod.write_drafts(folder, found)
            self.assertEqual(len(written), 1)
            f = written[0]
            self.assertTrue(f.read_text().startswith("<!--"))
            f.write_text(f.read_text() + "Done.\n")

            pending = threads_mod.read_reply_dir(folder, 9, found)
            self.assertEqual(len(pending), 1)

            calls = []
            threads_mod.post("o/r", 9, "me", pending, api=lambda args: calls.append(args), pause=0)

            self.assertFalse(f.exists())
            self.assertEqual(calls[0][1], "repos/o/r/pulls/9/comments/5/replies")
            self.assertTrue(any("resolveReviewThread" in part for part in calls[1]))

    def test_post_skips_reply_already_posted(self):
        found = [{"thread": "T1", "resolved": False, "comment": 5, "path": "a.py", "line": 3,
                  "author": "bob", "body": "fix this", "hunk": "x", "replies": [("me", "Done.")]}]
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d) / "replies"
            folder.mkdir()
            (folder / "5.md").write_text("Done.\n")
            pending = threads_mod.read_reply_dir(folder, 9, found)
            calls = []
            threads_mod.post("o/r", 9, "me", pending, api=lambda args: calls.append(args), pause=0)
            # only the resolve call, no reply post, since "me" already replied this text
            self.assertEqual(len(calls), 1)
            self.assertTrue(any("resolveReviewThread" in part for part in calls[0]))

    def test_read_reply_dir_rejects_unknown_comment(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            (folder / "99.md").write_text("hi\n")
            with self.assertRaises(ValueError):
                threads_mod.read_reply_dir(folder, 9, [])


# ---------------------------------------------------------------------------
# checklist.py
# ---------------------------------------------------------------------------

BOX_LINE = re.compile(r"^- \[( |x)\] (.*)$")


def task_list_adf(lines: list[str]) -> dict:
    items = []
    for line in lines:
        m = BOX_LINE.match(line)
        state = "DONE" if m[1] == "x" else "TODO"
        items.append({"type": "taskItem", "attrs": {"state": state},
                      "content": [{"type": "text", "text": m[2]}]})
    return {"type": "doc", "version": 1, "content": [{"type": "taskList", "content": items}]}


# A rich fixture covering every node type H1 says must survive a checklist edit untouched: a link
# mark, a mention, a standalone inlineCard, a panel, a codeBlock, an orderedList, and a two-item
# taskList. localIds are included so the test can also prove they survive.
RICH_ADF = {
    "type": "doc", "version": 1,
    "content": [
        {"type": "paragraph", "content": [
            {"type": "text", "text": "See "},
            {"type": "text", "text": "the docs", "marks": [{"type": "link", "attrs": {"href": "https://example.com"}}]},
            {"type": "text", "text": " and "},
            {"type": "mention", "attrs": {"id": "u1", "text": "@bob"}},
        ]},
        {"type": "inlineCard", "attrs": {"url": "https://example.com/card"}},
        {"type": "panel", "attrs": {"panelType": "info"}, "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "Heads up."}]},
        ]},
        {"type": "codeBlock", "attrs": {"language": "python"}, "content": [
            {"type": "text", "text": "print('hi')"},
        ]},
        {"type": "orderedList", "attrs": {"order": 1}, "content": [
            {"type": "listItem", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "first"}]}]},
            {"type": "listItem", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "second"}]}]},
        ]},
        {"type": "taskList", "attrs": {"localId": "tl-1"}, "content": [
            {"type": "taskItem", "attrs": {"state": "TODO", "localId": "ti-1"},
             "content": [{"type": "text", "text": "step one"}]},
            {"type": "taskItem", "attrs": {"state": "TODO", "localId": "ti-2"},
             "content": [{"type": "text", "text": "step two"}]},
        ]},
    ],
}


class FakeJira:
    """Mimics checklist.Jira's description/put_description/add_comment surface. Each description()
    and put_description() hands back/stores an independent deep copy, as real GET/PUT would, so a
    test can tell "Jira kept the write" apart from "the caller's object happened to be mutated in
    place" -- the two are easy to conflate when run() edits the ADF tree it was handed, in place.
    """

    def __init__(self, adf):
        self.adf = adf
        self.puts = []
        self.comments = []

    def description(self, key):
        return copy.deepcopy(self.adf)

    def put_description(self, key, adf):
        self.puts.append((key, copy.deepcopy(adf)))
        self.adf = copy.deepcopy(adf)

    def add_comment(self, key, adf):
        self.comments.append((key, adf))


class Checklist(unittest.TestCase):
    def test_ticks_box_and_comments(self):
        jira = FakeJira(task_list_adf(["- [ ] step one", "- [ ] step two"]))
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "issue.md"
            f.write_text("- [x] step one\n- [ ] step two\n")
            result = checklist.run("CIAM-1", f, jira)
        self.assertEqual(result, "CIAM-1: 1 box changed")
        self.assertFalse(f.exists())
        self.assertEqual(len(jira.puts), 1)
        self.assertEqual(len(jira.comments), 1)
        put_key, put_adf = jira.puts[0]
        self.assertEqual(put_key, "CIAM-1")
        self.assertEqual(put_adf["content"][0]["content"][0]["attrs"]["state"], "DONE")
        self.assertEqual(put_adf["content"][0]["content"][1]["attrs"]["state"], "TODO")

    def test_refuses_when_other_text_differs(self):
        jira = FakeJira(task_list_adf(["- [ ] step one"]))
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "issue.md"
            f.write_text("- [ ] step ONE\n")
            with self.assertRaises(checklist.Refused):
                checklist.run("CIAM-1", f, jira)
            self.assertTrue(f.exists())
        self.assertEqual(jira.puts, [])

    def test_no_changes_deletes_file_without_writing(self):
        jira = FakeJira(task_list_adf(["- [x] step one"]))
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "issue.md"
            f.write_text("- [x] step one\n")
            result = checklist.run("CIAM-1", f, jira)
        self.assertEqual(result, "CIAM-1: no box to change")
        self.assertEqual(jira.puts, [])

    def test_refuses_when_jira_does_not_keep_the_change(self):
        jira = FakeJira(task_list_adf(["- [ ] step one"]))
        jira.put_description = lambda key, adf: None  # swallow the write; nothing sticks
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "issue.md"
            f.write_text("- [x] step one\n")
            with self.assertRaises(checklist.Refused):
                checklist.run("CIAM-1", f, jira)
            self.assertTrue(f.exists())

    def test_hardbreak_paragraph_before_tasks_ticks_the_right_box(self):
        """N1: a paragraph with a hardBreak renders to two lines; task_nodes must still gain a
        slot per rendered line, or the taskList's indices drift and ticking x ticks y instead
        (reviewer's live repro: [('x','TODO'),('y','DONE'),('z','TODO')])."""
        adf = {
            "type": "doc", "version": 1,
            "content": [
                {"type": "paragraph", "content": [
                    {"type": "text", "text": "line1"},
                    {"type": "hardBreak"},
                    {"type": "text", "text": "line2"},
                ]},
                {"type": "taskList", "content": [
                    {"type": "taskItem", "attrs": {"state": "TODO"}, "content": [{"type": "text", "text": "x"}]},
                    {"type": "taskItem", "attrs": {"state": "TODO"}, "content": [{"type": "text", "text": "y"}]},
                    {"type": "taskItem", "attrs": {"state": "TODO"}, "content": [{"type": "text", "text": "z"}]},
                ]},
            ],
        }
        jira = FakeJira(adf)
        current, _ = checklist.render(adf)
        self.assertEqual(current, "line1\nline2\n- [ ] x\n- [ ] y\n- [ ] z\n")
        wanted = current.replace("- [ ] x", "- [x] x", 1)
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "issue.md"
            f.write_text(wanted)
            result = checklist.run("CIAM-1", f, jira)
        self.assertEqual(result, "CIAM-1: 1 box changed")
        put_key, put_adf = jira.puts[0]
        items = put_adf["content"][1]["content"]
        states = {c["content"][0]["text"]: c["attrs"]["state"] for c in items}
        self.assertEqual(states, {"x": "DONE", "y": "TODO", "z": "TODO"})

    def test_multiple_hardbreaks_before_tasks_tick_the_right_box(self):
        """N1: a paragraph with TWO hardBreaks renders to three lines from one ADF node."""
        adf = {
            "type": "doc", "version": 1,
            "content": [
                {"type": "paragraph", "content": [
                    {"type": "text", "text": "a"},
                    {"type": "hardBreak"},
                    {"type": "text", "text": "b"},
                    {"type": "hardBreak"},
                    {"type": "text", "text": "c"},
                ]},
                {"type": "taskList", "content": [
                    {"type": "taskItem", "attrs": {"state": "TODO"}, "content": [{"type": "text", "text": "one"}]},
                    {"type": "taskItem", "attrs": {"state": "TODO"}, "content": [{"type": "text", "text": "two"}]},
                ]},
            ],
        }
        jira = FakeJira(adf)
        current, _ = checklist.render(adf)
        self.assertEqual(current, "a\nb\nc\n- [ ] one\n- [ ] two\n")
        wanted = current.replace("- [ ] two", "- [x] two", 1)
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "issue.md"
            f.write_text(wanted)
            result = checklist.run("CIAM-1", f, jira)
        self.assertEqual(result, "CIAM-1: 1 box changed")
        put_key, put_adf = jira.puts[0]
        items = put_adf["content"][1]["content"]
        states = {c["content"][0]["text"]: c["attrs"]["state"] for c in items}
        self.assertEqual(states, {"one": "TODO", "two": "DONE"})

    def test_nested_tasklist_ticks_only_the_inner_box(self):
        """N1/N4b: a nested taskList is a sibling of taskItem nodes inside the parent taskList's
        own content (ADF schema), never nested inside a taskItem's content. Ticking the nested
        item must not disturb its siblings, and the written attrs.state must land on the nested
        taskItem, never on the taskList node itself."""
        adf = {
            "type": "doc", "version": 1,
            "content": [
                {"type": "taskList", "content": [
                    {"type": "taskItem", "attrs": {"state": "TODO"}, "content": [{"type": "text", "text": "A"}]},
                    {"type": "taskList", "content": [
                        {"type": "taskItem", "attrs": {"state": "TODO"}, "content": [{"type": "text", "text": "B"}]},
                    ]},
                    {"type": "taskItem", "attrs": {"state": "TODO"}, "content": [{"type": "text", "text": "C"}]},
                ]},
            ],
        }
        jira = FakeJira(adf)
        current, _ = checklist.render(adf)
        self.assertEqual(current, "- [ ] A\n  - [ ] B\n- [ ] C\n")
        wanted = current.replace("  - [ ] B", "  - [x] B", 1)
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "issue.md"
            f.write_text(wanted)
            result = checklist.run("CIAM-1", f, jira)
        self.assertEqual(result, "CIAM-1: 1 box changed")
        put_key, put_adf = jira.puts[0]
        top = put_adf["content"][0]["content"]
        self.assertEqual(top[0]["attrs"]["state"], "TODO")  # A
        self.assertEqual(top[1]["type"], "taskList")
        self.assertNotIn("state", top[1].get("attrs", {}))  # never written on the taskList itself
        self.assertEqual(top[1]["content"][0]["attrs"]["state"], "DONE")  # B
        self.assertEqual(top[2]["attrs"]["state"], "TODO")  # C

    def test_back_adf_differing_only_by_normalization_still_verifies(self):
        """N4: Jira is free to normalize ADF on write (add a default attr, assign a localId,
        reorder a mark) without that being a wrong write. The old `back_adf != current_adf`
        byte-exact compare would refuse this; verify_back must look through render() instead."""
        jira = FakeJira(task_list_adf(["- [ ] step one"]))
        orig_description = jira.description
        calls = {"n": 0}

        def description(key):
            calls["n"] += 1
            adf = orig_description(key)
            if calls["n"] == 2:
                # Simulate Jira echoing the write back with an added attr that changes nothing
                # about the rendered text or the taskItem's state.
                adf["content"][0]["content"][0]["attrs"]["localId"] = "ti-99"
            return adf

        jira.description = description
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "issue.md"
            f.write_text("- [x] step one\n")
            result = checklist.run("CIAM-1", f, jira)
        self.assertEqual(result, "CIAM-1: 1 box changed")
        self.assertFalse(f.exists())

    def test_refuses_to_write_state_onto_non_taskitem_node(self):
        """N4b: task_nodes must only ever hold a taskItem; if a mapping bug ever produced a
        taskList (or anything else) at a changed index, run() must refuse rather than silently
        write attrs.state onto the wrong node type."""
        jira = FakeJira(task_list_adf(["- [ ] step one"]))
        bogus_node = {"type": "taskList", "content": []}
        with mock.patch.object(checklist, "render", return_value=("- [ ] step one\n", [bogus_node])):
            with tempfile.TemporaryDirectory() as d:
                f = Path(d) / "issue.md"
                f.write_text("- [x] step one\n")
                with self.assertRaises(checklist.Refused):
                    checklist.run("CIAM-1", f, jira)
                self.assertTrue(f.exists())
        self.assertEqual(jira.puts, [])


class RichADF(unittest.TestCase):
    def test_edits_only_task_state_leaves_everything_else_byte_for_byte(self):
        """H1: the PUT body must equal the GET body except the one taskItem's attrs.state -- every
        link, mention, inlineCard, panel, codeBlock, orderedList and localId untouched."""
        jira = FakeJira(copy.deepcopy(RICH_ADF))
        current_text, _ = checklist.render(RICH_ADF)
        wanted_text = current_text.replace("[ ] step one", "[x] step one", 1)
        self.assertNotEqual(wanted_text, current_text)
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "issue.md"
            f.write_text(wanted_text)
            result = checklist.run("CIAM-1", f, jira)
        self.assertEqual(result, "CIAM-1: 1 box changed")

        expected = copy.deepcopy(RICH_ADF)
        expected["content"][-1]["content"][0]["attrs"]["state"] = "DONE"
        self.assertEqual(len(jira.puts), 1)
        self.assertEqual(jira.puts[0], ("CIAM-1", expected))


# ---------------------------------------------------------------------------
# new_issue.py
# ---------------------------------------------------------------------------

class FakeJiraRest:
    """Mimics checklist.Jira's .send(method, path, body) over an in-memory issue store."""

    def __init__(self):
        self.projects = {"CIAM": {"issueTypes": [{"name": "Task"}, {"name": "Story"}]}}
        self.issues = {}  # key -> fields dict
        self.links_posted = []
        self._next = 100

    def add_issue(self, key, **fields):
        self.issues[key] = fields

    def send(self, method, path, body=None):
        path, _, query = path.partition("?")
        if method == "GET" and path.startswith("/rest/api/3/project/"):
            key = path.rsplit("/", 1)[-1]
            if key not in self.projects:
                raise checklist.Refused(f"Jira GET {path}: HTTP 404")
            return self.projects[key]
        if method == "GET" and path == "/rest/api/3/search/jql":
            jql = urllib.parse.parse_qs(query)["jql"][0]
            # Jira Cloud's real bug (verified live: CIAM-169): `summary = "..."` silently returns
            # no results. The fake must reproduce that, not just echo an exact match, or it would
            # hide the bug H2 found rather than catching a regression back to it.
            if re.search(r'summary\s*=\s*"', jql):
                return {"issues": []}
            m = re.search(r'summary\s*~\s*"(.*)"$', jql)
            if not m:
                return {"issues": []}
            # Undo jql_quote() first (quote-escape, then un-double backslashes -- the reverse
            # order from how jql_quote applied them), THEN strip the phrase's own quotes, THEN
            # undo lucene_escape()'s single-backslash escaping. Each step must be its own pass:
            # doing it in one regex mishandles the doubled backslashes jql_quote leaves behind.
            raw = m[1].replace('\\"', '"').replace('\\\\', '\\')
            if raw.startswith('"') and raw.endswith('"'):
                raw = raw[1:-1]
            phrase = re.sub(r'\\(.)', r'\1', raw)
            found = [{"key": k, "fields": {"summary": v.get("summary")}}
                     for k, v in self.issues.items() if phrase and phrase in (v.get("summary") or "")]
            return {"issues": found}
        if method == "GET" and re.match(r"^/rest/api/3/issue/[^/]+$", path):
            key = path.rsplit("/", 1)[-1]
            if key not in self.issues:
                raise checklist.Refused(f"Jira GET {path}: HTTP 404")
            return {"key": key, "fields": dict(self.issues[key])}
        if method == "POST" and path == "/rest/api/3/issue":
            key = f"GEN-{self._next}"
            self._next += 1
            fields = dict(body["fields"])
            fields["project"] = fields["project"]["key"]
            fields["issuetype"] = fields["issuetype"]
            if "parent" in fields:
                pass
            self.issues[key] = {
                "summary": fields["summary"],
                "issuetype": {"name": fields["issuetype"]["name"]},
                "parent": fields.get("parent"),
                "issuelinks": [],
            }
            return {"key": key}
        if method == "POST" and path == "/rest/api/3/issueLink":
            self.links_posted.append(body)
            # Real Jira: an issue's own issuelinks entries show only the OTHER issue in the pair,
            # labeled by the OTHER issue's role -- never both fields, and never the issue's own key.
            inward_key = body["inwardIssue"]["key"]
            outward_key = body["outwardIssue"]["key"]
            if inward_key in self.issues:
                self.issues[inward_key].setdefault("issuelinks", []).append(
                    {"type": body["type"], "outwardIssue": {"key": outward_key}})
            if outward_key in self.issues:
                self.issues[outward_key].setdefault("issuelinks", []).append(
                    {"type": body["type"], "inwardIssue": {"key": inward_key}})
            return None
        raise AssertionError(f"FakeJiraRest: unexpected {method} {path}")


class SummarySearch(unittest.TestCase):
    """H2: Jira Cloud's `summary = "..."` silently returns nothing (verified live: CIAM-169); only
    `~` phrase-matching works. existing() must use `~` and then post-filter to an exact match."""

    def test_existing_finds_issue_via_phrase_match_summary_equals_would_miss(self):
        jira = FakeJiraRest()
        jira.add_issue("CIAM-9", summary="Retire the old src tree", issuetype={"name": "Task"}, issuelinks=[])
        self.assertEqual(new_issue.existing(jira, "CIAM", "Retire the old src tree"), "CIAM-9")

    def test_fake_reproduces_the_real_summary_equals_bug(self):
        jira = FakeJiraRest()
        jira.add_issue("CIAM-9", summary="Retire the old src tree", issuetype={"name": "Task"}, issuelinks=[])
        q = urllib.parse.quote('project = "CIAM" AND summary = "Retire the old src tree"')
        result = jira.send("GET", f"/rest/api/3/search/jql?jql={q}")
        self.assertEqual(result["issues"], [])

    def test_existing_escapes_lucene_specials_in_summary(self):
        jira = FakeJiraRest()
        jira.add_issue("CIAM-10", summary="Fix (urgent): a+b", issuetype={"name": "Task"}, issuelinks=[])
        self.assertEqual(new_issue.existing(jira, "CIAM", "Fix (urgent): a+b"), "CIAM-10")

    def test_existing_follows_next_page_token_to_find_an_exact_match_on_page_two(self):
        """N6: /search/jql pages its results; existing() used to GET once and stop, so a match
        sitting on a later page was invisible -- it would report "no existing issue" and the
        caller would create a duplicate. A fake that pages two results, with the exact match only
        on page 2 (and a decoy near-match on page 1), confirms existing() actually follows
        nextPageToken rather than taking page 1 at face value."""

        class PagedFakeJiraRest:
            def send(self, method, path, body=None):
                assert method == "GET"
                path, _, query = path.partition("?")
                assert path == "/rest/api/3/search/jql"
                params = urllib.parse.parse_qs(query)
                if "nextPageToken" not in params:
                    # Page 1: a near-match only (substring match via ~, not an exact summary).
                    return {
                        "issues": [{"key": "CIAM-20", "fields": {"summary": "Retire the old src tree, draft"}}],
                        "nextPageToken": "page2",
                    }
                assert params["nextPageToken"][0] == "page2"
                # Page 2: the real exact match, and no further token -- exhausted after this.
                return {"issues": [{"key": "CIAM-21", "fields": {"summary": "Retire the old src tree"}}]}

        jira = PagedFakeJiraRest()
        self.assertEqual(new_issue.existing(jira, "CIAM", "Retire the old src tree"), "CIAM-21")


class LinkDirection(unittest.TestCase):
    """H3: issue X blocked by B shows, on X's own GET, an issuelinks entry of type Blocks with
    inwardIssue = B (matches lib/jira.py's blockers()). add_link()'s POST direction, and the fake's
    storage of the POST result on each side, both have to match that."""

    def test_add_link_blocked_by_posts_blocker_as_inward(self):
        jira = FakeJiraRest()
        jira.add_issue("CIAM-1", summary="Self", issuetype={"name": "Task"}, issuelinks=[])
        jira.add_issue("CIAM-2", summary="Blocker", issuetype={"name": "Task"}, issuelinks=[])
        new_issue.add_link(jira, "CIAM-1", "CIAM-2", "blocked_by")
        posted = jira.links_posted[0]
        self.assertEqual(posted["inwardIssue"]["key"], "CIAM-2")
        self.assertEqual(posted["outwardIssue"]["key"], "CIAM-1")

    def test_linked_detects_blocked_by_from_the_blocked_issues_own_get(self):
        links = [{"type": {"name": "Blocks"}, "inwardIssue": {"key": "CIAM-2"}}]
        self.assertTrue(new_issue.linked(links, "CIAM-2", "blocked_by"))

    def test_linked_does_not_mistake_the_reverse_direction_for_blocked_by(self):
        # If CIAM-1 instead blocks CIAM-2 (the opposite relationship), CIAM-1's own GET shows the
        # other issue as outwardIssue, not inwardIssue -- that is NOT "CIAM-1 blocked_by CIAM-2".
        links = [{"type": {"name": "Blocks"}, "outwardIssue": {"key": "CIAM-2"}}]
        self.assertFalse(new_issue.linked(links, "CIAM-2", "blocked_by"))

    def test_add_link_then_fake_storage_matches_real_jira_shape_on_both_sides(self):
        jira = FakeJiraRest()
        jira.add_issue("CIAM-1", summary="Self", issuetype={"name": "Task"}, issuelinks=[])
        jira.add_issue("CIAM-2", summary="Blocker", issuetype={"name": "Task"}, issuelinks=[])
        new_issue.add_link(jira, "CIAM-1", "CIAM-2", "blocked_by")
        self.assertEqual(jira.issues["CIAM-1"]["issuelinks"],
                          [{"type": {"name": "Blocks"}, "inwardIssue": {"key": "CIAM-2"}}])
        self.assertEqual(jira.issues["CIAM-2"]["issuelinks"],
                          [{"type": {"name": "Blocks"}, "outwardIssue": {"key": "CIAM-1"}}])
        self.assertTrue(new_issue.linked(jira.issues["CIAM-1"]["issuelinks"], "CIAM-2", "blocked_by"))


def new_issue_file(d, **fields):
    front = "\n".join(f"{k}: {v}" for k, v in fields.items())
    body = "## Task\nDo it.\n\n## Guardrails\nNone.\n\n## Lenses\n\n## Done\n- [ ] a\n- [ ] b\n"
    f = Path(d) / "issue.md"
    f.write_text(f"---\n{front}\n---\n{body}")
    return f


class NewIssue(unittest.TestCase):
    def setUp(self):
        self.lenses, self.extra = [], []

    def test_parse_splits_front_matter_and_body(self):
        with tempfile.TemporaryDirectory() as d:
            f = new_issue_file(d, project="CIAM", type="Task", summary="Retire src")
            fields, body = new_issue.parse(f.read_text())
        self.assertEqual(fields, {"project": "CIAM", "type": "Task", "summary": "Retire src"})
        self.assertTrue(body.startswith("## Task"))

    def test_parse_refuses_unknown_field(self):
        with self.assertRaises(checklist.Refused):
            new_issue.parse("---\nbogus: x\n---\nbody\n")

    def test_parse_defaults_project_to_ciam(self):
        fields, _ = new_issue.parse("---\ntype: Task\nsummary: X\n---\nbody\n")
        self.assertEqual(fields["project"], "CIAM")

    def test_creates_generic_issue_and_links(self):
        jira = FakeJiraRest()
        jira.projects["OPS"] = {"issueTypes": [{"name": "Bug"}]}
        jira.add_issue("OPS-5", summary="Blocker", issuetype={"name": "Bug"}, issuelinks=[])
        with tempfile.TemporaryDirectory() as d:
            f = new_issue_file(d, project="OPS", type="Bug", summary="New bug", blocked_by="OPS-5")
            with mock.patch("new_issue.create_ciam") as create_ciam:
                result = new_issue.run(f, jira, False, self.lenses, self.extra)
        create_ciam.assert_not_called()
        self.assertIn("created", result)
        self.assertEqual(len(jira.links_posted), 1)
        self.assertEqual(jira.links_posted[0]["type"]["name"], "Blocks")

    def test_check_only_writes_nothing(self):
        jira = FakeJiraRest()
        with tempfile.TemporaryDirectory() as d:
            f = new_issue_file(d, project="CIAM", type="Task", summary="Check me")
            result = new_issue.run(f, jira, True, self.lenses, self.extra)
        self.assertIn("a run creates it", result)
        self.assertEqual(jira.issues, {})

    def test_second_run_is_idempotent_and_adds_missing_link(self):
        jira = FakeJiraRest()
        jira.add_issue("CIAM-1", summary="Retire src", issuetype={"name": "Task"}, issuelinks=[])
        jira.add_issue("CIAM-2", summary="Related", issuetype={"name": "Task"}, issuelinks=[])
        with tempfile.TemporaryDirectory() as d:
            f = new_issue_file(d, project="CIAM", type="Task", summary="Retire src", relates="CIAM-2")
            result = new_issue.run(f, jira, False, self.lenses, self.extra)
        self.assertIn("already there", result)
        self.assertEqual(len(jira.links_posted), 1)
        self.assertEqual(jira.links_posted[0]["type"]["name"], "Relates")

    def test_parent_key_is_kept_full_same_project(self):
        # Uses a non-CIAM project so this goes through create_generic (a plain REST POST this fake
        # can answer), never create_ciam's real ~/.claude/skills/jira-ciam-create/create-ciam-issue.sh
        # helper -- that script would be a genuine live Jira write, which this suite never makes.
        jira = FakeJiraRest()
        jira.projects["OPS"] = {"issueTypes": [{"name": "Task"}]}
        jira.add_issue("OPS-9", summary="Epic", issuetype={"name": "Task"}, issuelinks=[])
        with tempfile.TemporaryDirectory() as d:
            f = new_issue_file(d, project="OPS", type="Task", summary="Child task", parent="OPS-9")
            with mock.patch("new_issue.create_ciam") as create_ciam:
                result = new_issue.run(f, jira, False, self.lenses, self.extra)
        create_ciam.assert_not_called()
        self.assertIn("created", result)
        created_key = next(k for k in jira.issues if k != "OPS-9")
        self.assertEqual(jira.issues[created_key]["parent"], {"key": "OPS-9"})

    def test_parent_key_is_kept_full_cross_project(self):
        # M1: the old code stripped parent to a bare number (fields["parent"].split("-", 1)[1]),
        # which would send "9" here -- a key that doesn't exist, 404ing in validate().
        jira = FakeJiraRest()
        jira.projects["OPS"] = {"issueTypes": [{"name": "Bug"}]}
        jira.add_issue("CIAM-9", summary="Epic", issuetype={"name": "Epic"}, issuelinks=[])
        with tempfile.TemporaryDirectory() as d:
            f = new_issue_file(d, project="OPS", type="Bug", summary="Child bug", parent="CIAM-9")
            with mock.patch("new_issue.create_ciam") as create_ciam:
                result = new_issue.run(f, jira, False, self.lenses, self.extra)
        create_ciam.assert_not_called()
        self.assertIn("created", result)
        created_key = next(k for k in jira.issues if k != "CIAM-9")
        self.assertEqual(jira.issues[created_key]["parent"], {"key": "CIAM-9"})

    def test_refuses_unknown_issue_type(self):
        jira = FakeJiraRest()
        with tempfile.TemporaryDirectory() as d:
            f = new_issue_file(d, project="CIAM", type="Epic", summary="X")
            with self.assertRaises(checklist.Refused):
                new_issue.run(f, jira, False, self.lenses, self.extra)

    def test_refuses_body_against_outline(self):
        jira = FakeJiraRest()
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "issue.md"
            f.write_text("---\nproject: CIAM\ntype: Task\nsummary: X\n---\nno sections here\n")
            with self.assertRaises(checklist.Refused):
                new_issue.run(f, jira, False, self.lenses, self.extra)

    def test_create_ciam_delegates_to_helper_script(self):
        with tempfile.TemporaryDirectory() as d:
            body_capture = {}

            def fake_run(args, capture_output, text):
                body_capture["args"] = args
                body_capture["body_file"] = Path(args[args.index("-m") + 1]).read_text()
                return subprocess.CompletedProcess(args, 0, stdout="CIAM-42 created\n", stderr="")

            with mock.patch("new_issue.subprocess.run", side_effect=fake_run):
                key = new_issue.create_ciam({"summary": "X", "type": "Task"}, "the body\n")
        self.assertEqual(key, "CIAM-42")
        self.assertEqual(body_capture["body_file"], "the body\n")

    def test_create_ciam_refuses_on_helper_failure(self):
        with mock.patch("new_issue.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess([], 1, stdout="", stderr="boom")
            with self.assertRaises(checklist.Refused):
                new_issue.create_ciam({"summary": "X", "type": "Task"}, "body\n")


# ---------------------------------------------------------------------------
# pr.sh (the merged-PR tail only; everything else is gh/git plumbing unchanged from the source)
# ---------------------------------------------------------------------------

FAKE_GH = """#!/usr/bin/env python3
import os
import sys
argv = sys.argv[1:]
if argv[:2] == ["repo", "view"]:
    print("main")
elif argv[:2] == ["pr", "view"]:
    field = argv[argv.index("--json") + 1]
    if field == "state":
        print("MERGED")
    elif field == "headRefOid":
        print(open(os.environ["HEAD_OID_FILE"]).read().strip())
    elif field == "url":
        print("https://github.com/o/r/pull/9")
elif argv[:2] == ["pr", "edit"]:
    pass
sys.exit(0)
"""

# M5: a fake gh for exercising pr.sh's commit step, which the MERGED-branch FAKE_GH above never
# reaches (it always reports state MERGED, so pr.sh exits before ever looking at commit.txt).
# `gh pr view` fails here -- no PR exists yet for the branch -- so pr.sh's `state` comes back
# empty and falls through to the uncommitted-changes branch instead.
FAKE_GH_NO_PR = """#!/usr/bin/env python3
import sys
argv = sys.argv[1:]
if argv[:2] == ["repo", "view"]:
    print("main")
elif argv[:2] == ["pr", "view"]:
    sys.exit(1)
sys.exit(0)
"""

# Same as FAKE_GH_NO_PR, but also used past the commit step; pr.sh still tries `git push` next
# (there's no fake for that -- it's real git, and the test repo has no remote), so that test reads
# commit.txt's fate from git log/the filesystem rather than from pr.sh's own exit code.
FAKE_GH_NO_PR_NO_PUSH = FAKE_GH_NO_PR


class PrDone(unittest.TestCase):
    def test_merged_pr_tells_to_run_plan_done(self):
        with tempfile.TemporaryDirectory() as d:
            outer = Path(d)
            root = outer / "repo"
            root.mkdir()
            run_git = lambda *a: subprocess.run(["git", *a], cwd=root, check=True,
                                                 capture_output=True, text=True)
            run_git("init", "-q")
            run_git("config", "user.email", "t@t.com")
            run_git("config", "user.name", "t")
            (root / "f.txt").write_text("x\n")
            run_git("add", "-A")
            run_git("commit", "-q", "-m", "init")
            run_git("checkout", "-q", "-b", "fix/CIAM-9-x")
            head = run_git("rev-parse", "HEAD").stdout.strip()
            (outer / "HEAD_OID").write_text(head)

            bindir = outer / "bin"
            bindir.mkdir()
            fake_gh = bindir / "gh"
            fake_gh.write_text(FAKE_GH)
            fake_gh.chmod(0o755)

            env = dict(os.environ)
            env["PATH"] = f"{bindir}:{env['PATH']}"
            env["HEAD_OID_FILE"] = str(outer / "HEAD_OID")

            result = subprocess.run([str(SCRIPTS / "pr.sh")], cwd=root, env=env,
                                     capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("run plan done CIAM-9 to clean up", result.stdout)

            # The flags come in any order before the key.
            result = subprocess.run([str(SCRIPTS / "pr.sh"), "--no-copilot", "done", "CIAM-9"], cwd=root,
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("run plan done CIAM-9 to clean up", result.stdout)

    def test_merged_pr_tells_to_run_plan_done_unprefixed_branch(self):
        # L2: pr.sh's key-from-branch sed required a <prefix>/ segment before KEY-slug; a bare
        # CIAM-9-x branch (no feat/, fix/, chore/, ...) must resolve its key the same way
        # lib/start.py's issueBranch() and lib/issuekeys.py's ISSUE_KEY_PATTERN already treat an unprefixed branch.
        with tempfile.TemporaryDirectory() as d:
            outer = Path(d)
            root = outer / "repo"
            root.mkdir()
            run_git = lambda *a: subprocess.run(["git", *a], cwd=root, check=True,
                                                 capture_output=True, text=True)
            run_git("init", "-q")
            run_git("config", "user.email", "t@t.com")
            run_git("config", "user.name", "t")
            (root / "f.txt").write_text("x\n")
            run_git("add", "-A")
            run_git("commit", "-q", "-m", "init")
            run_git("checkout", "-q", "-b", "CIAM-9-x")
            head = run_git("rev-parse", "HEAD").stdout.strip()
            (outer / "HEAD_OID").write_text(head)

            bindir = outer / "bin"
            bindir.mkdir()
            fake_gh = bindir / "gh"
            fake_gh.write_text(FAKE_GH)
            fake_gh.chmod(0o755)

            env = dict(os.environ)
            env["PATH"] = f"{bindir}:{env['PATH']}"
            env["HEAD_OID_FILE"] = str(outer / "HEAD_OID")

            result = subprocess.run([str(SCRIPTS / "pr.sh")], cwd=root, env=env,
                                     capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("run plan done CIAM-9 to clean up", result.stdout)

    def test_refuses_commit_message_with_smart_commit_token(self):
        # M5: a #<word> next to a Jira key in .tmp/KEY/commit.txt would run as a Jira smart commit
        # once pushed -- Jira's GitHub integration reads it off the commit, not off anything pr.sh
        # itself calls, so pr.sh must refuse before committing rather than relying on anything
        # downstream to catch it.
        with tempfile.TemporaryDirectory() as d:
            outer = Path(d)
            root = outer / "repo"
            root.mkdir()
            run_git = lambda *a: subprocess.run(["git", *a], cwd=root, check=True,
                                                 capture_output=True, text=True)
            run_git("init", "-q")
            run_git("config", "user.email", "t@t.com")
            run_git("config", "user.name", "t")
            (root / "f.txt").write_text("x\n")
            run_git("add", "-A")
            run_git("commit", "-q", "-m", "init")
            run_git("checkout", "-q", "-b", "fix/CIAM-9-x")
            (root / "f.txt").write_text("changed\n")

            tmp_dir = root / ".tmp" / "CIAM-9"
            tmp_dir.mkdir(parents=True)
            (tmp_dir / "commit.txt").write_text("CIAM-9 #close fixes the bug\n")

            bindir = outer / "bin"
            bindir.mkdir()
            fake_gh = bindir / "gh"
            fake_gh.write_text(FAKE_GH_NO_PR)
            fake_gh.chmod(0o755)

            env = dict(os.environ)
            env["PATH"] = f"{bindir}:{env['PATH']}"

            result = subprocess.run([str(SCRIPTS / "pr.sh")], cwd=root, env=env,
                                     capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("looks like a Jira smart commit", result.stderr)
            # Refused before committing: no new commit, and the file it flagged is still there.
            log = run_git("log", "--oneline").stdout
            self.assertEqual(len(log.strip().splitlines()), 1)
            self.assertTrue((tmp_dir / "commit.txt").exists())

    def test_commits_a_message_with_a_key_but_no_smart_commit_token(self):
        # The flip side of the refusal above: a commit message can still name the ticket (which
        # almost every commit.txt does) as long as it has no "#<word>" beside it.
        with tempfile.TemporaryDirectory() as d:
            outer = Path(d)
            root = outer / "repo"
            root.mkdir()
            run_git = lambda *a: subprocess.run(["git", *a], cwd=root, check=True,
                                                 capture_output=True, text=True)
            run_git("init", "-q")
            run_git("config", "user.email", "t@t.com")
            run_git("config", "user.name", "t")
            (root / "f.txt").write_text("x\n")
            run_git("add", "-A")
            run_git("commit", "-q", "-m", "init")
            run_git("checkout", "-q", "-b", "fix/CIAM-9-x")
            (root / "f.txt").write_text("changed\n")

            tmp_dir = root / ".tmp" / "CIAM-9"
            tmp_dir.mkdir(parents=True)
            (tmp_dir / "commit.txt").write_text("CIAM-9 fixes the bug, no hashtags here\n")

            bindir = outer / "bin"
            bindir.mkdir()
            fake_gh = bindir / "gh"
            fake_gh.write_text(FAKE_GH_NO_PR_NO_PUSH)
            fake_gh.chmod(0o755)

            env = dict(os.environ)
            env["PATH"] = f"{bindir}:{env['PATH']}"
            env["GIT_TERMINAL_PROMPT"] = "0"

            result = subprocess.run([str(SCRIPTS / "pr.sh")], cwd=root, env=env,
                                     capture_output=True, text=True)
            # No remote is configured, so the push after the commit fails -- that's fine, the
            # point of this test is only that the commit itself was allowed to happen.
            log = run_git("log", "--oneline").stdout
            self.assertEqual(len(log.strip().splitlines()), 2, result.stderr)
            self.assertFalse((tmp_dir / "commit.txt").exists())

    def test_refuses_smart_commit_already_on_branch_not_just_commit_txt(self):
        # N2: refuse_smart_commits_file only ever saw commit.txt, the ONE new message pr.sh was
        # about to write. A commit already on the branch -- made with `git commit -am "..."`,
        # never passing through commit.txt at all -- would reach origin on push unseen. origin/
        # <base> must actually exist locally for the range scan to run, so push a bare remote's
        # main once before branching.
        with tempfile.TemporaryDirectory() as d:
            outer = Path(d)
            origin = outer / "origin.git"
            root = outer / "repo"
            subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
            root.mkdir()
            run_git = lambda *a: subprocess.run(["git", *a], cwd=root, check=True,
                                                 capture_output=True, text=True)
            run_git("init", "-q", "-b", "main")
            run_git("config", "user.email", "t@t.com")
            run_git("config", "user.name", "t")
            run_git("remote", "add", "origin", str(origin))
            (root / "f.txt").write_text("x\n")
            run_git("add", "-A")
            run_git("commit", "-q", "-m", "init")
            run_git("push", "-q", "-u", "origin", "main")
            run_git("checkout", "-q", "-b", "fix/CIAM-9-x")
            # Bypasses refuse_smart_commits_file entirely: committed directly, never through
            # .tmp/CIAM-9/commit.txt.
            (root / "f.txt").write_text("bad\n")
            run_git("commit", "-q", "-am", "CIAM-9 #close already on the branch")
            (root / "f.txt").write_text("good\n")

            tmp_dir = root / ".tmp" / "CIAM-9"
            tmp_dir.mkdir(parents=True)
            (tmp_dir / "commit.txt").write_text("CIAM-9 fixes the bug, no hashtags here\n")

            bindir = outer / "bin"
            bindir.mkdir()
            fake_gh = bindir / "gh"
            fake_gh.write_text(FAKE_GH_NO_PR)
            fake_gh.chmod(0o755)

            env = dict(os.environ)
            env["PATH"] = f"{bindir}:{env['PATH']}"

            result = subprocess.run([str(SCRIPTS / "pr.sh")], cwd=root, env=env,
                                     capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("a commit already on", result.stderr)
            self.assertIn("looks like a Jira smart commit", result.stderr)
            # The new commit.txt-based commit was still allowed (it's clean); only the push was
            # stopped, so nothing bad reached origin.
            log = run_git("log", "--oneline").stdout
            self.assertEqual(len(log.strip().splitlines()), 3, result.stderr)
            origin_log = subprocess.run(["git", "log", "--oneline", "main"], cwd=origin,
                                         capture_output=True, text=True).stdout
            self.assertEqual(len(origin_log.strip().splitlines()), 1)

    def test_refuses_smart_commit_when_origin_base_ref_is_missing_locally(self):
        # N7: reviewer's exact repro. refuse_smart_commits_range used to run only when
        # `origin/$base` resolved locally, reasoning the push itself would fail or need a fetch
        # first otherwise -- false: deleting the remote-tracking ref (git update-ref -d) doesn't
        # stop `git push` from working, so a smart commit slipped straight through to origin.
        # Fixed to scan `HEAD --not --remotes=origin`, which needs no particular origin ref to
        # exist, and to run unconditionally.
        with tempfile.TemporaryDirectory() as d:
            outer = Path(d)
            origin = outer / "origin.git"
            root = outer / "repo"
            subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
            root.mkdir()
            run_git = lambda *a: subprocess.run(["git", *a], cwd=root, check=True,
                                                 capture_output=True, text=True)
            run_git("init", "-q", "-b", "main")
            run_git("config", "user.email", "t@t.com")
            run_git("config", "user.name", "t")
            run_git("remote", "add", "origin", str(origin))
            (root / "f.txt").write_text("x\n")
            run_git("add", "-A")
            run_git("commit", "-q", "-m", "init")
            run_git("push", "-q", "-u", "origin", "main")
            run_git("checkout", "-q", "-b", "fix/CIAM-9-x")
            # The reviewer's exact repro: delete the local origin/main remote-tracking ref, then
            # commit a smart commit token directly (bypassing commit.txt), and run pr.
            run_git("update-ref", "-d", "refs/remotes/origin/main")
            (root / "f.txt").write_text("bad\n")
            run_git("commit", "-q", "-am", "CIAM-9 #close sneaky")

            bindir = outer / "bin"
            bindir.mkdir()
            fake_gh = bindir / "gh"
            fake_gh.write_text(FAKE_GH_NO_PR)
            fake_gh.chmod(0o755)

            env = dict(os.environ)
            env["PATH"] = f"{bindir}:{env['PATH']}"

            result = subprocess.run([str(SCRIPTS / "pr.sh")], cwd=root, env=env,
                                     capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("looks like a Jira smart commit", result.stderr)
            # Nothing reached origin: the bare remote still has only the one "init" commit.
            origin_log = subprocess.run(["git", "log", "--oneline", "--all"], cwd=origin,
                                         capture_output=True, text=True).stdout
            self.assertEqual(len(origin_log.strip().splitlines()), 1)

    def test_refuses_a_path_shaped_key_argument(self):
        # N10: n (the KEY argument, or the branch-derived fallback) is used to build .tmp/$n
        # paths before anything downstream (ghbody.sh, checklist.py) would ever validate it -- an
        # unanchored/path-shaped value like "../x" must be refused immediately, not threaded into
        # a path.
        with tempfile.TemporaryDirectory() as d:
            outer = Path(d)
            root = outer / "repo"
            root.mkdir()
            run_git = lambda *a: subprocess.run(["git", *a], cwd=root, check=True,
                                                 capture_output=True, text=True)
            run_git("init", "-q", "-b", "main")
            run_git("config", "user.email", "t@t.com")
            run_git("config", "user.name", "t")
            (root / "f.txt").write_text("x\n")
            run_git("add", "-A")
            run_git("commit", "-q", "-m", "init")
            run_git("checkout", "-q", "-b", "fix/CIAM-9-x")

            bindir = outer / "bin"
            bindir.mkdir()
            fake_gh = bindir / "gh"
            fake_gh.write_text(FAKE_GH_NO_PR)
            fake_gh.chmod(0o755)

            env = dict(os.environ)
            env["PATH"] = f"{bindir}:{env['PATH']}"

            result = subprocess.run([str(SCRIPTS / "pr.sh"), "../x"], cwd=root, env=env,
                                     capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("isn't a Jira key", result.stderr)
            self.assertFalse((outer / "x").exists())
            self.assertFalse((root.parent / "x").exists())

    def test_refuses_on_default_branch(self):
        with tempfile.TemporaryDirectory() as d:
            outer = Path(d)
            root = outer / "repo"
            root.mkdir()
            run_git = lambda *a: subprocess.run(["git", *a], cwd=root, check=True,
                                                 capture_output=True, text=True)
            run_git("init", "-q", "-b", "main")
            run_git("config", "user.email", "t@t.com")
            run_git("config", "user.name", "t")
            (root / "f.txt").write_text("x\n")
            run_git("add", "-A")
            run_git("commit", "-q", "-m", "init")

            bindir = outer / "bin"
            bindir.mkdir()
            fake_gh = bindir / "gh"
            fake_gh.write_text(FAKE_GH)
            fake_gh.chmod(0o755)
            (outer / "HEAD_OID").write_text("unused")

            env = dict(os.environ)
            env["PATH"] = f"{bindir}:{env['PATH']}"
            env["HEAD_OID_FILE"] = str(outer / "HEAD_OID")

            result = subprocess.run([str(SCRIPTS / "pr.sh")], cwd=root, env=env,
                                     capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("switch to the issue's branch", result.stderr)


# ---------------------------------------------------------------------------
# ghbody.sh
# ---------------------------------------------------------------------------


class GhBody(unittest.TestCase):
    def test_pr_edit_resolves_key_from_unprefixed_branch(self):
        # L2: ghbody.sh's key-from-branch sed required a <prefix>/ segment before KEY-slug, same as
        # pr.sh's; a bare CIAM-9-x branch (no feat/, fix/, chore/, ...) must resolve too.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            run_git = lambda *a: subprocess.run(["git", *a], cwd=root, check=True,
                                                 capture_output=True, text=True)
            run_git("init", "-q")
            run_git("config", "user.email", "t@t.com")
            run_git("config", "user.name", "t")
            (root / "f.txt").write_text("x\n")
            run_git("add", "-A")
            run_git("commit", "-q", "-m", "init")
            run_git("checkout", "-q", "-b", "CIAM-9-x")

            tmp_dir = root / ".tmp" / "CIAM-9"
            tmp_dir.mkdir(parents=True)
            (tmp_dir / "pr-body.md").write_text("the body\n")

            env = dict(os.environ)
            env["GHBODY_DRY"] = "1"
            result = subprocess.run(
                [str(SCRIPTS / "ghbody.sh"), "pr-edit"], cwd=root, env=env,
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("gh pr edit --body-file", result.stdout)
            self.assertIn(str(tmp_dir / "pr-body.md"), result.stdout)

    def test_pr_edit_refuses_a_body_with_a_smart_commit_token(self):
        # M5: a #<word> beside a Jira key in the PR body would run as a Jira smart commit once
        # Jira's GitHub integration reads it off the PR, so ghbody.sh must refuse before gh pr
        # edit ever sends it.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            run_git = lambda *a: subprocess.run(["git", *a], cwd=root, check=True,
                                                 capture_output=True, text=True)
            run_git("init", "-q")
            run_git("config", "user.email", "t@t.com")
            run_git("config", "user.name", "t")
            (root / "f.txt").write_text("x\n")
            run_git("add", "-A")
            run_git("commit", "-q", "-m", "init")
            run_git("checkout", "-q", "-b", "CIAM-9-x")

            tmp_dir = root / ".tmp" / "CIAM-9"
            tmp_dir.mkdir(parents=True)
            (tmp_dir / "pr-body.md").write_text("CIAM-9 #comment all set\n")

            env = dict(os.environ)
            env["GHBODY_DRY"] = "1"
            result = subprocess.run(
                [str(SCRIPTS / "ghbody.sh"), "pr-edit"], cwd=root, env=env,
                capture_output=True, text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("looks like a Jira smart commit", result.stderr)
            self.assertNotIn("gh pr edit", result.stdout)

    def test_pr_create_refuses_a_title_with_a_smart_commit_token(self):
        # M5: pr-create's title comes from the branch's first commit subject, a different source
        # than the body file -- the title needs the same check, since Jira's integration reads
        # smart commits off PR titles too.
        with tempfile.TemporaryDirectory() as d:
            outer = Path(d)
            root = outer / "repo"
            root.mkdir()
            bare = outer / "bare.git"
            subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
            run_git = lambda *a: subprocess.run(["git", *a], cwd=root, check=True,
                                                 capture_output=True, text=True)
            run_git("init", "-q", "-b", "main")
            run_git("config", "user.email", "t@t.com")
            run_git("config", "user.name", "t")
            run_git("remote", "add", "origin", str(bare))
            (root / "f.txt").write_text("x\n")
            run_git("add", "-A")
            run_git("commit", "-q", "-m", "init")
            # Local ref standing in for origin/main, without a real push: pr-create's title comes
            # from commits ahead of this, same as git log --reverse origin/main..HEAD would see.
            main_oid = run_git("rev-parse", "HEAD").stdout.strip()
            run_git("update-ref", "refs/remotes/origin/main", main_oid)
            run_git("checkout", "-q", "-b", "CIAM-9-x")
            (root / "f.txt").write_text("changed\n")
            run_git("add", "-A")
            run_git("commit", "-q", "-m", "CIAM-9 #done fixes the bug")

            tmp_dir = root / ".tmp" / "CIAM-9"
            tmp_dir.mkdir(parents=True)
            (tmp_dir / "pr-body.md").write_text("an ordinary body\n")

            bindir = outer / "bin"
            bindir.mkdir()
            fake_gh = bindir / "gh"
            fake_gh.write_text(
                "#!/usr/bin/env python3\n"
                "import sys\n"
                "argv = sys.argv[1:]\n"
                "if argv[:2] == ['repo', 'view']:\n"
                "    print('main')\n"
                "sys.exit(0)\n"
            )
            fake_gh.chmod(0o755)

            env = dict(os.environ)
            env["GHBODY_DRY"] = "1"
            env["PATH"] = f"{bindir}:{env['PATH']}"
            result = subprocess.run(
                [str(SCRIPTS / "ghbody.sh"), "pr-create"], cwd=root, env=env,
                capture_output=True, text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("looks like a Jira smart commit", result.stderr)
            self.assertNotIn("gh pr create", result.stdout)


    def test_pr_create_leaves_copilot_off_with_no_copilot(self):
        with tempfile.TemporaryDirectory() as d:
            outer = Path(d)
            root = new_pr_branch(outer)
            for no_copilot, asked in (("", True), ("1", False)):
                env = fake_gh_env(outer, GHBODY_DRY="1", GHBODY_NO_COPILOT=no_copilot)
                result = subprocess.run([str(SCRIPTS / "ghbody.sh"), "pr-create"], cwd=root, env=env,
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("gh pr create", result.stdout)
                self.assertEqual("--add-reviewer @copilot" in result.stdout, asked)


# A repo on branch CIAM-9-x, one commit ahead of a local origin/main, with a drafted PR body.
def new_pr_branch(outer):
    root = outer / "repo"
    root.mkdir()
    run_git = lambda *a: subprocess.run(["git", *a], cwd=root, check=True, capture_output=True, text=True)
    run_git("init", "-q", "-b", "main")
    run_git("config", "user.email", "t@t.com")
    run_git("config", "user.name", "t")
    (root / "f.txt").write_text("x\n")
    run_git("add", "-A")
    run_git("commit", "-q", "-m", "init")
    run_git("update-ref", "refs/remotes/origin/main", run_git("rev-parse", "HEAD").stdout.strip())
    run_git("checkout", "-q", "-b", "CIAM-9-x")
    (root / "f.txt").write_text("changed\n")
    run_git("commit", "-qam", "Fix the bug (CIAM-9)")
    tmp_dir = root / ".tmp" / "CIAM-9"
    tmp_dir.mkdir(parents=True)
    (tmp_dir / "pr-body.md").write_text("an ordinary body\n")
    return root


# The environment for a gh that answers only `gh repo view` (the default branch, main).
def fake_gh_env(outer, **extra):
    bindir = outer / "bin"
    bindir.mkdir(exist_ok=True)
    fake_gh = bindir / "gh"
    fake_gh.write_text("#!/usr/bin/env python3\nimport sys\n"
                       "if sys.argv[1:3] == ['repo', 'view']:\n    print('main')\n")
    fake_gh.chmod(0o755)
    return {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", **extra}


if __name__ == "__main__":
    unittest.main()
