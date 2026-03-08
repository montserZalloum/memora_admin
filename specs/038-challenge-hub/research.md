# Research: Challenge Hub (038)

**Date**: 2026-03-08
**Status**: Complete

## R-001: Review Item as Question Source

**Decision**: Use `Memora Review Item` table filtered by `topic` link + `stage_type = "QUESTION"` (MCQ only).

**Rationale**: Review Items already contain all MCQ data: `question_text`, `choice_1..4`, `correct_choice` (1-based index), `item_id` (UUID). Non-MCQ types (FILL_BLANK, MATCHING, MINDMAP) exist but are explicitly out of scope per PRD.

**Key Fields Used**:
- `item_id` (UUID, primary key) — used for FSRS submission and per-question analytics
- `topic` (Link → Memora Topic) — filter key for "all MCQs in a topic"
- `question_text`, `choice_1..4`, `correct_choice` — question content
- `subject`, `track`, `unit` — hierarchy context (denormalized)

**Alternatives Considered**:
- Query `Memora Lesson` stages directly → rejected: Review Item is the canonical extracted/denormalized view, already synced by `review_item_sync.py`
- Use all stage types → rejected: PRD explicitly says MCQ only

## R-002: Topic MCQ Count for Empty Topic Detection

**Decision**: Query `Memora Review Item` with `stage_type = "QUESTION"` grouped by topic to determine which topics have zero MCQ questions.

**Rationale**: The CDN JSON build will pre-compute this. The hierarchy endpoint can embed an `mcq_count` field per topic for the Challenge Hub hierarchy view, avoiding an extra query at browse time.

**Key Implementation Detail**: The `generate_plan_json()` build pipeline already walks all lessons per topic. Extend it to count Review Items per topic and include this in the topic-level JSON for Challenge Hub.

## R-003: FSRS Integration Method

**Decision**: Push challenge answers to `memora:buffer:interactions` (same Redis LIST used by normal gameplay), letting the existing `flush_interaction_buffer` + `process_fsrs_reviews` pipeline handle them.

**Rationale**:
- Reuses the entire FSRS pipeline (flush → parse → batch SQL → FSRS compute → Memory State update)
- No new scheduled tasks needed
- Correct rating mapping already exists: `fail_count=0` → Good, `fail_count>=2` → Again
- Challenge answers are semantically identical to normal gameplay answers for FSRS purposes

**Format**: Each question result pushed as JSON to the interaction buffer:
```json
{
  "player": "PLAYER-00001",
  "lesson": "LES-XXXXX",
  "stage_id": "STG-XXXXX",
  "item_id": "uuid-of-review-item",
  "event_type": "Completed",
  "errors_count": 0,  // 0 for correct, 1+ for incorrect
  "time_spent": 15,
  "timestamp": "2026-03-08T10:00:00.000Z",
  "metadata": {"source": "challenge_hub"}
}
```

**Note**: `lesson` and `stage_id` are required by the flush pipeline. Review Items already have these fields denormalized.

**Alternatives Considered**:
- Call `submit_reviews()` Frappe API directly → rejected: synchronous, blocks the request handler, violates sub-20ms principle
- Create new FSRS submission channel → rejected: unnecessary duplication of a working pipeline

## R-004: Challenge Progress Storage Architecture

**Decision**: Hybrid Redis + MariaDB.
- **Redis** (hot path): `HASH` per student per subject for fast unlock-chain lookups
- **MariaDB** (cold path): `Memora Challenge Progress` DocType for persistence and analytics

**Rationale**:
- Unlock logic runs on every hierarchy browse (must be fast)
- Need O(1) lookup: "is topic X stamped for student Y?"
- Redis hash with topic_id → JSON(stamped, best_correct, best_score_pct, total_xp) gives O(1) per topic
- MariaDB stores the authoritative record, hydrated on cache miss (same self-healing pattern)

**Key**: `memora:ch:progress:{player_id}:{subject_id}`
**Type**: HASH — field = topic_id, value = JSON `{stamped, best_correct, best_score_pct, best_passing_pct, total_xp, attempt_count}`

## R-005: Challenge Leaderboard Design

**Decision**: Reuse the existing `LeaderboardService` ZSET + tier index infrastructure with new key patterns (`memora:lb:ch:*`).

**Rationale**:
- Existing system already handles: ZSET-based ranking, dense rank via tier index, plan-scoped + subject-scoped keys, top N + own rank, profile resolution
- Challenge leaderboard is semantically identical: plan-scoped ZSET of Challenge XP
- Only difference: no daily/weekly rotation — Challenge XP is cumulative within a season

**Key Patterns**:
```
memora:lb:ch:{season_id}:plan:{plan_id}                         # Plan-level (all subjects)
memora:lb:ch:{season_id}:plan:{plan_id}:subject:{subject_id}   # Per-subject
```

**TTL**: None within season (cumulative). Cleaned up on season reset.

**Tier metadata**: Same pattern (`tieridx`, `tiercnt`) for O(log T) rank computation.

**Update trigger**: After each attempt submission that earns XP delta > 0, call `LeaderboardService.update_leaderboards()` with Challenge-specific keys.

**Alternatives Considered**:
- Separate leaderboard service → rejected: identical logic, just different key prefix
- MariaDB-based leaderboard → rejected: O(N) query for rank, doesn't scale to 100k
- Real-time updates → rejected per spec: periodic refresh is acceptable

## R-006: Question Cache File Format (CDN)

**Decision**: Extend the build pipeline to generate one JSON file per topic containing all MCQ questions.

**Path**: `challenges/{subject_id}/topics/{topic_id}_q.json`

**Content**:
```json
{
  "topic_id": "TOPIC-00001",
  "subject_id": "SUBJ-00001",
  "total_questions": 25,
  "questions": [
    {
      "item_id": "uuid",
      "question_text": "...",
      "choices": ["...", "...", "...", "..."],
      "correct_choice": 2
    }
  ]
}
```

**Rationale**:
- Zero DB load on challenge start (CDN-served)
- Rebuilt by existing build pipeline when Review Items change (extend `on_content_updated` trigger)
- Frontend shuffles locally — no server-side randomization needed
- `correct_choice` included in CDN file because frontend needs it (shows correct answer after wrong answer, per D-013)

**Trigger for rebuild**: Review Item sync updates → fire build queue entry for affected subject/topic.

## R-007: Attempt Submission & Idempotency

**Decision**: Use `attempt_key = {player_id}:{topic_id}:{timestamp_ms}` as client-generated idempotency key, with Redis SET NX EX (5-minute TTL) to reject duplicates.

**Rationale**:
- Network failures may cause duplicate submissions (Edge Case #12)
- Client includes a unique attempt key in the submission request
- Server checks Redis `SET NX EX 300` — if key exists, return cached result
- If new, process normally and cache result

**Alternatives Considered**:
- Sequential attempt number as dedup key → rejected: requires pre-registration of attempt start (violates D-009, abandoned attempts leave no trace)
- No idempotency → rejected: network retries would create duplicate attempts

## R-008: Configurable Settings Integration

**Decision**: Add 4 new fields to existing `Memora Settings` singleton DocType + cache in Redis.

**Fields**:
- `challenge_xp_per_question` (Int, default 5)
- `challenge_pass_threshold` (Int, default 50, percentage)
- `challenge_lb_top_count` (Int, default 20)
- `challenge_lb_refresh_interval` (Int, default 300, seconds)

**Rationale**: Memora Settings is the existing pattern for all configurable game parameters. `SettingsService` already caches these in Redis with TTL.

## R-009: Season Reset Mechanism

**Decision**: Extend existing season expiry hook to clear Challenge Hub data.

**Process**:
1. On season end event, scan and delete all `memora:ch:progress:*` keys
2. Delete all `memora:lb:ch:{season_id}:*` leaderboard keys
3. MariaDB records remain (historical archive) — add `season` field to Challenge Progress and Challenge Attempt DocTypes

**Rationale**: Season reset at END of season (D-025). Redis data is ephemeral anyway. MariaDB records are the archive (D-026).

## R-010: Empty Topic Auto-Stamp Chain Logic

**Decision**: Compute auto-stamps at read time (not write time). When building the topic state list for a student:
1. Walk topics in order within each unit
2. If topic has 0 MCQ questions → inherit stamped state from predecessor
3. If predecessor is stamped (explicitly or inherited) → this empty topic is auto-stamped
4. Next real topic sees the auto-stamped predecessor and can unlock

**Rationale**:
- No write required when empty topics "auto-stamp"
- State is deterministic from: (a) explicit stamps in Challenge Progress, (b) MCQ counts in hierarchy
- Consistent with D-016: visual progress computed client-side (but server provides the state array)

**Alternatives Considered**:
- Write auto-stamp records to Redis/DB when predecessor stamps → rejected: unnecessary writes, fragile if hierarchy changes
- Client-side only → rejected: server must enforce unlock logic (FR-005)
