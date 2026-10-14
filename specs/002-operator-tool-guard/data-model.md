# Data model: token attribute and request identity

**Summary**: One boolean field is added to the stored bearer token record and
copied into the per-request identity. No other entity changes.

## Bearer token record (`TokenInfo`, `src/live_mem/core/models.py:259-278`)

| Field | Type | Existing or new | Rule |
|---|---|---|---|
| `hash` | text | existing | `sha256:` + 64 lowercase hex |
| `name` | text | existing | client name; non-empty |
| `email` | text | existing | optional |
| `permissions` | list of text | existing | subset of `read`, `write`, `manage`, `admin`; no duplicates |
| `space_ids` | list of text | existing | `[]` for admin |
| `created_at`, `expires_at`, `last_used_at` | text or null | existing | ISO 8601 |
| `revoked` | boolean | existing | non-boolean refuses the store load |
| `operator_tools` | boolean | new | absent reads as `true`; non-boolean refuses the store load |

Validation rule added to `TokenService._store_from_data`
(`src/live_mem/core/tokens.py:1932-2055`): when the key `operator_tools` is
present, its value must be a boolean, otherwise the store is corrupt and the
load fails, as for `revoked`.

State transitions of `operator_tools`:

- Creation through `token_create` (`create_delegated_token`): `false`.
- Creation through `admin_create_token` (`create_token`): the `operator_tools`
  argument, default `true`.
- Change through `admin_update_token` (`update_token`) or
  `admin_bulk_update_tokens` (`bulk_update_tokens`): `"true"` or `"false"`
  text argument; empty text leaves it unchanged; other text is refused before
  the store is loaded.
- Records written before the feature: the key is absent in the file and the
  model reads `true`; the next save writes the key explicitly.

## Request identity (`request_token_info_snapshot`, `src/live_mem/auth/context.py:87-115`)

| Field | Source | Rule |
|---|---|---|
| `type` | validation | `token` or `bootstrap` |
| `client_name` | record `name` | |
| `token_hash` | record `hash` | `None` for bootstrap |
| `email`, `created_at`, `expires_at` | record | |
| `permissions`, `allowed_resources` | record | copied lists |
| `operator_tools` (new) | record `operator_tools` | absent for bootstrap, which the guard reads as allowed |

The identity is attached to the ASGI request state on every HTTP request and
read by the guard through `get_mcp_request_token_info()` on `/mcp` and
`get_effective_token_info()` on `/api/tool`.

## Exposure registry entry (`ToolExposure`, `src/live_mem/tools/exposure.py:47-57`)

Unchanged. The guard uses `exposure_by_registered_name()`
(`exposure.py:647-658`), which maps each canonical name and each alias to its
entry, and reads `entry.audience`.

## Refusal and audit entry

See [contracts/refusal.md](contracts/refusal.md).
