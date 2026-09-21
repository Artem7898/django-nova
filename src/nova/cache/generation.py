"""Optional shared generation capability for synchronous cache backends."""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Protocol, runtime_checkable
from uuid import uuid4

from nova.core.exceptions import NovaCacheError


class GenerationUnavailableError(NovaCacheError):
    """A generation could not be read or its replacement acknowledged."""


class GenerationWriter(Protocol):
    """One acknowledged write attempt; never replay an applied command.

    A lost response must raise. Retrying the entire Nova operation creates
    a fresh token. Custom implementations must preserve this distinction.
    """

    def write(self, key: str, token: bytes, *, only_if_absent: bool) -> bool: ...


@runtime_checkable
class GenerationBackend(Protocol):
    """Optional capability; wrappers must explicitly forward both methods."""

    def get_generation(self, scope: str) -> str: ...

    def rotate_generation(self, scope: str) -> str: ...


@runtime_checkable
class BatchGenerationBackend(GenerationBackend, Protocol):
    """Optional read optimization; return a valid token for every scope.

    Missing metadata must use the same safe initialization as get_generation.
    Partial or failed reads must raise, never reuse a previous local snapshot.
    Wrappers opt in by explicitly forwarding this method too.
    """

    def get_generations(self, scopes: tuple[str, ...]) -> dict[str, str]: ...


def generation_scope(model_name: str, database: str) -> str:
    """A short model name matches Nova's existing conservative alias semantics."""
    return json.dumps([model_name, database], ensure_ascii=True, separators=(",", ":"))


def generation_key(scope: str) -> str:
    return f"nova:qsg:v1:{sha256(scope.encode('utf-8')).hexdigest()}"


def new_generation() -> str:
    # Never restart a counter at zero after eviction: old result keys can survive.
    return uuid4().hex


def validate_generation(raw: object) -> str:
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("ascii")
        except UnicodeDecodeError as exc:
            raise GenerationUnavailableError("Invalid shared cache generation") from exc
    if (
        not isinstance(raw, str)
        or len(raw) != 32
        or any(char not in "0123456789abcdef" for char in raw)
    ):
        # Do not blindly overwrite corruption: a concurrent commit may already
        # have installed a valid generation. An explicit rotation repairs it.
        raise GenerationUnavailableError("Invalid shared cache generation")
    return raw
