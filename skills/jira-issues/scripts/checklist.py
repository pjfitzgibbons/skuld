#!/usr/bin/env python3
"""Tick a Jira issue's checkboxes from a copy of its description, and change nothing else.

    checklist.py CIAM-79 .tmp/CIAM-79/issue.md      # what pr runs after its push

The file is a plain-text/Markdown rendering of the issue's ADF description with boxes ticked or
unticked. It is applied only when that is its one difference from the description Jira holds now,
so an edit made in Jira since the copy was taken is never overwritten: any other difference stops
it, naming the first such line.

The change is made by editing the live ADF in place: the taskItem nodes in the description Jira
just returned are matched, by text and in order, to the checklist lines in the file, and only
their `attrs.state` is flipped. Nothing else in the tree is touched, so it is PUT back otherwise
byte-for-byte identical to what Jira held — no Markdown->ADF conversion happens here at all
(md_to_adf.py is for new_issue.py's brand-new description, never for an edit to an existing one:
round-tripping an existing description through Markdown would lose links, mentions, inlineCards,
panels, codeBlocks, list numbering, paragraph boundaries and taskItem localIds). The PUT is read
back and compared as ADF (not as rendered text) to verify it stuck, and then the file is deleted.
It is safe to repeat: a file that changes no box is deleted with nothing written.

Auth is via ~/.netrc, machine cryoport.atlassian.net (login = email, password = API token). This
script never prints the token, and it never transitions the issue's status.
"""

import json
import netrc as netrc_module
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

BOX = re.compile(r"^(\s*[-*+]\s+)\[([ xX])\]")


class Refused(Exception):
    pass


def lines(text: str) -> list[str]:
    return text.replace("\r\n", "\n").rstrip().split("\n")


def unboxed(line: str) -> str:
    return BOX.sub(r"\1[ ]", line)


def ticked(line: str) -> bool | None:
    m = BOX.match(line)
    return None if m is None else m.group(2) != " "


def changes(current: str, wanted: str) -> list[tuple[int, bool]]:
    """Each line whose box wanted changes, as (index, ticked); Refused if anything else differs."""
    have, want = lines(current), lines(wanted)
    for i in range(max(len(have), len(want))):
        a = have[i] if i < len(have) else None
        b = want[i] if i < len(want) else None
        if a is None or b is None or unboxed(a) != unboxed(b):
            shown = b if b is not None else a
            raise Refused(f"line {i + 1} differs by more than its box: {shown!r}")
    return [(i, ticked(b)) for i, (a, b) in enumerate(zip(have, want)) if ticked(a) != ticked(b)]


def note(current: str, found: list[tuple[int, bool]]) -> str:
    have = lines(current)
    return [f"{'Ticked' if on else 'Unticked'}: {BOX.sub('', have[i]).strip()}" for i, on in found]


def _append_line(out: list[str], task_nodes: list[dict | None], text: str, node: dict | None) -> None:
    """Appends text to out, splitting it on any embedded "\\n" a hardBreak produced (N1): a single
    ADF node (a paragraph, a taskItem, a bulletList item) can render to more than one line, and
    task_nodes has to gain exactly one slot per resulting line or every later index drifts -- the
    bug the reviewer reproduced as ticking the wrong box. The node (a taskItem, when there is one)
    is attached to the FIRST line only, since that's the one carrying the "- [ ]" marker; a
    continuation line from an embedded hardBreak is never itself a checklist box, so it gets None.
    """
    parts = text.split("\n")
    out.extend(parts)
    task_nodes.append(node)
    task_nodes.extend([None] * (len(parts) - 1))


def adf_text(node: dict, out: list[str], task_nodes: list[dict | None], indent: str = "") -> None:
    """Walk an ADF node, appending the Markdown-ish lines checklist.py's line-diff can compare, and
    in task_nodes (aligned 1:1 with out, via _append_line) a reference to the taskItem a line came
    from, or None. task_nodes is the whole point: it is how run() finds, without re-parsing or
    reconstructing anything, exactly which live ADF node a changed checklist line must flip
    attrs.state on -- and it is only ever a taskItem node (N4b): a nested taskList (a sibling of
    taskItem inside the parent taskList's content, per ADF's schema) is never itself ticked, only
    recursed into for its own taskItems, indented one level under its parent item's line.
    """
    t = node.get("type")
    if t == "taskList":
        for child in node.get("content", []):
            if child.get("type") == "taskList":
                adf_text(child, out, task_nodes, indent + "  ")
                continue
            state = child.get("attrs", {}).get("state") == "DONE"
            text = "".join(adf_inline_text(c) for c in child.get("content", []))
            _append_line(out, task_nodes, f"{indent}- [{'x' if state else ' '}] {text}", child)
        return
    if t in ("paragraph", "heading"):
        prefix = "#" * node.get("attrs", {}).get("level", 0) + " " if t == "heading" else ""
        text = prefix + "".join(adf_inline_text(c) for c in node.get("content", []))
        _append_line(out, task_nodes, text, None)
        return
    if t == "bulletList":
        for item in node.get("content", []):
            text = "".join(adf_inline_text(c) for c in _flatten(item))
            _append_line(out, task_nodes, f"- {text}", None)
        return
    for child in node.get("content", []):
        adf_text(child, out, task_nodes)


def _flatten(node: dict) -> list[dict]:
    out = []
    for child in node.get("content", []):
        if child.get("type") == "paragraph":
            out += child.get("content", [])
        else:
            out.append(child)
    return out


def adf_inline_text(node: dict) -> str:
    if node.get("type") == "text":
        return node.get("text", "")
    if node.get("type") == "hardBreak":
        return "\n"
    return "".join(adf_inline_text(c) for c in node.get("content", []))


def render(description: dict | str | None) -> tuple[str, list[dict | None]]:
    """A plain-text/Markdown-ish rendering of an ADF description, good enough for the line diff,
    and the taskItem node (or None) each of its lines came from, 1:1 by index. This is read-only:
    it is never converted back into ADF. The one thing ever written back is attrs.state on one of
    these same node objects, flipped in place on the tree jira.description() returned.
    """
    if not description:
        return "", []
    if isinstance(description, str):
        return description, [None] * len(lines(description))
    out: list[str] = []
    task_nodes: list[dict | None] = []
    for node in description.get("content", []):
        adf_text(node, out, task_nodes)
    return "\n".join(out) + "\n", task_nodes


class Jira:
    SITE = "cryoport.atlassian.net"

    def __init__(self):
        auth = netrc_module.netrc().authenticators(self.SITE)
        if auth is None:
            raise Refused(f"no ~/.netrc entry for machine {self.SITE}; add login=<email> password=<API token>")
        self.login, _, self.token = auth

    def send(self, method: str, path: str, body: dict | None = None):
        import base64
        data = json.dumps(body).encode() if body is not None else None
        creds = base64.b64encode(f"{self.login}:{self.token}".encode()).decode()
        req = urllib.request.Request(f"https://{self.SITE}{path}", data=data, method=method,
                                     headers={"Authorization": f"Basic {creds}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            raise Refused(f"Jira {method} {path}: HTTP {e.code}") from None
        except urllib.error.URLError as e:
            raise Refused(f"Jira can't be reached: {e.reason}") from None
        return json.loads(raw) if raw.strip() else None

    def description(self, key: str):
        return self.send("GET", f"/rest/api/3/issue/{key}?fields=description")["fields"].get("description")

    def put_description(self, key: str, adf: dict) -> None:
        self.send("PUT", f"/rest/api/3/issue/{key}", {"fields": {"description": adf}})

    def add_comment(self, key: str, adf: dict) -> None:
        self.send("POST", f"/rest/api/3/issue/{key}/comment", {"body": adf})


def run(key: str, file: Path, jira) -> str:
    current_adf = jira.description(key)
    current, task_nodes = render(current_adf)
    found = changes(current, file.read_text(encoding="utf-8"))
    if found:
        for i, on in found:
            node = task_nodes[i] if i < len(task_nodes) else None
            if node is None:
                # changes() only matches lines by position; a line that diffs by exactly its box
                # but isn't a taskItem's line at all is a mismatch between the file and what this
                # description's structure can support, not something to guess at.
                raise Refused(f"line {i + 1} looks like a checklist box, but isn't a taskItem in {key}'s description")
            if node.get("type") != "taskItem":
                # N4b: task_nodes should only ever hold a taskItem (adf_text only attaches a node
                # to the line carrying its own "- [ ]" marker) -- a taskList itself is never
                # ticked, so this would be a mapping bug, not a real board state to write.
                raise Refused(f"line {i + 1} maps to a {node.get('type')} node, not a taskItem; refusing to tick it")
            node.setdefault("attrs", {})["state"] = "DONE" if on else "TODO"
        # current_adf's taskItem nodes were just mutated in place; everything else in the tree —
        # links, mentions, inlineCards, panels, codeBlocks, list numbering, paragraph boundaries,
        # every localId — is the exact object Jira returned. PUT that tree back, not a
        # reconstruction of it.
        jira.put_description(key, current_adf)
        jira.add_comment(key, adf_comment_simple(note(current, found)))
        back_adf = jira.description(key)
        verify_back(key, current, found, back_adf, file)
    file.unlink()
    return f"{key}: {len(found)} box{'es' if len(found) != 1 else ''} changed" if found else f"{key}: no box to change"


def verify_back(key: str, current: str, found: list[tuple[int, bool]], back_adf: dict, file: Path) -> None:
    """N4: the old check was `back_adf != current_adf` -- byte-exact JSON equality of the whole
    tree. Jira is free to normalize ADF on write (reorder a mark, add/drop a default attr, assign
    a localId) without that being a wrong write; a byte-exact compare would refuse a correct tick
    just because the server echoed the tree back slightly differently shaped. What actually
    matters is rendered, not structural: every taskItem's state matches what was intended, and
    every other line's text is exactly what it was before. Compare through render() (the same
    text+state projection changes() already uses), not through the raw trees.
    """
    back_text, _ = render(back_adf)
    cur_lines, back_lines = lines(current), lines(back_text)
    if len(cur_lines) != len(back_lines):
        raise Refused(f"Jira's description now has a different number of lines; {file} is kept")
    wanted = dict(found)
    for i in range(len(cur_lines)):
        if unboxed(back_lines[i]) != unboxed(cur_lines[i]):
            raise Refused(f"Jira changed non-task content at line {i + 1}; {file} is kept")
        intended = wanted.get(i, ticked(cur_lines[i]))
        if ticked(back_lines[i]) != intended:
            raise Refused(f"Jira kept {key}'s old description; {file} is kept")


def adf_comment_simple(text_lines: list[str]) -> dict:
    return {
        "type": "doc", "version": 1,
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "Checklist updated by pr:"}]},
            {"type": "bulletList", "content": [
                {"type": "listItem", "content": [{"type": "paragraph",
                    "content": [{"type": "text", "text": line}]}]} for line in text_lines
            ]},
        ],
    }


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2 or not re.match(r"^[A-Z][A-Z0-9]+-\d+$", args[0]):
        sys.exit("usage: checklist.py <KEY> <file>")
    file = Path(args[1])
    try:
        print(f"pr: {run(args[0], file, Jira())}")
    except Refused as e:
        sys.exit(f"pr: {file} not applied: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
