"""Bounded, immutable repository snapshots for delegated MCP workers."""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from pathlib import Path
from typing import Any

from agent_commons.errors import (
    ConfigurationError,
    LifecycleConflictError,
    SecurityPolicyError,
    ValidationError,
)
from agent_commons.runtime import resolve_trusted_executable
from agent_commons.services import CommonsManager

_SENSITIVE_NAMES = {".env", ".env.local", "credentials", "credentials.json"}


class _OversizedScopedFile(ConfigurationError):
    pass


def _is_outside_review_scope(path: Path) -> bool:
    return (
        path.is_absolute()
        or ".." in path.parts
        or path.name in _SENSITIVE_NAMES
        or path.name.startswith(".env.")
        or path.suffix.lower() in {".key", ".pem", ".p12", ".pfx"}
    )


class ScopedRepoReader:
    """Immutable, bounded, no-symlink text view for delegated reviewers."""

    def __init__(self, manager: CommonsManager, *, git_executable: str = "/usr/bin/git") -> None:
        self.manager = manager
        self.root = manager.repo_root.resolve()
        self.policy = manager.policy
        self.files: dict[str, tuple[str, int]] = {}
        self.registered_files: dict[str, tuple[str, int]] = {}
        total = 0
        self.git_executable = resolve_trusted_executable(
            git_executable,
            workspace_root=self.root,
        )
        try:
            result = subprocess.run(
                (
                    self.git_executable,
                    "-C",
                    str(self.root),
                    "ls-files",
                    "--cached",
                    "--others",
                    "--exclude-standard",
                    "-z",
                ),
                check=True,
                capture_output=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ConfigurationError("scoped reviewer could not enumerate Git files") from exc
        if len(result.stdout) > 4 * 1024 * 1024:
            raise ConfigurationError("scoped reviewer Git file list exceeds 4 MiB")
        try:
            names = sorted(item for item in result.stdout.decode("utf-8").split("\0") if item)
        except UnicodeDecodeError as exc:
            raise ConfigurationError("scoped reviewer requires UTF-8 Git paths") from exc
        for relative in names:
            normalized = Path(relative)
            if _is_outside_review_scope(normalized):
                continue
            try:
                digest, size = self._digest(normalized)
            except _OversizedScopedFile:
                continue
            except LifecycleConflictError:
                # A tracked path may have been replaced with a final or parent
                # symlink after it entered the Git index. Exclude it from the
                # immutable reviewer snapshot without ever following it.
                continue
            total += size
            if len(self.files) >= 5_000 or total > 64 * 1024 * 1024:
                raise ConfigurationError("scoped reviewer workspace exceeds safe snapshot limits")
            self.files[normalized.as_posix()] = (digest, size)

    def assert_unchanged(self) -> None:
        """Fail before a canonical result if any visible subject file moved."""

        current = ScopedRepoReader(
            self.manager,
            git_executable=self.git_executable,
        )
        if current.files != self.files:
            raise LifecycleConflictError(
                "delegated workspace changed after reviewer snapshot creation"
            )
        for relative, frozen in self.registered_files.items():
            if self._digest(self._safe_candidate(relative)) != frozen:
                raise LifecycleConflictError(
                    "registered review artifact changed after it was inspected"
                )

    def _safe_candidate(self, relative: str) -> Path:
        normalized = Path(relative)
        if _is_outside_review_scope(normalized):
            raise ValidationError("artifact source path is outside the safe review scope")
        return normalized

    def _open_regular(self, relative: Path) -> int:
        """Open one repository-relative file without following any path symlink."""

        if (
            not relative.parts
            or relative.is_absolute()
            or ".." in relative.parts
            or not hasattr(os, "O_DIRECTORY")
            or not hasattr(os, "O_NOFOLLOW")
            or os.open not in os.supports_dir_fd
        ):
            raise LifecycleConflictError(
                "scoped reviewer path cannot be opened with no-symlink guarantees"
            )
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        file_flags = os.O_RDONLY | os.O_NOFOLLOW
        descriptors: list[int] = []
        try:
            current = os.open(self.root, directory_flags)
            descriptors.append(current)
            for component in relative.parts[:-1]:
                current = os.open(component, directory_flags, dir_fd=current)
                descriptors.append(current)
            descriptor = os.open(relative.parts[-1], file_flags, dir_fd=current)
            return descriptor
        except OSError as exc:
            raise LifecycleConflictError("scoped reviewer path must not traverse symlinks") from exc
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    def _read_bytes(self, relative: Path) -> bytes:
        descriptor = self._open_regular(relative)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise ConfigurationError("scoped reviewer files must be regular")
            if metadata.st_size > 1_048_576:
                raise _OversizedScopedFile("scoped reviewer files must be at most 1 MiB")
            chunks: list[bytes] = []
            remaining = metadata.st_size + 1
            while remaining:
                chunk = os.read(descriptor, min(remaining, 64 * 1024))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            body = b"".join(chunks)
            if len(body) != metadata.st_size or len(body) > 1_048_576:
                raise LifecycleConflictError("scoped reviewer file changed while it was read")
            return body
        finally:
            os.close(descriptor)

    def _digest(self, relative: Path) -> tuple[str, int]:
        body = self._read_bytes(relative)
        return hashlib.sha256(body).hexdigest(), len(body)

    def _review_content(self, content: str, *, context: str) -> tuple[str, list[dict[str, Any]]]:
        """Redact unsafe lines without quarantining the remaining review surface."""

        blocked_by_line: dict[int, list[Any]] = {}
        for start_line, end_line, finding in self.policy.scan_text_lines(content):
            if finding.classification not in self.policy.blocked_classifications:
                continue
            for line_number in range(start_line, end_line + 1):
                blocked_by_line.setdefault(line_number, []).append(finding)

        rendered: list[str] = []
        redactions: list[dict[str, Any]] = []
        for line_number, line in enumerate(content.splitlines(keepends=True), start=1):
            blocked = blocked_by_line.get(line_number, [])
            if not blocked:
                rendered.append(line)
                continue
            ending = (
                "\r\n"
                if line.endswith("\r\n")
                else "\n"
                if line.endswith("\n")
                else "\r"
                if line.endswith("\r")
                else ""
            )
            rendered.append("[agent-commons redacted source line]" + ending)
            redactions.append(
                {
                    "line": line_number,
                    "categories": sorted({finding.category for finding in blocked}),
                    "classifications": sorted(
                        {finding.classification.value for finding in blocked}
                    ),
                }
            )
        safe_content = "".join(rendered)
        # Retain the whole-document fail-closed check for any pattern that spans
        # line boundaries or survives the bounded line redactions.
        self.policy.assert_safe(safe_content, context=context)
        return safe_content, redactions

    def list_files(self, *, prefix: str = "", max_items: int = 200) -> list[dict[str, Any]]:
        if (
            not isinstance(max_items, int)
            or isinstance(max_items, bool)
            or not 1 <= max_items <= 500
        ):
            raise ValidationError("max_items must be between 1 and 500")
        normalized = prefix.strip().replace("\\", "/")
        if normalized.startswith("/") or ".." in Path(normalized).parts:
            raise ValidationError("workspace prefix must remain relative")
        return [
            {"path": path, "sha256": digest, "size_bytes": size}
            for path, (digest, size) in sorted(self.files.items())
            if path.startswith(normalized)
        ][:max_items]

    def read(self, path: str, *, expected_sha256: str | None = None) -> dict[str, Any]:
        normalized = Path(path)
        if normalized.is_absolute() or ".." in normalized.parts:
            raise ValidationError("workspace path must remain relative")
        relative = normalized.as_posix()
        frozen = self.files.get(relative)
        if frozen is None:
            raise LifecycleConflictError("workspace file is outside the delegated snapshot")
        body = self._read_bytes(normalized)
        digest, size = hashlib.sha256(body).hexdigest(), len(body)
        if (digest, size) != frozen:
            raise LifecycleConflictError("workspace file changed after reviewer snapshot creation")
        if expected_sha256 is not None and expected_sha256 != digest:
            raise LifecycleConflictError("workspace file does not match expected_sha256")
        try:
            content = body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationError("scoped reviewer can read UTF-8 text files only") from exc
        content, redactions = self._review_content(content, context="scoped reviewer file content")
        return {
            "path": relative,
            "sha256": digest,
            "content": content,
            "redactions": redactions,
        }

    def search(
        self, query: str, *, prefix: str = "", max_matches: int = 100
    ) -> list[dict[str, Any]]:
        if not isinstance(query, str) or not query or len(query) > 256 or "\x00" in query:
            raise ValidationError("search query must contain 1 to 256 safe characters")
        if (
            not isinstance(max_matches, int)
            or isinstance(max_matches, bool)
            or not 1 <= max_matches <= 200
        ):
            raise ValidationError("max_matches must be between 1 and 200")
        results: list[dict[str, Any]] = []
        for item in self.list_files(prefix=prefix, max_items=500):
            try:
                content = self.read(item["path"])["content"]
            except (SecurityPolicyError, ValidationError):
                continue
            for line_number, line in enumerate(content.splitlines(), start=1):
                if query in line:
                    results.append(
                        {"path": item["path"], "line": line_number, "text": line[:1_000]}
                    )
                    if len(results) >= max_matches:
                        return results
        return results

    def read_registered_artifact(
        self,
        *,
        source_path: str,
        expected_revision: str,
        expected_size: int,
    ) -> dict[str, Any]:
        """Read one exact task artifact, including an otherwise ignored evidence file."""

        if not expected_revision.startswith("sha256:"):
            raise ValidationError("registered artifact revision must use sha256")
        expected_digest = expected_revision.removeprefix("sha256:")
        if len(expected_digest) != 64 or any(
            character not in "0123456789abcdef" for character in expected_digest
        ):
            raise ValidationError("registered artifact revision is invalid")
        if (
            isinstance(expected_size, bool)
            or not isinstance(expected_size, int)
            or not 0 <= expected_size <= 1_048_576
        ):
            raise ValidationError("registered artifact size exceeds the review limit")
        relative_path = self._safe_candidate(source_path)
        body = self._read_bytes(relative_path)
        digest, size = hashlib.sha256(body).hexdigest(), len(body)
        if digest != expected_digest or size != expected_size:
            raise LifecycleConflictError(
                "registered artifact bytes do not match their immutable manifest"
            )
        try:
            content = body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationError("scoped reviewer can read UTF-8 artifacts only") from exc
        content, redactions = self._review_content(
            content, context="scoped registered artifact content"
        )
        relative = Path(source_path).as_posix()
        self.registered_files[relative] = (digest, size)
        return {
            "path": relative,
            "sha256": digest,
            "content": content,
            "redactions": redactions,
        }
