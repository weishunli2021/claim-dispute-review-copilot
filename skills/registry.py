"""Deterministic registry of available Skills.

No LLM-based skill selection here, or anywhere in this stage -- an agent
or a human picks a skill by name, and this registry only stores and
retrieves already-built skill callables alongside their SkillMetadata.
Registration is explicit (skills/__init__.py registers the four skills
built in this stage into DEFAULT_REGISTRY) rather than automatic
discovery, so the set of available skills stays easy to audit.
"""

from __future__ import annotations

from typing import Callable

from skills.base import SkillMetadata, SkillResult

SkillCallable = Callable[..., SkillResult]


class DuplicateSkillError(RuntimeError):
    """Raised when registering a skill whose (name, version) pair is
    already registered. Registering the SAME name at a NEW version is
    allowed -- multiple versions may coexist -- but re-registering an
    identical (name, version) pair is treated as a caller bug."""


class SkillNotFoundError(RuntimeError):
    """Raised when looking up a skill name (or a specific version of a
    known name) that isn't registered."""


def _version_key(version: str) -> tuple[int, ...]:
    """Sort key for dotted version strings (e.g. "1.10.0" > "1.9.0")."""
    return tuple(int(part) for part in version.split("."))


class SkillRegistry:
    """An in-memory, deterministic registry of skill (metadata, callable) pairs."""

    def __init__(self) -> None:
        self._skills: dict[str, dict[str, tuple[SkillMetadata, SkillCallable]]] = {}

    def register(self, metadata: SkillMetadata, func: SkillCallable) -> None:
        """Register a skill callable under its metadata's (name, version).

        Raises DuplicateSkillError if this exact (name, version) pair is
        already registered.
        """
        versions = self._skills.setdefault(metadata.name, {})
        if metadata.version in versions:
            raise DuplicateSkillError(
                f"Skill {metadata.name!r} version {metadata.version!r} is already registered."
            )
        versions[metadata.version] = (metadata, func)

    def get(self, name: str, version: str | None = None) -> tuple[SkillMetadata, SkillCallable]:
        """Return (metadata, callable) for `name` at `version` (or the
        highest registered version if version is omitted).

        Raises SkillNotFoundError if the name, or that specific version
        of it, isn't registered.
        """
        versions = self._skills.get(name)
        if not versions:
            raise SkillNotFoundError(f"No skill registered under name {name!r}.")
        resolved_version = version if version is not None else max(versions, key=_version_key)
        if resolved_version not in versions:
            raise SkillNotFoundError(f"Skill {name!r} has no registered version {resolved_version!r}.")
        return versions[resolved_version]

    def list_skills(self) -> list[SkillMetadata]:
        """Return metadata for every registered skill, one entry per
        (name, version), sorted by (name, version) for stable ordering."""
        entries = [
            metadata
            for versions in self._skills.values()
            for metadata, _func in versions.values()
        ]
        return sorted(entries, key=lambda m: (m.name, _version_key(m.version)))
