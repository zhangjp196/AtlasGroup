"""
Platform path bootstrap (stdlib only).

Loaded via importlib from the service directory before any ``from services.shared.*`` imports.
Service-level ``shared/`` packages must not shadow the platform root ``shared/``.
"""
from __future__ import annotations

import os
import sys


def resolve_roots(entry_file: str) -> tuple[str, str]:
    entry = os.path.abspath(entry_file)
    service_dir = os.path.dirname(entry)
    parent = os.path.dirname(service_dir)
    parent_name = os.path.basename(parent)
    if parent_name == "services":
        project_root = os.path.dirname(parent)
    else:
        project_root = parent
    return service_dir, project_root


def setup_paths(project_root: str, service_dir: str | None) -> str:
    root_s = os.path.abspath(project_root)
    svc_s = os.path.abspath(service_dir) if service_dir else None
    for p in (root_s, svc_s):
        if not p:
            continue
        while p in sys.path:
            sys.path.remove(p)
    sys.path.insert(0, root_s)
    if svc_s:
        sys.path.insert(1, svc_s)
    return root_s


def refresh_shared_namespace() -> None:
    from pkgutil import extend_path

    import services.shared as shared

    shared.__path__ = extend_path(shared.__path__, shared.__name__)


def merge_service_shared_into_namespace(service_dir: str | None) -> None:
    """pkgutil.extend_path 只合并 ``{sys.path}/services/shared``，不会发现 ``{service}/shared``。"""
    import services.shared as shared_pkg

    refresh_shared_namespace()
    if not service_dir:
        return
    svc_shared = os.path.join(os.path.abspath(service_dir), "shared")
    if os.path.isdir(svc_shared) and svc_shared not in shared_pkg.__path__:
        shared_pkg.__path__.append(svc_shared)


def bootstrap(entry_file: str, *, service_dir: str | None = None) -> tuple[str, str]:
    svc, project_root = resolve_roots(entry_file)
    if service_dir is not None:
        svc = os.path.abspath(service_dir)
    setup_paths(project_root, svc)
    merge_service_shared_into_namespace(svc)
    return svc, project_root