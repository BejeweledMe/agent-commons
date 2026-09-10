"""Per-project board arrangement: positions and department frames.

The board layout is *operational* state, never canonical truth. It records
where the operator placed role cards and which cards they grouped into a
department frame. Losing the file costs an auto-layout, nothing more; the
ledger neither reads nor stores it (ADR 0020, ADR 0009: coordinates are not a
fact about the organisation).

The store lives outside the checkout under the collaboration state root,
partitioned by project path and workspace id like the live-preview registry,
and is written atomically with a compare-and-swap revision so two panels on
the same project cannot silently clobber each other's arrangement.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from agent_commons.core.canonical import canonical_json_bytes, loads_json_strict

BOARD_LAYOUT_SCHEMA = "agent_commons.board-layout.v1"
MAX_LAYOUT_BYTES = 262_144
MAX_FRAMES = 200
MAX_POSITIONS = 2_000
MAX_MEMBERS_PER_FRAME = 200
MAX_TITLE_BYTES = 120
COORDINATE_LIMIT = 1_000_000
SIZE_LIMIT = 100_000

_AGENT_ID = re.compile(r"^agent\.[0-7][0-9A-HJKMNP-TV-Z]{25}$")
_FRAME_ID = re.compile(r"^frame\.[a-f0-9]{8,32}$")
_BLUEPRINT_ID = re.compile(r"^[A-Za-z0-9._:-]{1,120}$")


class BoardLayoutError(Exception):
    """A typed refusal the route turns into a status and a stable code."""

    def __init__(self, code: str, status_code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def empty_layout() -> dict[str, Any]:
    return {"schema": BOARD_LAYOUT_SCHEMA, "revision": 0, "positions": {}, "frames": []}


def _int(value: object, field: str, *, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise BoardLayoutError(
            "invalid_board_layout", 422, f"{field} must be an integer in {minimum}..{maximum}"
        )
    return value


def _text(value: object, field: str, *, maximum: int) -> str:
    if not isinstance(value, str):
        raise BoardLayoutError("invalid_board_layout", 422, f"{field} must be a string")
    cleaned = "".join(ch for ch in value if ch.isprintable()).strip()
    if not cleaned or len(cleaned.encode("utf-8")) > maximum:
        raise BoardLayoutError(
            "invalid_board_layout", 422, f"{field} must be 1..{maximum} printable bytes"
        )
    return cleaned


def validate_layout_input(value: object) -> dict[str, Any]:
    """Return a normalized layout body (without revision) or raise a typed refusal."""

    if not isinstance(value, Mapping):
        raise BoardLayoutError("invalid_board_layout", 422, "layout must be an object")
    unknown = set(value) - {"positions", "frames", "expected_revision", "schema"}
    if unknown:
        raise BoardLayoutError(
            "invalid_board_layout", 422, "unsupported fields: " + ", ".join(sorted(unknown))
        )
    raw_positions = value.get("positions", {})
    if not isinstance(raw_positions, Mapping) or len(raw_positions) > MAX_POSITIONS:
        raise BoardLayoutError(
            "invalid_board_layout", 422, f"positions must map at most {MAX_POSITIONS} agent ids"
        )
    positions: dict[str, dict[str, int]] = {}
    for agent_id, raw in raw_positions.items():
        if not isinstance(agent_id, str) or _AGENT_ID.fullmatch(agent_id) is None:
            raise BoardLayoutError("invalid_board_layout", 422, "position key is not an agent id")
        if not isinstance(raw, Mapping) or set(raw) != {"x", "y"}:
            raise BoardLayoutError("invalid_board_layout", 422, "position must carry x and y")
        positions[agent_id] = {
            "x": _int(raw["x"], "x", minimum=-COORDINATE_LIMIT, maximum=COORDINATE_LIMIT),
            "y": _int(raw["y"], "y", minimum=-COORDINATE_LIMIT, maximum=COORDINATE_LIMIT),
        }
    raw_frames = value.get("frames", [])
    if not isinstance(raw_frames, list) or len(raw_frames) > MAX_FRAMES:
        raise BoardLayoutError(
            "invalid_board_layout", 422, f"frames must list at most {MAX_FRAMES}"
        )
    frames: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_frames:
        if not isinstance(raw, Mapping):
            raise BoardLayoutError("invalid_board_layout", 422, "frame must be an object")
        allowed = {"id", "title", "x", "y", "width", "height", "members", "blueprint_id"}
        if set(raw) - allowed or not {"id", "title", "x", "y", "width", "height"} <= set(raw):
            raise BoardLayoutError("invalid_board_layout", 422, "frame has unsupported fields")
        frame_id = raw["id"]
        if not isinstance(frame_id, str) or _FRAME_ID.fullmatch(frame_id) is None:
            raise BoardLayoutError("invalid_board_layout", 422, "frame id is malformed")
        if frame_id in seen:
            raise BoardLayoutError("invalid_board_layout", 422, "frame id repeats")
        seen.add(frame_id)
        members_raw = raw.get("members", [])
        if not isinstance(members_raw, list) or len(members_raw) > MAX_MEMBERS_PER_FRAME:
            raise BoardLayoutError("invalid_board_layout", 422, "frame members are unbounded")
        members: list[str] = []
        for member in members_raw:
            if not isinstance(member, str) or _AGENT_ID.fullmatch(member) is None:
                raise BoardLayoutError("invalid_board_layout", 422, "member is not an agent id")
            if member not in members:
                members.append(member)
        blueprint = raw.get("blueprint_id")
        if blueprint is not None and (
            not isinstance(blueprint, str) or _BLUEPRINT_ID.fullmatch(blueprint) is None
        ):
            raise BoardLayoutError("invalid_board_layout", 422, "blueprint_id is malformed")
        frames.append(
            {
                "id": frame_id,
                "title": _text(raw["title"], "title", maximum=MAX_TITLE_BYTES),
                "x": _int(raw["x"], "x", minimum=-COORDINATE_LIMIT, maximum=COORDINATE_LIMIT),
                "y": _int(raw["y"], "y", minimum=-COORDINATE_LIMIT, maximum=COORDINATE_LIMIT),
                "width": _int(raw["width"], "width", minimum=1, maximum=SIZE_LIMIT),
                "height": _int(raw["height"], "height", minimum=1, maximum=SIZE_LIMIT),
                "members": members,
                "blueprint_id": blueprint,
            }
        )
    return {"positions": positions, "frames": frames}


class BoardLayoutStore:
    """One bounded JSON document per (project path, workspace) under the state root."""

    def __init__(
        self, state_root: str | Path, *, project_root: str | Path, workspace_id: str
    ) -> None:
        state = Path(os.path.abspath(Path(state_root).expanduser()))
        project = Path(project_root).expanduser().resolve()
        if not workspace_id or state.resolve().is_relative_to(project):
            raise BoardLayoutError("board_layout_unavailable", 409, "board state root is unsafe")
        partition = hashlib.sha256(
            f"{hashlib.sha256(str(project).encode()).hexdigest()}:{workspace_id}".encode()
        ).hexdigest()
        self.root = state / "runtime" / "board-layouts" / partition
        self.path = self.root / "layout.json"

    def load(self) -> dict[str, Any]:
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return empty_layout()
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_mode & 0o077
                or info.st_size > MAX_LAYOUT_BYTES
            ):
                raise BoardLayoutError(
                    "board_layout_unavailable", 409, "board layout file is unsafe"
                )
            with os.fdopen(fd, "rb", closefd=False) as stream:
                raw = stream.read(MAX_LAYOUT_BYTES + 1)
        finally:
            os.close(fd)
        try:
            value = loads_json_strict(raw)
        except Exception as exc:
            raise BoardLayoutError(
                "board_layout_unavailable", 409, "board layout is unreadable"
            ) from exc
        if not isinstance(value, Mapping) or value.get("schema") != BOARD_LAYOUT_SCHEMA:
            raise BoardLayoutError("board_layout_unavailable", 409, "board layout schema mismatch")
        body = validate_layout_input(
            {"positions": value.get("positions", {}), "frames": value.get("frames", [])}
        )
        revision = value.get("revision")
        if type(revision) is not int or revision < 0:
            raise BoardLayoutError(
                "board_layout_unavailable", 409, "board layout revision is invalid"
            )
        return {"schema": BOARD_LAYOUT_SCHEMA, "revision": revision, **body}

    def save(self, value: object) -> dict[str, Any]:
        """Replace the layout when ``expected_revision`` matches the stored one."""

        if not isinstance(value, Mapping):
            raise BoardLayoutError("invalid_board_layout", 422, "layout must be an object")
        expected = value.get("expected_revision")
        if type(expected) is not int or expected < 0:
            raise BoardLayoutError(
                "invalid_board_layout", 422, "expected_revision must be an integer"
            )
        body = validate_layout_input(value)
        current = self.load()
        if current["revision"] != expected:
            raise BoardLayoutError(
                "board_revision_conflict",
                409,
                f"board layout is at revision {current['revision']}, not {expected}",
            )
        layout = {"schema": BOARD_LAYOUT_SCHEMA, "revision": expected + 1, **body}
        encoded = canonical_json_bytes(layout)
        if len(encoded) > MAX_LAYOUT_BYTES:
            raise BoardLayoutError("invalid_board_layout", 422, "board layout is too large")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, name = tempfile.mkstemp(prefix=".layout-", suffix=".json", dir=self.root)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(name, 0o600)
            os.replace(name, self.path)
        except Exception:
            try:
                os.unlink(name)
            except OSError:
                pass
            raise
        return layout


def register_board_routes(
    routes: Any,
    *,
    dependencies: list[object],
    store_factory: Callable[[], BoardLayoutStore],
    register_writes: bool,
) -> None:
    """Mount the board layout read and, for operator panels, its replace route.

    The replace is a POST like every other non-GET route of the panel: the
    route tables and their invariant tests enumerate POST pairs only.
    """

    def _refusal(exc: BoardLayoutError) -> Response:
        return JSONResponse(
            {"error": {"code": exc.code, "message": str(exc)}}, status_code=exc.status_code
        )

    @routes.get("/api/board", dependencies=dependencies)
    async def read_board_layout() -> Response:
        try:
            layout = store_factory().load()
        except BoardLayoutError as exc:
            return _refusal(exc)
        return JSONResponse(layout, headers={"Cache-Control": "no-store"})

    if not register_writes:
        return

    @routes.post("/api/board", dependencies=dependencies)
    async def replace_board_layout(request: Request) -> Response:
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > MAX_LAYOUT_BYTES:
                return _refusal(
                    BoardLayoutError("invalid_board_layout", 413, "board layout is too large")
                )
        try:
            payload = loads_json_strict(bytes(content))
        except Exception:
            return _refusal(
                BoardLayoutError("invalid_board_layout", 422, "board layout is not JSON")
            )
        try:
            layout = store_factory().save(payload)
        except BoardLayoutError as exc:
            return _refusal(exc)
        return JSONResponse(layout, headers={"Cache-Control": "no-store"})
