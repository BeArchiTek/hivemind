# -*- coding: utf-8 -*-
"""
Outils MCP — Catégorie Live (3 outils).

Notes en temps réel : écrire, lire, rechercher.

Permissions :
    - live_note   ✏️ (write) — Écrit une note (append-only, zéro conflit)
    - live_read   🔑 (read)  — Lit les notes récentes avec filtres
    - live_search 🔑 (read)  — Recherche texte dans les notes

Les notes live sont l'outil principal utilisé par les agents pendant
leur travail. Chaque note = 1 fichier S3 unique → aucun conflit
entre agents écrivant simultanément.

Catégories standard :
    observation — Constat factuel ("Le build passe")
    decision    — Choix technique ("On part sur S3")
    todo        — Tâche à faire ("Implémenter le backup")
    insight     — Pattern découvert ("Le pattern X marche")
    question    — Question ouverte ("Supporter le CSV ?")
    progress    — Avancement ("Module auth : 80%")
    issue       — Problème, bug ("Timeout LLM > 60s")
"""

from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field


def register(mcp: MCPServer) -> int:
    """
    Enregistre les 3 outils live sur l'instance MCP.

    Args:
        mcp: Instance MCPServer

    Returns:
        Nombre d'outils enregistrés (3)
    """

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=False))
    async def live_note(
        space_id: Annotated[str, Field(description="Space identifier: letters, digits, hyphens and underscores, at most 64 characters, for example 'project-alpha'")],
        category: Annotated[
            str,
            Field(
                description="Note category, one of observation|decision|todo|insight|question|progress|issue, for example 'observation'"
            ),
        ],
        content: Annotated[str, Field(description="Free-form note content, for example 'The build passes after the dependency update'")],
        tags: Annotated[
            str,
            Field(
                default="",
                description="Comma-separated tags, for example 'auth,security,urgent'. Default: empty, no tag",
            ),
        ] = "",
    ) -> dict:
        """Append one atomic note to the short-term memory of a space.
        Canonical name: short_note (also registered as live_note).
        Use when: during work, for each durable fact, in one of the seven categories observation, decision, todo, insight, question, progress and issue; and once at the end of a work block for the summary note.
        Do not use when: the fact is transient or already in the bank. mid_write is not an alternative for agents: it is an operator tool that bypasses consolidation, and agents never write the bank directly.
        Before calling: permission write; access to space_id; space_rules, mid_read_all and short_read first.
        Peer tools: short_read (before), mid_consolidate (after, at most once per work block).
        Result: the note is stored; the agent identity comes from the token and is never passed as an argument. Never put tokens, endpoints or secrets in a note.
        """
        from ..auth.context import check_access, check_write_permission
        from ..core.engines import RegistryRefused, get_engine_registry
        from ..core.hivemind.models import CorruptedStateError
        from ..core.write_sink import (
            DirectLocalWriteSink,
            StagedWriteNotImplemented,
        )
        from .guidance import write_route_refused

        try:
            # Vérifier accès à l'espace + permission write
            access_err = check_access(space_id)
            if access_err:
                return access_err

            write_err = check_write_permission()
            if write_err:
                return write_err

            # P3-7 ROUTE-FIRST-THEN-DELEGATE: resolve the per-space WriteSink
            # BEFORE any durable write. The single durable mutation is the live
            # note PUT, which lives INSIDE LiveService.write_note (it builds the
            # filename and calls get_storage() directly; the held sink is inert).
            # LiveService independently re-proves DIRECT_LOCAL at that final
            # mutation boundary so a retained engine cannot cross #413 source
            # preparation. At this initial tool gate:
            #   - DIRECT_LOCAL (non-Hivemind) -> delegate to the short engine's
            #     write_note: byte-for-byte identical legacy PUT via get_storage.
            #   - STAGED (Hivemind-healthy) -> raise StagedWriteNotImplemented
            #     BEFORE LiveService runs, so NO PUT ever happens (the staged
            #     sink would refuse, but the key is built inside LiveService, so
            #     we surface the typed refusal directly).
            #   - REFUSE (unsafe/resync) / corrupt -> resolve_sink raises
            #     (RegistryRefused / CorruptedStateError) before any write,
            #     and the except below renders it via safe_error.
            # Build the engine with exactly one registry sink resolution and gate
            # on THAT sink — never resolve a second competing sink here. The
            # separate LiveService check is a last-mutation fence, not a sink
            # whose verdict can fall through to the legacy write.
            registry = get_engine_registry()
            engine = await registry.short_engine(space_id)
            if not isinstance(engine.write_sink, DirectLocalWriteSink):
                raise StagedWriteNotImplemented(
                    op="put", key=f"{space_id}/live/"
                )
            return await engine.write_note(
                space_id=space_id,
                category=category,
                content=content,
                tags=tags,
            )
        except StagedWriteNotImplemented:
            # Specification 001, FR-008: a shared (staged) space refuses the
            # write with server-owned text, never "Internal server error".
            return write_route_refused(
                "Writes to a shared space are not accepted on this route",
                "The space is shared; an operator applies the staged write "
                "workflow described in the deployment guide. Do not retry on "
                "another route.",
            )
        except RegistryRefused:
            return write_route_refused(
                "Writes are refused while the space is in recovery",
                "An operator resolves the space's recovery state (resync or "
                "re-import) before writes resume. Do not retry on another route.",
            )
        except CorruptedStateError:
            return write_route_refused(
                "Writes are refused because the space's critical state is corrupt",
                "An operator repairs or resyncs the space's critical state "
                "before writes resume. Do not retry on another route.",
            )
        except Exception as e:
            from ..auth.context import safe_error

            return safe_error(e, "live")

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def live_read(
        space_id: Annotated[str, Field(description="Space identifier: letters, digits, hyphens and underscores, at most 64 characters, for example 'project-alpha'")],
        limit: Annotated[
            int,
            Field(
                default=50, description='Maximum number of notes to return, between 1 and 500. Default: 50'
            ),
        ] = 50,
        category: Annotated[
            str,
            Field(
                default="",
                description='Filter by category, one of observation|decision|todo|insight|question|progress|issue. Default: empty, no filter',
            ),
        ] = "",
        agent: Annotated[
            str, Field(default="", description="Filter by agent identifier (the client_name of the token that wrote the note), for example 'agent-cline'. Default: empty, no filter")
        ] = "",
        since: Annotated[
            str,
            Field(
                default="",
                description="Return notes after this ISO 8601 timestamp, for example '2026-03-08T10:00:00'. Default: empty, no lower bound",
            ),
        ] = "",
    ) -> dict:
        """Read the recent short-term notes of a space, newest first.
        Canonical name: short_read (also registered as live_read).
        Use when: at the start of every task, as the third start-of-task call, and to view what other agents noted since the last consolidation.
        Do not use when: the agent searches for a text (call short_search) or needs the consolidated bank (call mid_read_all).
        Before calling: permission read; access to space_id; space_rules and mid_read_all first.
        Peer tools: space_rules and mid_read_all (before), short_note (after, to add notes).
        Result: read the notes as recent unconsolidated facts; if the call fails, stop before any mutation.
        """
        from ..auth.context import check_access
        from ..core.live import get_live_service

        try:
            access_err = check_access(space_id)
            if access_err:
                return access_err

            return await get_live_service().read_notes(
                space_id=space_id,
                limit=limit,
                category=category,
                agent=agent,
                since=since,
            )
        except Exception as e:
            from ..auth.context import safe_error

            return safe_error(e, "live")

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def live_search(
        space_id: Annotated[str, Field(description="Space identifier: letters, digits, hyphens and underscores, at most 64 characters, for example 'project-alpha'")],
        query: Annotated[
            str, Field(description="Case-insensitive text to find in note content, for example 'timeout'")
        ],
        limit: Annotated[
            int, Field(default=20, description='Maximum number of results, between 1 and 500. Default: 20')
        ] = 20,
    ) -> dict:
        """Search the short-term notes of a space by text, newest first.
        Canonical name: short_search (also registered as live_search).
        Use when: the agent looks for a specific fact, decision or issue among recent notes.
        Do not use when: the agent needs the latest notes without a filter (call short_read) or historical context (call long_query).
        Before calling: permission read; access to space_id; no prior call.
        Peer tools: short_read (recent notes), long_query (historical context).
        Result: read the matching notes; verify important facts against the repository files.
        """
        from ..auth.context import check_access
        from ..core.live import get_live_service

        try:
            access_err = check_access(space_id)
            if access_err:
                return access_err

            return await get_live_service().search_notes(
                space_id=space_id,
                query=query,
                limit=limit,
            )
        except Exception as e:
            from ..auth.context import safe_error

            return safe_error(e, "live")

    return 3  # Nombre d'outils enregistrés
