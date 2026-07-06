"""AST Analyzer — structural analysis of changed code.

Parses Python source files using the ``ast`` module to extract the
structural context (classes, functions, imports) of changed code.
This provides the LLM with a better understanding of the broader
codebase structure surrounding the diff.
"""

from __future__ import annotations

import ast
import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Regex to extract filenames and changed line numbers from unified diff
_DIFF_FILE_PATTERN = re.compile(r"^\+\+\+ b/(.+)$", re.MULTILINE)
_DIFF_HUNK_PATTERN = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", re.MULTILINE)


@dataclass
class ASTNode:
    """Represents a code structure node (class, function, etc.).

    Attributes:
        node_type: Type of AST node (e.g. ``class``, ``function``, ``method``).
        name: Name of the class/function.
        start_line: First line number.
        end_line: Last line number.
        parent: Parent class name if this is a method.
        decorators: List of decorator names.
        docstring: First line of the docstring, if present.
    """

    node_type: str
    name: str
    start_line: int
    end_line: int
    parent: str = ""
    decorators: list[str] = field(default_factory=list)
    docstring: str = ""


class ASTAnalyzer:
    """Analyzes Python source code structure using the AST module.

    Provides methods to extract classes, functions, and their relationships
    from source code, and to identify which structures are affected by
    a given diff.
    """

    @staticmethod
    def parse_source(source: str) -> list[ASTNode]:
        """Parses Python source code and returns a list of structural nodes.

        Args:
            source: Python source code as a string.

        Returns:
            List of ``ASTNode`` objects representing classes, functions,
            and methods found in the source.
        """
        try:
            tree = ast.parse(source)
        except SyntaxError:
            logger.warning("AST parse failed — source may not be valid Python.")
            return []

        nodes: list[ASTNode] = []

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                nodes.append(
                    ASTNode(
                        node_type="class",
                        name=node.name,
                        start_line=node.lineno,
                        end_line=node.end_lineno or node.lineno,
                        decorators=[_decorator_name(d) for d in node.decorator_list],
                        docstring=ast.get_docstring(node, clean=True) or "",
                    )
                )

                # Extract methods within the class
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        nodes.append(
                            ASTNode(
                                node_type="method",
                                name=item.name,
                                start_line=item.lineno,
                                end_line=item.end_lineno or item.lineno,
                                parent=node.name,
                                decorators=[_decorator_name(d) for d in item.decorator_list],
                                docstring=ast.get_docstring(item, clean=True) or "",
                            )
                        )

            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Top-level functions only (methods handled above)
                if not any(
                    isinstance(parent, ast.ClassDef)
                    for parent in ast.walk(tree)
                    if hasattr(parent, "body") and node in getattr(parent, "body", [])
                ):
                    nodes.append(
                        ASTNode(
                            node_type="function",
                            name=node.name,
                            start_line=node.lineno,
                            end_line=node.end_lineno or node.lineno,
                            decorators=[_decorator_name(d) for d in node.decorator_list],
                            docstring=ast.get_docstring(node, clean=True) or "",
                        )
                    )

        return sorted(nodes, key=lambda n: n.start_line)

    @staticmethod
    def extract_changed_lines_from_diff(diff: str) -> dict[str, list[int]]:
        """Extracts changed line numbers per file from a unified diff.

        Args:
            diff: Unified diff string.

        Returns:
            Dictionary mapping filenames to lists of changed line numbers.
        """
        file_to_lines: dict[str, list[int]] = {}
        current_file: str | None = None

        for line in diff.splitlines():
            file_match = _DIFF_FILE_PATTERN.match(line)
            if file_match:
                current_file = file_match.group(1)
                if current_file not in file_to_lines:
                    file_to_lines[current_file] = []
                continue

            hunk_match = _DIFF_HUNK_PATTERN.match(line)
            if hunk_match and current_file:
                start = int(hunk_match.group(1))
                count = int(hunk_match.group(2)) if hunk_match.group(2) else 1
                file_to_lines[current_file].extend(range(start, start + count))

        return file_to_lines

    @staticmethod
    def find_affected_nodes(
        nodes: list[ASTNode],
        changed_lines: list[int],
    ) -> list[ASTNode]:
        """Finds AST nodes that overlap with the changed line numbers.

        Args:
            nodes: List of AST nodes from ``parse_source()``.
            changed_lines: List of changed line numbers.

        Returns:
            List of affected AST nodes.
        """
        if not changed_lines:
            return []

        changed_set = set(changed_lines)
        affected: list[ASTNode] = []

        for node in nodes:
            node_lines = set(range(node.start_line, node.end_line + 1))
            if node_lines & changed_set:
                affected.append(node)

        return affected

    @staticmethod
    def format_context(nodes: list[ASTNode]) -> str:
        """Formats AST nodes into a human-readable structural context.

        Args:
            nodes: List of AST nodes.

        Returns:
            Formatted string describing the code structure.
        """
        if not nodes:
            return "No structural context extracted."

        parts: list[str] = ["**Code Structure Context:**"]

        for node in nodes:
            prefix = f"  [{node.node_type}]"
            name = f"{node.parent}.{node.name}" if node.parent else node.name
            line_info = f"(lines {node.start_line}–{node.end_line})"
            parts.append(f"{prefix} {name} {line_info}")

            if node.decorators:
                parts.append(f"    Decorators: {', '.join(node.decorators)}")

            if node.docstring:
                first_line = node.docstring.split("\n")[0][:120]
                parts.append(f"    Doc: {first_line}")

        return "\n".join(parts)


def analyze_diff_context(diff: str, file_to_source: dict[str, str]) -> str:
    """Convenience function: analyzes a diff and returns structural context.

    Args:
        diff: Unified diff string.
        file_to_source: Mapping of filename to full source code content.
            Only Python files (ending in ``.py``) are analyzed.

    Returns:
        Formatted structural context string for all affected files.
    """
    analyzer = ASTAnalyzer()
    changed = analyzer.extract_changed_lines_from_diff(diff)
    all_affected: list[ASTNode] = []

    for filename, lines in changed.items():
        if not filename.endswith(".py"):
            continue

        source = file_to_source.get(filename)
        if not source:
            continue

        nodes = analyzer.parse_source(source)
        affected = analyzer.find_affected_nodes(nodes, lines)
        all_affected.extend(affected)

    return analyzer.format_context(all_affected)


def _decorator_name(node: ast.expr) -> str:
    """Extracts the decorator name from an AST decorator node.

    Args:
        node: AST expression node representing a decorator.

    Returns:
        Decorator name string.
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    return ""


class AstLinter:
    """Static pre-LLM check to identify security issues using AST."""

    @staticmethod
    def lint_source(filename: str, source: str, changed_lines: set[int]) -> list[str]:
        findings = []
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return findings

        for node in ast.walk(tree):
            if hasattr(node, "lineno") and node.lineno in changed_lines:
                if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "eval":
                    findings.append(
                        f"🔴 **CRITICAL** [`{filename}:{node.lineno}`] Use of `eval()` detected. This is a severe security risk."
                    )

                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            var_name = target.id.lower()
                            if any(
                                k in var_name
                                for k in ("password", "secret", "token", "api_key", "credentials")
                            ):
                                if (
                                    isinstance(node.value, ast.Constant)
                                    and isinstance(node.value.value, str)
                                    and len(node.value.value.strip()) > 3
                                ):
                                    findings.append(
                                        f"🔴 **CRITICAL** [`{filename}:{node.lineno}`] Hardcoded secret assigned to '{target.id}'. Use environment variables instead."
                                    )

        return findings


def run_linter_on_diff(diff: str, file_to_source: dict[str, str]) -> str:
    """Runs the pre-LLM static linter on changed files.

    Args:
        diff: Unified diff string.
        file_to_source: Mapping of filename to full source code.

    Returns:
        Markdown string with findings, or empty string if none.
    """
    analyzer = ASTAnalyzer()
    changed = analyzer.extract_changed_lines_from_diff(diff)
    all_findings = []

    for filename, lines in changed.items():
        if not filename.endswith(".py"):
            continue

        source = file_to_source.get(filename)
        if not source:
            continue

        findings = AstLinter.lint_source(filename, source, set(lines))
        all_findings.extend(findings)

    if not all_findings:
        return ""

    parts = ["### 🛑 Static Analysis (Pre-LLM Linter)"]
    parts.extend(all_findings)
    return "\n\n".join(parts)
