# -*- coding: utf-8 -*-
"""
Outils MCP — Catégorie Admin (9 outils).

Gestion des tokens d'authentification et maintenance.

Permissions :
    - admin_audit_recent       👑 (admin) — Lit l'audit local récent
    - admin_create_token       👑 (admin) — Crée un token
    - admin_list_tokens        👑 (admin) — Liste les tokens (avec filtres)
    - admin_revoke_token       👑 (admin) — Révoque un token
    - admin_delete_token       👑 (admin) — Supprime physiquement un token
    - admin_purge_tokens       👑 (admin) — Purge en masse les tokens
    - admin_update_token       👑 (admin) — Modifie un token (remplacement ou delta)
    - admin_bulk_update_tokens 👑 (admin) — Bulk update : delta sur N tokens
    - admin_gc_notes           👑 (admin) — GC des notes orphelines

Tous les outils admin requièrent la permission "admin".
Voir AUTH_AND_COLLABORATION.md pour le modèle de tokens.
"""

from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field


def register(mcp: MCPServer) -> int:
    """
    Enregistre les 9 outils admin sur l'instance MCP.

    Args:
        mcp: Instance MCPServer

    Returns:
        Nombre d'outils enregistrés (9)
    """

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=False))
    async def admin_create_token(
        name: Annotated[
            str,
            Field(
                description="Descriptive token name, for example 'agent-cline' or 'ci-pipeline'"
            ),
        ],
        permissions: Annotated[
            str,
            Field(
                description=(
                    "Comma-separated subset of read,write,manage,admin, for example 'read,write'; common profiles are 'read', 'read,write', 'read,write,manage' and 'read,write,manage,admin'"
                )
            ),
        ],
        space_ids: Annotated[
            str,
            Field(
                default="",
                description=(
                    "Comma-separated accessible spaces, for example 'project-alpha,project-beta'; '*' or 'all' takes a snapshot of the current spaces; ignored for admin tokens, whose scope is global. Default: empty, no access until a manager or administrator invites the token"
                ),
            ),
        ] = "",
        expires_in_days: Annotated[
            int,
            Field(
                default=0,
                ge=0,
                description='Validity period in days, 0 or more; 0 means no expiration. Default: 0',
            ),
        ] = 0,
        email: Annotated[
            str,
            Field(
                default="", description="Owner email for traceability, for example 'owner@example.org'. Default: empty"
            ),
        ] = "",
        operator_tools: Annotated[
            bool,
            Field(
                default=True,
                description=(
                    'true lets the token call the operator tools (the hidden maintenance, backup and token tools) over MCP; false refuses them, which is the right value for a token handed to an agent. Default: true'
                ),
            ),
        ] = True,
    ) -> dict:
        """Operator tool: not advertised in tools/list. Create a token with permissions, space scope and operator tool access; the plaintext token is returned once and only its hash is stored. Requires permission admin. A manager creates agent tokens with token_create.
        """
        from ..auth.context import check_admin_permission
        from ..core.tokens import get_token_service

        try:
            admin_err = check_admin_permission()
            if admin_err:
                return admin_err

            return await get_token_service().create_token(
                name=name,
                permissions=permissions,
                space_ids=space_ids,
                expires_in_days=expires_in_days,
                email=email,
                operator_tools=operator_tools,
            )
        except Exception as e:
            from ..auth.context import safe_error

            return safe_error(e, "admin")

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def admin_list_tokens(
        name_contains: Annotated[
            str,
            Field(
                default="",
                description=(
                    "Case-insensitive substring of the token name, for example 'agent'. Default: empty, no filter"
                ),
            ),
        ] = "",
        has_space: Annotated[
            str,
            Field(
                default="",
                description=(
                    "Exact, case-sensitive space identifier that the token scope must contain, for example 'project-alpha'. Default: empty, no filter"
                ),
            ),
        ] = "",
        include_revoked: Annotated[
            bool,
            Field(
                default=True,
                description=(
                    'true includes revoked tokens; false lists active tokens only. Default: true'
                ),
            ),
        ] = True,
    ) -> dict:
        """Operator tool: not advertised in tools/list. List token metadata with optional filters; plaintext credentials are never returned. Requires permission admin.
        """
        from ..auth.context import check_admin_permission
        from ..core.tokens import get_token_service

        try:
            admin_err = check_admin_permission()
            if admin_err:
                return admin_err

            return await get_token_service().list_tokens(
                name_contains=name_contains,
                has_space=has_space,
                include_revoked=include_revoked,
            )
        except Exception as e:
            from ..auth.context import safe_error

            return safe_error(e, "admin")

    @mcp.tool(
        annotations=ToolAnnotations(readOnlyHint=True),
    )
    async def admin_audit_recent(
        limit: Annotated[
            int,
            Field(
                default=50,
                description=(
                    'Number of recent events, between 1 and 500. Default: 50'
                ),
            ),
        ] = 50,
    ) -> dict:
        """Operator tool: not advertised in tools/list. Return the recent best-effort local audit events of this server instance. Requires permission admin.
        """
        from ..auth.context import check_admin_permission
        from ..core.audit_ring import AUDIT_SCOPE_NOTE, capacity, snapshot

        try:
            admin_err = check_admin_permission()
            if admin_err:
                return admin_err

            effective_limit = min(max(limit, 1), 500)
            entries = list(reversed(snapshot()))[:effective_limit]
            return {
                "status": "ok",
                "entries": entries,
                "total": len(entries),
                "capacity": capacity(),
                "scope_note": AUDIT_SCOPE_NOTE,
            }
        except Exception as e:
            from ..auth.context import safe_error

            return safe_error(e, "admin")

    @mcp.tool(
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True),
    )
    async def admin_revoke_token(
        token_hash: Annotated[
            str,
            Field(
                description=(
                    "Token hash from admin_list_tokens, in the format sha256: followed by 64 lowercase hexadecimal characters; the 'sha256:' prefix is optional and at least 16 hexadecimal characters are required"
                )
            ),
        ],
    ) -> dict:
        """Operator tool: not advertised in tools/list. Permanently revoke a token by its hash. Requires permission admin.
        """
        from ..auth.context import check_admin_permission
        from ..core.tokens import get_token_service

        try:
            admin_err = check_admin_permission()
            if admin_err:
                return admin_err

            return await get_token_service().revoke_token(token_hash)
        except Exception as e:
            from ..auth.context import safe_error

            return safe_error(e, "admin")

    @mcp.tool(
        annotations=ToolAnnotations(destructiveHint=True, idempotentHint=True),
    )
    async def admin_delete_token(
        token_hash: Annotated[
            str,
            Field(
                description=(
                    "Token hash from admin_list_tokens, in the format sha256: followed by 64 lowercase hexadecimal characters; the 'sha256:' prefix is optional and at least 16 hexadecimal characters are required"
                )
            ),
        ],
    ) -> dict:
        """Operator tool: not advertised in tools/list. Permanently delete a token record by its hash. Requires permission admin.
        """
        from ..auth.context import check_admin_permission
        from ..core.tokens import get_token_service

        try:
            admin_err = check_admin_permission()
            if admin_err:
                return admin_err

            return await get_token_service().delete_token(token_hash)
        except Exception as e:
            from ..auth.context import safe_error

            return safe_error(e, "admin")

    @mcp.tool(annotations=ToolAnnotations(destructiveHint=True, idempotentHint=False))
    async def admin_purge_tokens(
        revoked_only: Annotated[
            bool,
            Field(
                default=True,
                description='true deletes only revoked tokens; false deletes every stored token and requires confirm=true. Default: true',
            ),
        ] = True,
        confirm: Annotated[
            bool,
            Field(
                default=False,
                description=(
                    'true confirms the deletion of every token when revoked_only is false; false refuses it. Default: false'
                ),
            ),
        ] = False,
    ) -> dict:
        """Operator tool: not advertised in tools/list. Permanently delete the revoked tokens, or every token when revoked_only=false with confirm=true. Requires permission admin. The bootstrap key is not affected.
        """
        from ..auth.context import check_admin_permission
        from ..core.tokens import get_token_service

        try:
            admin_err = check_admin_permission()
            if admin_err:
                return admin_err

            # LM2-31 fix : exiger confirm=True pour la purge totale.
            # La purge des seuls révoqués reste possible sans confirm (nettoyage
            # courant, non-destructeur pour les agents actifs).
            if not revoked_only and not confirm:
                from .guidance import confirm_required

                return confirm_required(
                    (
                        "Full purge refused: confirm=True is required when "
                        "revoked_only=False. This operation deletes ALL tokens "
                        "and leaves the server accessible only through the "
                        "bootstrap_key. If this is intended, call the tool "
                        "again with confirm=True."
                    ),
                    "delete every token",
                )

            return await get_token_service().purge_tokens(revoked_only=revoked_only)
        except Exception as e:
            from ..auth.context import safe_error

            return safe_error(e, "admin")

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True))
    async def admin_update_token(
        token_hash: Annotated[
            str,
            Field(
                description=(
                    "Token hash from admin_list_tokens, in the format sha256: followed by 64 lowercase hexadecimal characters; the 'sha256:' prefix is optional and at least 16 hexadecimal characters are required"
                )
            ),
        ],
        space_ids: Annotated[
            str,
            Field(
                default="",
                description=(
                    "Replacement mode: complete comma-separated space scope, for example 'project-alpha,project-beta'; '*' or 'all' takes a snapshot of the current spaces; prefer space_ids_add for a safe additive update. Default: empty, scope unchanged"
                ),
            ),
        ] = "",
        permissions: Annotated[
            str,
            Field(
                default="",
                description=(
                    "New comma-separated subset of read,write,manage,admin, for example 'read,write'; promotion to admin clears the space scope and a downgrade starts with an empty scope. Default: empty, permissions unchanged"
                ),
            ),
        ] = "",
        email: Annotated[
            str,
            Field(
                default="",
                description="New owner email, for example 'owner@example.org'. Default: empty, unchanged",
            ),
        ] = "",
        space_ids_add: Annotated[
            str,
            Field(
                default="",
                description=(
                    "Delta mode: comma-separated spaces to add, for example 'project-beta'; idempotent and incompatible with replacement mode; '*' and 'all' are not accepted. Default: empty"
                ),
            ),
        ] = "",
        space_ids_remove: Annotated[
            str,
            Field(
                default="",
                description=(
                    "Delta mode: comma-separated spaces to remove, for example 'project-alpha'; idempotent; removals apply before additions. Default: empty"
                ),
            ),
        ] = "",
        operator_tools: Annotated[
            str,
            Field(
                default="",
                description=(
                    "One of 'true' (the token may call operator tools over MCP) or 'false' (refused). Default: empty, unchanged"
                ),
            ),
        ] = "",
    ) -> dict:
        """Operator tool: not advertised in tools/list. Update the permissions, email, space scope or operator tool access of one token; the scope changes either by complete replacement (space_ids) or by delta (space_ids_add and space_ids_remove), never both. Requires permission admin.
        """
        from ..auth.context import check_admin_permission
        from ..core.tokens import get_token_service

        try:
            admin_err = check_admin_permission()
            if admin_err:
                return admin_err

            return await get_token_service().update_token(
                token_hash=token_hash,
                space_ids=space_ids,
                permissions=permissions,
                email=email,
                space_ids_add=space_ids_add,
                space_ids_remove=space_ids_remove,
                operator_tools=operator_tools,
            )
        except Exception as e:
            from ..auth.context import safe_error

            return safe_error(e, "admin")

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True))
    async def admin_bulk_update_tokens(
        names: Annotated[
            str,
            Field(
                default="",
                description=(
                    "Comma-separated exact token names, for example 'agent-laptop,agent-desktop'; combines with name_contains and has_space (AND); at least one selection filter is required. Default: empty"
                ),
            ),
        ] = "",
        name_contains: Annotated[
            str,
            Field(
                default="",
                description=(
                    "Case-insensitive token-name substring, for example 'agent'; combines with names and has_space (AND). Default: empty"
                ),
            ),
        ] = "",
        has_space: Annotated[
            str,
            Field(
                default="",
                description=(
                    "Exact, case-sensitive space identifier the token scope must contain, for example 'project-alpha'; combines with the other filters (AND). Default: empty"
                ),
            ),
        ] = "",
        permissions: Annotated[
            str,
            Field(
                default="",
                description=(
                    "New comma-separated subset of read,write,manage,admin for the selected tokens, for example 'read,write'; promotion to admin clears the space scopes and a downgrade applies deltas from an empty scope. Default: empty, unchanged"
                ),
            ),
        ] = "",
        email: Annotated[
            str,
            Field(
                default="",
                description="New email for the selected tokens, for example 'owner@example.org'. Default: empty, unchanged",
            ),
        ] = "",
        space_ids_add: Annotated[
            str,
            Field(
                default="",
                description=(
                    "Comma-separated spaces to add, for example 'project-beta'; idempotent; '*' and 'all' are not accepted. Default: empty"
                ),
            ),
        ] = "",
        space_ids_remove: Annotated[
            str,
            Field(
                default="",
                description=(
                    "Comma-separated spaces to remove, for example 'project-alpha'; idempotent; '*' and 'all' are not accepted. Default: empty"
                ),
            ),
        ] = "",
        include_revoked: Annotated[
            bool,
            Field(
                default=False,
                description=(
                    'true includes revoked tokens in the update; false skips them and returns them in skipped_revoked. Default: false'
                ),
            ),
        ] = False,
        operator_tools: Annotated[
            str,
            Field(
                default="",
                description=(
                    "One of 'true' (the selected tokens may call operator tools over MCP) or 'false' (refused). Default: empty, unchanged"
                ),
            ),
        ] = "",
    ) -> dict:
        """Operator tool: not advertised in tools/list. Update several tokens selected by names, name_contains or has_space in one persisted write; permission, email and operator tool changes apply to every selected token and space deltas are idempotent. Requires permission admin.
        """
        from ..auth.context import check_admin_permission
        from ..core.tokens import get_token_service

        try:
            admin_err = check_admin_permission()
            if admin_err:
                return admin_err

            return await get_token_service().bulk_update_tokens(
                names=names,
                name_contains=name_contains,
                has_space=has_space,
                permissions=permissions,
                email=email,
                space_ids_add=space_ids_add,
                space_ids_remove=space_ids_remove,
                include_revoked=include_revoked,
                operator_tools=operator_tools,
            )
        except Exception as e:
            from ..auth.context import safe_error

            return safe_error(e, "admin")

    @mcp.tool(
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True),
    )
    async def admin_gc_notes(
        space_id: Annotated[
            str,
            Field(
                default="", description="Target space, for example 'project-alpha'. Default: empty, which scans every space"
            ),
        ] = "",
        max_age_days: Annotated[
            int,
            Field(
                default=7,
                ge=0,
                description='Age in days from which a note is orphaned, 0 or more. Default: 7',
            ),
        ] = 7,
        confirm: Annotated[
            bool,
            Field(
                default=False,
                description='false runs a dry run (scan only); true consolidates, or deletes with delete_only. Default: false',
            ),
        ] = False,
        delete_only: Annotated[
            bool,
            Field(
                default=False,
                description='true deletes without consolidating (data loss) when confirm is true; false consolidates. Default: false',
            ),
        ] = False,
        expected_eligible_set_token: Annotated[
            str,
            Field(
                default="",
                description=(
                    'Opaque precondition returned by the previous dry run, in the format the dry run prints; required with confirm=true and delete_only=true. Default: empty'
                ),
            ),
        ] = "",
    ) -> dict:
        """Operator tool: not advertised in tools/list. Scan (confirm=false), consolidate (confirm=true) or delete (confirm=true with delete_only=true and expected_eligible_set_token) the orphaned short-term notes older than max_age_days, in one space or in every space. Requires permission admin.
        """
        from ..auth.context import check_admin_permission
        from ..core.engines import RegistryRefused
        from ..core.gc import get_gc_service
        from ..core.hivemind import CorruptedStateError
        from ..core.write_sink import StagedWriteNotImplemented
        from .guidance import write_route_refused

        try:
            admin_err = check_admin_permission()
            if admin_err:
                return admin_err

            # Défense en profondeur pour les appels directs à la fonction
            # enregistrée (les clients MCP sont déjà bornés par ``Field(ge=0)``).
            # Un seuil négatif déplacerait le cutoff dans le futur et pourrait
            # rendre éligibles des notes qui ne sont pas anciennes.
            if max_age_days < 0:
                from .guidance import ErrorKind, argument_refusal

                return argument_refusal(
                    "max_age_days must be greater than or equal to 0.",
                    kind=ErrorKind.INVALID_ARGUMENT,
                    arguments=["max_age_days"],
                    reason="max_age_days is a whole number of days, 0 or more, for example 30.",
                    extra_fields={"reason": "invalid_max_age_days"},
                )

            gc = get_gc_service()

            if confirm and delete_only:
                # Mode suppression sans consolidation (perte de données)
                return await gc.delete_old_notes(
                    space_id=space_id,
                    max_age_days=max_age_days,
                    expected_eligible_set_token=expected_eligible_set_token,
                )
            elif confirm:
                # Mode consolidation (défaut avec confirm)
                return await gc.consolidate_old_notes(
                    space_id=space_id,
                    max_age_days=max_age_days,
                )
            else:
                # Mode dry-run : scanner seulement
                result = await gc.scan_old_notes(
                    space_id=space_id,
                    max_age_days=max_age_days,
                )
                for sid in result.get("spaces", {}):
                    if "keys" in result["spaces"][sid]:
                        count = len(result["spaces"][sid]["keys"])
                        del result["spaces"][sid]["keys"]
                        result["spaces"][sid]["keys_count"] = count
                result["mode"] = "dry-run"
                result["message"] = (
                    f"Dry run: {result['total_old_notes']} orphaned notes "
                    f"found. Use confirm=True to consolidate, or "
                    f"confirm=True+delete_only=True with "
                    f"expected_eligible_set_token to delete."
                )
                return result

        except StagedWriteNotImplemented:
            return write_route_refused(
                (
                    "GC refused: staged Hivemind writes are not supported "
                    "for this operation."
                ),
                "The space is shared; an operator runs the GC on the space's "
                "source node. Do not retry on another route.",
                extra_fields={"reason": "route_staged_not_implemented"},
            )
        except RegistryRefused:
            return write_route_refused(
                (
                    "GC refused: the space cannot be modified in its current "
                    "Hivemind state."
                ),
                "An operator resolves the space's recovery state (resync or "
                "re-import) before the GC can run. Do not retry on another route.",
                extra_fields={"reason": "route_refused"},
            )
        except CorruptedStateError:
            return write_route_refused(
                (
                    "GC refused: the space's critical Hivemind state is "
                    "corrupted."
                ),
                "An operator repairs or resyncs the space's critical state "
                "before the GC can run. Do not retry on another route.",
                extra_fields={"reason": "state_corrupt"},
            )
        except Exception as e:
            from ..auth.context import safe_error

            return safe_error(e, "admin")

    return 9  # Nombre d'outils enregistrés
