# Phase 8: Device Management - Context

**Gathered:** 2026-02-02
**Status:** Ready for planning

<domain>
## Phase Boundary

Secure device registration with 3-device limit enforcement. Users' devices are registered on login with metadata. Exceeding the device limit blocks login. Device registration must be atomic to prevent race conditions with concurrent logins.

</domain>

<decisions>
## Implementation Decisions

### Device Identification
- Primary identifier: Client-generated UUID stored locally by the app
- Fingerprint fallback: Use `user_agent` field to recognize same device when UUID is lost (cleared storage, reinstall)
- If fingerprint matches existing device but UUID is new, update existing slot (don't consume new slot)
- Metadata fields already defined in `Memora Player Device` DocType (device_id, device_name, last_login, user_agent, platform, push_token)

### Device Naming
- Auto-generate device names from platform + model info
- No user editing of device names

### Limit Enforcement
- Hard block when device limit exceeded — login fails, user must contact support
- Device limit configured globally via `max_devices_per_player` in Memora Settings
- No per-user limit overrides
- No trust level tracking — all registered devices are equal

### Device Removal
- Admin/support only — users cannot self-serve device removal
- When admin removes a device, session is immediately invalidated (device kicked out)
- Removed devices are deleted completely — no history/audit trail

### Lifecycle
- No automatic device removal for inactivity
- `last_login` timestamp updates only on actual login events, not every API call

### Error Responses
- Device limit exceeded: HTTP 429 with specific message "Device limit reached (X/X). Contact support to manage your devices."
- Error codes: Use specific codes for programmatic handling
  - `DEVICE_LIMIT_EXCEEDED` — login blocked due to limit
  - `DEVICE_NOT_REGISTERED` — device not in user's list
  - `DEVICE_REVOKED` — device was removed by admin (401)
- Revoked device gets 401 with `DEVICE_REVOKED` code so client can show meaningful message

### Claude's Discretion
- Exact fingerprint matching algorithm (hash user_agent or parse components)
- Device name generation format
- Redis key structure for device registration atomicity

</decisions>

<specifics>
## Specific Ideas

- Fingerprint matching should be lenient enough to handle minor browser version changes but strict enough to differentiate actual devices
- Error messages should guide user to contact support, not leave them confused

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 08-device-management*
*Context gathered: 2026-02-02*
