import sys
import types
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


if "dotenv" not in sys.modules:
    dotenv = types.ModuleType("dotenv")
    dotenv.load_dotenv = lambda *args, **kwargs: False
    sys.modules["dotenv"] = dotenv


if "langfuse" not in sys.modules:
    langfuse = types.ModuleType("langfuse")

    class _StubLangfuse:  # pragma: no cover - only used in host-side test shims
        def __init__(self, *args, **kwargs):
            raise RuntimeError("langfuse stub should not be instantiated in unit tests")

    langfuse.Langfuse = _StubLangfuse
    sys.modules["langfuse"] = langfuse
