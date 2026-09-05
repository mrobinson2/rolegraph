"""Configuration. Everything comes from environment variables or YAML - never code."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_PRIVILEGED_ROLES = ("Owner", "Contributor", "User Access Administrator")


def _env_path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value) if value else default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass(slots=True)
class Settings:
    database_url: str
    data_dir: Path
    demo_dataset: Path
    privileged_roles_file: Path
    max_upload_bytes: int
    app_name: str = "RoleGraph"
    #: The app makes no outbound network calls. Kept explicit so the promise is
    #: visible in config rather than only in documentation.
    allow_outbound_network: bool = False
    privileged_roles: tuple[str, ...] = field(default=DEFAULT_PRIVILEGED_ROLES)
    privileged_role_notes: dict[str, str] = field(default_factory=dict)

    @property
    def privileged_roles_lower(self) -> set[str]:
        return {r.lower() for r in self.privileged_roles}

    def is_privileged(self, role_name: str) -> bool:
        return role_name.lower() in self.privileged_roles_lower


def load_privileged_roles(path: Path) -> tuple[tuple[str, ...], dict[str, str]]:
    """Read ``config/privileged_roles.yaml``. Falls back to the Azure defaults."""
    if not path.exists():
        return DEFAULT_PRIVILEGED_ROLES, {}
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    entries = raw.get("privilegedRoles") or []
    names: list[str] = []
    notes: dict[str, str] = {}
    for entry in entries:
        if isinstance(entry, str):
            names.append(entry)
        elif isinstance(entry, dict) and entry.get("name"):
            names.append(entry["name"])
            if entry.get("why"):
                notes[entry["name"].lower()] = entry["why"]
    return (tuple(names) or DEFAULT_PRIVILEGED_ROLES), notes


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    data_dir = _env_path("ROLEGRAPH_DATA_DIR", REPO_ROOT / "data")
    data_dir.mkdir(parents=True, exist_ok=True)
    privileged_file = _env_path(
        "ROLEGRAPH_PRIVILEGED_ROLES_FILE", REPO_ROOT / "config" / "privileged_roles.yaml"
    )
    roles, notes = load_privileged_roles(privileged_file)
    return Settings(
        database_url=os.environ.get("ROLEGRAPH_DATABASE_URL", f"sqlite:///{data_dir / 'rolegraph.db'}"),
        data_dir=data_dir,
        demo_dataset=_env_path("ROLEGRAPH_DEMO_DATASET", REPO_ROOT / "data" / "demo" / "contoso.json"),
        privileged_roles_file=privileged_file,
        max_upload_bytes=_env_int("ROLEGRAPH_MAX_UPLOAD_BYTES", 25 * 1024 * 1024),
        privileged_roles=roles,
        privileged_role_notes=notes,
    )
