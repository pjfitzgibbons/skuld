# What two versions of a Python module can't show statement by statement: that statements moved, or
# that two share a line. provenance_units.py then reverts the whole file, since putting some
# statements back without the others can make code that neither version has.
import ast
import collections

DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
IMPORTS = (ast.Import, ast.ImportFrom)
ASSIGNMENTS = (ast.Assign, ast.AnnAssign, ast.AugAssign)


def sharesLines(module):
    """Whether two statements of the module, or of one of its classes, are on one line."""
    bodies = [module.body, *(node.body for node in ast.walk(module) if isinstance(node, ast.ClassDef))]
    return any(later.lineno <= earlier.end_lineno for body in bodies for earlier, later in zip(body, body[1:]))


def isReordered(before, after):
    """Whether the statements both modules have run in another order. Which moves change behaviour
    depends on what the statements do when they run, so every move counts but a free statement's."""
    old, new = orderedKeys(before, after), orderedKeys(after, before)
    shared = set(old) & set(new)
    return [key for key in old if key in shared] != [key for key in new if key in shared]


def orderedKeys(module, other):
    """The statements whose order counts, each by the key that finds it in the other version."""
    taken = namesTaken(module) | namesTaken(other)
    said = {ast.dump(node) for node in other.body}
    seen = collections.Counter()
    keys = []
    for node in module.body:
        if isFree(node, taken):
            continue
        for key in keysOf(node, said):
            keys.append((key, seen[key]))
            seen[key] += 1
    return keys


def namesTaken(module):
    """The names something besides one definition binds. `import *` may bind any, so with one every
    function's name counts."""
    starred = any(alias.name == "*" for node in ast.walk(module) if isinstance(node, ast.ImportFrom)
                  for alias in node.names)
    functions = {node.name for node in module.body if isinstance(node, DEFINITIONS)}
    return boundAgain(module) | (functions if starred else set())


def isFree(node, taken):
    """A statement free to move: a string on its own, or a function that nothing runs where it is
    defined and whose name nothing else binds."""
    if isinstance(node, ast.Expr):
        return isinstance(getattr(node.value, "value", None), str)
    if not isinstance(node, DEFINITIONS) or node.name in taken:
        return False
    return not (node.decorator_list or node.args.defaults or any(node.args.kw_defaults))


def keysOf(node, said):
    """A statement's keys: one for each name an import binds, so one that gains a name as it moves is
    still found, and one for any other statement."""
    if not isinstance(node, IMPORTS):
        return [keyOf(node, said)]
    source = (getattr(node, "module", None), getattr(node, "level", 0))
    return sorted((("import", *source, alias.name, alias.asname) for alias in node.names), key=str)


def keyOf(node, said):
    """What finds a statement that is no import in the other version, even one moved and edited: a
    definition's name; an assignment's kind and targets; an unchanged statement's own text; for any
    other, its kind and what it calls."""
    if isinstance(node, (*DEFINITIONS, ast.ClassDef)):
        return "definition", node.name
    if isinstance(node, ASSIGNMENTS):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        return (type(node).__name__, *map(ast.dump, targets))
    text = ast.dump(node)
    if text in said:
        return (text,)
    return type(node).__name__, calleeOf(node)


def calleeOf(node):
    """What a statement that is one call calls, or nothing."""
    value = getattr(node, "value", None)
    return ast.dump(value.func) if isinstance(value, ast.Call) else ""


def boundAgain(module):
    """The names the module binds in more than one place."""
    places = collections.Counter()
    for node in ast.walk(module):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            places[node.id] += 1
        elif isinstance(node, (*DEFINITIONS, ast.ClassDef)):
            places[node.name] += 1
        elif isinstance(node, IMPORTS):
            places.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
    return {name for name, count in places.items() if count > 1}
