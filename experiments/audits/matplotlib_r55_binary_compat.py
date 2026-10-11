"""Pytest-only ABI bridge for Matplotlib 3.8 Python and 3.10 Agg binaries.

The public r55 source expects both the single- and multi-triangle extension
methods.  The locally available CPython 3.13 wheel implements only the newer
multi-triangle method; RendererBase already supplies the single-triangle
fallback.  This plugin leaves that inherited fallback in place and binds the
remaining extension methods exactly as the public source does.
"""

from __future__ import annotations

import sys
import types


def pytest_configure(config) -> None:  # pragma: no cover - exercised cross-env
    del config
    # Saving PNG metadata asks setuptools_scm for a display-only version when
    # the checkout marker is present.  The local binary wheel has already
    # supplied a generated fallback version, so expose only that narrow API.
    if "setuptools_scm" not in sys.modules:
        shim = types.ModuleType("setuptools_scm")
        shim.get_version = lambda **kwargs: kwargs["fallback_version"]
        sys.modules["setuptools_scm"] = shim

    from matplotlib.backends.backend_agg import RendererAgg

    original = RendererAgg._update_methods
    marker = "_crewai_r55_abi_bridge"
    if getattr(RendererAgg, marker, False):
        return

    def compatible_update_methods(self) -> None:
        if hasattr(self._renderer, "draw_gouraud_triangle"):
            return original(self)
        # Do not create an instance attribute for draw_gouraud_triangle:
        # RendererBase.draw_gouraud_triangle converts one triangle to the
        # plural call supplied by the extension.
        self.draw_gouraud_triangles = self._renderer.draw_gouraud_triangles
        self.draw_image = self._renderer.draw_image
        self.draw_markers = self._renderer.draw_markers
        self.draw_path_collection = self._renderer.draw_path_collection
        self.draw_quad_mesh = self._renderer.draw_quad_mesh
        self.copy_from_bbox = self._renderer.copy_from_bbox

    RendererAgg._update_methods = compatible_update_methods
    setattr(RendererAgg, marker, True)

