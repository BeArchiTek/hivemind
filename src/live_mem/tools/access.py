# -*- coding: utf-8 -*-
"""Outils MCP — provisionnement délégué manage (LM2-11).

Ces outils sont volontairement séparés des opérations ``admin_*`` : un token
``manage`` peut créer un credential non-admin sans scope initial puis l'inviter
dans un espace qu'il gère. Il ne peut ni lister, révoquer, rescope négativement
ou promouvoir un token admin.
"""

from typing import Annotated, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field


def register(mcp: MCPServer) -> int:
    """Enregistre les deux outils cross-cutting LM2-11 (aucun alias tier)."""

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=False))
    async def token_create(
        name: Annotated[
            str,
            Field(description="Descriptive name of the token, for example 'agent-cline'"),
        ],
        permissions: Annotated[
            Literal["read", "read,write", "read,write,manage"],
            Field(
                description=(
                    "Permission profile, one of 'read', 'read,write' or 'read,write,manage'"
                )
            ),
        ],
        expires_in_days: Annotated[
            int,
            Field(
                default=0,
                ge=0,
                description='Lifetime in days, 0 or more; 0 means no expiration. Default: 0',
            ),
        ] = 0,
        email: Annotated[
            str,
            Field(default="", description="Owner email address, for example 'owner@example.org'. Default: empty"),
        ] = "",
    ) -> dict:
        """Create a non-admin token without initial space access.
        Use when: a manager provisions a credential for a new agent.
        Do not use when: the token needs access to a space (call space_invite_token with its hash) or an existing token must change (an administrator calls admin_update_token).
        Before calling: permission manage; no space access needed; no prior call.
        Peer tools: space_invite_token (after), system_whoami (the new token views its scope).
        Result: the secret is returned once; store it outside notes, instructions and logs, then invite the token to its space.
        """
        from ..auth.context import (
            check_manage_permission,
            get_effective_token_info,
            safe_error,
        )
        from ..core.tokens import get_token_service

        try:
            manage_err = check_manage_permission()
            if manage_err:
                return manage_err
            actor = get_effective_token_info()
            actor_hash = actor.get("token_hash", "") if actor else ""
            return await get_token_service().create_delegated_token(
                actor_token_hash=actor_hash,
                name=name,
                permissions=permissions,
                expires_in_days=expires_in_days,
                email=email,
            )
        except Exception as exc:
            return safe_error(exc, "token_create")

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True))
    async def space_invite_token(
        space_id: Annotated[
            str,
            Field(description="Space identifier: letters, digits, hyphens and underscores, at most 64 characters, for example 'project-alpha'"),
        ],
        token_hash: Annotated[
            str,
            Field(
                description=(
                    'Complete target token hash in the format sha256: followed by 64 lowercase hexadecimal characters'
                )
            ),
        ],
    ) -> dict:
        """Grant an active token access to a space the caller manages.
        Use when: a manager gives an agent token access to a space, including a fresh token with no space.
        Do not use when: the token should lose access (an administrator calls admin_update_token).
        Before calling: permission manage; access to space_id; the target token hash from token_create or admin_list_tokens.
        Peer tools: token_create (before), system_whoami (the invited token views its spaces).
        Result: the token can call the space tools; the call is idempotent.
        """
        from ..auth.context import (
            check_access,
            check_manage_permission,
            get_effective_token_info,
            safe_error,
        )
        from ..core.tokens import get_token_service

        try:
            access_err = check_access(space_id)
            if access_err:
                return access_err
            manage_err = check_manage_permission()
            if manage_err:
                return manage_err
            actor = get_effective_token_info()
            actor_hash = actor.get("token_hash", "") if actor else ""
            return await get_token_service().invite_token_to_space(
                actor_token_hash=actor_hash,
                space_id=space_id,
                target_token_hash=token_hash,
            )
        except Exception as exc:
            return safe_error(exc, "space_invite_token")

    return 2
