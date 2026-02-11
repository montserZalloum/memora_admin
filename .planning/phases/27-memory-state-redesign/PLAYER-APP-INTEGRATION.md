# Phase 27 — Memory State Redesign: Player App Integration Guide

> **Audience**: Player App developer
> **Date**: 2026-02-11
> **Backend branch**: `partitioning`

---

## What Changed (TL;DR)

The spaced-repetition memory system now tracks **individual items** (each matching pair, each word, each highlight) instead of whole stages. This gives students finer-grained review scheduling.

**What the Player App needs to do:**

1. Parse `item_id` UUIDs from lesson JSON stage configs
2. Track per-item failures during gameplay
3. Send per-item results when ending a session
4. Update the review flow to work with items instead of stages

---

## 1. Lesson JSON Changes

Every interactive sub-element inside a stage now carries a stable UUID `item_id`. These are pre-assigned by content authors and will never change for a given element.

### MATCHING stage

```json
{
  "stage_type": "MATCHING",
  "config_json": {
    "instruction": "Match the pairs",
    "pairs": [
      {
        "item_id": "550e8400-e29b-41d4-a716-446655440000",
        "id": "1",
        "right": "كتاب",
        "left": "Book"
      },
      {
        "item_id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
        "id": "2",
        "right": "قلم",
        "left": "Pen"
      }
    ]
  }
}
```


> **Backward compat**: The app should handle both formats. If `words` is a string array (no `item_id`), fall back to stage-level reporting (don't send `items` array in session end).

---

## 2. Session End API — Per-Item Results

### `POST /api/v1/sessions/{session_id}/end`

**Request body** — new `items` field on each stage:

```json
{
  "stages": [
    {
      "stage_id": "STG-00001",
      "time_spent": 45000,
      "fail_count": 2,
      "completed_at": "2026-02-11T10:00:00Z",
      "metadata": {},
      "items": [
        { "item_id": "550e8400-e29b-41d4-a716-446655440000", "fail_count": 0 },
        { "item_id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8", "fail_count": 1 },
        { "item_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7", "fail_count": 1 }
      ]
    }
  ]
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `items` | `ItemResult[]` | No | Empty array or omitted = legacy stage-level processing |
| `items[].item_id` | `string` | Yes (if items present) | UUID from stage config |
| `items[].fail_count` | `int` | No (default 0) | Number of times the player failed this specific item |

**Response** — unchanged:

```json
{
  "success": true,
  "xp_awarded": 15,
  "is_replay": false,
  "streak": 5
}
```

**Important notes:**
- The stage-level `fail_count` is still used for hearts/XP calculation
- The per-item `fail_count` is used for FSRS memory scheduling only
- If `items` is empty/omitted, the backend falls back to stage-level processing (old behavior)

---

## 3. Review APIs — Items Replace Stages

### 3a. Review Overview

`GET /api/v1/reviews`

**Response** — format unchanged, but `due_count` now counts items:

```json
{
  "subjects": [
    { "subject_id": "SUBJ-00001", "due_count": 25 }
  ]
}
```

> Previously a MATCHING stage with 5 pairs showed `due_count: 1`. Now it can show up to `due_count: 5` if all pairs are due.

---

### 3b. Get Due Items (BREAKING)

`GET /api/v1/reviews/{subject}`

**Response:**

```json
{
  "subject_id": "SUBJ-00001",
  "items": [
    {
      "item_id": "550e8400-e29b-41d4-a716-446655440000",
      "stage_id": "STG-00001",
      "lesson_id": "LSN-00001",
      "stage_type": "MATCHING"
    },
    {
      "item_id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
      "stage_id": "STG-00001",
      "lesson_id": "LSN-00001",
      "stage_type": "MATCHING"
    }
  ],
  "has_more": true
}
```

| Change | Before | After |
|--------|--------|-------|
| Response array key | `stages` | `items` |
| Each element has | `stage_id`, `lesson_id`, `stage_type` | `item_id`, `stage_id`, `lesson_id`, `stage_type` |
| Granularity | 1 entry per stage | 1 entry per item (multiple items can share same `stage_id`) |

**What the app should do with this:**
1. Group items by `stage_id` + `lesson_id` to reconstruct which stage to present
2. Fetch the lesson's stage config to display the review UI
3. For each item the player reviews, track whether they got it right (`fail_count`)

---

### 3c. Submit Reviews (BREAKING)

`POST /api/v1/reviews/{subject}/submit`

**Request:**

```json
{
  "items": [
    { "item_id": "550e8400-e29b-41d4-a716-446655440000", "fail_count": 0 },
    { "item_id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8", "fail_count": 1 }
  ]
}
```

| Change | Before | After |
|--------|--------|-------|
| Request array key | `stages` | `items` |
| Identifier | `stage_id` | `item_id` (UUID) |
| Batch limit | 1-10 | 1-10 (unchanged) |

**Response** — unchanged:

```json
{
  "processed": 2,
  "remaining_due": 3,
  "has_more": true,
  "xp_awarded": 3
}
```

- XP: 3 XP per review session (not per item) — unchanged
- Reviews do NOT update streak — unchanged

---

## 4. Profile Mastery API

The mastery endpoint format is unchanged, but counts now reflect **items** instead of stages:

`GET /api/v1/profile/mastery?subject_id=SUBJ-00001` (or without filter for all subjects)

```json
{
  "mature": 45,
  "learning": 23,
  "new_items": 12,
  "total": 80
}
```

| Classification | Meaning |
|----------------|---------|
| `mature` | Items with stability >= 21 days (high retention) |
| `learning` | Items with 0 < stability < 21 days (reviewed, not yet mature) |
| `new_items` | Items with stability = 0 (never reviewed or first review) |

---

## 5. Summary of Breaking vs Backward-Compatible Changes

| API | Breaking? | What Changed |
|-----|-----------|-------------|
| `POST /sessions/{id}/end` | No | New optional `items[]` on each stage. Omit for old behavior. |
| `GET /reviews` (overview) | No | Same format, counts now reflect items not stages |
| `GET /reviews/{subject}` | **Yes** | `stages` → `items`, new `item_id` field |
| `POST /reviews/{subject}/submit` | **Yes** | `stages` → `items`, `stage_id` → `item_id` |
| Profile mastery | No | Same format, counts reflect items not stages |
| Lesson JSON | **Partial** | New `item_id` fields in configs; SENTENCE_BUILDER format changed (backward compat for old format) |

---

## 6. Implementation Checklist for Player App

- [ ] **Parse `item_id`** from lesson JSON for all stage types (MATCHING, QUIZ and so on)
- [ ] **is_skippable STAGES**: when the stage has this flag `is_skippable:true` it means this stage will not be part of the FSRS and it will not has any `item_id`, but we still want to want to send it to the backend, send the stage_id and the time as usual
- [ ] **Track per-item failures** during gameplay (which pair was wrong, which word was misplaced, etc.)
- [ ] **Send `items[]` array** in session end request with each item's `item_id` and `fail_count`
- [ ] **Update review fetch**: parse `items` array (not `stages`) from `GET /reviews/{subject}`
- [ ] **Group due items by stage** for review UI presentation (multiple items can belong to same stage)
- [ ] **Update review submit**: send `items` with `item_id` (not `stages` with `stage_id`)
- [ ] **Update mastery display** — numbers will be higher now (items vs stages), consider if UI copy needs adjustment
- [ ] **Update review overview** — `due_count` numbers will be higher, same consideration
