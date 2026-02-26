# Research: Player Plan Change

**Feature**: 028-player-plan-change
**Date**: 2026-02-26

## R1: Freeze Mechanism Design

**Decision**: Single Redis key `memora:freeze:{player_id}` with 30s TTL, set via `SET NX EX`, serves as both distributed lock (prevents concurrent plan changes) and freeze signal (blocks sync jobs and session endpoints).

**Rationale**: A combined key simplifies the design — the freeze period IS the plan change operation period. If `SET NX` fails, another plan change is already in progress (return 409). The 30s TTL acts as a safety net if the operation crashes mid-way; the key auto-expires and normal operations resume. Cache self-healing handles any inconsistency.

**Alternatives considered**:
- **Separate lock + freeze keys**: Two keys with identical lifetime. Rejected — unnecessary complexity with no benefit.
- **Lua script guard in SESSION_COMPLETE_SCRIPT**: Would add latency to every session completion (hot path) to check a key that's almost never set. Rejected — freeze check at the FastAPI endpoint level is sufficient since the game session key is deleted before the freeze window matters.
- **Database-level lock (SELECT FOR UPDATE on player row)**: Would require holding a DB lock across Redis operations. Rejected — cross-system locks are fragile and the Frappe ORM doesn't support explicit lock management.

**Integration points (freeze key consumers)**:

| Consumer | Location | Check | Action on Freeze |
|----------|----------|-------|-------------------|
| `sync_dirty_wallets()` | `memora_admin/tasks/sync.py` | Before processing each player | Skip player, leave in dirty set for next cycle |
| `sync_dirty_progress()` | `memora_admin/tasks/sync.py` | Before processing each entry | Skip entry, leave in dirty set for next cycle |
| `POST /sessions/start` | `fastapi_app/api/v1/endpoints/sessions.py` | Before session creation | Return 409 "Plan change in progress" |
| `POST /sessions/end` | `fastapi_app/api/v1/endpoints/sessions.py` | Before session completion | Return 409 (game session already deleted anyway) |

---

## R2: Dirty Set Cleanup Strategy

**Decision**: Use progress key SCAN to derive dirty entries, then pipeline SREM.

**Rationale**: The `memora:dirty:progress` set contains entries formatted as `{player_id}:{subject_id}:v{version}`. SSCAN with MATCH iterates the entire set internally (O(N) regardless of match count). Instead, SCAN for `memora:progress:{player_id}:*` keys (which we already need for cleanup), extract subject:version pairs, and pipeline SREM the corresponding dirty entries. This is O(K) where K = player's subject count (typically 3-10).

**Cleanup sequence**:
```
1. SCAN memora:progress:{player_id}:* → collect progress key list
2. Extract {subject}:v{version} suffixes from matched keys
3. Pipeline:
   a. SREM memora:dirty:wallets {player_id}          # O(1)
   b. SREM memora:dirty:progress {player_id}:{subj}:v{ver}  # one per subject
4. Pipeline DEL for each progress key (done later in full cleanup)
```

**Alternatives considered**:
- **SSCAN dirty:progress with MATCH `{player_id}:*`**: Works but O(N) iteration on potentially 100k+ entries during peak load. Rejected — SCAN on progress keys is bounded by player's subject count.
- **Maintain per-player dirty index set**: Adds write overhead to every lesson completion (hot path). Rejected — violates Sub-20ms principle.

---

## R3: Frappe Transaction Atomicity

**Decision**: Single Frappe whitelisted API call `execute_plan_change()` performs all DB operations. Frappe's request lifecycle provides automatic transaction management.

**Rationale**: Frappe wraps each API call in a database transaction. All `frappe.db` operations within the call share a single connection. If any exception is raised, the transaction rolls back automatically. No explicit `BEGIN`/`COMMIT`/`ROLLBACK` needed.

**DB operations within the transaction (in order)**:
1. **Validate**: Check cooldown (query latest history record), check plan eligibility (active season, published)
2. **Snapshot**: Read current wallet, subscriptions, progress records
3. **Insert history**: `Memora Player Plan History` with snapshot data
4. **Delete subscriptions**: `frappe.db.delete("Memora Player Subscription", {"player": player_id})`
5. **Delete progress**: `frappe.db.delete("Memora Structure Progress", {"player": player_id})`
6. **Reset wallet**: Update wallet record — zero all counters, clear `daily_xp_json`
7. **Update profile**: Set new plan, grade, major, season on player profile

If any step fails, the entire transaction rolls back — no partial state (FR-021).

**Alternatives considered**:
- **Multiple Frappe API calls (validate, then execute)**: Introduces TOCTOU race between validation and execution. Rejected — single call with validation-then-mutation is safer.
- **Raw SQL from FastAPI**: Bypasses Frappe ORM hooks (`on_update` for profile, `on_trash` for subscriptions). Rejected — hooks handle cache invalidation automatically.

---

## R4: Leaderboard Archive Cleanup

**Decision**: SCAN `memora:lb:*` in batches, pipeline ZREM player from each matched key.

**Rationale**: Leaderboard keys span multiple patterns (alltime, daily, weekly, plan-scoped, archived). The total key count could reach ~1500+ during peak (30 days of daily + 12 weeks of weekly + subject variants + plan variants). SCAN with MATCH `memora:lb:*` batched at 200 keys per iteration, with pipeline ZREM (100 per pipeline), completes in ~15 Redis round-trips. Each ZREM is O(log N) on the ZSET. Total time: <50ms.

**Key patterns to match**:
```
memora:lb:alltime*                   # Global + per-subject all-time (~11 keys)
memora:lb:daily:*                    # Current + archived daily (~300+ keys)
memora:lb:weekly:*                   # Current + archived weekly (~120+ keys)
memora:lb:archive:*                  # Archived snapshots (~300+ keys)
```

**Alternatives considered**:
- **Enumerate keys deterministically** (compute exact key names from known dates/subjects): Requires knowing all subjects, all plan IDs, all archive dates. Complex and brittle. Rejected.
- **Only clean all-time + current day/week, skip archives**: Violates FR-013 which explicitly requires archive cleanup. Rejected.
- **Background cleanup job**: Defers cleanup to a scheduled task. Rejected — FR-014 requires cache cleanup during the plan change operation, and FR-022 already allows non-fatal cache failures.

---

## R5: Cooldown Mechanism

**Decision**: Dual check — fast Redis check via `memora:plan_change_ts:{player_id}` (24h TTL), with DB-level validation as safety net in the Frappe API.

**Rationale**: The cooldown check is the most common rejection path (UI may not perfectly enforce it). A Redis check avoids acquiring the freeze key and disrupting normal operations for an invalid request. The Frappe API re-validates as a safety net against Redis key expiry edge cases.

**Flow**:
```
1. GET memora:plan_change_ts:{player_id} → timestamp or nil
2. If exists AND (now - timestamp) < 24h → return 429 immediately (no freeze, no disruption)
3. If nil or expired → proceed with plan change
4. Frappe API also checks: SELECT MAX(changed_at) FROM `tabMemora Player Plan History` WHERE player = %s
5. After successful plan change: SET memora:plan_change_ts:{player_id} {unix_ts} EX 86400
```

**Alternatives considered**:
- **DB-only check**: Every cooldown check hits Frappe API. Rejected — adds latency and load for the most common rejection case.
- **Redis-only check**: No DB safety net. Rejected — Redis key could be lost (restart), allowing back-to-back plan changes. Low risk but violates the "source of truth in MariaDB" principle.

---

## R6: Available Plans Query

**Decision**: Frappe whitelisted API `get_available_plans()` with SQL JOIN to Season table, no Redis caching.

**Rationale**: This is a browse-before-action query, not a hot path. Even if many players browse simultaneously during a season transition, the query is a simple indexed JOIN returning typically <20 rows. MariaDB handles this efficiently. Caching would add complexity for minimal benefit and risk showing stale plan data.

**Query structure**:
```sql
SELECT ap.name, ap.plan_name, ap.grade, ap.major, ap.season,
       g.grade_name, m.major_name, s.season_title
FROM `tabMemora Academic Plan` ap
INNER JOIN `tabMemora Season` s ON s.name = ap.season
LEFT JOIN `tabMemora Grade` g ON g.name = ap.grade
LEFT JOIN `tabMemora Major` m ON m.name = ap.major
WHERE ap.is_published = 1
  AND s.end_date >= CURDATE()
  AND ap.name != %(current_plan)s
ORDER BY g.grade_name, m.major_name, ap.plan_name
```

**Alternatives considered**:
- **Cache in Redis (5 min TTL)**: The list changes rarely but adds cache invalidation complexity. Rejected — YAGNI.
- **Query from FastAPI via raw SQL**: Bypasses Frappe's permission model. Rejected — Frappe whitelisted APIs maintain architectural consistency.

---

## R7: Session Invalidation and Re-login Flow

**Decision**: Delete auth session key + publish to cache invalidation channel. Player receives 401 on next API call and must re-login with new plan context.

**Rationale**: The auth session stores `{"fid": family_id, "plan": plan_id, "season": season_id}`. Deleting the session key makes all existing JWTs invalid (session validation fails). On re-login, a new session is created with the new plan/season, and a fresh JWT is issued. The existing `plan_change_sync.py` hook already handles this — it fires automatically when the player profile's plan field changes via Frappe ORM.

**FR-020 (real-time notification)**: Publishing `{"type": "plan_changed", "player_id": ..., "reason": "plan_changed"}` to `memora:cache:invalidate` channel. Connected clients (if any WebSocket exists) can receive this. Even without WebSocket, the player gets 401 on next API call which triggers re-login.

---

## R8: What NOT to Clean (FR-019 Compliance)

The following records are explicitly preserved per FR-019:

| Record Type | Table | Reason |
|-------------|-------|--------|
| Interaction logs | `tabMemora Interaction Log` | Historical learning analytics |
| Voucher redemption logs | `tabMemora Voucher Redemption Log` | Financial audit trail |
| Subscription transactions | `tabMemora Subscription Transaction` | Payment/grant history |
| Memory state records | `tabMemora Memory State` | Isolated by `season_seq` — automatic separation |

The `memora:buffer:interactions` Redis list is also left untouched per the spec edge case: "Interactions less than 1 minute old (pending flush) may be attributed to the wrong season post-change. This is an accepted edge case with no material impact."
