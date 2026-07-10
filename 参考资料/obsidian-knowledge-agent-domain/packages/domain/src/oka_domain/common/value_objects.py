from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
import re
import uuid

from oka_domain.exceptions import ValidationError

_IDENTIFIER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]*$")
_SHA256_RE = re.compile(r"^[A-Fa-f0-9]{64}$")
_IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
_CAPABILITY_RE = re.compile(r"^[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)+$")


@dataclass(frozen=True, slots=True)
class Identifier:
    value: str

    def __post_init__(self) -> None:
        if not (3 <= len(self.value) <= 128) or not _IDENTIFIER_RE.fullmatch(self.value):
            raise ValidationError(f"Invalid identifier: {self.value!r}")

    @classmethod
    def new(cls, prefix: str) -> Identifier:
        return cls(f"{prefix}_{uuid.uuid4().hex}")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class NoteId(Identifier):
    @classmethod
    def new(cls, prefix: str = "note") -> NoteId:
        return cls(f"{prefix}_{uuid.uuid4().hex}")


@dataclass(frozen=True, slots=True)
class TaskId(Identifier):
    @classmethod
    def new(cls, prefix: str = "task") -> TaskId:
        return cls(f"{prefix}_{uuid.uuid4().hex}")


@dataclass(frozen=True, slots=True)
class ProjectId(Identifier):
    @classmethod
    def new(cls, prefix: str = "project") -> ProjectId:
        return cls(f"{prefix}_{uuid.uuid4().hex}")


@dataclass(frozen=True, slots=True)
class TagId(Identifier):
    @classmethod
    def new(cls, prefix: str = "tag") -> TagId:
        return cls(f"{prefix}_{uuid.uuid4().hex}")


@dataclass(frozen=True, slots=True)
class OperationId(Identifier):
    @classmethod
    def new(cls, prefix: str = "op") -> OperationId:
        return cls(f"{prefix}_{uuid.uuid4().hex}")


@dataclass(frozen=True, slots=True)
class PlanId(Identifier):
    @classmethod
    def new(cls, prefix: str = "plan") -> PlanId:
        return cls(f"{prefix}_{uuid.uuid4().hex}")


@dataclass(frozen=True, slots=True)
class EventId(Identifier):
    @classmethod
    def new(cls, prefix: str = "event") -> EventId:
        return cls(f"{prefix}_{uuid.uuid4().hex}")


@dataclass(frozen=True, slots=True)
class Sha256Hash:
    value: str

    def __post_init__(self) -> None:
        if not _SHA256_RE.fullmatch(self.value):
            raise ValidationError("SHA-256 hash must contain exactly 64 hexadecimal characters")

    def __str__(self) -> str:
        return self.value.lower()


@dataclass(frozen=True, slots=True)
class IdempotencyKey:
    value: str

    def __post_init__(self) -> None:
        if not (8 <= len(self.value) <= 256) or not _IDEMPOTENCY_RE.fullmatch(self.value):
            raise ValidationError(f"Invalid idempotency key: {self.value!r}")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class CapabilityId:
    value: str

    def __post_init__(self) -> None:
        if not (3 <= len(self.value) <= 128) or not _CAPABILITY_RE.fullmatch(self.value):
            raise ValidationError(f"Invalid capability id: {self.value!r}")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class VaultPath:
    value: str

    def __post_init__(self) -> None:
        value = self.value.strip()
        if not value or len(value) > 1024:
            raise ValidationError("Vault path must contain 1-1024 characters")
        if value.startswith("/") or "\\" in value or "\x00" in value:
            raise ValidationError(f"Vault path must be a POSIX relative path: {value!r}")
        parts = PurePosixPath(value).parts
        if any(part in {".", "..", ""} for part in parts):
            raise ValidationError(f"Vault path cannot contain traversal segments: {value!r}")
        object.__setattr__(self, "value", str(PurePosixPath(value)))

    @property
    def name(self) -> str:
        return PurePosixPath(self.value).name

    @property
    def stem(self) -> str:
        return PurePosixPath(self.value).stem

    @property
    def suffix(self) -> str:
        return PurePosixPath(self.value).suffix

    @property
    def parent(self) -> VaultPath | None:
        parent = str(PurePosixPath(self.value).parent)
        return None if parent == "." else VaultPath(parent)

    @property
    def is_markdown(self) -> bool:
        return self.suffix.lower() == ".md"

    def join(self, *parts: str) -> VaultPath:
        return VaultPath(str(PurePosixPath(self.value).joinpath(*parts)))

    def with_name(self, name: str) -> VaultPath:
        return VaultPath(str(PurePosixPath(self.value).with_name(name)))

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class TagName:
    value: str

    def __post_init__(self) -> None:
        normalized = self.normalize(self.value)
        if not normalized:
            raise ValidationError("Tag name cannot be empty")
        if len(normalized) > 256:
            raise ValidationError("Tag name cannot exceed 256 characters")
        object.__setattr__(self, "value", normalized)

    @staticmethod
    def normalize(raw: str) -> str:
        value = raw.strip().lstrip("#").strip()
        value = re.sub(r"\s+", "-", value)
        value = re.sub(r"-{2,}", "-", value)
        value = re.sub(r"/{2,}", "/", value)
        value = value.strip("-/")
        if any(char in value for char in "#[]|^\n\r\t"):
            raise ValidationError(f"Tag contains unsupported characters: {raw!r}")
        return value.casefold()

    def __str__(self) -> str:
        return self.value
