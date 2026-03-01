# Quickstart: Exact Dense Rank at Scale (Tier Index)

**Feature Branch**: `033-dense-rank-tier-index`
**Date**: 2026-03-01

## What This Feature Does

Replaces the O(T × log N) iterative tier-walking Lua script with O(log T) indexed tier lookups for dense rank computation. For bottom-ranked players on 100k-player leaderboards, this reduces rank query time from ~200ms to <1ms.

## Files to Change

### New Files
- None (all changes are modifications to existing files)

### Modified Files

| File | Change |
|------|--------|
| `fastapi_app/core/redis_keys.py` | Add `LBMETA_PREFIX`, `lbmeta_tieridx_key()`, `lbmeta_tiercnt_key()`, `lbmeta_lock_key()`, `lbmeta_keys_from_lb_key()` |
| `fastapi_app/services/leaderboard.py` | Replace `_RANK_LUA` with `_TIER_AWARE_ZINCRBY_LUA`. Modify `get_my_rank()` for indexed read path with fallback. Modify `update_leaderboards()` to use Lua eval + metadata EXPIRE. |
| `memora_admin/tasks/leaderboard_cleanup.py` | Add `memora:lbmeta:daily:*` and `memora:lbmeta:weekly:*` scan patterns |
| `memora_admin/tasks/leaderboard_backfill.py` | New task file for the backfill management command |
| `memora_admin/hooks.py` | Register backfill as a bench command (if using Frappe commands) |

## How to Test

### 1. Run unit tests
```bash
cd apps/memora_admin
pytest tests/test_leaderboard_tier_index.py -v
```

### 2. Verify write path
```bash
# Award XP and check tier metadata was created
python3 -c "
import asyncio, redis.asyncio as aioredis
async def test():
    r = await aioredis.from_url('redis://127.0.0.1:13001', decode_responses=True)
    # Check tier index exists after XP award
    keys = []
    async for key in r.scan_iter('memora:lbmeta:*'):
        keys.append(key)
    print(f'Metadata keys: {len(keys)}')
    for k in sorted(keys)[:10]:
        print(f'  {k}')
asyncio.run(test())
"
```

### 3. Verify read path performance
```bash
# Compare rank query time before and after
curl -w '%{time_total}s\n' -s http://127.0.0.1:8002/api/v1/leaderboard/daily/me \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

### 4. Run backfill
```bash
# From bench directory
bench --site x.conanacademy.com execute memora_admin.tasks.leaderboard_backfill.backfill_tier_metadata
```

### 5. Verify backfill integrity
```bash
python3 -c "
import asyncio, redis.asyncio as aioredis
async def check():
    r = await aioredis.from_url('redis://127.0.0.1:13001', decode_responses=True)
    async for key in r.scan_iter('memora:lb:daily:*'):
        if ':plan:' in key or ':subject:' in key:
            continue  # check global only for simplicity
        lb_card = await r.zcard(key)
        suffix = key.replace('memora:lb:', '')
        tidx = f'memora:lbmeta:{suffix}:tieridx'
        tcnt = f'memora:lbmeta:{suffix}:tiercnt'
        # Sum of tier counts should equal ZCARD
        counts = await r.hgetall(tcnt)
        total = sum(int(v) for v in counts.values())
        status = 'OK' if total == lb_card else 'MISMATCH'
        print(f'{key}: members={lb_card}, tier_sum={total} [{status}]')
asyncio.run(check())
"
```

## Deployment Order

1. **Deploy code** — fallback ensures existing behavior unchanged
2. **Restart FastAPI** — `pkill -f "uvicorn fastapi_app.main:app"` (auto-restarts)
3. **Run backfill** — populates metadata for existing leaderboards
4. **Monitor** — check logs for `fallback_used=True` (should drop to 0 after backfill)
5. **Optional**: Remove fallback code in a follow-up PR once stable

## Key Design Decisions

1. **No API changes**: All response shapes, nullability, and edge cases are identical
2. **Lua atomicity**: Single Lua script per leaderboard variant prevents tier count races
3. **Separate prefix**: `memora:lbmeta:*` keeps archive/union operations safe
4. **TTL outside Lua**: EXPIRE set in application pipeline, not inside Lua script
5. **Fallback-first**: Legacy path works until metadata is populated
