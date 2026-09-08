"""Workspace-scoped activation registry for immutable taxonomy catalogs.

The registry only serves catalog reads on the request path.  Construction,
review, and promotion happen offline; activation selects one immutable digest,
and rollback only moves the active pointer to a previously registered digest.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Optional

from runtime.policy import require_runtime_permission
from seocho.taxonomy_catalog import TaxonomyCatalog


@dataclass(frozen=True, slots=True)
class TaxonomyActivation:
    """Auditable active-pointer receipt, without exposing catalog source paths."""

    workspace_id: str
    scheme_id: str
    catalog_sha256: str
    version: str

    def to_dict(self) -> dict[str, str]:
        return {
            "workspace_id": self.workspace_id,
            "scheme_id": self.scheme_id,
            "catalog_sha256": self.catalog_sha256,
            "version": self.version,
        }


class RuntimeTaxonomyRegistry:
    """Thread-safe per-workspace store of immutable taxonomy catalog versions."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._catalogs: dict[tuple[str, str], TaxonomyCatalog] = {}
        self._registrations: dict[tuple[str, str], TaxonomyActivation] = {}
        self._active: dict[str, TaxonomyActivation] = {}

    def register(self, catalog: TaxonomyCatalog, *, workspace_id: str, version: str) -> TaxonomyActivation:
        if not isinstance(catalog, TaxonomyCatalog):
            raise TypeError("catalog must be a TaxonomyCatalog")
        workspace = str(workspace_id).strip()
        label = str(version).strip()
        if not workspace or not label:
            raise ValueError("workspace_id and version are required")
        activation = TaxonomyActivation(workspace, catalog.scheme_id, catalog.digest, label)
        with self._lock:
            self._catalogs[(workspace, catalog.digest)] = catalog
            self._registrations[(workspace, catalog.digest)] = activation
        return activation

    def activate(self, *, workspace_id: str, catalog_sha256: str) -> TaxonomyActivation:
        workspace = str(workspace_id).strip()
        digest = str(catalog_sha256).strip()
        with self._lock:
            catalog = self._catalogs.get((workspace, digest))
            if catalog is None:
                raise KeyError("catalog digest is not registered for this workspace")
            activation = self._registrations[(workspace, digest)]
            self._active[workspace] = activation
            return activation

    def activate_registered(self, activation: TaxonomyActivation) -> TaxonomyActivation:
        """Activate the exact receipt returned by :meth:`register`."""
        with self._lock:
            if (activation.workspace_id, activation.catalog_sha256) not in self._catalogs:
                raise KeyError("catalog digest is not registered for this workspace")
            self._active[activation.workspace_id] = activation
            return activation

    def rollback(self, *, workspace_id: str, catalog_sha256: str) -> TaxonomyActivation:
        """Rollback by activating a known immutable digest; no history is mutated."""
        return self.activate(workspace_id=workspace_id, catalog_sha256=catalog_sha256)

    def active(self, *, workspace_id: str) -> Optional[TaxonomyActivation]:
        with self._lock:
            return self._active.get(str(workspace_id).strip())

    def resolve(
        self,
        term: str,
        *,
        workspace_id: str,
        facets: Optional[dict[str, str]] = None,
        expected_catalog_sha256: Optional[str] = None,
    ) -> dict[str, Any]:
        """Read from the active catalog after runtime workspace-policy enforcement."""
        require_runtime_permission(action="read_taxonomy", workspace_id=workspace_id)
        activation = self.active(workspace_id=workspace_id)
        if activation is None:
            return {"status": "unavailable", "reason": "no active taxonomy catalog", "workspace_id": workspace_id}
        if expected_catalog_sha256 and expected_catalog_sha256 != activation.catalog_sha256:
            return {"status": "stale_context", "workspace_id": workspace_id, "active": activation.to_dict()}
        with self._lock:
            catalog = self._catalogs[(activation.workspace_id, activation.catalog_sha256)]
        return {**catalog.resolve(term, facets=facets), "workspace_id": workspace_id, "activation": activation.to_dict()}

    def clear(self) -> None:
        with self._lock:
            self._catalogs.clear()
            self._registrations.clear()
            self._active.clear()
