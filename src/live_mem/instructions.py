# -*- coding: utf-8 -*-
"""Server instructions sent to an agent at initialize.

This module holds the rule records (identifier, sentence, source document)
built from the memory-bank rules and ``render_instructions()``, which joins
them in the order ``specs/001-tool-guidance-remediation/contracts/server-instructions.md``
fixes (FR-013). The text is the same for every token (FR-016) and names no
space identifier, token, endpoint or deployment value (FR-014): where the
project instruction block says "space {SPACE}", the instructions say "the
space the token works in".

Items 1 and 2 come first so a client that truncates the text still shows the
start-of-task calls and the write discipline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from .core.live import VALID_CATEGORIES

_CATEGORIES = ", ".join(VALID_CATEGORIES)


@dataclass(frozen=True)
class RuleRecord:
    """One rule of the instructions and the document that states it."""

    identifier: str
    sentence: str
    source_document: str
    # Phrases the contract test finds in the rendered text.
    markers: tuple[str, ...]
    # Phrases the contract test finds in each source document (case-insensitive).
    source_markers: Mapping[str, tuple[str, ...]] = field(
        default_factory=lambda: MappingProxyType({})
    )


RULES: tuple[RuleRecord, ...] = (
    RuleRecord(
        identifier="start_of_task",
        sentence=(
            "Start of every task: before changing files, tests, external state "
            "or project direction, call space_rules, mid_read_all and short_read "
            "on the space the token works in, and read the returned content to "
            "identify the current focus. If any of these calls fails, times out "
            "or returns a status other than ok, stop before any mutation; do not "
            "substitute local memory or another memory endpoint."
        ),
        source_document="docs/AGENT_MEMORY_SETUP.md",
        markers=("space_rules", "mid_read_all", "short_read", "stop before"),
        source_markers=MappingProxyType(
            {
                "docs/AGENT_MEMORY_SETUP.md": (
                    "space_rules",
                    "mid_read_all",
                    "short_read",
                    "stop before mutation",
                ),
                "RULES/live-mem.standard.memory.bank.md": ("mid_read_all",),
            }
        ),
    ),
    RuleRecord(
        identifier="write_discipline",
        sentence=(
            "Write discipline: during work, write concise atomic notes with "
            f"short_note, only for durable facts, in one of the categories "
            f"{_CATEGORIES}. The agent identity comes from the token: never pass "
            "or invent an agent identity. Agents never write the bank directly "
            "and never edit mid-memory files during normal agent operation; "
            "only consolidation writes the bank. Never put tokens, endpoints or "
            "secrets in notes, instructions, long memory, commits or logs."
        ),
        source_document="docs/AGENT_MEMORY_SETUP.md",
        markers=("short_note", *VALID_CATEGORIES, "identity", "never write the bank", "secrets"),
        source_markers=MappingProxyType(
            {
                "docs/AGENT_MEMORY_SETUP.md": (
                    "short_note",
                    *VALID_CATEGORIES,
                    "agent identity",
                    "secrets",
                ),
                "RULES/live-mem.standard.memory.bank.md": (
                    "short_note",
                    *VALID_CATEGORIES,
                    "write directly to the bank",
                ),
            }
        ),
    ),
    RuleRecord(
        identifier="end_of_block",
        sentence=(
            "End of a meaningful work block: write one summary short_note, ask "
            "for or confirm user validation, then call mid_consolidate at most "
            "once and return to the user without polling. The acknowledgement "
            "carries next_action return_to_user_without_polling: do not read mid "
            "memory expecting the job's result and do not submit a second job "
            "because the bank has not changed yet. bank_consolidation_status is "
            "only for an explicit status request from the user."
        ),
        source_document="docs/AGENT_MEMORY_SETUP.md",
        markers=("mid_consolidate", "at most once", "without polling"),
        source_markers=MappingProxyType(
            {
                "docs/AGENT_MEMORY_SETUP.md": (
                    "mid_consolidate",
                    "at most once",
                    "without polling",
                    "bank_consolidation_status",
                ),
                "RULES/live-mem.standard.memory.bank.md": (
                    "mid_consolidate",
                    "at most once",
                    "without polling",
                ),
            }
        ),
    ),
    RuleRecord(
        identifier="permission_model",
        sentence=(
            "Permission model: the four levels are inclusive, admin includes "
            "manage, manage includes write, write includes read. A token views "
            "only the tools its permission allows; operator tools are not "
            "advertised and a refused call says so. system_whoami shows the "
            "token's permissions and accessible spaces. A token with no space "
            "needs an invitation from a manager (space_invite_token) before any "
            "space call succeeds."
        ),
        source_document="docs/MCP_TOOLS_SPEC.md",
        markers=("admin", "manage", "write", "read", "system_whoami", "invitation"),
        source_markers=MappingProxyType(
            {"docs/MCP_TOOLS_SPEC.md": ("admin", "manage", "write", "read", "system_whoami")}
        ),
    ),
    RuleRecord(
        identifier="long_memory",
        sentence=(
            "Long memory: use long_query for historical context, prior "
            "incidents, runbooks, decisions and cross-document relationships, "
            "then re-read the referenced canonical file before acting. Long "
            "memory is derived and non-authoritative; repository files are the "
            "final authority. Do not run long_push, alter long bindings or ingest "
            "documents as routine session-end work."
        ),
        source_document="docs/AGENT_MEMORY_SETUP.md",
        markers=("long_query", "non-authoritative", "long_push"),
        source_markers=MappingProxyType(
            {"docs/AGENT_MEMORY_SETUP.md": ("long_query", "non-authoritative", "long_push")}
        ),
    ),
    RuleRecord(
        identifier="reading_a_refusal",
        sentence=(
            "Reading a refusal: a refused call is an MCP error result whose "
            "payload keeps status and message and carries error_kind (the cause) "
            "and remediation (the next step). Follow remediation.next_tool with "
            "remediation.arguments, or complete remediation.required_prior_step, "
            "then retry when remediation.retryable is true. When retryable is "
            "false, report the message to the user; an operator must act."
        ),
        source_document="docs/MCP_TOOLS_SPEC.md",
        markers=("error_kind", "remediation"),
        source_markers=MappingProxyType(
            {"docs/MCP_TOOLS_SPEC.md": ("error_kind", "remediation")}
        ),
    ),
)


def render_instructions() -> str:
    """Join the rule sentences in the FR-013 order into the initialize text."""
    header = (
        "Hivemind is the shared memory across agents and sessions. One space "
        "owns short, mid and long memory; the token decides which spaces the "
        "agent can use. Follow these rules in every session."
    )
    body = "\n\n".join(f"{index}. {rule.sentence}" for index, rule in enumerate(RULES, 1))
    return f"{header}\n\n{body}\n"
