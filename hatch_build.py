"""Hatchling build hook: materialize the VCS-ignored mermaid bundle before packaging.

The actual download lives in ``scripts/vendor_mermaid.py`` (the single source of truth);
this hook just runs it at build time so the wheel/sdist includes the file. The
``artifacts`` setting in pyproject.toml is what lets the VCS-ignored asset be collected
into the distribution.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class MermaidBuildHook(BuildHookInterface):
    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        script = Path(self.root) / "scripts" / "vendor_mermaid.py"
        spec = importlib.util.spec_from_file_location("vendor_mermaid", script)
        if spec is None or spec.loader is None:  # pragma: no cover - defensive
            raise RuntimeError(f"could not load build helper: {script}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.vendor()
