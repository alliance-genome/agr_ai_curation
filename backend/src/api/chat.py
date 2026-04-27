"""Public chat API facade.

The route implementations live in focused chat_* modules.  This module keeps
``src.api.chat.router`` and the historical helper import surface stable for the
application and existing tests.
"""

import sys
import types
from typing import Any

from . import chat_models as _chat_models
from . import chat_common as _chat_common
from . import chat_documents as _chat_documents
from . import chat_sessions as _chat_sessions
from . import chat_stream as _chat_stream
from . import chat_execute_flow as _chat_execute_flow

_PROPAGATED_MODULES = (
    _chat_models,
    _chat_common,
    _chat_documents,
    _chat_sessions,
    _chat_stream,
    _chat_execute_flow,
)


def _export_module_symbols(module: types.ModuleType) -> None:
    for name in dir(module):
        if name.startswith("__"):
            continue
        globals()[name] = getattr(module, name)


for _module in _PROPAGATED_MODULES:
    _export_module_symbols(_module)

router = _chat_common.router


class _ChatFacadeModule(types.ModuleType):
    def __setattr__(self, name: str, value: Any) -> None:
        # Existing tests patch helpers on src.api.chat; mirror those assignments
        # into the implementation modules whose route functions own the globals.
        super().__setattr__(name, value)
        for module in _PROPAGATED_MODULES:
            if hasattr(module, name):
                setattr(module, name, value)


sys.modules[__name__].__class__ = _ChatFacadeModule

__all__ = [name for name in globals() if not name.startswith("__")]
