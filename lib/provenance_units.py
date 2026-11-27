# The units of a change that provenance-check proves: a Python function, method, class declaration
# or module-level name, and a file in any other language as a whole. Text in, text out: which units
# differ between two versions of a file, and a version with units put back as a reference has them.
# docs/architecture.md §7.1 states the rules.
import ast
import collections
import copy
import difflib
import json
import os
import re
from dataclasses import dataclass, field

# The unit name of a whole file. In a Python file it is also the module's own statements: those that
# are no definition, named assignment or import.
WHOLE_FILE = ""
# The kinds that need a row of their own; a row's other units revert with its one.
OWN_ROW = ("function", "file")
Changed = collections.namedtuple("Changed", "line kind")
TEST_FOLDERS = {"test", "tests", "e2e", "__tests__"}
# *_sandbox.py is skuld's test support (lib/store_sandbox.py).
TEST_FILE = re.compile(r"^(test_.*|conftest\.py|.*(_test|_sandbox)\.py|.*\.(test|spec)\.[cm]?[jt]sx?)$")
# A *.test.json array is a rule's table, which a test in a language other than Python loads.
JSON_TABLE = ".test.json"
# Files no test can prove: lock files, ignore files, images and text. .vscode/ holds editor settings.
INERT_FOLDERS = {".vscode"}
INERT_FILE = re.compile(r"^(.*\.lock|package-lock\.json|npm-shrinkwrap\.json|pnpm-lock\.yaml|\.terraform\.lock\.hcl|go\.sum"
                        r"|\..*ignore|.*\.(txt|png|jpe?g|gif|svg|webp|ico))$", re.IGNORECASE)
# shortcut: a file that isn't Python is one unit; add a splitter when a language needs a row per function.
# A file with no suffix is a script: a Python one is split before this, so what's left is shell.
SHELL_SUFFIXES = ("", ".sh")
SLASH_COMMENTED = (".js", ".jsx", ".mjs", ".cjs", ".ts")
# A block or JSDoc comment that starts a line and ends one. One that shares its line with code is
# left in: telling it from the same characters inside a string takes a tokenizer.
BLOCK_COMMENT = re.compile(r"^[ \t]*/\*(?:[^*]|\*(?!/))*\*/[ \t]*$", re.MULTILINE)
COMMENTS = {"": "#", ".sh": "#", ".yml": "#", ".yaml": "#", ".toml": "#",
            **dict.fromkeys((".js", ".jsx", ".mjs", ".cjs", ".ts"), "//")}
STUB_BODY = "raise NotImplementedError('reverted by provenance-check')"
# By suffix, the text that can hold lines of a multi-line string, where a line that looks like a
# comment, or is blank, is part of what the file produces.
MULTILINE_TEXT = {"": r"<<", ".sh": r"<<", ".yml": r"[|>][-+0-9]*[ \t]*(#.*)?$", ".yaml": r"[|>][-+0-9]*[ \t]*(#.*)?$",
                  ".toml": '''"""|\'\'\'''', **dict.fromkeys((".js", ".jsx", ".mjs", ".cjs", ".ts"), r"^[^`\n]*(`[^`\n]*`[^`\n]*)*`[^`\n]*$")}


class Unrevertable(Exception):
    pass


@dataclass
class Unit:
    kind: str  # "function", "class", "name", "rest" or "file"
    # [(shape, (first line, last line))] of the unit's own statements. A shape is what runs,
    # normalised: equal when only comments or docstrings differ.
    parts: list = field(default_factory=list)
    lines: set = field(default_factory=set)  # the lines on which a change to this unit shows
    whole: tuple = ()  # a class's full span, its methods included

    @property
    def shape(self):
        return "".join(shape for shape, _span in self.parts)


def needsNoRow(path, text):
    """Tests, rule tables, Markdown prose and inert files need no manifest row."""
    *folders, name = path.split("/")
    inExemptFolder = bool((TEST_FOLDERS | INERT_FOLDERS).intersection(folders))
    isTable = name.endswith(JSON_TABLE) and tableArray(text) is not None
    return inExemptFolder or isTable or name.endswith(".md") or bool(TEST_FILE.match(name) or INERT_FILE.match(name))


def tableArray(text):
    """The array a JSON table's text holds, or None when it holds none."""
    try:
        rows = json.loads(text)
    except ValueError:
        return None
    return rows if isinstance(rows, list) else None


def isPython(path, text):
    shebang = text.split("\n", 1)[0]
    return path.endswith(".py") or (shebang.startswith("#!") and "python" in shebang)


def units(path, text):
    """{name: Unit} of a file; a Python file that doesn't parse is one unit, like any other file."""
    if isPython(path, text):
        try:
            return pythonUnits(text)
        except (SyntaxError, ValueError):  # ValueError: text in another encoding than UTF-8
            pass
    return {WHOLE_FILE: fileUnit(path, text)}


def fileUnit(path, text):
    suffix = os.path.splitext(path)[1]
    quoted = suffix in SHELL_SUFFIXES and quotesSpanLines(text)
    if quoted or (suffix in MULTILINE_TEXT and re.search(MULTILINE_TEXT[suffix], text, re.M)):
        # Any of its characters may be part of what the file produces, trailing space included.
        return Unit("file", [(text, (1, 1))], set(range(1, len(text.split("\n")) + 1)))
    comment = COMMENTS.get(suffix)
    commented = blockCommentLines(text) if suffix in SLASH_COMMENTED else set()
    code = [(number, line.rstrip()) for number, line in enumerate(text.split("\n"), 1)
            if number not in commented and isCode(line, comment)]
    shape = "\n".join(line for _number, line in code)
    return Unit("file", [(shape, (1, 1))], {number for number, _line in code})


def quotesSpanLines(text):
    """Whether a shell file has a quoted string that runs past the end of its line."""
    quote, inComment, escaped, previous = None, False, False, " "
    for char in text:
        if char == "\n":
            if quote:
                return True
            inComment = False
        elif inComment or escaped:
            pass
        elif char == "\\" and quote != "'":
            escaped = True
            continue
        elif quote:
            quote = None if char == quote else quote
        elif char in "'\"":
            quote = char
        elif char == "#" and previous.isspace():
            inComment = True
        escaped, previous = False, char
    return False


def blockCommentLines(text):
    """The lines that block comments fill; none in a file with a continued line, where a line that
    starts like a comment can be the rest of a string."""
    if re.search(r"\\$", text, re.MULTILINE):
        return set()
    found = set()
    for comment in BLOCK_COMMENT.finditer(text):
        first = text.count("\n", 0, comment.start()) + 1
        found.update(range(first, first + comment[0].count("\n") + 1))
    return found


def isCode(line, comment):
    stripped = line.strip()
    isComment = bool(comment) and stripped.startswith(comment) and not stripped.startswith("#!")
    return bool(stripped) and not isComment


def pythonUnits(text):
    found = {}
    for node in withoutDocstring(ast.parse(text).body):
        if isDefinition(node):
            addDefinition(found, node, node.name)
        elif assignedNames(node):
            for name in assignedNames(node):
                addStatement(found, name, "name", node)
        elif not isinstance(node, (ast.Import, ast.ImportFrom)):
            addStatement(found, WHOLE_FILE, "rest", node)
    return found


def addStatement(found, name, kind, node):
    addPart(found, name, kind, ast.dump(node), spanOf(node), linesOf(node))


# A name defined twice (a property and its setter) is one unit with two parts.
def addPart(found, name, kind, shape, span, lines):
    unit = found.setdefault(name, Unit(kind))
    unit.parts.append((shape, span))
    unit.lines |= lines


def addDefinition(found, node, name):
    """A function is one unit. A class is its declaration (all but its methods and classes), and
    each of those is a unit of its own."""
    if not isinstance(node, ast.ClassDef):
        definition = copy.copy(node)
        definition.body = withoutDocstring(node.body)
        addPart(found, name, "function", ast.dump(definition), spanOf(node), linesOf(node))
        return
    addDeclaration(found, node, name)
    for member in filter(isDefinition, node.body):
        addDefinition(found, member, f"{name}.{member.name}")


def addDeclaration(found, node, name):
    """The class's header, then each statement of its body that is no method or class."""
    body = withoutDocstring(node.body)
    statements = [statement for statement in body if not isDefinition(statement)]
    header = copy.copy(node)
    first, last = spanOf(node)
    onOneLine = node.body[0].lineno == node.lineno
    header.body = statements if onOneLine else []
    headerEnd = last if onOneLine else spanOf(node.body[0])[0] - 1
    addPart(found, name, "class", ast.dump(header), (first, headerEnd), set(range(first, headerEnd + 1)))
    for statement in [] if onOneLine else statements:
        addStatement(found, name, "class", statement)
    found[name].whole = (first, last)


def isDefinition(node):
    return isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))


def assignedNames(node):
    """The names an assignment statement binds, when it binds only names."""
    if isinstance(node, ast.Assign):
        targets = [element for target in node.targets for element in getattr(target, "elts", [target])]
    elif isinstance(node, ast.AnnAssign):
        targets = [node.target]
    else:
        return []
    return [target.id for target in targets] if all(isinstance(target, ast.Name) for target in targets) else []


def withoutDocstring(body):
    first = body[0] if body else None
    isDocstring = isinstance(first, ast.Expr) and isinstance(getattr(first.value, "value", None), str)
    return body[1:] if isDocstring else body


def spanOf(node):
    decorators = getattr(node, "decorator_list", [])
    return min([node.lineno, *(decorator.lineno for decorator in decorators)]), node.end_lineno


def linesOf(node):
    first, last = spanOf(node)
    return set(range(first, last + 1))


def changedUnits(path, old, new):
    """{name: Changed} of new's units that old lacks or has differently, with the first changed line
    of each. old is None for a new file."""
    before = units(path, old) if old is not None else {}
    touched = changedLines(old or "", new)
    return {
        name: Changed(min(unit.lines & touched, default=min(unit.lines, default=1)), unit.kind)
        for name, unit in units(path, new).items()
        if shapeOf(before, name) != comparable(unit)
    }


# A missing file unit has the shape of an empty file, so a new empty file is no change.
def shapeOf(found, name):
    unit = found.get(name)
    if unit is None:
        return "" if name == WHOLE_FILE else None
    return comparable(unit)


def comparable(unit):
    return unit.shape if unit.kind == "file" else (unit.kind, unit.shape)


def changedLines(old, new):
    """The line numbers of new that differ from old; a deletion shows on the line after it."""
    matcher = difflib.SequenceMatcher(None, old.split("\n"), new.split("\n"), autojunk=False)
    changed = set()
    for tag, _i1, _i2, first, end in matcher.get_opcodes():
        if tag != "equal":
            changed.update(range(first + 1, max(end, first + 1) + 1))
    return changed


def reverted(path, current, reference, targets):
    """current with each target unit as reference has it, and no other unit touched. reference is
    None where the file didn't exist. A function the reference lacks becomes one that raises, a name
    or class becomes None, module statements go, and a file that isn't split is emptied: the tests
    that name the code still load."""
    if not isPython(path, current) or (targets == [WHOLE_FILE] and isOneUnit(path, current)):
        if targets != [WHOLE_FILE]:
            raise Unrevertable(f"{path} isn't Python, so a row names the whole file")
        return reference or ""
    here = parsedUnits(path, current, "in the worktree")
    there = parsedUnits(path, reference, "in the reference") if reference is not None else {}
    lines = current.split("\n")
    edits = {}  # {span: [the texts that replace it, one for each unit that differs]}
    # In the reference's order, so names that go back into one place keep the order they ran in.
    for name in sorted(targets, key=lambda name: there[name].parts[0][1] if name in there else (0, 0)):
        if name not in here:
            raise Unrevertable(f"{path} has no {name or 'module statements'}")
        refuseSharedStatement(path, name, targets, (here, there))
        for span, text in editsFor(name, here[name], there.get(name), lines, reference).items():
            texts = edits.setdefault(span, [])
            if text not in texts:
                texts.append(text)
    joined = {span: [line for text in texts for line in text] for span, texts in edits.items()}
    return parsing(path, "\n".join(applied(joined, lines)))


def refuseSharedStatement(path, name, targets, versions):
    """Reverting one name of `A, B = ...` rewrites the others, so a test of another would prove it:
    a row names all the names a statement binds, in either version, that the file still has."""
    here = versions[0]
    for found in versions:
        unnamed = (sharers(found, name) & set(here)) - set(targets)
        if unnamed:
            raise Unrevertable(f"{path}: {name} shares a statement with {', '.join(sorted(unnamed))}; "
                               "one row names them all")


def sharers(found, name):
    """The names bound by the statements that bind name."""
    if name not in found or found[name].kind != "name":
        return set()
    spans = {span for _shape, span in found[name].parts}
    return {other for other, unit in found.items()
            if unit.kind == "name" and spans.intersection(span for _shape, span in unit.parts)}


def isOneUnit(path, text):
    """Whether the file can't be split, as a Python file that doesn't parse can't."""
    whole = units(path, text).get(WHOLE_FILE)
    return whole is not None and whole.kind == "file"


def editsFor(name, unit, old, lines, reference):
    """{(first, last): new lines} that put one unit back; (n, n - 1) inserts before line n."""
    if old is not None:
        return swapped(unit.parts, old.parts, reference.split("\n"))
    spans = [span for _shape, span in unit.parts]
    if unit.kind == "function":
        return {span: stubFunction(lines, span, shortName(name)) for span in spans}
    if unit.kind == "rest":
        return {span: [] for span in spans}
    first, *others = [unit.whole] if unit.kind == "class" else spans
    stub = f"{indentOf(lines[first[0] - 1])}{shortName(name)} = None"
    return {first: [stub], **{span: [] for span in others}}


def swapped(parts, oldParts, oldLines):
    """The edits that turn a unit's statements into the reference's, matched as a diff matches lines."""
    matcher = difflib.SequenceMatcher(None, [shape for shape, _ in parts], [shape for shape, _ in oldParts], autojunk=False)
    edits = {}
    for tag, first, end, oldFirst, oldEnd in matcher.get_opcodes():
        if tag == "equal":
            continue
        text = [line for _shape, (top, bottom) in oldParts[oldFirst:oldEnd] for line in oldLines[top - 1:bottom]]
        if first == end:
            at = parts[first][1][0] if first < len(parts) else parts[-1][1][1] + 1
            edits[(at, at - 1)] = text
            continue
        edits[parts[first][1]] = text
        edits.update({span: [] for _shape, span in parts[first + 1:end]})
    return edits


def applied(edits, lines):
    """lines with the edits made, bottom up; one inside another's span goes with that one."""
    outer = []
    for span in sorted(edits, key=lambda span: (span[0], -span[1])):
        if not (outer and span[1] <= outer[-1][1]):
            outer.append(span)
    for first, last in reversed(outer):
        lines[first - 1:last] = edits[(first, last)]
    return lines


def shortName(name):
    return name.rsplit(".", 1)[-1]


def stubFunction(lines, span, name):
    first, last = span
    definition = next(index for index in range(first - 1, last) if lines[index].lstrip().startswith(("def ", "async def ")))
    indent = indentOf(lines[definition])
    prefix = "async " if lines[definition].lstrip().startswith("async ") else ""
    decorators = lines[first - 1:definition]
    return [*decorators, f"{indent}{prefix}def {name}(*args, **kwargs):", f"{indent}    {STUB_BODY}"]


def indentOf(line):
    return line[: len(line) - len(line.lstrip())]


def parsedUnits(path, text, where):
    try:
        return pythonUnits(text)
    except (SyntaxError, ValueError) as error:
        raise Unrevertable(f"{path} {where} doesn't parse: line {getattr(error, 'lineno', 1)}") from error


def parsing(path, text):
    try:
        ast.parse(text)
    except SyntaxError as error:
        raise Unrevertable(f"{path} doesn't parse once reverted: line {error.lineno}") from error
    return text
