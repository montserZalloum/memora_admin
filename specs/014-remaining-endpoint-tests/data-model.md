# Data Model: Remaining Endpoint Tests

**Feature**: 014-remaining-endpoint-tests
**Date**: 2026-02-17

## Overview

This feature creates test files only -- no new data models are introduced. This document maps the existing data entities that tests interact with (via mocks and Redis seeding) to enable correct test fixture construction.

## Test Entities (Mock/Seed Targets)

### 1. Catalog Product (Mock)

Returned by `CatalogService.get_player_catalog()`.

| Field | Type | Example | Notes |
|-------|------|---------|-------|
| product_grant_id | str | "GRNT-00239" | ERPNext Item ID |
| bundle_name | str | "Math Bundle" | Display name |
| price | float | 100.0 | Price in local currency |
| subjects | list[dict] | `[{subject_id, alias_title, notes}]` | Included subjects |

### 2. Plan Manifest (Mock)

Returned by `PlanService.get_manifest()`.

| Field | Type | Example | Notes |
|-------|------|---------|-------|
| schema_version | int | 1 | Always 1 |
| version | int | 1708000000 | Unix timestamp |
| generated_at | str | "2026-02-17T..." | ISO datetime |
| plan_id | str | "PLAN-001" | Plan identifier |
| title | str | "Grade 6 Plan" | Display name |
| grade_id | str or None | "GRD-001" | Optional |
| subjects | list[dict] | `[{id, title, total_lessons, ...}]` | Subject list |

### 3. Profile Hero (Mock)

Returned by `ProfilePageService.get_hero()`.

| Field | Type | Example | Notes |
|-------|------|---------|-------|
| display_name | str | "Ahmed" | Player name |
| avatar | str | "avatar_01" | Avatar ID |
| level | int | 5 | Current level |
| level_title | str | "Explorer" | Level name |
| current_xp | int | 500 | Total XP |
| xp_in_level | int | 200 | XP within current level |
| xp_for_next_level | int | 100 | XP needed for next level |

### 4. Leaderboard Entry (Mock)

Returned by `LeaderboardService.get_top()`.

| Field | Type | Example | Notes |
|-------|------|---------|-------|
| rank | int | 1 | Dense ranking |
| player_id | str | "PLAYER-001" | Player identifier |
| display_name | str | "Ahmed" | From ProfileService batch |
| xp | int | 1500 | Total XP for period |
| avatar | str or None | "avatar_01" | From ProfileService batch |
| is_me | bool | false | Marked true for current user |

### 5. Review Due Item (Mock)

Returned by `ReviewService.get_due_items()`.

| Field | Type | Example | Notes |
|-------|------|---------|-------|
| item_id | str | "uuid-string" | FSRS item UUID |
| stage_id | str | "STG-001" | Stage identifier |
| lesson_id | str | "LSN-001" | Lesson identifier |
| stage_type | str | "quiz" | Stage type |

### 6. Webhook Payload (Request Body)

Sent to `POST /api/v1/webhooks/payment`.

| Field | Type | Example | Notes |
|-------|------|---------|-------|
| event_id | str | "evt-abc123" | Idempotency key |
| event_type | str | "payment.completed" | Event type |
| transaction_id | str | "TXN-001" | Transaction reference |
| player_id | str | "PLAYER-001" | Target player |
| product_grant_id | str | "GRNT-001" | Product to grant |

### 7. Voucher Request/Response (Mock)

**Preview Request**: `{pin: "ABC123"}`
**Preview Response**: `{face_value: "50 EGP", grants: [{grant_id: "GRNT-001", name: "Math"}]}`
**Redeem Request**: `{pin: "ABC123", grant_id: "GRNT-001"}`
**Redeem Response**: `{status: "success", transaction_id: "TXN-001"}`

### 8. Redis Keys Seeded by Tests

| Key Pattern | Seeded By | Purpose |
|-------------|-----------|---------|
| `memora:session:{player_id}` | `authed_client` fixture | Session validation |
| `memora:voucher_fail:player:{id}` | Rate limit tests | Pre-seed failure counter |
| `memora:voucher_fail:ip:{ip}` | Rate limit tests | Pre-seed IP failure counter |
| `memora:webhook:{event_id}` | Idempotency tests | Mark event as processed |

## Relationships

```
Player (JWT token)
  ├── has Catalog (filtered by plan_id)
  ├── has Subscriptions (grants + plan_subjects)
  ├── has Profile (hero, stats, mastery, activity)
  ├── appears on Leaderboard (daily, weekly, alltime)
  ├── has Review Items (per subject, FSRS scheduled)
  ├── can Preview/Redeem Vouchers (rate limited)
  └── receives Notifications (WebSocket pub/sub)

Plan (public, no auth)
  └── has Manifest (subjects list, cached)

Settings (public, no auth)
  └── Gamification config (cached 5min)

Webhook (external, no auth)
  └── triggers Grant provisioning (idempotent)
```
