# -*- coding: utf-8 -*-
"""
Outils MCP — Catégorie Space (9 outils).

Gestion des espaces mémoire : créer, lister, inspecter, exporter, supprimer.

Permissions :
    - space_create        🔧 (manage)  — Crée un nouvel espace
    - space_update        ✏️ (write)   — Met à jour description/owner
    - space_update_rules  🔧 (manage)  — Met à jour les rules d'un espace
    - space_list          🔑 (read)    — Liste les espaces accessibles
    - space_info          🔑 (read)    — Infos détaillées d'un espace
    - space_rules         🔑 (read)    — Lit les rules
    - space_summary       🔑 (read)    — Synthèse complète (rules + bank)
    - space_export        🔑 (read)    — Export tar.gz en base64
    - space_delete        🔧 (manage)  — Supprime un espace (irréversible)

Chaque outil délègue au SpaceService (core/space.py) après vérification
des permissions via les helpers auth/context.py.
"""

from typing import Annotated

from mcp.server.mcpserver import MCPServer

from ..auth.context import authentication_required
from mcp.types import ToolAnnotations
from pydantic import Field


def register(mcp: MCPServer) -> int:
    """
    Enregistre les 9 outils space sur l'instance MCP.

    Args:
        mcp: Instance MCPServer

    Returns:
        Nombre d'outils enregistrés (9)
    """

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=False))
    async def space_create(
        space_id: Annotated[
            str,
            Field(
                description=(
                    "Unique space identifier: letters, digits, hyphens and underscores, at most 64 characters, for example 'project-alpha'"
                )
            ),
        ],
        description: Annotated[
            str, Field(description="Short description of the space, for example 'Payment service memory'")
        ],
        rules: Annotated[
            str,
            Field(
                default="",
                description=(
                    'Markdown consolidation rules that define the memory-bank structure, for example the content of RULES/live-mem.standard.memory.bank.md. Default: empty, which loads the configured default rules file'
                ),
            ),
        ] = "",
        owner: Annotated[
            str,
            Field(
                default="",
                description="Informational owner of the space, for example 'platform-team'. Default: empty",
            ),
        ] = "",
    ) -> dict:
        """Create a memory space with its consolidation rules.
        Use when: an operator provisions a new space for a project.
        Do not use when: the space exists (call space_info) or its rules must change (an operator calls space_update_rules).
        Before calling: permission manage; the new space_id must match the identifier format; a token with read,write cannot create a space, an operator must.
        Peer tools: space_invite_token (after, to grant agent tokens access), space_rules (after, to read the rules back).
        Result: the space exists; invite the agent tokens, which then call space_rules, mid_read_all and short_read.
        """
        from pathlib import Path
        from ..auth.context import (
            check_manage_permission,
            get_effective_token_info,
        )
        from ..config import get_settings
        from ..core.space import get_space_service
        from .guidance import ErrorKind, argument_refusal

        rules_reason = (
            "Pass rules as the Markdown consolidation rules of the space, for "
            "example the content of a template under RULES/ such as "
            "live-mem.standard.memory.bank.md."
        )

        try:
            # LM2-11 : provisionner un espace est une opération manage, pas une
            # écriture ordinaire. SpaceService revalide ensuite tout token S3
            # exact sous le verrou tokens ; ce check est l'early deny MCP.
            manage_err = check_manage_permission()
            if manage_err:
                return manage_err
            actor = get_effective_token_info()
            if actor is None:
                return authentication_required()

            # Si rules vide, charger les rules par défaut
            effective_rules = rules
            if not effective_rules.strip():
                settings = get_settings()
                if settings.default_rules_file:
                    rules_path = Path(settings.default_rules_file)
                    if rules_path.is_file():
                        effective_rules = rules_path.read_text(encoding="utf-8")
                    else:
                        return argument_refusal(
                            f"Default rules file not found: {settings.default_rules_file}",
                            kind=ErrorKind.MISSING_ARGUMENT,
                            arguments=["rules"],
                            reason=rules_reason,
                        )
                else:
                    return argument_refusal(
                        (
                            "The 'rules' parameter is required. "
                            "No default rules file is configured (DEFAULT_RULES_FILE)."
                        ),
                        kind=ErrorKind.MISSING_ARGUMENT,
                        arguments=["rules"],
                        reason=rules_reason,
                    )

            return await get_space_service().create(
                space_id=space_id,
                description=description,
                rules=effective_rules,
                owner=owner,
                actor_token_hash=actor.get("token_hash", ""),
                bootstrap_admin=(
                    actor.get("type") == "bootstrap"
                    and "admin" in actor.get("permissions", [])
                ),
            )
        except Exception as e:
            from ..auth.context import safe_error

            return safe_error(e, "space")

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True))
    async def space_update(
        space_id: Annotated[
            str, Field(description="Space identifier: letters, digits, hyphens and underscores, at most 64 characters, for example 'project-alpha'")
        ],
        description: Annotated[
            str,
            Field(
                default="",
                description="New description, for example 'Payment service memory'. Default: empty, no change",
            ),
        ] = "",
        owner: Annotated[
            str,
            Field(
                default="",
                description="New owner, for example 'platform-team'. Default: empty, no change",
            ),
        ] = "",
    ) -> dict:
        """Operator tool: not advertised in tools/list. Update the description or owner of a space; only non-empty fields change. Requires permission write and access to space_id. Rules change through space_update_rules.
        """
        from ..auth.context import check_access, check_write_permission
        from ..core.space import get_space_service

        try:
            access_err = check_access(space_id)
            if access_err:
                return access_err

            write_err = check_write_permission()
            if write_err:
                return write_err

            return await get_space_service().update(
                space_id=space_id,
                description=description if description else None,
                owner=owner if owner else None,
            )
        except Exception as e:
            from ..auth.context import safe_error

            return safe_error(e, "space")

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True))
    async def space_update_rules(
        space_id: Annotated[str, Field(description="Space identifier: letters, digits, hyphens and underscores, at most 64 characters, for example 'project-alpha'")],
        rules: Annotated[str, Field(description='Complete replacement rules in Markdown, for example the content of a template under RULES/')],
    ) -> dict:
        """Operator tool: not advertised in tools/list. Replace the consolidation rules of a space with a complete Markdown document, without recreating the space. Requires permission manage and access to space_id. Agents read the rules with space_rules.
        """
        from ..auth.context import check_access, check_manage_permission
        from ..core.space import get_space_service

        try:
            access_err = check_access(space_id)
            if access_err:
                return access_err

            manage_err = check_manage_permission()
            if manage_err:
                return manage_err

            return await get_space_service().update_rules(
                space_id=space_id,
                rules=rules,
            )
        except Exception as e:
            from ..auth.context import safe_error

            return safe_error(e, "space")

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def space_list(
        include_counts: Annotated[bool, Field(description='true includes short-note and bank-file counts; false reads only space metadata. Default: true')] = True,
    ) -> dict:
        """List the memory spaces this token can access, with their statistics.
        Use when: the agent does not know which space to work in, or a refusal listed accessible_spaces.
        Do not use when: the agent already knows its space (call space_rules, mid_read_all and short_read) or wants to create one (an operator calls space_create).
        Before calling: permission read; no space access needed; no prior call.
        Peer tools: system_whoami (scope), space_info (one space in detail).
        Result: pick the space_id from spaces; an empty list means a manager must invite the token with space_invite_token.
        """
        from ..auth.context import _get_effective_token_info
        from ..core.space import get_space_service

        try:
            # Récupérer les space_ids autorisés depuis le token (données fraîches)
            token_info = _get_effective_token_info()
            if token_info is None:
                return authentication_required()

            permissions = token_info.get("permissions", [])
            allowed = token_info.get("allowed_resources", [])
            # Admin → accès à tous les espaces
            # Non-admin + allowed vide → aucun espace (v1.5.0)
            if "admin" in permissions:
                allowed_ids = None  # Pas de filtre
            elif not allowed:
                allowed_ids = []  # Aucun espace
            else:
                allowed_ids = allowed

            return await get_space_service().list_spaces(allowed_space_ids=allowed_ids, include_counts=include_counts)
        except Exception as e:
            from ..auth.context import safe_error

            return safe_error(e, "space")

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def space_info(
        space_id: Annotated[str, Field(description="Space identifier: letters, digits, hyphens and underscores, at most 64 characters, for example 'project-alpha'")],
    ) -> dict:
        """Return the metadata and statistics of one space.
        Use when: the agent needs note counts, bank file statistics or the consolidation status of a space.
        Do not use when: the agent needs the rules (call space_rules) or the bank content (call mid_read_all).
        Before calling: permission read; access to space_id; no prior call.
        Peer tools: space_list (before), space_summary (rules, bank and statistics in one call).
        Result: read the counts; no action is needed.
        """
        from ..auth.context import check_access
        from ..core.space import get_space_service

        try:
            access_err = check_access(space_id)
            if access_err:
                return access_err

            return await get_space_service().get_info(space_id)
        except Exception as e:
            from ..auth.context import safe_error

            return safe_error(e, "space")

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def space_rules(
        space_id: Annotated[str, Field(description="Space identifier: letters, digits, hyphens and underscores, at most 64 characters, for example 'project-alpha'")],
    ) -> dict:
        """Read the consolidation rules of a space: the memory-bank structure the consolidator follows.
        Use when: at the start of every task, as the first of the three start-of-task calls, and before writing notes in a space the agent has not read yet.
        Do not use when: the agent wants to change the rules; an operator with permission manage calls space_update_rules.
        Before calling: permission read; access to space_id; no prior call.
        Peer tools: mid_read_all and short_read (after, the other two start-of-task calls), space_summary (the three in one call).
        Result: read the rules before writing notes; if the call fails, times out or returns a non-ok status, stop before any mutation.
        """
        from ..auth.context import check_access
        from ..core.space import get_space_service

        try:
            access_err = check_access(space_id)
            if access_err:
                return access_err

            return await get_space_service().get_rules(space_id)
        except Exception as e:
            from ..auth.context import safe_error

            return safe_error(e, "space")

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def space_summary(
        space_id: Annotated[str, Field(description="Space identifier: letters, digits, hyphens and underscores, at most 64 characters, for example 'project-alpha'")],
    ) -> dict:
        """Load a space bootstrap in one call: rules, every memory-bank file, the residual summary and statistics.
        Use when: the agent needs the whole compact context of a space in one request at session start.
        Do not use when: the agent needs recent notes (call short_read, which this tool does not include) or one bank file again (call mid_read).
        Before calling: permission read; access to space_id; no prior call.
        Peer tools: space_rules and mid_read_all (the same content as separate calls), short_read (after).
        Result: read the rules and bank files to identify the current focus before working.
        """
        from ..auth.context import check_access
        from ..core.space import get_space_service

        try:
            access_err = check_access(space_id)
            if access_err:
                return access_err

            return await get_space_service().get_summary(space_id)
        except Exception as e:
            from ..auth.context import safe_error

            return safe_error(e, "space")

    @mcp.tool(
        annotations=ToolAnnotations(readOnlyHint=True),
    )
    async def space_export(
        space_id: Annotated[
            str, Field(description="Space identifier: letters, digits, hyphens and underscores, at most 64 characters, for example 'project-alpha'")
        ],
    ) -> dict:
        """Operator tool: not advertised in tools/list. Export a space as a base64-encoded tar archive. Requires permission read and access to space_id.
        """
        from ..auth.context import check_access
        from ..core.space import get_space_service

        try:
            access_err = check_access(space_id)
            if access_err:
                return access_err

            return await get_space_service().export_space(space_id)
        except Exception as e:
            from ..auth.context import safe_error

            return safe_error(e, "space")

    @mcp.tool(
        annotations=ToolAnnotations(destructiveHint=True, idempotentHint=False),
    )
    async def space_delete(
        space_id: Annotated[
            str, Field(description="Space identifier: letters, digits, hyphens and underscores, at most 64 characters, for example 'project-alpha'")
        ],
        confirm: Annotated[
            bool,
            Field(
                default=False,
                description='true confirms the deletion; false refuses it. Default: false',
            ),
        ] = False,
        unsafe_recovery: Annotated[
            bool,
            Field(
                default=False,
                description=(
                    'true allows the deletion of a shared space in an unsafe recovery state (corrupt state still fails closed); false refuses it. Default: false'
                ),
            ),
        ] = False,
        recover_access_grants: Annotated[
            bool,
            Field(
                default=False,
                description=(
                    'true removes surviving token scopes only when the space prefix is already confirmed empty after an older or interrupted deletion; false keeps them. Never set it merely because an identifier is absent: it can destroy intentional pre-grants. Default: false'
                ),
            ),
        ] = False,
    ) -> dict:
        """Operator tool: not advertised in tools/list. Permanently delete a space and its coordinated state; confirm=true is required and the deletion cannot be undone. Requires permission manage and access to space_id.
        """
        from ..auth.context import (
            check_access,
            check_manage_permission,
            get_effective_token_info,
        )
        from ..core.space import get_space_service

        try:
            # Double vérification : accès + manage
            access_err = check_access(space_id)
            if access_err:
                return access_err

            manage_err = check_manage_permission()
            if manage_err:
                return manage_err
            actor = get_effective_token_info()
            if actor is None:
                return authentication_required()

            # Sécurité : confirm obligatoire
            if not confirm:
                from .guidance import confirm_required

                return confirm_required(
                    (
                        "Deletion refused: confirm=True is required. "
                        "This operation cannot be undone."
                    ),
                    "delete the space",
                )

            return await get_space_service().delete(
                space_id,
                unsafe_recovery=unsafe_recovery,
                recover_access_grants=recover_access_grants,
                actor_token_hash=actor.get("token_hash", ""),
                bootstrap_admin=(
                    actor.get("type") == "bootstrap"
                    and "admin" in actor.get("permissions", [])
                ),
            )
        except Exception as e:
            from ..auth.context import safe_error

            return safe_error(e, "space")

    return 9  # Nombre d'outils enregistrés
