"""Ratchets and checked documentation: code does not grow unnoticed, modules keep their interfaces, and
every command, policy key and link the docs mention exists."""

import ast
import html
import re
import sys
import tomllib
from pathlib import Path

import pytest
from conftest import REPO

from whatsapp_context.cli import parser
from whatsapp_context.policy import parse_policy

# Line ceilings. A file may shrink but never grow past its number. Lower a number when you shorten a
# file; raise one only in a change of its own that says why.
LINE_CEILINGS = {
    "helper/main.swift": 297,
    "scripts/register.py": 199,
    "scripts/grant.sh": 50,
    "scripts/setup.sh": 112,
    "src/whatsapp_context/__init__.py": 0,
    "src/whatsapp_context/cli.py": 71,
    "src/whatsapp_context/exporter.py": 236,
    "src/whatsapp_context/mcp_server.py": 158,
    "src/whatsapp_context/paths.py": 46,
    "src/whatsapp_context/policy.py": 280,
    "src/whatsapp_context/policy_edit.py": 164,
    "src/whatsapp_context/store.py": 364,
    "src/whatsapp_context/text.py": 128,
}
WATCHED = ["src/whatsapp_context/*.py", "scripts/*", "helper/main.swift"]
PYTHON = ["src/whatsapp_context/*.py", "scripts/*.py"]
DOCS = ["*.md", "docs/*.md", "docs/index.html"]


def files(patterns: list[str]) -> list[Path]:
    return sorted(p for pattern in patterns for p in REPO.glob(pattern) if p.is_file())


def test_files_stay_under_their_line_ceiling():
    counts = {str(p.relative_to(REPO)): len(p.read_text().splitlines()) for p in files(WATCHED)}
    assert set(counts) == set(LINE_CEILINGS), "add new files to LINE_CEILINGS (and drop deleted ones)"
    grown = {name: f"{n} > {LINE_CEILINGS[name]}" for name, n in counts.items() if n > LINE_CEILINGS[name]}
    assert not grown, f"files grew past their ceiling: {grown}"


def private_imports(path: Path) -> list[str]:
    """`from <this package> import _name` and `module._name` on a module of this package."""
    tree = ast.parse(path.read_text())
    modules, found = set(), []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.level or (node.module or "").startswith("whatsapp_context")):
            for alias in node.names:
                if alias.name.startswith("_") and not alias.name.startswith("__"):
                    found.append(f"{path.name}:{node.lineno} imports {alias.name}")
                modules.add(alias.asname or alias.name)
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in modules
                and node.attr.startswith("_") and not node.attr.startswith("__")):
            found.append(f"{path.name}:{node.lineno} uses {node.value.id}.{node.attr}")
    return found


def test_modules_only_use_each_others_public_names():
    found = [problem for path in files(PYTHON) for problem in private_imports(path)]
    assert not found, "use (or add) a public function instead: " + "; ".join(found)


def test_private_import_check_catches_both_forms(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("from .policy import _match\nfrom . import paths\npaths._secret()\n")
    assert len(private_imports(bad)) == 2


# Documentation: code is what sits in fenced blocks, `inline code` and <code>/<pre>.
FENCE = re.compile(r"^[ \t]*(```|~~~)(\w*)[^\n]*\n(.*?)^[ \t]*\1", re.M | re.S)
INLINE = re.compile(r"`([^`\n]+)`")
HTML_CODE = re.compile(r"<(code|pre)\b[^>]*>(.*?)</\1>", re.S)
MD_LINK = re.compile(r"\[[^\]\n]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)|^\s*\[[^\]\n]+\]:\s*(\S+)", re.M)
HTML_LINK = re.compile(r"\b(?:href|src)\s*=\s*[\"']([^\"']+)[\"']")
MAKE = re.compile(r"(?:^|[\s;&|(])make((?:\s+-C\s+\S+|\s+-\S+|\s+[A-Z_]+=\S+)*)\s+([a-z][\w-]*)")
WA = re.compile(r"(?:^|[\s;&|(])wa\s+([a-z][\w-]*)([^|;&#\n]*)")


def doc_parts(path: Path) -> tuple[list[str], list[str], list[str]]:
    """(code snippets, ```toml blocks, link targets) of one document."""
    text = path.read_text()
    if path.suffix == ".html":
        code = [html.unescape(re.sub(r"<[^>]+>", "", m.group(2))) for m in HTML_CODE.finditer(text)]
        return code, [], HTML_LINK.findall(re.sub(r"<!--.*?-->", "", text, flags=re.S))
    fences = list(FENCE.finditer(text))
    prose = FENCE.sub("", text)
    code = [m.group(3) for m in fences] + INLINE.findall(prose)
    toml = [m.group(3) for m in fences if m.group(2) == "toml"]
    links = [a or b for a, b in MD_LINK.findall(INLINE.sub("", prose))] + HTML_LINK.findall(prose)
    return code, toml, links


def without_comments(snippet: str) -> str:
    return re.sub(r"(^|\s)#.*$", "", snippet, flags=re.M)


def doc_problems(kind: str) -> list[str]:
    targets = set(re.findall(r"^([a-zA-Z][\w-]*):", (REPO / "Makefile").read_text(), re.M))
    commands = next(a for a in parser()._actions if a.dest == "cmd").choices
    problems = []
    for path in files(DOCS):
        name = path.relative_to(REPO)
        code, toml, links = doc_parts(path)
        for snippet in map(without_comments, code if kind in ("make", "wa") else []):
            if kind == "make":
                problems += [f"{name}: make {t}" for _, t in MAKE.findall(snippet) if t not in targets]
            for sub, rest in WA.findall(snippet) if kind == "wa" else []:
                if sub not in commands:
                    problems.append(f"{name}: wa {sub}")
                    continue
                problems += [f"{name}: wa {sub} {flag}" for flag in re.findall(r"(?<!\S)--?[a-z][\w-]*", rest)
                             if flag not in commands[sub].format_help()]
        for block in toml if kind == "toml" else []:
            try:
                parse_policy(tomllib.loads(block), Path(str(name)))
            except ValueError as exc:
                problems.append(f"{name}: {exc}")
        for link in links if kind == "links" else []:
            target = link.split("#")[0].split("?")[0]
            if target and not re.match(r"^[a-z][\w+.-]*:", target) and not (path.parent / target).exists():
                problems.append(f"{name}: {link}")
    return problems


# Stale references the docs still carry after `make register-desktop-wait` was folded into
# `make register-desktop`. Tolerated only until the docs are updated; never add to this set.


@pytest.mark.parametrize("kind", ["make", "wa", "toml", "links"])
def test_docs_mention_only_what_exists(kind):
    problems = doc_problems(kind)
    assert not problems, f"docs mention {kind} references that do not exist: " + "; ".join(problems)


def test_doc_check_catches_bad_references(tmp_path, monkeypatch):
    doc = tmp_path / "README.md"
    doc.write_text("Run `make nope` and `uv run wa chats --nope`.\n\n```toml\n[excludes]\n```\n\n[x](missing.md)\n"
                   "- step:\n\n  ```bash\n  make also-nope\n  ```\n")
    (tmp_path / "Makefile").write_text((REPO / "Makefile").read_text())
    monkeypatch.setattr(sys.modules[__name__], "REPO", tmp_path)
    monkeypatch.setattr(sys.modules[__name__], "DOCS", ["README.md"])
    assert [len(doc_problems(k)) for k in ("make", "wa", "toml", "links")] == [2, 1, 1, 1]
