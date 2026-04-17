"""Runtime recovery loader for sourceless forex_trading_system modules.

This package was previously generated with many modules compiled into
``__pycache__`` files. If source ``.py`` files are missing (for example after
an accidental cleanup), this loader allows imports to continue by loading
matching ``.pyc`` files.
"""

from __future__ import annotations

import importlib.abc
import importlib.machinery
import importlib.util
import sys
from pathlib import Path
from typing import Optional


_PACKAGE_NAME = __name__
_PACKAGE_ROOT = Path(__file__).resolve().parent
_CACHE_TAG = sys.implementation.cache_tag or "cpython-314"


def _find_cached_pyc(cache_dir: Path, stem: str) -> Optional[Path]:
    """Find a version-tagged .pyc file in a __pycache__ directory."""
    preferred = cache_dir / f"{stem}.{_CACHE_TAG}.pyc"
    if preferred.exists():
        return preferred

    matches = sorted(cache_dir.glob(f"{stem}.*.pyc"))
    return matches[0] if matches else None


class _SourcelessRecoveryFinder(importlib.abc.MetaPathFinder):
    """Import hook that falls back to package-local __pycache__ bytecode."""

    def find_spec(self, fullname: str, path=None, target=None):
        if not fullname.startswith(_PACKAGE_NAME + "."):
            return None

        rel_parts = fullname.split(".")[1:]
        if not rel_parts:
            return None

        parent_dir = _PACKAGE_ROOT.joinpath(*rel_parts[:-1])
        module_name = rel_parts[-1]
        package_dir = _PACKAGE_ROOT.joinpath(*rel_parts)

        # Do nothing when source exists (normal import should handle it).
        if (parent_dir / f"{module_name}.py").exists() or (package_dir / "__init__.py").exists():
            return None

        # Package fallback: <pkg>/__pycache__/__init__.cpython-XYZ.pyc
        pkg_cache = package_dir / "__pycache__"
        pkg_pyc = _find_cached_pyc(pkg_cache, "__init__") if pkg_cache.exists() else None
        if pkg_pyc:
            loader = importlib.machinery.SourcelessFileLoader(fullname, str(pkg_pyc))
            return importlib.util.spec_from_file_location(
                fullname,
                str(pkg_pyc),
                loader=loader,
                submodule_search_locations=[str(package_dir)],
            )

        # Module fallback: <parent>/__pycache__/<module>.cpython-XYZ.pyc
        mod_cache = parent_dir / "__pycache__"
        mod_pyc = _find_cached_pyc(mod_cache, module_name) if mod_cache.exists() else None
        if mod_pyc:
            loader = importlib.machinery.SourcelessFileLoader(fullname, str(mod_pyc))
            return importlib.util.spec_from_file_location(fullname, str(mod_pyc), loader=loader)

        return None


def _install_recovery_finder() -> None:
    for finder in sys.meta_path:
        if isinstance(finder, _SourcelessRecoveryFinder):
            return
    sys.meta_path.insert(0, _SourcelessRecoveryFinder())


_install_recovery_finder()

