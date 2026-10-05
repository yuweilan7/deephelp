"""Protect canonical DTO identity and the one-way dependency graph after organization."""

import ast
import importlib
from pathlib import Path

import pytest

from deephelp_app.domain import models

pytestmark = pytest.mark.unit


def test_public_dto_exports_are_the_canonical_definitions():
    folder = Path(models.__file__).parent
    definitions = {}
    for path in folder.glob("*.py"):
        if path.stem == "__init__":
            continue
        module = importlib.import_module(f"deephelp_app.domain.models.{path.stem}")
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.ClassDef):
                assert node.name not in definitions, f"Duplicate DTO: {node.name}"
                definitions[node.name] = module
                assert getattr(models, node.name) is getattr(module, node.name)
    public_classes = {name for name, value in vars(models).items() if isinstance(value, type)}
    assert set(definitions) == public_classes


def test_model_dependencies_are_acyclic_and_do_not_import_the_public_catalog():
    folder = Path(models.__file__).parent
    graph = {}
    for path in folder.glob("*.py"):
        if path.stem == "__init__":
            continue
        graph[path.stem] = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                assert node.module != "deephelp_app.domain.models", path
                if node.level == 1:
                    graph[path.stem].add(node.module)

    def visit(name, ancestors):
        assert name not in ancestors, f"Cyclic DTO dependency: {ancestors} -> {name}"
        for dependency in graph[name]:
            visit(dependency, (*ancestors, name))

    for name in graph:
        visit(name, ())
