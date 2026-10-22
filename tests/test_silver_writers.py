"""Only the files in the silver write register (ibkr_etl.silver_writers) write the silver layer.

A static scan, no database. Every Python file under src/ and scripts/ is parsed and its string
literals are collected: plain, implicitly concatenated and multi-line strings, f-strings, +
chains, and str.format, psycopg sql.SQL(...).format and % templates. Docstrings and comments are
not code, so they are skipped. In each string the scan finds the SQL that needs write rights on
a table: INSERT INTO, UPDATE ... SET, DELETE FROM, TRUNCATE, COPY ... FROM, MERGE INTO, LOCK
TABLE, and SELECT ... FOR UPDATE / NO KEY UPDATE / SHARE / KEY SHARE (whose FROM and JOIN
targets count). A silver relation counts when it is named bare (found through search_path), as
public.<name> (the compatibility view of migration 070) or as silver.<name>, in any case.

A table name filled in at run time is followed back to string constants where it can be: a
constant, a module constant (also one imported from another file), a loop over a constant
tuple, list or dict, sql.Identifier, a quoting helper, what a small function returns, or a
function parameter whose callers pass one of those. A part that cannot be followed is a
wildcard: pg_temp.<anything> or <anything>_history can never be a silver relation, so it is
settled; a target that could still be one is reported, and its file must be listed in
silver_writers.UNRESOLVED_WRITES saying what it writes, so nothing is skipped silently.

Run: PYTHONPATH=src python -m unittest tests.test_silver_writers
"""
from __future__ import annotations

import ast
import itertools
import math
import re
import tempfile
import unittest
from functools import lru_cache
from pathlib import Path

from ibkr_etl import silver_writers as register

ROOT = Path(__file__).resolve().parents[1]
SCANNED = ("src", "scripts")
MIGRATION = ROOT / "migrations" / "postgres" / "070_medallion_silver_layer.sql"
SILVER_SCHEMAS = (None, "public", "silver")
MASK = "…"  # a job name that carries an account number is written with it cut to "u…"

# ------------------------------------------------------------------ SQL patterns

# A name part: an identifier, possibly quoted, in which an f-string or + hole (\x00<n>\x00) may
# stand for some characters; or a whole str.format / sql.SQL field ({...}) or % operand (%s).
WORD = r"(?:[A-Za-z_][\w$]*|\x00\d+\x00)(?:[\w$]|\x00\d+\x00)*"
FIELD_HOLE = r"\{[^{}]*\}|%s|%\(\w+\)s"
PART = rf'"?{WORD}"?|{FIELD_HOLE}'
TARGET = rf"(?:(?P<schema>{PART})\s*\.\s*)?(?P<name>{PART})"
WRITES = tuple((kind, re.compile(pattern, re.I)) for kind, pattern in (
    ("INSERT", rf"\bINSERT\s+INTO\s+{TARGET}"),
    ("UPDATE", rf"\bUPDATE\s+(?:ONLY\s+)?{TARGET}(?:\s+(?:AS\s+)?(?!SET\b)[A-Za-z_]\w*)?\s+SET\b"),
    ("DELETE", rf"\bDELETE\s+FROM\s+(?:ONLY\s+)?{TARGET}"),
    ("TRUNCATE", rf"\bTRUNCATE\s+(?:TABLE\s+)?(?:ONLY\s+)?{TARGET}"),
    ("COPY", rf"\bCOPY\s+{TARGET}\s*(?:\([^()]*\)\s*)?FROM\b"),
    ("MERGE", rf"\bMERGE\s+INTO\s+(?:ONLY\s+)?{TARGET}"),
    ("LOCK", rf"\bLOCK\s+(?:TABLE\s+)?(?:ONLY\s+)?{TARGET}"),
))
NEXT_TARGET = re.compile(rf"\s*,\s*(?:ONLY\s+)?{TARGET}", re.I)  # TRUNCATE a, b / LOCK a, b
ROW_LOCK = re.compile(r"\bFOR\s+(?:NO\s+KEY\s+UPDATE|UPDATE|KEY\s+SHARE|SHARE)\b", re.I)
SELECT = re.compile(r"\bSELECT\b|^\s*FROM\b", re.I)
SELECT_TARGET = re.compile(rf"\b(?:FROM|JOIN)\s+(?:ONLY\s+)?{TARGET}", re.I)
# A write keyword at the very end of a string: the table is appended by code the scan cannot see.
DANGLING = re.compile(r"\b(?:INSERT\s+INTO|DELETE\s+FROM|TRUNCATE\s+TABLE|MERGE\s+INTO|LOCK\s+TABLE)\s*$", re.I)
NOT_A_NAME = {"set", "only", "table", "select", "values", "with"}
PREFILTER = re.compile(r"(?i)\b(?:insert|delete|truncate|merge|lock|copy)\b|\bupdate\s|\bfor\s+(?:no\s+key\s+)?update"
                       r"|\bfor\s+(?:key\s+)?share")
FIELD = re.compile(r"\{\{|\}\}|\{([^{}]*)\}")
PERCENT = re.compile(r"%%|%\((\w+)\)s|%[sd]")

# Values are sets of strings; WILD stands for a part the scan cannot follow.
WILD = "\x01"
UNKNOWN = frozenset([WILD])
NOTHING: frozenset = frozenset()
MAX_DEPTH = 14
MAX_VALUES = 256
SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
LOOPS = (ast.For, ast.AsyncFor, ast.While)


def migration_relations() -> tuple[str, ...]:
    """The `moved` array of migration 070: the 29 relations in the silver schema."""
    text = MIGRATION.read_text(encoding="utf-8")
    block = re.search(r"\bmoved\s+CONSTANT\s+text\[\]\s*:=\s*ARRAY\[(.*?)\];", text, re.S)
    return tuple(re.findall(r"'([a-z_][a-z0-9_]*)'", block.group(1)))


SILVER = frozenset(migration_relations())


def _cap(values) -> frozenset:
    values = frozenset(re.sub(WILD + "+", WILD, value) for value in values)
    return UNKNOWN if len(values) > MAX_VALUES else values


def _union(sets) -> frozenset:
    return _cap(frozenset().union(*sets))


def _product(sets, separator: str = "") -> frozenset:
    sets = [sorted(found) for found in sets]
    if any(not found for found in sets):
        return NOTHING
    if math.prod(len(found) for found in sets) > MAX_VALUES:
        return UNKNOWN
    return _cap(separator.join(combo) for combo in itertools.product(*sets))


def _position(node: ast.AST) -> tuple[int, int]:
    return (getattr(node, "lineno", 0), getattr(node, "col_offset", 0))


def _slot(target: ast.AST, name: str):
    """Where name sits in an assignment or loop target: None (absent), "all", an index of a flat
    tuple, or "deep" when nested further."""
    if isinstance(target, ast.Name):
        return "all" if target.id == name else None
    if isinstance(target, (ast.Tuple, ast.List)):
        for index, element in enumerate(target.elts):
            if isinstance(element, ast.Name) and element.id == name:
                return index
            if isinstance(element, ast.Starred) and _slot(element.value, name) is not None:
                return "deep"
            if isinstance(element, (ast.Tuple, ast.List)) and _slot(element, name) is not None:
                return "deep"
    return None


def _own_nodes(scope: ast.AST):
    """The nodes of a function, class or module body, without nested functions and classes."""
    todo = list(ast.iter_child_nodes(scope))
    while todo:
        node = todo.pop()
        yield node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            todo.extend(ast.iter_child_nodes(node))


def _call_name(call: ast.Call) -> str:
    func = call.func
    return func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""


# ------------------------------------------------------------------ modules


class Module:
    """One parsed file, with parent links."""

    def __init__(self, rel: str, source: str):
        self.rel = rel
        self.tree = ast.parse(source, rel)
        self.parents: dict[int, ast.AST] = {}
        for node in ast.walk(self.tree):
            for child in ast.iter_child_nodes(node):
                self.parents[id(child)] = node

    def parent(self, node: ast.AST) -> ast.AST | None:
        return self.parents.get(id(node))

    def ancestors(self, node: ast.AST):
        current = self.parent(node)
        while current is not None:
            yield current
            current = self.parent(current)

    def inside(self, node: ast.AST, container: ast.AST) -> bool:
        return node is container or any(a is container for a in self.ancestors(node))

    def scope_of(self, node: ast.AST) -> ast.AST:
        """The function (or the module) whose names the node sees first."""
        for ancestor in self.ancestors(node):
            if isinstance(ancestor, SCOPES):
                return ancestor
        return self.tree

    def enclosing_class(self, node: ast.AST) -> ast.ClassDef | None:
        for ancestor in self.ancestors(node):
            if isinstance(ancestor, ast.ClassDef):
                return ancestor
        return None

    def is_method(self, function: ast.AST) -> bool:
        if not isinstance(self.parent(function), ast.ClassDef):
            return False
        decorators = {getattr(d, "id", getattr(d, "attr", "")) for d in function.decorator_list}
        return "staticmethod" not in decorators

    def reaches(self, binder: ast.AST, use: ast.AST | None) -> bool:
        """Whether a binding can still be the value at use (a rough reaching-definitions test)."""
        if use is None:
            return True
        if isinstance(binder, ast.comprehension):  # a comprehension's names stay inside it
            return self.inside(use, self.parent(binder))
        if _position(binder) <= _position(use):
            return True
        return any(isinstance(loop, LOOPS) and self.inside(binder, loop) for loop in self.ancestors(use))

    def dominates(self, binder: ast.AST, use: ast.AST) -> bool:
        """Whether every path to use passes the binding first."""
        if isinstance(binder, ast.comprehension):
            return self.inside(use, self.parent(binder))
        if isinstance(binder, (ast.For, ast.AsyncFor)):
            return any(self.inside(use, statement) for statement in binder.body)
        if not isinstance(binder, (ast.Assign, ast.AnnAssign)):
            return False
        holder = self.parent(binder)
        for field in ("body", "orelse", "finalbody"):
            block = getattr(holder, field, None)
            if isinstance(block, list) and any(statement is binder for statement in block):
                start = next(i for i, statement in enumerate(block) if statement is binder)
                return any(self.inside(use, statement) for statement in block[start + 1:])
        return False


class Repository:
    def __init__(self, root: Path):
        self.root = root
        self._modules: dict[str, Module | None] = {}
        self._texts: dict[str, str] = {}
        self._files: list[str] | None = None
        self._callers: dict[tuple[str, int], list] = {}
        self._active: set = set()

    # files -----------------------------------------------------------------------

    def files(self) -> list[str]:
        if self._files is None:
            found = []
            for folder in SCANNED:
                if (self.root / folder).is_dir():
                    found.extend(p.relative_to(self.root).as_posix() for p in (self.root / folder).rglob("*.py"))
            self._files = sorted(found)
        return self._files

    def text(self, rel: str) -> str:
        if rel not in self._texts:
            self._texts[rel] = (self.root / rel).read_text(encoding="utf-8")
        return self._texts[rel]

    def module(self, rel: str) -> Module | None:
        if rel not in self._modules:
            try:
                self._modules[rel] = Module(rel, self.text(rel))
            except (OSError, SyntaxError, ValueError):
                self._modules[rel] = None
        return self._modules[rel]

    def import_target(self, module: Module, dotted: str, level: int) -> str | None:
        """The repository file an import names, or None outside src/ and scripts/."""
        if level:
            base = Path(module.rel).parent
            for _ in range(level - 1):
                base = base.parent
            bases = [base]
        else:
            bases = [Path("src"), Path("scripts")]
        for base in bases:
            path = base.joinpath(*dotted.split(".")) if dotted else base
            for candidate in (path.with_suffix(".py"), path / "__init__.py"):
                if (self.root / candidate).is_file():
                    return candidate.as_posix()
        return None

    # names -------------------------------------------------------------------------

    def bindings(self, module: Module, name: str, scope: ast.AST, use: ast.AST | None) -> list[tuple]:
        """What name can be bound to at use, searched from scope outwards. Each entry starts with
        its kind: value (module, expr, scope), iter (module, iterable, scope, index), param
        (module, function, name), function (module, def), module (file), import (file, name),
        unknown."""
        while True:
            found = []  # (binder, entry)
            for node in _own_nodes(scope):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        slot = _slot(target, name)
                        if slot == "all":
                            found.append((node, ("value", module, node.value, scope)))
                        elif isinstance(slot, int) and isinstance(node.value, (ast.Tuple, ast.List)) \
                                and slot < len(node.value.elts):
                            found.append((node, ("value", module, node.value.elts[slot], scope)))
                        elif slot is not None:
                            found.append((node, ("unknown",)))
                elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)) and isinstance(node.target, ast.Name) \
                        and node.target.id == name and node.value is not None:
                    found.append((node, ("value", module, node.value, scope)))
                elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
                    found.append((node, ("unknown",)))
                elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
                    slot = _slot(node.target, name)
                    if slot is not None:
                        index = None if slot == "all" else slot
                        found.append((node, ("unknown",) if slot == "deep" else ("iter", module, node.iter, scope, index)))
                elif isinstance(node, (ast.With, ast.AsyncWith)):
                    for item in node.items:
                        if item.optional_vars is not None and _slot(item.optional_vars, name) is not None:
                            found.append((node, ("unknown",)))
                elif isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        if (alias.asname or alias.name) == name:
                            source = self.import_target(module, node.module or "", node.level)
                            submodule = self.import_target(
                                module, ".".join(filter(None, [node.module or "", alias.name])), node.level)
                            entry = ("module", submodule) if submodule else \
                                ("import", source, alias.name) if source else ("unknown",)
                            found.append((node, entry))
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if (alias.asname or alias.name.split(".")[0]) == name:
                            target = self.import_target(module, alias.name if alias.asname else name, 0)
                            found.append((node, ("module", target) if target else ("unknown",)))
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                    found.append((node, ("function", module, node)))
                elif isinstance(node, ast.ClassDef) and node.name == name:
                    found.append((node, ("unknown",)))
                elif isinstance(node, ast.ExceptHandler) and node.name == name:
                    found.append((node, ("unknown",)))
            if isinstance(scope, SCOPES):
                arguments = scope.args
                if any(a.arg == name for a in arguments.posonlyargs + arguments.args + arguments.kwonlyargs):
                    found.append((scope, ("param", module, scope, name)))
                elif any(a is not None and a.arg == name for a in (arguments.vararg, arguments.kwarg)):
                    found.append((scope, ("unknown",)))
            found = [(binder, entry) for binder, entry in found if module.reaches(binder, use)]
            if use is not None and found:
                dominating = [binder for binder, _ in found if binder is not scope and module.dominates(binder, use)]
                if dominating:
                    last = _position(max(dominating, key=_position))
                    found = [(b, e) for b, e in found if b is not scope and _position(b) >= last]
            if found:
                return [entry for _, entry in found]
            if scope is module.tree:
                return [("unknown",)]
            scope, use = module.scope_of(scope), None

    def _values_of_name(self, module: Module, name: str, scope: ast.AST, use, depth: int, how) -> frozenset:
        results = []
        for entry in self.bindings(module, name, scope, use):
            kind = entry[0]
            if kind == "value":
                results.append(how(entry[1], entry[2], entry[3], depth + 1))
            elif kind == "iter" and how == self.strings:
                results.append(self.elements(entry[1], entry[2], entry[3], depth + 1, entry[4]))
            elif kind == "param":
                results.append(self.argument(entry[1], entry[2], entry[3], depth, how))
            elif kind == "import":
                other = self.module(entry[1])
                results.append(self._values_of_name(other, entry[2], other.tree, None, depth + 1, how)
                               if other else UNKNOWN)
            else:
                results.append(UNKNOWN)
        return _union(results)

    def _function(self, module: Module, func: ast.AST, scope: ast.AST, depth: int = 0):
        """The (module, def) a call's func names, when there is exactly one."""
        if depth > 4:
            return None
        if isinstance(func, ast.Name):
            entries = self.bindings(module, func.id, scope, func if module.parent(func) is not None else None)
            if len(entries) == 1 and entries[0][0] == "function":
                return entries[0][1], entries[0][2]
            if len(entries) == 1 and entries[0][0] == "import":
                other = self.module(entries[0][1])
                if other is not None:
                    return self._function(other, ast.Name(id=entries[0][2]), other.tree, depth + 1)
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            if func.value.id in ("self", "cls"):
                cls = module.enclosing_class(func)
                methods = [n for n in (cls.body if cls else []) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                           and n.name == func.attr]
                return (module, methods[0]) if len(methods) == 1 else None
            entries = self.bindings(module, func.value.id, scope, func.value)
            if len(entries) == 1 and entries[0][0] == "module":
                other = self.module(entries[0][1])
                if other is not None:
                    return self._function(other, ast.Name(id=func.attr), other.tree, depth + 1)
        return None

    def callers(self, module: Module, function: ast.AST) -> list[tuple[Module, ast.Call, bool]]:
        """Calls of a function anywhere under src/ and scripts/: (module, call, via_attribute)."""
        key = (module.rel, id(function))
        if key not in self._callers:
            self._callers[key] = self._find_callers(module, function)
        return self._callers[key]

    def _find_callers(self, module: Module, function: ast.AST) -> list[tuple[Module, ast.Call, bool]]:
        name = function.name
        method = module.is_method(function)
        word = re.compile(rf"\b{re.escape(name)}\b")
        stem = re.compile(rf"\b{re.escape(Path(module.rel).stem)}\b")
        found = []
        for rel in self.files():
            text = self.text(rel)
            if rel != module.rel and not (word.search(text) and stem.search(text)):
                continue
            other = self.module(rel)
            if other is None:
                continue
            for node in ast.walk(other.tree):
                if not (isinstance(node, ast.Call) and _call_name(node) == name):
                    continue
                if isinstance(node.func, ast.Attribute) and method:
                    found.append((other, node, True))  # any obj.method(...): the type is not known
                elif self._function(other, node.func, other.scope_of(node)) == (module, function):
                    found.append((other, node, False))
        return found

    def argument(self, module: Module, function: ast.AST, name: str, depth: int, how) -> frozenset:
        """Values a parameter takes, from every call of its function (how = strings or elements)."""
        arguments = function.args
        positional = [a.arg for a in arguments.posonlyargs + arguments.args]
        defaults = dict(zip(positional[len(positional) - len(arguments.defaults):], arguments.defaults))
        defaults.update({a.arg: d for a, d in zip(arguments.kwonlyargs, arguments.kw_defaults) if d is not None})
        if isinstance(function, ast.Lambda):  # called where the scan cannot see: its default, if any
            return how(module, defaults[name], module.scope_of(function), depth + 1) if name in defaults else UNKNOWN
        calls = self.callers(module, function)
        if not calls:
            return UNKNOWN
        results = []
        for caller, call, via_attribute in calls:
            index = positional.index(name) if name in positional else None
            if index is not None and via_attribute and module.is_method(function):
                index -= 1
            if index is not None and any(isinstance(a, ast.Starred) for a in call.args[: index + 1]):
                results.append(UNKNOWN)
                continue
            expr = call.args[index] if index is not None and 0 <= index < len(call.args) else None
            for keyword in call.keywords:
                if keyword.arg == name:
                    expr = keyword.value
                elif keyword.arg is None and expr is None:
                    results.append(UNKNOWN)
            if expr is not None:
                results.append(how(caller, expr, caller.scope_of(call), depth + 1))
            elif name in defaults:
                results.append(how(module, defaults[name], module.scope_of(function), depth + 1))
        return _union(results)

    def returns(self, module: Module, function: ast.AST, depth: int) -> frozenset:
        values = [self.strings(module, node.value, function, depth + 1) for node in _own_nodes(function)
                  if isinstance(node, ast.Return) and node.value is not None]
        return _union(values) if values else UNKNOWN

    # values ------------------------------------------------------------------------

    def _guarded(self, key, compute) -> frozenset:
        if key in self._active:
            return NOTHING  # a cycle adds nothing new
        self._active.add(key)
        try:
            return compute()
        finally:
            self._active.discard(key)

    def strings(self, module: Module, expr: ast.AST, scope: ast.AST, depth: int = 0) -> frozenset:
        """The strings expr can evaluate to (WILD for a part the scan cannot follow)."""
        if depth > MAX_DEPTH:
            return UNKNOWN
        return self._guarded(("s", module.rel, id(expr)), lambda: self._strings(module, expr, scope, depth))

    def _strings(self, module, expr, scope, depth) -> frozenset:
        if isinstance(expr, ast.Constant):
            return frozenset([expr.value]) if isinstance(expr.value, str) else NOTHING
        if isinstance(expr, ast.JoinedStr):
            return _product([frozenset([str(v.value)]) if isinstance(v, ast.Constant)
                             else self.strings(module, v.value, scope, depth + 1) for v in expr.values])
        if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
            return _product([self.strings(module, expr.left, scope, depth + 1),
                             self.strings(module, expr.right, scope, depth + 1)])
        if isinstance(expr, ast.IfExp):
            return _union([self.strings(module, expr.body, scope, depth + 1),
                           self.strings(module, expr.orelse, scope, depth + 1)])
        if isinstance(expr, ast.BoolOp):
            return _union([self.strings(module, value, scope, depth + 1) for value in expr.values])
        if isinstance(expr, ast.Name):
            return self._values_of_name(module, expr.id, scope, expr, depth, self.strings)
        if isinstance(expr, ast.Attribute):
            return self._attribute(module, expr, scope, depth, self.strings)
        if isinstance(expr, ast.Subscript):
            return self._subscript(module, expr, scope, depth)
        if isinstance(expr, ast.Call):
            name = _call_name(expr)
            func = expr.func
            if name == "Identifier" and expr.args and not expr.keywords:  # psycopg sql.Identifier(schema, name)
                return _product([self.strings(module, a, scope, depth + 1) for a in expr.args], ".")
            if isinstance(func, ast.Attribute) and name == "join" and len(expr.args) == 1:
                return self.elements(module, expr.args[0], scope, depth + 1)  # each listed name counts
            if isinstance(func, ast.Attribute) and name in ("lower", "upper", "strip") and not expr.args:
                return self.strings(module, func.value, scope, depth + 1)
            if len(expr.args) == 1 and not expr.keywords and (
                    name in ("SQL", "str") or "quote" in name.lower() or "ident" in name.lower()):
                return self.strings(module, expr.args[0], scope, depth + 1)
            target = self._function(module, func, scope)
            if target is not None:
                return self.returns(target[0], target[1], depth)
            return UNKNOWN
        return UNKNOWN

    def _attribute(self, module, expr, scope, depth, how) -> frozenset:
        owner = expr.value
        if isinstance(owner, ast.Name) and owner.id in ("self", "cls"):
            cls = module.enclosing_class(expr)
            assigned = cls is not None and any(_slot(t, expr.attr) for node in cls.body
                                               if isinstance(node, ast.Assign) for t in node.targets)
            return self._values_of_name(module, expr.attr, cls, None, depth + 1, how) if assigned else UNKNOWN
        if isinstance(owner, ast.Name):
            entries = self.bindings(module, owner.id, scope, owner)
            if len(entries) == 1 and entries[0][0] == "module" and self.module(entries[0][1]) is not None:
                other = self.module(entries[0][1])
                return self._values_of_name(other, expr.attr, other.tree, None, depth + 1, how)
        return UNKNOWN

    def _literal(self, module, expr, scope, depth):
        """The literal (module, node, scope) a name stands for, when there is exactly one."""
        if isinstance(expr, (ast.Dict, ast.DictComp, ast.Tuple, ast.List, ast.Set)):
            return (module, expr, scope)
        if isinstance(expr, ast.Name) and depth <= MAX_DEPTH:
            entries = self.bindings(module, expr.id, scope, expr if module.parent(expr) is not None else None)
            if len(entries) == 1 and entries[0][0] == "value":
                return self._literal(entries[0][1], entries[0][2], entries[0][3], depth + 1)
            if len(entries) == 1 and entries[0][0] == "import" and self.module(entries[0][1]) is not None:
                other = self.module(entries[0][1])
                return self._literal(other, ast.Name(id=entries[0][2]), other.tree, depth + 1)
        return None

    def _subscript(self, module, expr, scope, depth) -> frozenset:
        literal = self._literal(module, expr.value, scope, depth + 1)
        if literal is None:
            return UNKNOWN
        owner, node, owner_scope = literal
        if isinstance(node, ast.Dict) and all(k is not None for k in node.keys):
            keys = self.strings(module, expr.slice, scope, depth + 1)
            chosen = [v for k, v in zip(node.keys, node.values)
                      if WILD in "".join(keys) or not keys or (isinstance(k, ast.Constant) and k.value in keys)]
            return _union([self.strings(owner, v, owner_scope, depth + 1) for v in chosen])
        if isinstance(node, (ast.Dict, ast.DictComp)):
            return self._mapping(owner, node, owner_scope, depth, keys=False, index=None)
        return UNKNOWN

    def elements(self, module: Module, expr: ast.AST, scope: ast.AST, depth: int = 0,
                 index: int | None = None) -> frozenset:
        """The strings that iterating expr yields (element index of each item when given)."""
        if depth > MAX_DEPTH:
            return UNKNOWN
        return self._guarded(("e", module.rel, id(expr), index),
                             lambda: self._elements(module, expr, scope, depth, index))

    def _item(self, module, expr, scope, depth, index) -> frozenset:
        if index is None:
            return self.strings(module, expr, scope, depth + 1)
        literal = self._literal(module, expr, scope, depth + 1)
        if literal is not None and isinstance(literal[1], (ast.Tuple, ast.List)):
            owner, node, owner_scope = literal
            if index < len(node.elts) and not any(isinstance(e, ast.Starred) for e in node.elts[: index + 1]):
                return self.strings(owner, node.elts[index], owner_scope, depth + 1)
        return UNKNOWN

    def _elements(self, module, expr, scope, depth, index) -> frozenset:
        if isinstance(expr, (ast.Tuple, ast.List, ast.Set)):
            return _union([self.elements(module, e.value, scope, depth + 1, index) if isinstance(e, ast.Starred)
                           else self._item(module, e, scope, depth, index) for e in expr.elts])
        if isinstance(expr, (ast.Dict, ast.DictComp)):
            return self._mapping(module, expr, scope, depth, keys=True, index=None) if index is None else UNKNOWN
        if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
            return _union([self.elements(module, expr.left, scope, depth + 1, index),
                           self.elements(module, expr.right, scope, depth + 1, index)])
        if isinstance(expr, ast.IfExp):
            return _union([self.elements(module, expr.body, scope, depth + 1, index),
                           self.elements(module, expr.orelse, scope, depth + 1, index)])
        if isinstance(expr, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
            return self._item(module, expr.elt, scope, depth, index)
        if isinstance(expr, ast.Call):
            func = expr.func
            if isinstance(func, ast.Attribute) and not expr.args:
                if func.attr == "items" and index in (0, 1):
                    return self._mapping(module, func.value, scope, depth, keys=index == 0, index=None)
                if func.attr == "keys" and index is None:
                    return self._mapping(module, func.value, scope, depth, keys=True, index=None)
                if func.attr == "values":
                    return self._mapping(module, func.value, scope, depth, keys=False, index=index)
                return UNKNOWN
            if isinstance(func, ast.Name) and expr.args:
                if func.id in ("sorted", "list", "tuple", "set", "frozenset", "reversed") and len(expr.args) == 1:
                    return self.elements(module, expr.args[0], scope, depth + 1, index)
                if func.id == "enumerate":
                    return NOTHING if index == 0 else \
                        self.elements(module, expr.args[0], scope, depth + 1) if index == 1 else UNKNOWN
                if func.id == "zip" and index is not None and index < len(expr.args):
                    return self.elements(module, expr.args[index], scope, depth + 1)
            target = self._function(module, func, scope)
            if target is not None:  # what a small function returns, e.g. a list comprehension over a constant
                owner, function = target
                found = [self.elements(owner, node.value, function, depth + 1, index)
                         for node in _own_nodes(function) if isinstance(node, ast.Return) and node.value is not None]
                return _union(found) if found else UNKNOWN
            return UNKNOWN
        if isinstance(expr, ast.Name):
            return self._values_of_name(module, expr.id, scope, expr, depth,
                                        lambda m, e, s, d: self.elements(m, e, s, d, index))
        if isinstance(expr, ast.Attribute):
            return self._attribute(module, expr, scope, depth, lambda m, e, s, d: self.elements(m, e, s, d, index))
        return UNKNOWN

    def _mapping(self, module, expr, scope, depth, *, keys: bool, index) -> frozenset:
        """The keys, or the values, of a dict (a literal, a comprehension, ** spreads included)."""
        if depth > MAX_DEPTH:
            return UNKNOWN
        literal = self._literal(module, expr, scope, depth + 1)
        if literal is None:
            return UNKNOWN
        owner, node, owner_scope = literal
        if isinstance(node, ast.DictComp):
            part = node.key if keys else node.value
            return self.strings(owner, part, owner_scope, depth + 1) if keys or index is None else \
                self._item(owner, part, owner_scope, depth, index)
        if not isinstance(node, ast.Dict):
            return UNKNOWN
        results = []
        for key, value in zip(node.keys, node.values):
            if key is None:  # **spread
                results.append(self._mapping(owner, value, owner_scope, depth + 1, keys=keys, index=index))
            elif keys:
                results.append(self.strings(owner, key, owner_scope, depth + 1))
            else:
                results.append(self._item(owner, value, owner_scope, depth, index))
        return _union(results)


# ------------------------------------------------------------------ strings in a file


class Template:
    """A string as the code builds it. A hole (\\x00<n>\\x00) is an expression filled in at run time."""

    def __init__(self, node: ast.AST, text: str, holes: list[ast.AST]):
        self.node = node
        self.text = text
        self.holes = holes


def _chain(node: ast.AST) -> list[ast.AST]:
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _chain(node.left) + _chain(node.right)
    return [node]


def _is_text(node: ast.AST) -> bool:
    return isinstance(node, ast.JoinedStr) or (isinstance(node, ast.Constant) and isinstance(node.value, str))


def templates(module: Module) -> list[Template]:
    skipped: set[int] = set()
    for node in ast.walk(module.tree):
        if isinstance(node, ast.Expr) and _is_text(node.value):  # a docstring or a bare string statement
            skipped.update(id(sub) for sub in ast.walk(node.value))
    found = []
    for node in ast.walk(module.tree):  # breadth first: a + chain is met before its parts
        if id(node) in skipped:
            continue
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            parts = _chain(node)
            if not any(_is_text(part) for part in parts):
                continue
            text, holes = [], []
            for part in parts:
                _add(part, text, holes, skipped)
            skipped.update(id(sub) for sub in ast.walk(node) if isinstance(sub, ast.BinOp))
            found.append(Template(node, "".join(text), holes))
        elif _is_text(node):
            text, holes = [], []
            _add(node, text, holes, skipped)
            found.append(Template(node, "".join(text), holes))
    return found


def _add(part: ast.AST, text: list[str], holes: list[ast.AST], skipped: set[int]) -> None:
    if isinstance(part, ast.Constant) and isinstance(part.value, str):
        text.append(part.value)
        skipped.add(id(part))
    elif isinstance(part, ast.JoinedStr):
        skipped.add(id(part))
        for value in part.values:
            skipped.add(id(value))
            if isinstance(value, ast.Constant):
                text.append(str(value.value))
            else:
                if value.format_spec is not None:
                    skipped.update(id(sub) for sub in ast.walk(value.format_spec))
                text.append(f"\x00{len(holes)}\x00")
                holes.append(value.value)
    else:
        text.append(f"\x00{len(holes)}\x00")
        holes.append(part)


def _format_call(module: Module, node: ast.AST) -> ast.Call | None:
    """The .format(...) call a template is the receiver of: "...".format() or sql.SQL("...").format()."""
    parent = module.parent(node)
    if isinstance(parent, ast.Call) and parent.args and parent.args[0] is node and _call_name(parent) == "SQL":
        node, parent = parent, module.parent(parent)
    if isinstance(parent, ast.Attribute) and parent.attr == "format" and parent.value is node:
        call = module.parent(parent)
        if isinstance(call, ast.Call) and call.func is parent:
            return call
    return None


def _is_hole(part: str) -> bool:
    return "\x00" in part or bool(re.fullmatch(FIELD_HOLE, part))


def _fill(repo: Repository, module: Module, template: Template, scope: ast.AST, match: re.Match,
          group: str) -> frozenset:
    """The strings the schema or name part of a matched target can be."""
    part = match.group(group)
    if not _is_hole(part):
        return frozenset([part])
    start, text = match.start(group), template.text
    if "\x00" in part:
        return _product([repo.strings(module, template.holes[int(piece.strip("\x00"))], scope)
                         if piece.startswith("\x00") else frozenset([piece])
                         for piece in re.split(r"(\x00\d+\x00)", part) if piece])
    if part.startswith("{"):
        call = _format_call(module, template.node)
        if call is None:
            return UNKNOWN
        automatic = 0
        for field in FIELD.finditer(text):
            if field.group(1) is None:
                continue
            key = re.split(r"[!:]", field.group(1), maxsplit=1)[0]
            if field.start() == start:
                if not key.isdigit() and key:
                    named = [k.value for k in call.keywords if k.arg == key]
                    return repo.strings(module, named[0], scope) if named else UNKNOWN
                position = int(key) if key else automatic
                if position >= len(call.args) or any(isinstance(a, ast.Starred) for a in call.args[: position + 1]):
                    return UNKNOWN
                return repo.strings(module, call.args[position], scope)
            automatic += key == ""
        return UNKNOWN
    # %s or %(name)s: the right side of a % operator
    parent = module.parent(template.node)
    if not (isinstance(parent, ast.BinOp) and isinstance(parent.op, ast.Mod) and parent.left is template.node):
        return UNKNOWN
    position = sum(1 for marker in PERCENT.finditer(text[:start]) if marker.group(0) != "%%")
    operand = parent.right
    named = re.fullmatch(r"%\((\w+)\)s", part)
    if named and isinstance(operand, ast.Dict):
        values = [v for k, v in zip(operand.keys, operand.values)
                  if isinstance(k, ast.Constant) and k.value == named.group(1)]
        return repo.strings(module, values[0], scope) if values else UNKNOWN
    if isinstance(operand, ast.Tuple):
        return repo.strings(module, operand.elts[position], scope) if position < len(operand.elts) else UNKNOWN
    return repo.strings(module, operand, scope) if position == 0 else UNKNOWN


def classify(target: str) -> tuple[str | None, bool]:
    """(silver relation or None, unresolved) for a written target such as 'silver.trades',
    'pg_temp.x' or one with WILD parts."""
    text = re.sub(r"\s+", "", target).replace('"', "").lower()
    if WILD not in text:
        pieces = text.split(".")
        schema = pieces[-2] if len(pieces) >= 2 else None
        return (pieces[-1], False) if pieces[-1] in SILVER and schema in SILVER_SCHEMAS else (None, False)
    pattern = re.compile(".*".join(re.escape(piece) for piece in text.split(WILD)))
    possible = any(pattern.fullmatch(form) for name in SILVER for form in (name, f"public.{name}", f"silver.{name}"))
    return None, possible


class FileScan:
    def __init__(self, rel: str):
        self.rel = rel
        self.writes: dict[str, set[str]] = {}             # relation -> statement kinds
        self.lines: dict[str, set[int]] = {}              # relation -> lines
        self.unresolved: list[tuple[int, str, str]] = []  # (line, kind, start of the statement)

    def add(self, relation: str, kind: str, line: int) -> None:
        self.writes.setdefault(relation, set()).add(kind)
        self.lines.setdefault(relation, set()).add(line)


def scan_file(repo: Repository, rel: str) -> FileScan:
    result = FileScan(rel)
    if not PREFILTER.search(repo.text(rel)):
        return result
    module = repo.module(rel)
    if module is None:
        raise AssertionError(f"{rel} does not parse")
    for template in templates(module):
        text = template.text
        if not PREFILTER.search(text):
            continue
        scope = module.scope_of(template.node)
        line = template.node.lineno
        matches = []
        for kind, pattern in WRITES:
            for match in pattern.finditer(text):
                matches.append((kind, match))
                position = match.end()
                while kind in ("TRUNCATE", "LOCK") and (more := NEXT_TARGET.match(text, position)) is not None:
                    matches.append((kind, more))
                    position = more.end()
        if ROW_LOCK.search(text) and SELECT.search(text):
            matches.extend(("FOR UPDATE", match) for match in SELECT_TARGET.finditer(text))
        for kind, match in matches:
            name = match.group("name")
            if not _is_hole(name) and name.strip('"').lower() in NOT_A_NAME:
                continue
            names = _fill(repo, module, template, scope, match, "name")
            schemas = _fill(repo, module, template, scope, match, "schema") if match.group("schema") else None
            unresolved = False
            for value in sorted(names):
                for schema in sorted(schemas) if schemas is not None else [None]:
                    relation, open_ = classify(value if schema is None else f"{schema}.{value}")
                    if relation is not None:
                        result.add(relation, kind, line)
                    unresolved |= open_
            if unresolved:
                snippet = re.sub(r"\s+", " ", text[match.start(): match.end() + 30]).replace("\x00", "~")
                result.unresolved.append((line, kind, snippet))
        if DANGLING.search(text):
            result.unresolved.append((line, "DANGLING", re.sub(r"\s+", " ", text[-40:]).replace("\x00", "~")))
    return result


@lru_cache(maxsize=None)
def scan() -> dict[str, FileScan]:
    """Every file under src/ and scripts/ that writes silver or has a write it cannot settle."""
    repo = Repository(ROOT)
    found = {}
    for rel in repo.files():
        result = scan_file(repo, rel)
        if result.writes or result.unresolved:
            found[rel] = result
    return found


# ------------------------------------------------------------------ Bicep


def bicep_job_names() -> set[str]:
    """The name of every Container Apps job a Bicep file deploys (not `existing` references)."""
    names = set()
    resource = re.compile(r"^resource \w+ 'Microsoft\.App/jobs@[^']+' = (?:if \([^)]*\) )?(\[for (\w+) in (\w+): )?\{",
                          re.M)
    for path in sorted((ROOT / "infra").glob("*.bicep")):
        text = path.read_text(encoding="utf-8")
        params = dict(re.findall(r"^param (\w+) string = '([^']*)'", text, re.M))
        for found in resource.finditer(text):
            name = re.compile(r"^\s+name: (.+)$", re.M).search(text, found.end()).group(1).strip()
            if name.startswith("'"):
                names.add(name.strip("'"))
            elif found.group(1) and name == f"{found.group(2)}.name":
                array = re.search(rf"^var {found.group(3)} = \[(.*?)^\]", text, re.M | re.S).group(1)
                names.update(re.findall(r"\bname: '([^']+)'", array))
            elif name in params:
                names.add(params[name])
            else:
                raise AssertionError(f"{path.name}: cannot read the job name {name}")
    return names


def job_matches(job: str, deployed: set[str]) -> list[str]:
    if job.endswith(MASK):
        return sorted(name for name in deployed if name.startswith(job[: -len(MASK)]))
    return [job] if job in deployed else []


# ------------------------------------------------------------------ tests


def _runs(entry) -> tuple[str, ...]:
    runs = entry[1]
    return (runs,) if isinstance(runs, str) else tuple(runs)


class SilverRelationsTest(unittest.TestCase):

    def test_the_register_lists_the_29_relations_of_migration_070(self):
        moved = migration_relations()
        self.assertEqual(len(moved), 29)
        self.assertEqual(len(set(register.SILVER_RELATIONS)), len(register.SILVER_RELATIONS), "duplicates")
        self.assertEqual(sorted(register.SILVER_RELATIONS), sorted(moved))


class WriterRegisterTest(unittest.TestCase):

    def test_every_file_that_writes_silver_is_registered_with_what_it_writes(self):
        problems = []
        for rel, result in sorted(scan().items()):
            entry = register.WRITER_FILES.get(rel)
            for relation in sorted(result.writes):
                if entry is None or relation not in entry[0]:
                    lines = ", ".join(str(line) for line in sorted(result.lines[relation]))
                    kinds = "/".join(sorted(result.writes[relation]))
                    problems.append(f"{rel} writes silver.{relation} ({kinds}, line {lines}) but "
                                    f"silver_writers.WRITER_FILES does not list it")
        self.assertEqual(problems, [], "\n" + "\n".join(problems))

    def test_every_registered_file_still_writes_what_it_lists(self):
        problems = []
        found = scan()
        for rel, entry in sorted(register.WRITER_FILES.items()):
            if not (ROOT / rel).is_file():
                problems.append(f"{rel} is in WRITER_FILES but does not exist")
                continue
            written = found[rel].writes if rel in found else {}
            if not written:
                problems.append(f"{rel} is in WRITER_FILES but no longer writes silver: remove it")
                continue
            for relation in entry[0]:
                if relation not in written:
                    problems.append(f"{rel}: WRITER_FILES lists silver.{relation}, which it no longer writes")
        self.assertEqual(problems, [], "\n" + "\n".join(problems))

    def test_every_unresolved_write_is_acknowledged(self):
        problems = []
        found = scan()
        for rel, result in sorted(found.items()):
            if result.unresolved and rel not in register.UNRESOLVED_WRITES:
                for line, kind, snippet in result.unresolved:
                    problems.append(f"{rel}:{line}: cannot tell whether this {kind} writes silver: {snippet!r}. "
                                    f"Say what it writes in silver_writers.UNRESOLVED_WRITES")
        for rel in sorted(register.UNRESOLVED_WRITES):
            if rel not in found or not found[rel].unresolved:
                problems.append(f"{rel} is in UNRESOLVED_WRITES but every write in it now resolves: remove it")
        self.assertEqual(problems, [], "\n" + "\n".join(problems))

    def test_register_entries_are_well_formed(self):
        for rel, entry in register.WRITER_FILES.items():
            with self.subTest(file=rel):
                relations, _ = entry
                self.assertTrue(rel.startswith(tuple(f"{folder}/" for folder in SCANNED)) and rel.endswith(".py"))
                self.assertTrue(relations)
                self.assertEqual(sorted(set(relations)), list(relations), "keep the relations sorted and unique")
                self.assertLessEqual(set(relations), set(register.SILVER_RELATIONS))
                runs = _runs(entry)
                self.assertTrue(runs)
                for run in runs:
                    self.assertTrue(run in register.LOADER_JOBS or run in register.NOT_A_JOB, run)
        for rel, (relations, why) in register.UNRESOLVED_WRITES.items():
            with self.subTest(unresolved=rel):
                self.assertTrue(why)
                self.assertLessEqual(set(relations), set(register.SILVER_RELATIONS))
                if relations:  # what an unresolved write does reach is registered like any other write
                    self.assertLessEqual(set(relations), set(register.WRITER_FILES.get(rel, ((), ()))[0]))

    def test_every_loader_job_runs_a_registered_writer(self):
        used = {run for entry in register.WRITER_FILES.values() for run in _runs(entry)}
        for job in register.LOADER_JOBS:
            with self.subTest(job=job):
                self.assertIn(job, used)

    def test_the_register_names_no_account(self):
        text = (ROOT / "src" / "ibkr_etl" / "silver_writers.py").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"\b[Uu]\d{4,}", text))


class LoaderJobsTest(unittest.TestCase):

    def test_every_loader_job_is_deployed_by_a_bicep_file(self):
        deployed = bicep_job_names()
        self.assertIn("ibkrv2stg-benchmark-daily", deployed)  # the reader works
        self.assertIn("ibkrv2stg-agent-holdings", deployed)
        for job, reason in register.LOADER_JOBS.items():
            with self.subTest(job=job):
                self.assertTrue(reason)
                self.assertEqual(len(job_matches(job, deployed)), 1, f"{job} is not a job in infra/*.bicep")


class ScannerTest(unittest.TestCase):
    """The scan itself, on small synthetic files."""

    def scan_source(self, source: str, other: str = "") -> FileScan:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "src" / "pkg").mkdir(parents=True)
            (root / "scripts").mkdir()
            (root / "src" / "pkg" / "__init__.py").write_text("", encoding="utf-8")
            (root / "src" / "pkg" / "sample.py").write_text(source, encoding="utf-8")
            (root / "src" / "pkg" / "other.py").write_text(other, encoding="utf-8")
            return scan_file(Repository(root), "src/pkg/sample.py")

    def test_finds_each_kind_of_write_in_any_spelling(self):
        result = self.scan_source(
            'A = "insert into Silver.TRADES (x) values (1)"\n'
            'B = """\n  UPDATE public.accounts a\n  SET alias = 1"""\n'
            'C = ("DELETE FROM " "fx_rates WHERE x")\n'
            'D = "TRUNCATE TABLE nav_snapshots, silver.cash_snapshots"\n'
            'E = "COPY instruments (conid) FROM STDIN"\n'
            'F = "MERGE INTO risk_free_rates t USING x"\n'
            'G = "LOCK TABLE benchmark_prices IN SHARE MODE"\n'
            'H = "SELECT 1 FROM silver.reference_fx_rates r JOIN corporate_actions c ON true FOR UPDATE OF r"\n'
            'I = \'INSERT INTO "silver"."market_data_prices" VALUES (1)\'\n')
        self.assertEqual(sorted(result.writes), sorted([
            "trades", "accounts", "fx_rates", "nav_snapshots", "cash_snapshots", "instruments", "risk_free_rates",
            "benchmark_prices", "reference_fx_rates", "corporate_actions", "market_data_prices"]))
        self.assertEqual(result.unresolved, [])

    def test_skips_comments_docstrings_other_schemas_and_reads(self):
        result = self.scan_source(
            '"""INSERT INTO trades is described here."""\n'
            '# DELETE FROM trades\n'
            'def f(connection):\n'
            '    "UPDATE accounts SET x = 1"\n'
            '    connection.execute("INSERT INTO bronze.trades VALUES (1)")\n'
            '    connection.execute("INSERT INTO pg_temp.trades VALUES (1)")\n'
            '    connection.execute("INSERT INTO trades_staging VALUES (1)")\n'
            '    connection.execute("SELECT * FROM trades")\n'
            '    connection.execute("INSERT INTO x (a) VALUES (1) ON CONFLICT (a) DO UPDATE SET a = 1")\n')
        self.assertEqual(result.writes, {})
        self.assertEqual(result.unresolved, [])

    def test_follows_dynamic_names_back_to_constants(self):
        result = self.scan_source(
            'from .other import KEYS\n'
            'TABLES = ("position_snapshots", "cash_snapshots")\n'
            'def upsert(connection, table, rows):\n'
            '    connection.execute(f"INSERT INTO {table} VALUES (?)", rows)\n'
            'def run(connection):\n'
            '    for table in TABLES:\n'
            '        connection.execute(f"DELETE FROM {table} WHERE x = ?", (1,))\n'
            '    for name, keys in {"cash_transactions": 1, **KEYS}.items():\n'
            '        upsert(connection, name, [])\n'
            '    upsert(connection, table="nav_snapshots", rows=[])\n'
            '    connection.execute("UPDATE {} SET x = 1".format("silver.instruments"))\n'
            '    connection.execute("UPDATE %s SET x = 1" % "fx_rates")\n'
            '    connection.execute(sql.SQL("TRUNCATE {}").format(sql.Identifier("silver", "accounts")))\n'
            '    connection.execute("INSERT INTO " + "risk_free_rates" + " VALUES (1)")\n'
            '    connection.execute(f"INSERT INTO pg_temp.{connection.name} VALUES (1)")\n'
            '    connection.execute(f"INSERT INTO silver.{connection.name}_history VALUES (1)")\n',
            other='KEYS = {"trades": ("a",), "bronze_only": ("b",)}\n')
        self.assertEqual(sorted(result.writes), sorted([
            "position_snapshots", "cash_snapshots", "cash_transactions", "trades", "nav_snapshots", "instruments",
            "fx_rates", "accounts", "risk_free_rates"]))
        self.assertEqual(result.unresolved, [])

    def test_a_later_assignment_replaces_an_earlier_one(self):
        result = self.scan_source(
            'def run(connection):\n'
            '    for table in ("trades",):\n'
            '        pass\n'
            '    table = "bronze.raw"\n'
            '    connection.execute(f"DELETE FROM {table}")\n')
        self.assertEqual(result.writes, {})

    def test_reports_what_it_cannot_resolve(self):
        result = self.scan_source(
            'def wipe(connection, table):\n'
            '    connection.execute(f"DELETE FROM {table}")\n'
            'def run(connection, name):\n'
            '    wipe(connection, name)\n')
        self.assertEqual(result.writes, {})
        self.assertEqual([kind for _, kind, _ in result.unresolved], ["DELETE"])


if __name__ == "__main__":
    unittest.main()
