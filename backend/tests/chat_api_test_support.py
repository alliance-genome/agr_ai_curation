"""Shared helpers for chat API tests."""

from __future__ import annotations

from collections.abc import Iterable
from types import ModuleType
from typing import Any


def patch_chat_impl(monkeypatch, modules: Iterable[ModuleType], name: str, value: Any) -> None:
    patched = False
    for module in modules:
        if hasattr(module, name):
            monkeypatch.setattr(module, name, value)
            patched = True
    if not patched:
        raise AttributeError(name)


def patch_chat_impl_for(modules: Iterable[ModuleType]):
    module_tuple = tuple(modules)

    def _patch(monkeypatch, name: str, value: Any) -> None:
        patch_chat_impl(monkeypatch, module_tuple, name, value)

    return _patch
