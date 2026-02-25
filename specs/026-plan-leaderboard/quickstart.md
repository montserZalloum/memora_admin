# Quickstart: Plan-Scoped Leaderboard

**Branch**: `026-plan-leaderboard`

## What Changes

### Files Modified

| File | Change |
|------|--------|
| `fastapi_app/core/redis_keys.py` | Add 4 plan-scoped leaderboard key builder functions |
| `fastapi_app/services/leaderboard.py` | Add `plan_id` to write path; read path uses plan-scoped keys; remove alltime reads |
| `fastapi_app/api/v1/endpoints/leaderboard.py` | Pass `user.plan` to service; remove `limit` param; restrict `lb_type` to daily/weekly |
| `fastapi_app/models/leaderboard.py` | Update `LeaderboardType` enum to remove "alltime"; make `rank` nullable in MyRankResponse |

### Files Unchanged

| File | Why |
|------|-----|
| `memora_admin/tasks/leaderboard_reset.py` | Archive jobs only handle global keys (plan keys expire via TTL) |
| `memora_admin/tasks/profile_cache.py` | Keeps warming from global leaderboards (same players) |
| `fastapi_app/api/v1/endpoints/sessions.py` | Only passes `user.plan` to existing `update_leaderboards()` call |

## Key Design Decisions

1. **Plan from JWT**: `user.plan` is already in the access token — zero extra queries
2. **Dual-write**: Plan-scoped + global keys written in same pipeline (1 RTT)
3. **No archival for plan keys**: 48h/8d TTL handles cleanup automatically
4. **No new endpoints**: Subject dropdown uses existing plan manifest
5. **Fixed top 20**: No `limit` parameter — always returns up to 20 entries

## Redis Key Summary

```
# Plan-scoped (NEW — read + write)
memora:lb:daily:{date}:plan:{plan_id}                           TTL: 48h
memora:lb:daily:{date}:plan:{plan_id}:subject:{subject_id}     TTL: 48h
memora:lb:weekly:{friday}:plan:{plan_id}                        TTL: 8d
memora:lb:weekly:{friday}:plan:{plan_id}:subject:{subject_id}  TTL: 8d

# Global (EXISTING — write-only, not read by endpoints)
memora:lb:alltime                                               TTL: none
memora:lb:daily:{date}                                          TTL: 30d
memora:lb:weekly:{friday}                                       TTL: 90d
(+ subject variants of each)
```

## Testing Strategy

1. **Unit**: Service methods with mocked Redis — verify correct key construction, ZINCRBY vs ZADD, TTL values
2. **Integration**: Real Redis with prefix isolation — verify dual-write (plan + global), verify plan isolation (two plans don't leak)
3. **Endpoint**: httpx AsyncClient — verify `alltime` returns 422, `limit` param rejected, response shape matches contract
