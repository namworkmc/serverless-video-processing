"""Test harness for the upload-handler Lambda (Story 1.3, ATDD red phase).

- Adds `lambdas/` to sys.path so `import upload_handler` resolves to
  `lambdas/upload_handler/` (in the Lambda zip the package sits at the zip
  root, same layout as `shared/`).
- Registers `lambdas/_shared` as the `shared` package (same alias as
  `lambdas/_shared/tests/conftest.py`) so the handler's `from shared import
  ...` imports resolve identically in tests and in the Lambda runtime.
"""
import importlib.util
import sys
from pathlib import Path

_LAMBDAS_DIR = Path(__file__).resolve().parents[2]

if str(_LAMBDAS_DIR) not in sys.path:
    sys.path.insert(0, str(_LAMBDAS_DIR))

_SHARED_DIR = _LAMBDAS_DIR / "_shared"
if "shared" not in sys.modules:
    _spec = importlib.util.spec_from_file_location(
        "shared",
        _SHARED_DIR / "__init__.py",
        submodule_search_locations=[str(_SHARED_DIR)],
    )
    _module = importlib.util.module_from_spec(_spec)
    sys.modules["shared"] = _module
    _spec.loader.exec_module(_module)
