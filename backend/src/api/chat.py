"""Public chat API facade.

Route implementations live in focused chat_* modules. The FastAPI application
imports ``src.api.chat.router`` as the public chat API entry point.
"""

import types

from . import chat_models as _chat_models
from . import chat_common as _chat_common
from . import chat_documents as _chat_documents
from . import chat_sessions as _chat_sessions
from . import chat_stream as _chat_stream
from . import chat_execute_flow as _chat_execute_flow


def _export_module_symbols(module: types.ModuleType) -> None:
    for name in dir(module):
        if name.startswith("__"):
            continue
        globals()[name] = getattr(module, name)


for _module in (
    _chat_models,
    _chat_common,
    _chat_documents,
    _chat_sessions,
    _chat_stream,
    _chat_execute_flow,
):
    _export_module_symbols(_module)

router = _chat_common.router

__all__ = [name for name in globals() if not name.startswith("__")]
