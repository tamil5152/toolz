"""Safe evaluator for rule check expressions. Never uses eval().

Supported: numbers, field names, ``+ - * /``, parentheses, comparisons
(``< <= > >= == !=``), ``x between a and b`` (inclusive), ``and``, ``or``, ``not``,
``true`` and ``false``.

Example: ``"clearance_pct between 3 and 10 and diameter_mm >= 1.0 * thickness_mm"``
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

Value = float | bool


class ExpressionError(ValueError):
    """The expression is malformed, or cannot be evaluated with the given values."""


# --- syntax tree ----------------------------------------------------------------------


@dataclass(frozen=True)
class Number:
    value: float


@dataclass(frozen=True)
class Boolean:
    value: bool


@dataclass(frozen=True)
class Name:
    name: str


@dataclass(frozen=True)
class Unary:
    op: str  # "-" or "not"
    operand: Node


@dataclass(frozen=True)
class Binary:
    op: str  # + - * / < <= > >= == != and or
    left: Node
    right: Node


@dataclass(frozen=True)
class Between:
    value: Node
    low: Node
    high: Node


Node = Number | Boolean | Name | Unary | Binary | Between


# --- tokenizer ------------------------------------------------------------------------

_TOKEN = re.compile(
    r"\s*(?:(?P<number>\d+(?:\.\d*)?|\.\d+)|(?P<name>[A-Za-z_][A-Za-z0-9_.]*)"
    r"|(?P<op><=|>=|==|!=|[-+*/()<>]))"
)
_KEYWORDS = {"and", "or", "not", "between", "true", "false"}


def _tokenize(text: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    pos = 0
    text = text.rstrip()
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if match is None or match.end() == pos:
            raise ExpressionError(f"unexpected character {text[pos:].lstrip()[:1]!r} in {text!r}")
        kind = match.lastgroup
        assert kind is not None
        value = match.group(kind)
        if kind == "name" and value in _KEYWORDS:
            kind = "keyword"
        tokens.append((kind, value))
        pos = match.end()
    return tokens


# --- parser ---------------------------------------------------------------------------


class _Parser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.tokens = _tokenize(text)
        self.pos = 0

    def parse(self) -> Node:
        node = self._or()
        if self.pos != len(self.tokens):
            raise ExpressionError(f"unexpected {self.tokens[self.pos][1]!r} in {self.text!r}")
        return node

    def _peek(self) -> str | None:
        return self.tokens[self.pos][1] if self.pos < len(self.tokens) else None

    def _take(self, expected: str | None = None) -> tuple[str, str]:
        if self.pos >= len(self.tokens):
            raise ExpressionError(f"unexpected end of {self.text!r}")
        token = self.tokens[self.pos]
        if expected is not None and token[1] != expected:
            raise ExpressionError(f"expected {expected!r}, found {token[1]!r} in {self.text!r}")
        self.pos += 1
        return token

    def _or(self) -> Node:
        node = self._and()
        while self._peek() == "or":
            self._take()
            node = Binary("or", node, self._and())
        return node

    def _and(self) -> Node:
        node = self._not()
        while self._peek() == "and":
            self._take()
            node = Binary("and", node, self._not())
        return node

    def _not(self) -> Node:
        if self._peek() == "not":
            self._take()
            return Unary("not", self._not())
        return self._comparison()

    def _comparison(self) -> Node:
        node = self._sum()
        op = self._peek()
        if op in ("<", "<=", ">", ">=", "==", "!="):
            self._take()
            return Binary(op, node, self._sum())
        if op == "between":
            self._take()
            low = self._sum()
            self._take("and")
            return Between(node, low, self._sum())
        return node

    def _sum(self) -> Node:
        node = self._term()
        while self._peek() in ("+", "-"):
            op = self._take()[1]
            node = Binary(op, node, self._term())
        return node

    def _term(self) -> Node:
        node = self._unary()
        while self._peek() in ("*", "/"):
            op = self._take()[1]
            node = Binary(op, node, self._unary())
        return node

    def _unary(self) -> Node:
        if self._peek() == "-":
            self._take()
            return Unary("-", self._unary())
        return self._primary()

    def _primary(self) -> Node:
        kind, value = self._take()
        if kind == "number":
            return Number(float(value))
        if kind == "name":
            return Name(value)
        if value in ("true", "false"):
            return Boolean(value == "true")
        if value == "(":
            node = self._or()
            self._take(")")
            return node
        raise ExpressionError(f"unexpected {value!r} in {self.text!r}")


# --- public API -----------------------------------------------------------------------


@dataclass(frozen=True)
class Expression:
    """A parsed expression, ready to evaluate against field values."""

    text: str
    tree: Node

    @property
    def names(self) -> list[str]:
        """Field names the expression reads, in first-use order."""
        found: list[str] = []
        _collect_names(self.tree, found)
        return found

    def evaluate(self, values: Mapping[str, Value]) -> Value:
        return _evaluate(self.tree, values)


def parse(text: str) -> Expression:
    return Expression(text, _Parser(text).parse())


def evaluate(text: str, values: Mapping[str, Value]) -> Value:
    return parse(text).evaluate(values)


def _collect_names(node: Node, found: list[str]) -> None:
    match node:
        case Name(name):
            if name not in found:
                found.append(name)
        case Unary(_, operand):
            _collect_names(operand, found)
        case Binary(_, left, right):
            _collect_names(left, found)
            _collect_names(right, found)
        case Between(value, low, high):
            for child in (value, low, high):
                _collect_names(child, found)
        case _:
            pass


def _number(value: Value, context: str) -> float:
    if isinstance(value, bool):
        raise ExpressionError(f"expected a number for {context}, got {value}")
    return value


def _boolean(value: Value, context: str) -> bool:
    if not isinstance(value, bool):
        raise ExpressionError(f"expected true/false for {context}, got {value}")
    return value


def _evaluate(node: Node, values: Mapping[str, Value]) -> Value:
    match node:
        case Number(value) | Boolean(value):
            return value
        case Name(name):
            if name not in values:
                raise ExpressionError(f"no value for {name!r}")
            value = values[name]
            return value if isinstance(value, bool) else float(value)
        case Unary("-", operand):
            return -_number(_evaluate(operand, values), "-")
        case Unary("not", operand):
            return not _boolean(_evaluate(operand, values), "not")
        case Binary("and", left, right):
            return _boolean(_evaluate(left, values), "and") and _boolean(
                _evaluate(right, values), "and"
            )
        case Binary("or", left, right):
            return _boolean(_evaluate(left, values), "or") or _boolean(
                _evaluate(right, values), "or"
            )
        case Binary(op, left, right):
            a = _number(_evaluate(left, values), op)
            b = _number(_evaluate(right, values), op)
            return _arithmetic_or_compare(op, a, b)
        case Between(value, low, high):
            v = _number(_evaluate(value, values), "between")
            lo = _number(_evaluate(low, values), "between")
            hi = _number(_evaluate(high, values), "between")
            return lo <= v <= hi
    raise ExpressionError(f"cannot evaluate {node!r}")


def _arithmetic_or_compare(op: str, a: float, b: float) -> Value:
    if op == "+":
        return a + b
    if op == "-":
        return a - b
    if op == "*":
        return a * b
    if op == "/":
        if b == 0:
            raise ExpressionError("division by zero")
        return a / b
    if op == "<":
        return a < b
    if op == "<=":
        return a <= b
    if op == ">":
        return a > b
    if op == ">=":
        return a >= b
    if op == "==":
        return a == b
    if op == "!=":
        return a != b
    raise ExpressionError(f"unknown operator {op!r}")
