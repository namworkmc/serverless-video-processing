"""Register the `_shared` directory as the `shared` package for local tests.

In the Lambda zip the package lives at the zip root as `shared/` (see
terraform/smoke.tf); locally the directory is `lambdas/_shared`. This alias
makes `from shared import ...` resolve identically in both environments.
"""

import importlib.util
import sys
from pathlib import Path

_SHARED_DIR = Path(__file__).resolve().parents[1]

# Always (re-)register from the local _shared directory: if an unrelated
# installed package named `shared` got imported first, it must not silently
# shadow the layer under test.
_spec = importlib.util.spec_from_file_location(
    "shared",
    _SHARED_DIR / "__init__.py",
    submodule_search_locations=[str(_SHARED_DIR)],
)
_module = importlib.util.module_from_spec(_spec)
sys.modules["shared"] = _module
_spec.loader.exec_module(_module)
