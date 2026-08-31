"""Named environment profiles for working across several Azure DevOps projects.

Switching projects otherwise means hand-editing ``.env.local``. A profile is a
``<name>.env`` file under ``~/.config/singularity/profiles/``; the active one is
selected by the ``SG_PROFILE`` environment variable or a persisted marker file
and is layered on top of ``.env.local`` / ``.env`` (real environment variables
still win).
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import sys


PROFILE_ENV_VARIABLE = "SG_PROFILE"
PROFILE_NAME_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,64}\Z")


def _config_home() -> Path:
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(xdg_config_home) if xdg_config_home else Path.home() / ".config"
    return base / "singularity"


def profiles_dir() -> Path:
    return _config_home() / "profiles"


def active_profile_path() -> Path:
    return _config_home() / "active-profile"


def is_valid_profile_name(name: str) -> bool:
    return bool(name) and PROFILE_NAME_PATTERN.fullmatch(name) is not None


def profile_env_file(name: str) -> Path:
    return profiles_dir() / f"{name}.env"


def list_profiles() -> list[str]:
    try:
        return sorted(entry.stem for entry in profiles_dir().glob("*.env") if entry.is_file())
    except OSError:
        return []


def resolve_active_profile() -> tuple[str | None, str | None]:
    """Return ``(name, source)`` with ``source`` one of ``"env"``, ``"file"``, ``None``."""
    env_value = os.environ.get(PROFILE_ENV_VARIABLE, "").strip()
    if env_value:
        if is_valid_profile_name(env_value):
            return env_value, "env"
        print(
            f"WARNING: Ignoring invalid {PROFILE_ENV_VARIABLE} value {env_value!r}.",
            file=sys.stderr,
        )
        return None, None

    try:
        stored = active_profile_path().read_text(encoding="utf-8").strip()
    except OSError:
        return None, None
    if stored and is_valid_profile_name(stored):
        return stored, "file"
    if stored:
        print(f"WARNING: Ignoring invalid stored profile name {stored!r}.", file=sys.stderr)
    return None, None


def set_active_profile(name: str | None) -> None:
    path = active_profile_path()
    if name is None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    if not is_valid_profile_name(name):
        raise ValueError(f"invalid profile name: {name!r}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(name + "\n", encoding="utf-8")
