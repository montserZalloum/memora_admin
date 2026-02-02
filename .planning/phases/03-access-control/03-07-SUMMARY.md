---
phase: 03-access-control
plan: 07
subsystem: admin-ui
tags: [frappe, javascript, desk, ui, grant-access]

dependency_graph:
  requires:
    - 03-02  # Redis access sync hooks
  provides:
    - Admin UI for granting player access
    - Grant button on Player Profile form
  affects:
    - Manual testing workflows

tech_stack:
  added: []
  patterns:
    - Frappe client-side form customization
    - Custom button with dialog workflow

key_files:
  created: []
  modified:
    - memora_admin/memora_admin/doctype/memora_player_profile/memora_player_profile.js
    - memora_admin/hooks.py

decisions:
  - id: grant-button-location
    choice: Actions group button
    reason: Standard Frappe pattern for document actions
  - id: season-expiration-default
    choice: Default expires_at to season end_date
    reason: Most grants should follow season lifecycle
  - id: duplicate-handling
    choice: Orange alert for duplicate subscriptions
    reason: Informative without blocking workflow

metrics:
  duration: 1min
  completed: 2026-02-02
---

# Phase 03 Plan 07: Grant Access Button Summary

**One-liner:** Frappe Desk Grant Access button on Player Profile creating subscriptions that auto-sync to Redis.

## What Was Built

### Player Profile Client Script

Added interactive "Grant Access" button to Memora Player Profile form in Frappe Desk:

1. **Button in Actions group** - Shows on all saved Player Profile documents
2. **Dialog with form fields:**
   - access_key (required): Format SUB-{subject} or TRK-{track}
   - expires_at (required): Defaults to season end_date if player has season
3. **Creates Memora Player Subscription** via `frappe.client.insert`
4. **Automatic Redis sync** via existing doc_events hook from 03-02

```javascript
// Key implementation in memora_player_profile.js
frm.add_custom_button(__("Grant Access"), function() {
    show_grant_dialog(frm);
}, __("Actions"));
```

### Hooks Configuration

Registered client script in hooks.py for explicit loading:

```python
doctype_js = {
    "Memora Player Profile": "memora_admin/doctype/memora_player_profile/memora_player_profile.js"
}
```

## Integration Flow

```
Admin clicks "Grant Access" button
  -> Dialog collects access_key + expires_at
  -> frappe.client.insert creates Memora Player Subscription
  -> doc_events.after_insert triggers on_subscription_change
  -> access_sync.py calls cache.sadd()
  -> Redis memora:access:{user_id} updated within 1 second
```

## Commits

| Hash | Type | Description |
|------|------|-------------|
| 65e4991 | feat | Add Grant Access button to Player Profile |
| 3086c5b | chore | Register Player Profile client script in hooks |

## Verification Results

All checks passed:
- JS file exists with custom button
- Creates Memora Player Subscription on submit
- hooks.py has doctype_js configuration
- Integration with existing Redis sync mechanism confirmed

## Deviations from Plan

None - plan executed exactly as written.

## Gap Closure Status

**Gap 2 from 03-VERIFICATION.md:** Admin grant flow requires Frappe Desk integration

**Status:** CLOSED

Admin can now:
1. Open any Memora Player Profile in Frappe Desk
2. Click Actions > Grant Access
3. Enter access key and expiration date
4. Submit to immediately grant access

Access is reflected in Redis within 1 second via existing sync mechanism.

## Next Phase Readiness

- All Phase 3 gaps closed (05, 06, 07 complete)
- Ready for Phase 3 final verification
