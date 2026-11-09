# -*- coding: utf-8 -*-
"""
Outils MCP — Catégorie Graph (7 outils).

Pont entre Live Memory et Graph Memory : connecter un space à une
instance de graphe de connaissances et y pousser la memory bank.

Permissions :
    - graph_connect     ✏️ (write) — Connecte un space à Graph Memory
    - graph_push        ✏️ (write) — Pousse la bank dans Graph Memory
    - graph_status      🔑 (read)  — Statut de la connexion + stats graphe
    - graph_disconnect  ✏️ (write) — Déconnecte le space de Graph Memory ; le
                                     mode use_embedded nécessite manage
    - long_query        🔑 (read)  — Interroge le graphe (P4-7, net-new long_*)
    - long_ingest       ✏️ (plan)  — Planifie l'ingestion canonique (P4-7,
                                     net-new long_*, source_path-keyed, PLAN-ONLY)
    - long_reindex      🛠️ (manage) — Reconstruit explicitement la projection
                                      d'embeddings du runtime embarqué

P4-7 : ``long_query`` / ``long_ingest`` sont DEUX outils long_* net-new,
enregistrés DIRECTEMENT par ``register()`` (PAS via ALIAS_MAP — ils n'ont aucun
jumeau ``graph_*``). ``long_ingest`` est l'ingestion canonique distincte du
mirror bank keyé par nom de fichier de ``graph_push`` : les documents sont keyés
par un ``source_path`` stable (PLAN-ONLY en v1, apply déféré ; ADR-0010).

Le push utilise une synchronisation intelligente :
    - Les fichiers existants sont supprimés puis ré-ingérés (recalcul du graphe)
    - Les fichiers disparus de la bank sont nettoyés dans le graphe
    - Les métriques de push sont tracées dans _meta.json

Voir core/graph_bridge.py pour la logique métier et le client MCP Streamable HTTP.
"""

import os
import json
import logging
from typing import Annotated, Optional

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

# P4-8 : audit structuré du forçage volatil (include_volatile=True), émis sur le
# MÊME logger que l'AuditMiddleware / tokens._emit_bulk_update_audit, au tool
# layer (ADR-0010 : engine/bridge restent pass-through, sans auth ni logging).
audit_logger = logging.getLogger("live_mem.audit")

# LM2-02 fix : validation anti-SSRF du paramètre `url` de graph_connect.
# P4-4 : le validateur a été relocalisé dans core/url_guard.py (source unique)
# pour que la couche adaptateur (core/graph_bridge.py) puisse l'appliquer avant
# de construire un client, SANS qu'un module core/ importe tools/ (back-edge
# interdit, ADR-0006/0010). On le ré-exporte ici sous son nom historique
# `_validate_gm_url` : le comportement et le call-site de graph_connect sont
# inchangés (byte-for-byte), les tests SSRF existants restent verts.
from ..core.url_guard import validate_gm_url as _validate_gm_url


def _emit_volatile_optin_audit(space_id: str, volatile_files: list[str]) -> None:
    """Émet l'événement d'audit structuré ``graph_push_volatile_optin``.

    Appelé UNIQUEMENT après que la garde 'manage' a accepté un push avec
    ``include_volatile=True`` (gouvernance : tracer qui force-pousse des fichiers
    volatils dans Graph Memory, et lesquels). Mirroir exact de
    ``core/tokens.py::_emit_bulk_update_audit`` : identité du caller + request_id
    récupérés best-effort depuis les ContextVar du middleware (fallback
    ``"system"`` / ``"-"`` hors-requête HTTP), ``json.dumps(..., ensure_ascii=
    False)``, et tout échec de logging est avalé (ne casse jamais l'opération).
    """
    try:
        from ..auth.context import current_token_info
        from ..middleware import current_request_id

        tinfo = current_token_info.get()
        caller = tinfo.get("client_name", "unknown") if tinfo else "system"
        req_id = current_request_id.get()
    except Exception:
        caller = "system"
        req_id = "-"

    entry = {
        "event": "graph_push_volatile_optin",
        "request_id": req_id,
        "caller": caller,
        "space_id": space_id,
        "volatile_files": sorted(volatile_files),
    }
    try:
        audit_logger.info(json.dumps(entry, ensure_ascii=False))
    except Exception:
        pass


def _emit_long_ingest_volatile_optin_audit(
    space_id: str, volatile_source_paths: list[str]
) -> None:
    """Émet l'événement d'audit structuré ``long_ingest_volatile_optin`` (P4-7).

    Miroir exact de :func:`_emit_volatile_optin_audit` pour le chemin
    ``long_ingest`` : appelé UNIQUEMENT après que la garde 'manage' a accepté un
    opt-in ``include_volatile=True`` (gouvernance : tracer qui force-admet des
    fichiers volatils dans l'ingestion canonique long-tier, et lesquels). NB :
    événement et champ DISTINCTS de ``graph_push_volatile_optin`` /
    ``volatile_files`` (le chemin graph_push) — ici l'événement est
    ``long_ingest_volatile_optin`` et le champ est ``volatile_source_paths``
    (on trace des ``source_path`` canoniques, pas des noms de fichiers bank).
    Tout échec de logging est avalé (ne casse jamais l'opération).
    """
    try:
        from ..auth.context import current_token_info
        from ..middleware import current_request_id

        tinfo = current_token_info.get()
        caller = tinfo.get("client_name", "unknown") if tinfo else "system"
        req_id = current_request_id.get()
    except Exception:
        caller = "system"
        req_id = "-"

    entry = {
        "event": "long_ingest_volatile_optin",
        "request_id": req_id,
        "caller": caller,
        "space_id": space_id,
        "volatile_source_paths": sorted(volatile_source_paths),
    }
    try:
        audit_logger.info(json.dumps(entry, ensure_ascii=False))
    except Exception:
        pass


from .guidance import ErrorKind, argument_refusal, refusal, remediation


def _volatile_refusal(message: str, offending: list) -> dict:
    """Volatile files need include_volatile=True and manage (specification 001)."""
    return refusal(
        message,
        ErrorKind.PERMISSION_INSUFFICIENT,
        rejected_volatile=offending,
        required_permission="manage",
        current_permissions=_current_permissions(),
        remediation=remediation(
            reason=(
                "An operator with manage or admin passes include_volatile=true "
                "to admit these files; agents leave volatile files out of "
                "long-term memory."
            ),
            retryable=False,
            arguments={"include_volatile": True},
        ),
    )


def _current_permissions() -> list:
    from ..auth.context import get_effective_token_info

    token_info = get_effective_token_info() or {}
    return [p for p in token_info.get("permissions", []) if isinstance(p, str)]


def _job_id_refusal() -> dict:
    return argument_refusal(
        "job_id must be a non-empty string",
        kind=ErrorKind.INVALID_ARGUMENT,
        arguments=["job_id"],
        reason="job_id is the identifier returned by long_ingest_async, for example 'ingest_1a2b3c'.",
    )


def _limit_refusal() -> dict:
    return argument_refusal(
        "limit must be an integer between 1 and 100",
        kind=ErrorKind.INVALID_ARGUMENT,
        arguments=["limit"],
        reason="limit is a whole number between 1 and 100, for example 50.",
    )


def _offset_refusal() -> dict:
    return argument_refusal(
        "offset must be a non-negative integer",
        kind=ErrorKind.INVALID_ARGUMENT,
        arguments=["offset"],
        reason="offset is a whole number, 0 or more, for example 0.",
    )


def register(mcp: MCPServer) -> int:
    """
    Enregistre les 7 outils graph sur l'instance MCP.

    Les 4 historiques (graph_connect / graph_push / graph_status /
    graph_disconnect) + les 3 outils long_* directs (long_query / long_ingest /
    long_reindex), enregistrés DIRECTEMENT ici (PAS via ALIAS_MAP — aucun
    jumeau graph_*).

    Args:
        mcp: Instance MCPServer

    Returns:
        Nombre d'outils enregistrés (7)
    """

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True))
    async def graph_connect(
        space_id: Annotated[
            str, Field(description="Space identifier: letters, digits, hyphens and underscores, at most 64 characters, for example 'project-alpha'")
        ],
        url: Annotated[
            str,
            Field(
                description="Graph Memory MCP address, for example 'https://graph.example.com/mcp'"
            ),
        ],
        token: Annotated[
            str, Field(description='Bearer token used to authenticate to Graph Memory, in the format that service issues')
        ],
        memory_id: Annotated[
            str, Field(description="Target memory identifier in Graph Memory, for example 'project-alpha'")
        ],
        ontology: Annotated[
            str,
            Field(
                default="general",
                description="Extraction ontology, one of general|legal|cloud|managed-services|presales. Default: 'general'",
            ),
        ] = "general",
    ) -> dict:
        """Operator tool: not advertised in tools/list. Canonical name: long_connect (also registered as graph_connect). Bind a space to an explicit Graph Memory instance; needed only for a custom remote instance, because without an explicit binding the first non-empty long_push automatically binds the configured embedded long-memory runtime. Requires permission write and access to space_id.
        """
        from ..auth.context import check_access, check_write_permission
        from ..core.engines import get_engine_registry

        try:
            # Vérifier accès au space + permission write
            access_err = check_access(space_id)
            if access_err:
                return access_err

            write_err = check_write_permission()
            if write_err:
                return write_err

            # LM2-02 fix : valider l'URL pour bloquer le SSRF (IP privées,
            # metadata cloud, schemes non HTTP). Doit être fait AVANT toute
            # tentative de connexion réseau ET avant la persistance S3.
            # P3-7: la validation SSRF reste DANS le tool layer (ADR-0010 :
            # LongEngine ne la réplique pas).
            url_err = _validate_gm_url(url)
            if url_err:
                return argument_refusal(
                    url_err,
                    kind=ErrorKind.INVALID_ARGUMENT,
                    arguments=["url"],
                    reason=(
                        "url is a public http or https address of a Graph Memory "
                        "server, for example 'https://graph.example.com/mcp'."
                    ),
                )

            # P3-7: graph_* est downstream-derived (ADR-0010) — JAMAIS un write
            # WriteSink, donc PAS de resolve_sink gate. LongEngine ne prend pas
            # de WriteSink ; connect écrit seulement le bloc local-only
            # graph_memory de _meta.json via le bridge.
            return await get_engine_registry().long_engine().connect(
                space_id=space_id,
                url=url,
                token=token,
                memory_id=memory_id,
                ontology=ontology,
            )
        except Exception as e:
            from ..auth.context import safe_error

            return safe_error(e, "graph")

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True))
    async def graph_push(
        space_id: Annotated[
            str, Field(description="Space identifier: letters, digits, hyphens and underscores, at most 64 characters, for example 'project-alpha'")
        ],
        include_volatile: Annotated[
            bool,
            Field(
                default=False,
                description=(
                    'true also projects activeContext.md and progress.md (requires permission manage and records an audit event); false skips them. Default: false'
                ),
            ),
        ] = False,
    ) -> dict:
        """Project the committed memory bank of a space into its long-term memory.
        Canonical name: long_push (also registered as graph_push).
        Use when: the user or an operator explicitly asks to refresh the long-term memory from the bank.
        Do not use when: ending a work block (long_push is not routine session-end work) or ingesting repository documents (that is long_ingest, permission manage).
        Before calling: permission write; access to space_id; a consolidation completed since the last push.
        Peer tools: mid_consolidate (before), long_status (after), long_ingest (canonical documents).
        Result: read the projection counts and skipped_volatile; activeContext.md and progress.md stay out unless a manager sets include_volatile.
        """
        from ..auth.context import (
            check_access,
            check_manage_permission,
            check_write_permission,
        )
        from ..core.engines import get_engine_registry

        try:
            access_err = check_access(space_id)
            if access_err:
                return access_err

            write_err = check_write_permission()
            if write_err:
                return write_err

            # P4-8 — Garde-fou volatil. Le forçage (include_volatile=True)
            # requiert 'manage' ET émet un audit structuré APRÈS la garde (un
            # refus n'audite jamais). Le tool layer porte la permission + l'audit
            # (ADR-0010 : engine/bridge restent pass-through).
            if include_volatile:
                manage_err = check_manage_permission()
                if manage_err:
                    return manage_err

                from ..config import get_settings

                _emit_volatile_optin_audit(
                    space_id, list(get_settings().graph_push_volatile_files)
                )

            # P3-7: downstream-derived (ADR-0010) — no resolve_sink gate.
            return await get_engine_registry().long_engine().push(
                space_id, include_volatile=include_volatile
            )
        except Exception as e:
            from ..auth.context import safe_error

            return safe_error(e, "graph")

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def graph_status(
        space_id: Annotated[str, Field(description="Space identifier: letters, digits, hyphens and underscores, at most 64 characters, for example 'project-alpha'")],
        include_graph: Annotated[
            bool,
            Field(
                description='true includes a bounded, sanitized graph view for the admin console; false returns the status only. Default: false'
            ),
        ] = False,
    ) -> dict:
        """Read the long-memory binding, connectivity and projection status of a space.
        Canonical name: long_status (also registered as graph_status).
        Use when: long_query returned nothing or an error and the agent must know whether the space has long memory.
        Do not use when: the agent wants to push the bank into long memory; that is not routine work, an operator decides on long_push.
        Before calling: permission read; access to space_id; no prior call.
        Peer tools: long_query (before), long_push (operator decision).
        Result: report the status to the user; do not bind, push or ingest as routine work.
        """
        from ..auth.context import check_access
        from ..core.engines import get_engine_registry

        try:
            access_err = check_access(space_id)
            if access_err:
                return access_err

            # P3-7: read-only delegation to LongEngine.status (bridge read);
            # not a WriteSink path.
            return await get_engine_registry().long_engine().status(
                space_id, include_graph=include_graph
            )
        except Exception as e:
            from ..auth.context import safe_error

            return safe_error(e, "graph")

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True))
    async def graph_disconnect(
        space_id: Annotated[
            str, Field(description="Space identifier: letters, digits, hyphens and underscores, at most 64 characters, for example 'project-alpha'")
        ],
        use_embedded: Annotated[
            bool,
            Field(
                default=False,
                description=(
                    'true replaces the explicit binding with the validated embedded long-memory runtime (requires permission manage; no remote data is deleted or ingested); false only removes the binding. Default: false'
                ),
            ),
        ] = False,
    ) -> dict:
        """Operator tool: not advertised in tools/list. Canonical name: long_disconnect (also registered as graph_disconnect). Remove the explicit Graph Memory binding of a space; with use_embedded=true (permission manage) the validated embedded runtime replaces it. Remote graph data is never deleted. Requires permission write and access to space_id.
        """
        from ..auth.context import (
            check_access,
            check_manage_permission,
            check_write_permission,
        )
        from ..core.engines import get_engine_registry

        try:
            access_err = check_access(space_id)
            if access_err:
                return access_err

            write_err = check_write_permission()
            if write_err:
                return write_err

            if use_embedded:
                manage_err = check_manage_permission()
                if manage_err:
                    return manage_err

            # P3-7: downstream-derived (ADR-0010) — no resolve_sink gate.
            # disconnect clears only the local-only graph_memory block in
            # _meta.json via the bridge.
            if use_embedded:
                return await get_engine_registry().long_engine().disconnect(
                    space_id, use_embedded=True
                )
            # Preserve the legacy default call shape for injected engines.
            return await get_engine_registry().long_engine().disconnect(space_id)
        except Exception as e:
            from ..auth.context import safe_error

            return safe_error(e, "graph")

    # ─────────────────────────────────────────────────────────
    # P4-7 — long_query : outil READ-ONLY thin sur LongEngine.query
    # ─────────────────────────────────────────────────────────

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def long_query(
        space_id: Annotated[
            str, Field(description="Space identifier: letters, digits, hyphens and underscores, at most 64 characters, for example 'project-alpha'")
        ],
        query: Annotated[
            str,
            Field(
                description=(
                    "Semantic query text, for example 'incident on the auth service'; the configured embedding endpoint embeds it and no generative chat completion is performed"
                )
            ),
        ],
        limit: Annotated[
            int,
            Field(default=10, description='Maximum number of results, between 1 and 100. Default: 10'),
        ] = 10,
    ) -> dict:
        """Query the long-term memory of a space: a derived, non-authoritative knowledge graph.
        Use when: the agent needs historical context, prior incidents, runbooks, decisions or cross-document relationships.
        Do not use when: the agent needs recent facts (call short_read or mid_read_all). Do not treat the results as the final authority; repository files win.
        Before calling: permission read; access to space_id; no prior call.
        Peer tools: short_search (recent notes), long_status (whether the space has long memory).
        Result: re-read the referenced canonical file before acting; long memory is non-authoritative.
        """
        from ..auth.context import check_access
        from ..core.engines import get_engine_registry

        try:
            access_err = check_access(space_id)
            if access_err:
                return access_err

            # READ-ONLY : delegation à LongEngine.query (bridge read) ; jamais un
            # WriteSink, jamais une source de commit (ADR-0010).
            return await get_engine_registry().long_engine().query(
                space_id, query, limit=limit
            )
        except Exception as e:
            from ..auth.context import safe_error

            return safe_error(e, "graph")

    # ─────────────────────────────────────────────────────────
    # P4-7 — long_ingest : ingestion canonique PLAN-ONLY (source_path-keyed)
    # ─────────────────────────────────────────────────────────

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True))
    async def long_ingest(
        space_id: Annotated[
            str, Field(description="Space identifier: letters, digits, hyphens and underscores, at most 64 characters, for example 'project-alpha'")
        ],
        documents: Annotated[
            list[dict],
            Field(
                description=(
                    'List of canonical documents in the format {source_path, content or content_base64, sha256 (optional)}, for example [{"source_path": "docs/runbook.md", "content": "..."}]'
                )
            ),
        ],
        mode: Annotated[
            str,
            Field(
                default="dry-run",
                description=(
                    "Planning mode, one of 'dry-run' (computes source_path and sha256 locally without network access), 'check-remote' (compares against the remote index without writing) or 'apply' (not supported; returns applied=false). Default: 'dry-run'"
                ),
            ),
        ] = "dry-run",
        include_volatile: Annotated[
            bool,
            Field(
                default=False,
                description=(
                    'true allows activeContext.md and progress.md (requires permission manage and records an audit event); false rejects them. Default: false'
                ),
            ),
        ] = False,
    ) -> dict:
        """Plan the ingestion of canonical repository documents into the long-term memory of a space, without writing.
        Use when: an operator prepares canonical documents (stable source_path) for long memory and wants a plan (mode dry-run or check-remote).
        Do not use when: the bank should be projected (call long_push). Do not use it as routine session-end work.
        Before calling: permission manage; access to space_id; no prior call.
        Peer tools: long_push (bank projection), long_status (after).
        Result: read the planned actions; mode apply is not supported and returns applied=false.
        """
        from ..auth.context import (
            check_access,
            check_manage_permission,
            safe_error,
        )
        from ..config import get_settings
        from ..core.engines import get_engine_registry

        try:
            access_err = check_access(space_id)
            if access_err:
                return access_err

            # Garde volatile (tool layer, ADR-0010) : match sur le BASENAME du
            # source_path. Rejetée par défaut sur TOUS les modes (y compris
            # dry-run) — la garde précède toute planification / tout transport.
            volatile = set(get_settings().graph_push_volatile_files)
            offending = sorted(
                {
                    d.get("source_path", "")
                    for d in documents
                    if os.path.basename(d.get("source_path", "")) in volatile
                }
            )
            if offending:
                if not include_volatile:
                    return _volatile_refusal(
                        (
                            "Volatile files (activeContext.md / progress.md) are "
                            "REJECTED from canonical long-tier ingestion by "
                            "default. These files are continuously rewritten; "
                            "use include_volatile=True (the 'manage' permission "
                            "is required) to force their admission."
                        ),
                        offending,
                    )
                # Opt-in : la permission 'manage' est requise. La garde précède
                # l'audit (un refus n'audite JAMAIS).
                manage_err = check_manage_permission()
                if manage_err:
                    return manage_err

                _emit_long_ingest_volatile_optin_audit(space_id, offending)

            # Délégation PLAN-ONLY : l'engine/bridge porte le dispatch de mode et
            # la planification source_path-keyed (downstream-only, ADR-0010).
            return await get_engine_registry().long_engine().plan_ingest(
                space_id, documents, mode=mode, include_volatile=include_volatile
            )
        except Exception as e:
            return safe_error(e, "graph")

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=False))
    async def long_reindex(
        space_id: Annotated[
            str,
            Field(
                description=(
                    "Space identifier: letters, digits, hyphens and underscores, at most 64 characters, for example 'project-alpha'"
                )
            ),
        ],
    ) -> dict:
        """Operator tool: not advertised in tools/list. Rebuild the embedded long-memory embedding projection of a space; synchronous, not idempotent, and only for a persisted embedded binding. Requires permission manage and access to space_id.
        """
        from ..auth.context import check_access, check_manage_permission
        from ..core.engines import get_engine_registry

        # Authorization is deliberately complete before engine lookup. The
        # bridge lookup resolves persisted binding state and may construct a
        # network client, so neither may happen for an unauthorized caller.
        try:
            access_err = check_access(space_id)
            if access_err:
                return access_err

            manage_err = check_manage_permission()
            if manage_err:
                return manage_err
        except Exception as e:
            from ..auth.context import safe_error

            return safe_error(e, "graph")

        try:
            return await get_engine_registry().long_engine().reindex(space_id)
        except Exception:
            from ..core.graph_bridge import _reindex_error

            return _reindex_error("reindex_failed")

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
    async def ontology_list(
        space_id: Annotated[
            str,
            Field(description="Space identifier: letters, digits, hyphens and underscores, at most 64 characters, for example 'project-alpha'"),
        ],
    ) -> dict:
        """Operator tool: not advertised in tools/list. List the ontologies available in Graph Memory for a space. Requires permission read and access to space_id.
        """
        from ..auth.context import check_access, safe_error
        from ..core.engines import get_engine_registry

        try:
            access_err = check_access(space_id)
            if access_err:
                return access_err
            return await get_engine_registry().long_engine().list_ontologies(space_id)
        except Exception as e:
            return safe_error(e, "graph")

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
    async def ontology_get(
        space_id: Annotated[
            str,
            Field(description="Space identifier: letters, digits, hyphens and underscores, at most 64 characters, for example 'project-alpha'"),
        ],
        name: Annotated[
            str,
            Field(description="Ontology name, for example 'general'"),
        ],
    ) -> dict:
        """Operator tool: not advertised in tools/list. Read one ontology definition by name, with its YAML content. Requires permission read and access to space_id.
        """
        from ..auth.context import check_access, safe_error
        from ..core.engines import get_engine_registry

        try:
            access_err = check_access(space_id)
            if access_err:
                return access_err
            return await get_engine_registry().long_engine().get_ontology(space_id, name)
        except Exception as e:
            return safe_error(e, "graph")

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
    async def ontology_validate(
        space_id: Annotated[
            str,
            Field(description="Space identifier: letters, digits, hyphens and underscores, at most 64 characters, for example 'project-alpha'"),
        ],
        content_yaml: Annotated[
            str,
            Field(description='Ontology definition in YAML format, for example the content returned by ontology_get'),
        ],
    ) -> dict:
        """Operator tool: not advertised in tools/list. Validate an ontology YAML definition without changing any state. Requires permission read and access to space_id.
        """
        from ..auth.context import check_access, safe_error
        from ..core.engines import get_engine_registry

        try:
            access_err = check_access(space_id)
            if access_err:
                return access_err

            MAX_ONTOLOGY_BYTES = 512 * 1024
            if not isinstance(content_yaml, str):
                return {
                    "status": "ok",
                    "valid": False,
                    "name": None,
                    "version": None,
                    "sha256": None,
                    "entity_types_count": 0,
                    "relation_types_count": 0,
                    "yaml_content": None,
                    "errors": ["content_yaml must be a string"],
                    "warnings": [],
                }

            try:
                encoded = content_yaml.encode("utf-8")
            except (UnicodeError, UnicodeEncodeError) as e:
                return {
                    "status": "ok",
                    "valid": False,
                    "name": None,
                    "version": None,
                    "sha256": None,
                    "entity_types_count": 0,
                    "relation_types_count": 0,
                    "yaml_content": None,
                    "errors": [f"Invalid Unicode encoding: {e}"],
                    "warnings": [],
                }

            if len(encoded) > MAX_ONTOLOGY_BYTES:
                return {
                    "status": "ok",
                    "valid": False,
                    "name": None,
                    "version": None,
                    "sha256": None,
                    "entity_types_count": 0,
                    "relation_types_count": 0,
                    "yaml_content": None,
                    "errors": [f"Ontology content exceeds maximum allowed size ({MAX_ONTOLOGY_BYTES} bytes)"],
                    "warnings": [],
                }

            return await get_engine_registry().long_engine().validate_ontology(space_id, content_yaml)
        except Exception as e:
            return safe_error(e, "graph")


    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=False))
    async def long_ingest_async(
        space_id: Annotated[
            str,
            Field(description="Space identifier: letters, digits, hyphens and underscores, at most 64 characters, for example 'project-alpha'"),
        ],
        documents: Annotated[
            list[dict],
            Field(description='List of documents in the format {filename, source_path, content_base64, sha256, metadata (optional), source_modified_at (optional, ISO 8601)}; the checksum is the hexadecimal sha256 of the decoded bytes'),
        ],
        options: Annotated[
            Optional[dict],
            Field(description="Ingestion options in the format {replace_existing: true|false, ontology: a registered name, a YAML definition or 'auto'}. Default: null, which uses the current memory default"),
        ] = None,
        include_volatile: Annotated[
            bool,
            Field(
                description=(
                    'true allows activeContext.md and progress.md (requires permission manage and records an audit event); false rejects them. Default: false'
                ),
            ),
        ] = False,
    ) -> dict:
        """Operator tool: not advertised in tools/list. Queue a batch of canonical documents for asynchronous ingestion into the long-term memory of a space; each document carries filename, source_path, content_base64 and the sha256 of the decoded bytes; this path does not convert plain content and does not compute a missing checksum for the caller. Requires permission manage and access to space_id. Follow a job with long_ingest_status.
        """
        from ..auth.context import (
            check_access,
            check_manage_permission,
            check_write_permission,
            safe_error,
        )
        from ..config import get_settings
        from ..core.engines import get_engine_registry

        try:
            access_err = check_access(space_id)
            if access_err:
                return access_err

            manage_err = check_manage_permission()
            if manage_err:
                return manage_err

            if not isinstance(documents, list) or not documents:
                return argument_refusal(
                    "documents must be a non-empty list",
                    kind=ErrorKind.MISSING_ARGUMENT,
                    arguments=["documents"],
                    reason=(
                        "documents is a list of at least one object with "
                        "source_path and content, for example "
                        "[{\"source_path\": \"docs/runbook.md\", \"content\": \"...\"}]."
                    ),
                )

            volatile = set(get_settings().graph_push_volatile_files)
            offending = sorted(
                {
                    d.get("source_path", "")
                    for d in documents
                    if isinstance(d, dict) and os.path.basename(d.get("source_path", "")) in volatile
                }
            )
            if offending:
                if not include_volatile:
                    return _volatile_refusal(
                        (
                            "Volatile files (activeContext.md / progress.md) are "
                            "REJECTED from canonical long-tier ingestion by "
                            "default. Use include_volatile=True (the 'manage' permission "
                            "is required) to force their admission."
                        ),
                        offending,
                    )
                manage_err = check_manage_permission()
                if manage_err:
                    return manage_err

                _emit_long_ingest_volatile_optin_audit(space_id, offending)

            return await get_engine_registry().long_engine().ingest_async(
                space_id=space_id,
                documents=documents,
                options=options,
            )
        except Exception as e:
            return safe_error(e, "graph")

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
    async def long_ingest_status(
        space_id: Annotated[
            str,
            Field(description="Space identifier: letters, digits, hyphens and underscores, at most 64 characters, for example 'project-alpha'"),
        ],
        job_id: Annotated[
            str,
            Field(description="Ingestion job identifier returned by long_ingest_async, for example 'ingest_1a2b3c'"),
        ],
        archive: Annotated[
            bool,
            Field(description='true reads a job of the pinned mid-memory archive; false reads the primary long memory. Default: false'),
        ] = False,
    ) -> dict:
        """Operator tool: not advertised in tools/list. Read the status and progress of one asynchronous ingestion job. Requires permission read and access to space_id.
        """
        from ..auth.context import check_access, safe_error
        from ..core.engines import get_engine_registry

        try:
            access_err = check_access(space_id)
            if access_err:
                return access_err

            if not isinstance(job_id, str) or not job_id.strip():
                return _job_id_refusal()

            if type(archive) is not bool:
                return argument_refusal(
                    "archive must be a boolean",
                    kind=ErrorKind.INVALID_ARGUMENT,
                    arguments=["archive"],
                    reason="archive is true or false, for example false.",
                )

            engine = get_engine_registry().long_engine()
            read_status = engine.archive_ingest_status if archive else engine.ingest_status
            return await read_status(
                space_id=space_id,
                job_id=job_id.strip(),
            )
        except Exception as e:
            return safe_error(e, "graph")

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
    async def long_ingest_list(
        space_id: Annotated[
            str,
            Field(description="Space identifier: letters, digits, hyphens and underscores, at most 64 characters, for example 'project-alpha'"),
        ],
        batch_id: Annotated[
            Optional[str],
            Field(description="Batch identifier filter, for example 'batch_1a2b3c'. Default: null, no filter"),
        ] = None,
        status: Annotated[
            Optional[str],
            Field(description='Job status filter, one of queued|running|succeeded|failed. Default: null, no filter'),
        ] = None,
        limit: Annotated[
            int,
            Field(description='Maximum number of jobs to return, between 1 and 100. Default: 50'),
        ] = 50,
        offset: Annotated[
            int,
            Field(description='Number of jobs to skip, 0 or more. Default: 0'),
        ] = 0,
        archive: Annotated[
            bool,
            Field(description='true lists the jobs of the pinned mid-memory archive; false lists the primary long memory. Default: false'),
        ] = False,
    ) -> dict:
        """Operator tool: not advertised in tools/list. List the asynchronous ingestion jobs of a space with pagination and filters. Requires permission read and access to space_id.
        """
        from ..auth.context import check_access, safe_error
        from ..core.engines import get_engine_registry

        try:
            access_err = check_access(space_id)
            if access_err:
                return access_err

            if type(limit) is not int or limit < 1 or limit > 100:
                return _limit_refusal()
            if type(offset) is not int or offset < 0:
                return _offset_refusal()
            arguments = dict(space_id=space_id, batch_id=batch_id, status=status,
                             limit=limit, offset=offset)
            if archive:
                arguments["archive"] = True
            return await get_engine_registry().long_engine().ingest_list(**arguments)
        except Exception as e:
            return safe_error(e, "graph")

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True))
    async def long_ingest_cancel(
        space_id: Annotated[
            str,
            Field(description="Space identifier: letters, digits, hyphens and underscores, at most 64 characters, for example 'project-alpha'"),
        ],
        job_id: Annotated[
            str,
            Field(description="Ingestion job identifier to cancel, for example 'ingest_1a2b3c'"),
        ],
    ) -> dict:
        """Operator tool: not advertised in tools/list. Request the cooperative cancellation of an asynchronous ingestion job. Requires permission write and access to space_id.
        """
        from ..auth.context import check_access, check_write_permission, safe_error
        from ..core.engines import get_engine_registry

        try:
            access_err = check_access(space_id)
            if access_err:
                return access_err

            write_err = check_write_permission()
            if write_err:
                return write_err

            if not isinstance(job_id, str) or not job_id.strip():
                return _job_id_refusal()

            return await get_engine_registry().long_engine().ingest_cancel(
                space_id=space_id,
                job_id=job_id.strip(),
            )
        except Exception as e:
            return safe_error(e, "graph")

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
    async def long_document_list(
        space_id: Annotated[
            str,
            Field(description="Space identifier: letters, digits, hyphens and underscores, at most 64 characters, for example 'project-alpha'"),
        ],
        limit: Annotated[
            int,
            Field(description='Maximum number of documents to return, between 1 and 100. Default: 50'),
        ] = 50,
        offset: Annotated[
            int,
            Field(description='Number of documents to skip, 0 or more. Default: 0'),
        ] = 0,
        status: Annotated[
            Optional[str],
            Field(description='Ingestion status filter, one of succeeded|failed. Default: null, no filter'),
        ] = None,
        query: Annotated[
            Optional[str],
            Field(description="Search text matched against filename or source_path, for example 'runbook'. Default: null, no filter"),
        ] = None,
    ) -> dict:
        """Operator tool: not advertised in tools/list. List the documents indexed in the long-term memory of a space with pagination and filters. Requires permission read and access to space_id.
        """
        from ..auth.context import check_access, safe_error
        from ..core.engines import get_engine_registry

        try:
            access_err = check_access(space_id)
            if access_err:
                return access_err

            if type(limit) is not int or limit < 1 or limit > 100:
                return _limit_refusal()
            if type(offset) is not int or offset < 0:
                return _offset_refusal()

            return await get_engine_registry().long_engine().list_documents(
                space_id=space_id,
                limit=limit,
                offset=offset,
                status=status,
                query=query,
            )
        except Exception as e:
            return safe_error(e, "graph")

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
    async def long_document_get(
        space_id: Annotated[
            str,
            Field(description="Space identifier: letters, digits, hyphens and underscores, at most 64 characters, for example 'project-alpha'"),
        ],
        document_id: Annotated[
            Optional[str],
            Field(description="Internal document identifier in UUID format, for example '2f1c7b0e-9d4a-4c1b-8f3e-5a6d7c8e9f00'. Default: null"),
        ] = None,
        source_path: Annotated[
            Optional[str],
            Field(description="Stable canonical source path of the document, for example 'docs/runbook.md'. Default: null"),
        ] = None,
        include_content: Annotated[
            bool,
            Field(description='true downloads and includes the content; false returns the metadata only. Default: false'),
        ] = False,
    ) -> dict:
        """Operator tool: not advertised in tools/list. Read the metadata and, on request, the content of one indexed document by document_id or source_path. Requires permission read and access to space_id.
        """
        from ..auth.context import check_access, safe_error
        from ..core.engines import get_engine_registry

        try:
            access_err = check_access(space_id)
            if access_err:
                return access_err

            if type(include_content) is not bool:
                return argument_refusal(
                    f"'include_content' must be a boolean (True/False), got {type(include_content).__name__}: {include_content!r}",
                    kind=ErrorKind.INVALID_ARGUMENT,
                    arguments=["include_content"],
                    reason="include_content is true or false, for example false.",
                )

            if not document_id and not source_path:
                return argument_refusal(
                    "At least one of 'document_id' or 'source_path' must be provided",
                    kind=ErrorKind.MISSING_ARGUMENT,
                    arguments=["document_id", "source_path"],
                    reason=(
                        "Pass document_id (an identifier from long_document_list) "
                        "or source_path (a repository path, for example "
                        "'docs/runbook.md')."
                    ),
                )

            if document_id is not None and (not isinstance(document_id, str) or not document_id.strip()):
                return argument_refusal(
                    "'document_id' must be a non-empty string when provided",
                    kind=ErrorKind.INVALID_ARGUMENT,
                    arguments=["document_id"],
                    reason="document_id is a non-empty identifier from long_document_list.",
                )
            if source_path is not None and (not isinstance(source_path, str) or not source_path.strip()):
                return argument_refusal(
                    "'source_path' must be a non-empty string when provided",
                    kind=ErrorKind.INVALID_ARGUMENT,
                    arguments=["source_path"],
                    reason="source_path is a non-empty repository path, for example 'docs/runbook.md'.",
                )

            return await get_engine_registry().long_engine().get_document(
                space_id=space_id,
                document_id=document_id.strip() if isinstance(document_id, str) else None,
                source_path=source_path.strip() if isinstance(source_path, str) else None,
                include_content=include_content,
            )
        except Exception as e:
            return safe_error(e, "graph")

    return 16  # 4 historiques + long_query / long_ingest / long_reindex + 3 ontology_* + 4 long_ingest_* + 2 long_document_*
