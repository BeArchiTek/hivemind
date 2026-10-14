# -*- coding: utf-8 -*-
"""Guidance error payload helpers for refused tool calls.

This module holds the closed set of ``error_kind`` values, the builder of the
``remediation`` object, the ``refusal`` helper every refusal site uses, and
the conversion helpers that turn a refusal dict into an MCP error result.
See ``specs/001-tool-guidance-remediation/contracts/guidance-error-payload.md``.

Every string a payload carries comes from literals in this repository, tool
names, argument names, permission names or the token's own space identifiers,
never from exception text (FR-010).
"""

from __future__ import annotations

import json
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping

from mcp.types import CallToolResult, TextContent

# Key of the tool outcome record written into the ASGI request state so the
# audit and metrics middlewares can read it (FR-030).
TOOL_OUTCOME_STATE_KEY = "live_mem.tool_outcome"

# Statuses that are refusals and travel as MCP error results (FR-026).
REFUSAL_STATUSES = frozenset({"error", "forbidden"})


class ErrorKind(StrEnum):
    """Closed set of refusal causes (FR-002)."""

    AUTHENTICATION_MISSING = "authentication_missing"
    TOKEN_SCOPE_CHANGED = "token_scope_changed"
    INVALID_SPACE_ID = "invalid_space_id"
    SPACE_ACCESS_DENIED = "space_access_denied"
    PERMISSION_INSUFFICIENT = "permission_insufficient"
    MISSING_ARGUMENT = "missing_argument"
    INVALID_ARGUMENT = "invalid_argument"
    REQUIRED_PRIOR_STEP_MISSING = "required_prior_step_missing"
    OPERATION_IN_PROGRESS = "operation_in_progress"
    WRITE_ROUTE_REFUSED = "write_route_refused"
    INTERNAL_FAILURE = "internal_failure"
    # Reserved: produced only by the operator tool guard (specification 002).
    OPERATOR_TOOLS_REFUSED = "operator_tools_refused"
    AUDIENCE_UNRESOLVED = "audience_unresolved"
    # Assigned by the conversion point to a refusal without error_kind.
    UNCLASSIFIED_REFUSAL = "unclassified_refusal"


# Fixed remediation sentences shared by several refusal sites.
INTERNAL_FAILURE_REASON = (
    "The server logged the failure under the request identifier; report it to "
    "an operator."
)
UNCLASSIFIED_REFUSAL_REASON = (
    "This refusal has no classification; report it to an operator."
)
OPERATOR_TOOLS_REFUSED_REASON = (
    "An operator performs this action through the admin console or the CLI "
    "with an operator token."
)

# Agent tools that replace a hidden operator tool, used to complete the
# ``reason`` of a permission refusal on that operator tool (FR-021). Operator
# tools absent from this map name no agent tool.
OPERATOR_TOOL_AGENT_ALTERNATIVES: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "mid_write": ("short_note", "mid_consolidate"),
        "bank_write": ("short_note", "mid_consolidate"),
    }
)


def remediation(
    *,
    reason: str,
    retryable: bool,
    next_tool: str | None = None,
    arguments: Mapping[str, Any] | None = None,
    missing_arguments: list[str] | tuple[str, ...] | None = None,
    required_prior_step: str | None = None,
) -> dict[str, Any]:
    """Build the ``remediation`` object (FR-003).

    ``reason`` and ``retryable`` are always present; the other fields are
    present only when known.
    """
    if not isinstance(reason, str) or not reason:
        raise ValueError("remediation reason must be a non-empty string")
    if not isinstance(retryable, bool):
        raise ValueError("remediation retryable must be a boolean")
    out: dict[str, Any] = {}
    if next_tool is not None:
        out["next_tool"] = next_tool
    if arguments is not None:
        out["arguments"] = dict(arguments)
    if missing_arguments is not None:
        out["missing_arguments"] = list(missing_arguments)
    if required_prior_step is not None:
        out["required_prior_step"] = required_prior_step
    out["reason"] = reason
    out["retryable"] = retryable
    return out


def refusal(
    message: str,
    error_kind: ErrorKind | str,
    *,
    status: str = "error",
    remediation: Mapping[str, Any],
    **extra: Any,
) -> dict[str, Any]:
    """Return a Guidance error payload with ``status`` and ``message`` first.

    ``extra`` carries the optional top-level fields (``required_permission``,
    ``current_permissions``, ``accessible_spaces``) and any existing per-tool
    field such as ``reason`` or ``recovery_required``.
    """
    payload: dict[str, Any] = {"status": status, "message": message}
    payload.update(extra)
    payload["error_kind"] = str(ErrorKind(error_kind))
    payload["remediation"] = dict(remediation)
    return payload


def write_route_refused(
    message: str, reason: str, *, extra_fields: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """A ``write_route_refused`` payload naming the operator workflow (FR-008).

    ``extra_fields`` carries existing per-tool fields such as ``reason`` codes.
    """
    return refusal(
        message,
        ErrorKind.WRITE_ROUTE_REFUSED,
        remediation=remediation(reason=reason, retryable=False),
        **dict(extra_fields or {}),
    )


def argument_refusal(
    message: str,
    *,
    kind: ErrorKind,
    arguments: list[str],
    reason: str,
    values: Mapping[str, Any] | None = None,
    extra_fields: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """A ``missing_argument`` or ``invalid_argument`` payload (FR-006)."""
    return refusal(
        message,
        kind,
        remediation=remediation(
            reason=reason,
            retryable=True,
            missing_arguments=arguments,
            arguments=values,
        ),
        **dict(extra_fields or {}),
    )


def confirm_required(message: str, action: str, **extra: Any) -> dict[str, Any]:
    """The ``confirm=True`` refusal shared by the destructive tools."""
    return argument_refusal(
        message,
        kind=ErrorKind.MISSING_ARGUMENT,
        arguments=["confirm"],
        values={"confirm": True},
        reason=f"Pass confirm=true to {action}; the action cannot be undone.",
        **extra,
    )


def canonical_tool_name(name: str) -> str:
    """Resolve an alias through ``TOOL_EXPOSURES``; ``unknown`` outside the registry."""
    from .exposure import exposure_by_registered_name

    entry = exposure_by_registered_name().get(name)
    return entry.canonical_name if entry is not None else "unknown"


def is_refusal(result: Any) -> bool:
    """Whether a handler result is a refusal (top-level ``status`` only)."""
    return isinstance(result, dict) and result.get("status") in REFUSAL_STATUSES


def classify_refusal(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``payload`` with ``error_kind`` and ``remediation`` present.

    A refusal that reaches the conversion point without ``error_kind`` is a
    defect indicator; it receives ``unclassified_refusal`` so the counter
    and the replay test make it visible.
    """
    out = dict(payload)
    if not isinstance(out.get("error_kind"), str) or out["error_kind"] not in {
        kind.value for kind in ErrorKind
    }:
        out["error_kind"] = ErrorKind.UNCLASSIFIED_REFUSAL.value
    if not isinstance(out.get("remediation"), dict):
        out["remediation"] = remediation(
            reason=UNCLASSIFIED_REFUSAL_REASON
            if out["error_kind"] == ErrorKind.UNCLASSIFIED_REFUSAL.value
            else INTERNAL_FAILURE_REASON,
            retryable=False,
        )
    return out


def complete_reason_for_call(name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Complete ``remediation.reason`` with facts about the called name.

    - An alias call names its canonical tool (FR-020, Edge Cases).
    - A permission refusal on a hidden operator tool names the agent tool that
      replaces it where one exists, and nothing else about hidden tools
      (FR-021).
    """
    canonical = canonical_tool_name(name)
    out = dict(payload)
    rem = dict(out.get("remediation") or {})
    reason = str(rem.get("reason", ""))
    if (
        out.get("error_kind") == ErrorKind.PERMISSION_INSUFFICIENT.value
        and canonical in OPERATOR_TOOL_AGENT_ALTERNATIVES
    ):
        alternatives = OPERATOR_TOOL_AGENT_ALTERNATIVES[canonical]
        reason = (
            f"{reason} {canonical} is an operator tool; agents write with "
            f"{alternatives[0]} and consolidate with {alternatives[1]}."
        ).strip()
    if canonical not in ("unknown", name):
        reason = f"{reason} Canonical tool: {canonical}.".strip()
    if reason != rem.get("reason"):
        rem["reason"] = reason
        out["remediation"] = rem
    return out


def to_call_tool_result(
    name: str, result: Any, *, is_error: bool
) -> CallToolResult | None:
    """Convert a refusal dict into the MCP error result (FR-026).

    Returns ``None`` for every result that is not a refusal so the caller
    keeps the SDK conversion. The payload travels twice: as JSON text and as
    ``structured_content``.
    """
    if not is_refusal(result):
        return None
    payload = complete_reason_for_call(name, classify_refusal(result))
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
        structured_content=payload,
        is_error=is_error,
    )


def payload_from_validation_error(name: str, exc: Any) -> dict[str, Any]:
    """Build the payload for an input-schema failure from field names only.

    Reads ``err["loc"]`` and ``err["type"]`` of each pydantic error; the
    message text and the rejected values are never copied (FR-010).
    """
    fields: list[str] = []
    types: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(part) for part in err.get("loc", ()))
        if loc and loc not in fields:
            fields.append(loc)
        types.append(str(err.get("type", "")))
    fields.sort()
    missing = all(t == "missing" for t in types) if types else False
    kind = ErrorKind.MISSING_ARGUMENT if missing else ErrorKind.INVALID_ARGUMENT
    names = ", ".join(fields) if fields else "arguments"
    if kind is ErrorKind.MISSING_ARGUMENT:
        reason = (
            f"Pass the required argument(s) {names} to {name}; the tool "
            "description states the format of each argument."
        )
    else:
        reason = (
            f"Pass {names} to {name} with the type and format stated in the "
            "tool description, then retry."
        )
    return refusal(
        f"Invalid arguments for {name}: {names}",
        kind,
        remediation=remediation(
            reason=reason, retryable=True, missing_arguments=fields or None
        ),
    )
