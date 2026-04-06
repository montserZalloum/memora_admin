# PRD: Dynamic Challenge Reward System

**Version:** 1.0
**Date:** 2026-04-06
**Status:** Draft

---

## Problem

The current Live Challenge Event uses 5 hardcoded XP fields (`participation_xp`, `first_place_xp`, `second_place_xp`, `third_place_xp`, `default_xp`). This limits rewards to XP only and locks the rank structure to top-3 + default.

Real-world events need flexible rewards: cash prizes (real money), physical items, subscriptions, or any combination — described as free text. Admins also need to assign rewards to arbitrary ranks (not just 1-3), and combine multiple reward types per rank.

---

## Solution

Replace the 5 flat XP fields with a **child table** (`Memora Challenge Reward`) on the Event DocType. Each row represents one reward for one rank (or a default fallback).

---

## Data Model

### New Child DocType: `Memora Challenge Reward`

| Field              | Type       | Required | Description                                                  |
|--------------------|------------|----------|--------------------------------------------------------------|
| `rank`             | Int        | Yes      | Target rank. **0 = fallback** (applies to all ranks without explicit rows) |
| `reward_type`      | Select     | Yes      | `XP` or `Prize`                                              |
| `xp_amount`        | Int        | No       | XP to award. Used only when `reward_type = XP`               |
| `prize_description`| Small Text | No       | Free-text description. Used only when `reward_type = Prize`   |

### Modified DocType: `Memora Live Challenge Event`

- **Remove fields**: `participation_xp`, `first_place_xp`, `second_place_xp`, `third_place_xp`, `default_xp`, `section_break_xp`, `column_break_xp`, `column_break_xp2`
- **Add field**: `rewards` (Table, options: `Memora Challenge Reward`) in a new section "Rewards"

---

## Reward Resolution Logic

For a given participant with rank `R`:

1. Collect all rows where `rank = R`
2. If **no rows found** for rank `R`, collect all rows where `rank = 0` (fallback)
3. From the collected rows:
   - Sum all `xp_amount` from rows with `reward_type = XP` → distribute via wallet
   - Rows with `reward_type = Prize` → display-only (manual fulfillment)

**Key rule**: Fallback (`rank = 0`) does **NOT** stack with explicit rank rows. If rank 1 has any explicit rows, rank 1 gets only those — not the fallback.

---

## Examples

### Example 1: XP + cash prizes for top 3, XP for everyone else

| rank | reward_type | xp_amount | prize_description           |
|------|-------------|-----------|-----------------------------|
| 1    | XP          | 200       |                             |
| 1    | Prize       |           | 1000 SAR                    |
| 2    | XP          | 150       |                             |
| 2    | Prize       |           | 500 SAR                     |
| 3    | XP          | 100       |                             |
| 3    | Prize       |           | Free 3-month subscription   |
| 0    | XP          | 50        |                             |

- Rank 1 → 200 XP + "1000 SAR"
- Rank 2 → 150 XP + "500 SAR"
- Rank 3 → 100 XP + "Free 3-month subscription"
- Rank 4+ → 50 XP (fallback)

### Example 2: Prizes only, no XP

| rank | reward_type | xp_amount | prize_description |
|------|-------------|-----------|-------------------|
| 1    | Prize       |           | iPad Air          |
| 2    | Prize       |           | AirPods Pro       |

- Rank 1 → "iPad Air" (no XP)
- Rank 2 → "AirPods Pro" (no XP)
- Rank 3+ → nothing (no fallback defined)

### Example 3: Flat XP for all participants

| rank | reward_type | xp_amount | prize_description |
|------|-------------|-----------|-------------------|
| 0    | XP          | 100       |                   |

- Everyone → 100 XP

---

## Backend Changes

### 1. DocType Changes

- Create `Memora Challenge Reward` child DocType
- Remove the 5 XP fields + section/column breaks from `Memora Live Challenge Event`
- Add `rewards` Table field linking to `Memora Challenge Reward`

### 2. Distribution Logic (`live_challenge_transitions.py`)

**Replace `compute_xp_awards()`** with a new function that:

1. Loads the `rewards` child table from the event
2. Groups rows by rank into a lookup: `{rank: [rows]}`
3. For each ranked participant:
   - Look up rows for their exact rank
   - If none, fall back to rows for rank 0
   - Sum `xp_amount` from XP-type rows → `total_xp`
4. Returns the same `[{name, player, total_xp}]` format

**`_distribute_xp()`** changes:
- Replace the `xp_config` dict construction with a call to the new function
- The Redis HINCRBY + dirty set logic stays the same

### 3. Idempotency

No change — the existing `xp_awarded > 0` check on Participation records remains valid.

### 4. Leaderboard JSON

The `leaderboard_json` stored on the event (top 20) should include reward info per rank so the mobile app can display what each player won:

```json
[
  {
    "player": "PLY-001",
    "rank": 1,
    "score": 95,
    "xp_awarded": 200,
    "prizes": ["1000 SAR"]
  }
]
```

### 5. Validation

On Event save (in `memora_live_challenge_event.py`):
- `xp_amount` must be >= 0 when `reward_type = XP`
- `prize_description` must not be empty when `reward_type = Prize`
- Warn (not block) if no fallback row (`rank = 0`) is defined

---

## Migration

- Existing events with XP values: write a patch that reads the 5 fields and creates equivalent child table rows (rank 1/2/3/0 XP rows), then removes the old fields
- Events with all-zero XP: no rows created (clean slate)

---

## Out of Scope

- Automated prize fulfillment (prizes are display-only, fulfilled manually)
- Coin/currency wallet (no new wallet fields — only XP is auto-distributed)
- Mobile app UI changes (separate client PRD)

---

## Files to Modify

| File | Change |
|------|--------|
| `memora_admin/doctype/memora_challenge_reward/` | **New** — child DocType |
| `memora_admin/doctype/memora_live_challenge_event/memora_live_challenge_event.json` | Remove XP fields, add `rewards` table |
| `memora_admin/doctype/memora_live_challenge_event/memora_live_challenge_event.py` | Add validation for reward rows |
| `memora_admin/tasks/live_challenge_transitions.py` | Replace `compute_xp_awards()` + update `_distribute_xp()` |
| `memora_admin/fixtures/` | Update if XP fields are in fixtures |
| `memora_admin/patches/` | Migration patch for existing events |
| `memora_admin/tests/test_live_challenge_xp.py` | Update tests for new reward model |
