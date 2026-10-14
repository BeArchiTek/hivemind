# -*- coding: utf-8 -*-
"""
Outils MCP — Catégorie Backup (5 outils).

Sauvegarde et restauration d'espaces mémoire.

Permissions :
    - backup_create   ✏️ (write)   — Crée un snapshot d'espace
    - backup_list     🔑 (read)    — Liste les backups disponibles
    - backup_restore  🔧 (manage)  — Restaure un espace depuis un backup
    - backup_download 🔑 (read)    — Télécharge un backup (tar.gz base64)
    - backup_delete   🔧 (manage)  — Supprime un backup

Les backups sont des snapshots complets stockés dans _backups/ sur S3.
Voir S3_DATA_MODEL.md pour l'arborescence.
"""

import re
from typing import Annotated

from mcp.server.mcpserver import MCPServer

from ..auth.context import authentication_required
from mcp.types import ToolAnnotations
from pydantic import Field


# LM2-09 fix : validation stricte du format backup_id avant tout accès S3.
# Le space_id doit matcher SPACE_ID_REGEX (déjà validé par check_access),
# et le timestamp doit matcher le format ISO produit par BackupService.create.
_SPACE_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}(?:-[0-9a-f]{32})?$"
)


def _parse_backup_id(backup_id: str) -> tuple[str | None, str | None, dict | None]:
    """
    LM2-09 fix : parse et valide un backup_id au format ``"space_id/timestamp"``.

    Returns:
        Tuple ``(space_id, timestamp, error)`` :

        - Si OK : ``(space_id, timestamp, None)``
        - Si invalide : ``(None, None, {"status": "error", ...})``

    Sécurité : la validation regex est appliquée AVANT tout accès S3
    pour empêcher path traversal (ex: ``"../_system/foo"``) ou injection
    de préfixes système.
    """
    from .guidance import ErrorKind, argument_refusal

    format_reason = (
        "backup_id has the format space_id/timestamp, for example "
        "'project-alpha/2026-03-08T10-00-00'; backup_list shows the identifiers."
    )
    if not backup_id or not isinstance(backup_id, str):
        return (
            None,
            None,
            argument_refusal(
                "backup_id is required",
                kind=ErrorKind.MISSING_ARGUMENT,
                arguments=["backup_id"],
                reason=format_reason,
            ),
        )

    parts = backup_id.split("/", 1)
    if len(parts) != 2:
        return (
            None,
            None,
            argument_refusal(
                "Invalid backup_id (expected format: space_id/timestamp)",
                kind=ErrorKind.INVALID_ARGUMENT,
                arguments=["backup_id"],
                reason=format_reason,
            ),
        )

    sid, ts = parts
    if not _SPACE_ID_RE.match(sid):
        return (
            None,
            None,
            argument_refusal(
                f"Invalid space_id in backup_id: '{sid[:64]}'",
                kind=ErrorKind.INVALID_ARGUMENT,
                arguments=["backup_id"],
                reason=(
                    "The space_id part of backup_id contains only letters, digits, "
                    "hyphens and underscores, for example 'project-alpha'."
                ),
            ),
        )
    if not _TIMESTAMP_RE.match(ts):
        return (
            None,
            None,
            argument_refusal(
                (
                    f"Invalid timestamp in backup_id: '{ts[:32]}' "
                    "(expected: YYYY-MM-DDTHH-MM-SS or a suffixed operation id)"
                ),
                kind=ErrorKind.INVALID_ARGUMENT,
                arguments=["backup_id"],
                reason=(
                    "The timestamp part of backup_id has the format "
                    "YYYY-MM-DDTHH-MM-SS, for example '2026-03-08T10-00-00'."
                ),
            ),
        )

    return sid, ts, None


def register(mcp: MCPServer) -> int:
    """
    Enregistre les 5 outils backup sur l'instance MCP.

    Args:
        mcp: Instance MCPServer

    Returns:
        Nombre d'outils enregistrés (5)
    """

    @mcp.tool(
        annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=False),
    )
    async def backup_create(
        space_id: Annotated[
            str,
            Field(
                description="Space to back up, for example 'project-alpha'; empty backs up every space and requires permission admin"
            ),
        ],
        description: Annotated[
            str,
            Field(
                default="",
                description="Backup description, for example 'before migration'. Default: empty",
            ),
        ] = "",
    ) -> dict:
        """Operator tool: not advertised in tools/list. Create a backup of one space, or of every space when space_id is empty (permission admin). Requires permission write and access to space_id.
        """
        from ..auth.context import (
            check_access,
            check_write_permission,
            check_admin_permission,
        )
        from ..core.backup import get_backup_service

        try:
            if not space_id:
                # Backup ALL spaces — admin only
                admin_err = check_admin_permission()
                if admin_err:
                    return admin_err
                return await get_backup_service().create_all(description)
            else:
                # Backup single space — write permission
                access_err = check_access(space_id)
                if access_err:
                    return access_err

                write_err = check_write_permission()
                if write_err:
                    return write_err

                return await get_backup_service().create(space_id, description)
        except Exception as e:
            from ..auth.context import safe_error

            return safe_error(e, "backup")

    @mcp.tool(
        annotations=ToolAnnotations(readOnlyHint=True),
    )
    async def backup_list(
        space_id: Annotated[
            str,
            Field(
                default="",
                description="Space filter, for example 'project-alpha'. Default: empty, every accessible space",
            ),
        ] = "",
    ) -> dict:
        """Operator tool: not advertised in tools/list. List the backups the token can view, for one space or for every accessible space. Requires permission read.
        """
        # HM-04 fix : lire le store FRAIS (_get_effective_token_info) et non le
        # contextvar figé. Dans une session MCP Streamable-HTTP, current_token_info
        # est la copie gelée à l'init de session — un token rétrogradé/rescopé
        # en cours de session continuait à filtrer sur ses anciennes permissions
        # (persistance de privilège). Aligne backup_list sur ses sœurs
        # (space_list, bank_stale_spaces, bank_consolidation_queues).
        from ..auth.context import _get_effective_token_info, check_access
        from ..core.backup import get_backup_service

        try:
            token_info = _get_effective_token_info()
            if token_info is None:
                return authentication_required()

            # Si un espace est spécifié, vérifier l'accès
            if space_id:
                access_err = check_access(space_id)
                if access_err:
                    return access_err

            result = await get_backup_service().list_backups(space_id)

            # Filtrage par space_ids du token (alignement Graph Memory v0.7.0)
            # Un client ne doit voir que les backups des spaces autorisés.
            # Admin bypass. Non-admin + allowed=[] → aucun backup (v1.5.0).
            permissions = token_info.get("permissions", [])
            allowed = token_info.get("allowed_resources", [])
            if "admin" not in permissions and result.get("status") == "ok" and not space_id:
                filtered = [
                    b
                    for b in result.get("backups", [])
                    if b.get("space_id", b.get("backup_id", "").split("/")[0])
                    in allowed
                ]
                result["backups"] = filtered
                result["total"] = len(filtered)
                result["filtered_by_token"] = True

            return result
        except Exception as e:
            from ..auth.context import safe_error

            return safe_error(e, "backup")

    @mcp.tool(
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=True, idempotentHint=False
        )
    )
    async def backup_restore(
        backup_id: Annotated[
            str,
            Field(description="Backup identifier in the format space_id/timestamp, for example 'project-alpha/2026-03-08T10-00-00'"),
        ],
        confirm: Annotated[
            bool,
            Field(
                default=False,
                description='true confirms the restore; false refuses it. Default: false',
            ),
        ] = False,
        unsafe_recovery: Annotated[
            bool,
            Field(
                default=False,
                description=(
                    'true allows a forward-only restore over an existing shared space in an unsafe recovery state (records an audit event, requires peer resynchronization, never bypasses corruption checks); false refuses it. Default: false'
                ),
            ),
        ] = False,
    ) -> dict:
        """Operator tool: not advertised in tools/list. Restore a space from a backup; confirm=true is required, the target space must not exist unless unsafe_recovery=true, and corrupt state always fails closed. Requires permission manage and access to the backup's space.
        """
        from ..auth.context import check_access, check_manage_permission
        from ..core.backup import get_backup_service

        try:
            # LM2-09 fix : valider le format backup_id AVANT tout accès S3
            space_id, _ts, parse_err = _parse_backup_id(backup_id)
            if parse_err:
                return parse_err

            # LM2-29 fix : vérifier l'accès à l'espace en plus de la permission
            # manage. Sans cela, un opérateur manage restreint à `["project-a"]`
            # pouvait restaurer un backup de `project-b` (cross-tenant leak).
            access_err = check_access(space_id)
            if access_err:
                return access_err

            manage_err = check_manage_permission()
            if manage_err:
                return manage_err

            if not confirm:
                from .guidance import confirm_required

                return confirm_required(
                    "Restore refused: confirm=True is required.",
                    "restore the backup over the space",
                )

            return await get_backup_service().restore(
                backup_id, unsafe_recovery=unsafe_recovery
            )
        except Exception as e:
            from ..auth.context import safe_error

            return safe_error(e, "backup")

    @mcp.tool(
        annotations=ToolAnnotations(readOnlyHint=True),
    )
    async def backup_download(
        backup_id: Annotated[
            str,
            Field(description="Backup identifier in the format space_id/timestamp, for example 'project-alpha/2026-03-08T10-00-00'"),
        ],
    ) -> dict:
        """Operator tool: not advertised in tools/list. Download a backup as a base64-encoded tar archive. Requires permission read and access to the backup's space.
        """
        # HM-04 fix : cohérence — store frais plutôt que contextvar figé.
        from ..auth.context import _get_effective_token_info, check_access
        from ..core.backup import get_backup_service

        try:
            token_info = _get_effective_token_info()
            if token_info is None:
                return authentication_required()

            # LM2-09 fix : valider strictement le backup_id avant accès S3
            space_id, _ts, parse_err = _parse_backup_id(backup_id)
            if parse_err:
                return parse_err

            access_err = check_access(space_id)
            if access_err:
                return access_err

            return await get_backup_service().download(backup_id)
        except Exception as e:
            from ..auth.context import safe_error

            return safe_error(e, "backup")

    @mcp.tool(
        annotations=ToolAnnotations(destructiveHint=True, idempotentHint=True),
    )
    async def backup_delete(
        backup_id: Annotated[
            str,
            Field(description="Backup identifier in the format space_id/timestamp, for example 'project-alpha/2026-03-08T10-00-00'"),
        ],
        confirm: Annotated[
            bool,
            Field(
                default=False,
                description='true confirms the permanent deletion; false refuses it. Default: false',
            ),
        ] = False,
    ) -> dict:
        """Operator tool: not advertised in tools/list. Permanently delete a backup; confirm=true is required. Requires permission manage and access to the backup's space.
        """
        from ..auth.context import check_access, check_manage_permission
        from ..core.backup import get_backup_service

        try:
            # LM2-09 fix : valider le format backup_id AVANT tout accès S3
            space_id, _ts, parse_err = _parse_backup_id(backup_id)
            if parse_err:
                return parse_err

            # LM2-29 fix : check_access en plus de check_manage_permission
            # (cf. backup_restore — même rationale anti cross-tenant).
            access_err = check_access(space_id)
            if access_err:
                return access_err

            manage_err = check_manage_permission()
            if manage_err:
                return manage_err

            if not confirm:
                from .guidance import confirm_required

                return confirm_required(
                    "Deletion refused: confirm=True is required.",
                    "delete the backup",
                )

            return await get_backup_service().delete(backup_id)
        except Exception as e:
            from ..auth.context import safe_error

            return safe_error(e, "backup")

    return 5  # Nombre d'outils enregistrés
