# Data Model: Exact Dense Rank at Scale (Tier Index)

**Feature Branch**: `033-dense-rank-tier-index`
**Date**: 2026-03-01

## Entities

### Tier Index (NEW — Redis ZSET)

A sorted set per leaderboard tracking only active XP tiers (tiers with ≥1 player).

| Field | Type | Description |
|-------|------|-------------|
| member | string | XP tier value as string (e.g., "193") |
| score | float (integer-valued) | Same tier value as number (e.g., 193.0) |

**Key pattern**: `memora:lbmeta:{period}:{date}[:{scope}]:tieridx`

**Invariants**:
- Every member's string value equals its score (member="193", score=193)
- A tier exists in this ZSET if and only if ≥1 player has that exact XP in the parent leaderboard
- `ZCARD` = number of distinct XP tiers in the leaderboard
- `ZCOUNT key (xp+1) +inf` = number of distinct tiers above XP value `xp`

**Lifecycle**: Created by first XP write or backfill. TTL mirrors parent leaderboard. Deleted by cleanup job.

---

### Tier Counts (NEW — Redis HASH)

A hash per leaderboard tracking exact player count per active XP tier.

| Field | Type | Description |
|-------|------|-------------|
| key | string | XP tier value as string (e.g., "193") |
| value | string (integer-valued) | Count of players at this tier (e.g., "5") |

**Key pattern**: `memora:lbmeta:{period}:{date}[:{scope}]:tiercnt`

**Invariants**:
- Every key must have a corresponding member in the Tier Index ZSET
- Value is always ≥1 (tier is removed when count reaches 0)
- Sum of all values = `ZCARD` of the parent leaderboard ZSET
- A key is removed (HDEL) atomically when its value reaches 0

**Lifecycle**: Same as Tier Index.

---

### Backfill Lock (NEW — Redis STRING)

A short-lived lock per leaderboard used during backfill to prevent metadata corruption from concurrent writes.

| Field | Type | Description |
|-------|------|-------------|
| key | string | `memora:lbmeta:lock:{lb_key_suffix}` |
| value | string | Lock identifier (e.g., "backfill:{timestamp}") |

**Lifecycle**: Created with `SET NX EX 30`. Released with `DEL` after backfill completes for that key. Auto-expires after 30s as safety net.

---

### Leaderboard (EXISTING — Redis ZSET, unchanged)

No changes to the existing leaderboard ZSET. Listed for reference.

| Field | Type | Description |
|-------|------|-------------|
| member | string | Player ID (e.g., "PLAYER-00001") |
| score | float (integer-valued) | Cumulative XP for the period |

**Key pattern**: `memora:lb:{period}:{date}[:{scope}]`

---

## Key Pattern Mapping

For every leaderboard key, there are exactly two corresponding metadata keys:

| Leaderboard Key | Tier Index Key | Tier Counts Key |
|---|---|---|
| `memora:lb:daily:{date}` | `memora:lbmeta:daily:{date}:tieridx` | `memora:lbmeta:daily:{date}:tiercnt` |
| `memora:lb:daily:{date}:subject:{id}` | `memora:lbmeta:daily:{date}:subject:{id}:tieridx` | `memora:lbmeta:daily:{date}:subject:{id}:tiercnt` |
| `memora:lb:daily:{date}:plan:{id}` | `memora:lbmeta:daily:{date}:plan:{id}:tieridx` | `memora:lbmeta:daily:{date}:plan:{id}:tiercnt` |
| `memora:lb:daily:{date}:plan:{id}:subject:{id}` | `memora:lbmeta:daily:{date}:plan:{id}:subject:{id}:tieridx` | `memora:lbmeta:daily:{date}:plan:{id}:subject:{id}:tiercnt` |
| `memora:lb:weekly:{friday}` | `memora:lbmeta:weekly:{friday}:tieridx` | `memora:lbmeta:weekly:{friday}:tiercnt` |
| `memora:lb:weekly:{friday}:subject:{id}` | `memora:lbmeta:weekly:{friday}:subject:{id}:tieridx` | `memora:lbmeta:weekly:{friday}:subject:{id}:tiercnt` |
| `memora:lb:weekly:{friday}:plan:{id}` | `memora:lbmeta:weekly:{friday}:plan:{id}:tieridx` | `memora:lbmeta:weekly:{friday}:plan:{id}:tiercnt` |
| `memora:lb:weekly:{friday}:plan:{id}:subject:{id}` | `memora:lbmeta:weekly:{friday}:plan:{id}:subject:{id}:tieridx` | `memora:lbmeta:weekly:{friday}:plan:{id}:subject:{id}:tiercnt` |

## TTL Policy

| Key Type | Period | TTL | Set By |
|----------|--------|-----|--------|
| Tier Index / Tier Counts | daily (global) | 30 days | `EXPIRE` in pipeline after Lua eval |
| Tier Index / Tier Counts | weekly (global) | 90 days | `EXPIRE` in pipeline after Lua eval |
| Tier Index / Tier Counts | daily (plan) | 48 hours | `EXPIRE` in pipeline after Lua eval |
| Tier Index / Tier Counts | weekly (plan) | 8 days | `EXPIRE` in pipeline after Lua eval |
| Backfill Lock | N/A | 30 seconds | `SET NX EX 30` |

## State Transitions

### Tier Lifecycle

```
[Non-existent] ---(first player at this XP)---> [Active: count=1]
[Active: count=N] ---(player joins tier)---> [Active: count=N+1]
[Active: count=N] ---(player leaves tier)---> [Active: count=N-1]  (N > 1)
[Active: count=1] ---(last player leaves)---> [Non-existent]  (ZREM + HDEL)
```

### Metadata Existence Lifecycle

```
[Missing] ---(backfill OR first XP write*)---> [Present]
[Present] ---(TTL expiry OR cleanup job)---> [Missing]
[Missing] ---(rank read)---> [Fallback to legacy _RANK_LUA]

* First XP write creates metadata for the new tier only.
  Full metadata requires backfill for pre-existing leaderboards.
```

## Validation Rules

1. **Tier count ≥ 1**: A tier in the HASH must always have count ≥ 1. If HINCRBY returns 0 or negative, immediately HDEL + ZREM.
2. **Tier index consistency**: Every member in the tier index ZSET must have a corresponding entry in the tier counts HASH, and vice versa.
3. **Sum invariant**: Sum of all tier counts = ZCARD of parent leaderboard. Verified by backfill and optionally by an integrity check command.
4. **No empty tiers**: `ZCARD(tier_index)` = `HLEN(tier_counts)` at all times during normal operation.
