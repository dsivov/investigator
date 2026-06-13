"""Secret loading with sensible precedence.

Order (first hit wins):
1. environment variable (already exported, e.g. by `.env` via python-dotenv)
2. an explicit toml path passed to the loader
3. ``~/.config/secrets.toml`` (legacy fallback)

Failure raises ``SecretNotFoundError`` with a message listing what was tried,
so it's obvious how to fix the deployment.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import toml


class SecretNotFoundError(RuntimeError):
    """Raised when a required secret cannot be located in any configured source."""


@dataclass
class SecretLoader:
    """Locate API keys / credentials across env vars and toml fallbacks.

    The legacy code path was a hardcoded ``../../../.config/secrets.toml`` relative
    to the project file — which broke whenever the checkout location changed.
    This loader replaces that with: env first (the production-correct source),
    then well-known toml paths as a developer convenience.
    """

    toml_paths: list[Path] = field(
        default_factory=lambda: [Path.home() / ".config" / "secrets.toml"]
    )

    def add_toml_path(self, path: Path | str) -> SecretLoader:
        self.toml_paths.insert(0, Path(path))
        return self

    def get(self, key: str, *, required: bool = True) -> str | None:
        """Return the value for ``key``, or raise / return ``None`` if missing."""
        if (value := os.environ.get(key)) is not None:
            return value

        for path in self.toml_paths:
            if not path.is_file():
                continue
            try:
                data = toml.load(path)
            except (toml.TomlDecodeError, OSError):
                continue
            if key in data:
                return str(data[key])

        if not required:
            return None
        tried = [f"env[{key}]"] + [str(p) for p in self.toml_paths]
        raise SecretNotFoundError(
            f"Secret {key!r} not found. Tried: {', '.join(tried)}"
        )

    def export_to_env(self, key: str, *, required: bool = True) -> str | None:
        """Resolve ``key`` and write it into ``os.environ`` for libraries that
        read directly from the environment (e.g. the OpenAI SDK reads
        ``OPENAI_API_KEY``).
        """
        value = self.get(key, required=required)
        if value is not None:
            os.environ[key] = value
        return value
