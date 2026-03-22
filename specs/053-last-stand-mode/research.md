# Research: Live Challenge Mode — Last Stand

**Feature Branch**: `053-last-stand-mode`
**Date**: 2026-03-22

## R-001: Round Engine Architecture

**Decision**: Implement the round engine as a single async coroutine per active Last Stand event, managed by `LiveChallengeService`, persisting full round state to Redis for crash recovery.

**Rationale**: The existing countdown loop (`_countdown_loop`) already demonstrates this pattern — a per-event async task that monitors time and broadcasts WebSocket messages. The round engine extends this with phase management (answer window → result window → next round). Storing round state in Redis allows a restarted FastAPI process to resume the engine from the current phase.

**Alternatives considered**:
- **Cron-driven rounds (Frappe scheduled task)**: Rejected — 60s cron granularity is too coarse for sub-second round timing. Round phases are 3-30 seconds.
- **Client-driven rounds (mobile polls to advance)**: Rejected — violates FR-008 (server-controlled synchronization) and creates race conditions at 10k scale.
- **Separate microservice for round management**: Rejected — over-engineering. The FastAPI sidecar already manages WebSocket connections and Redis state. Adding another service increases deployment complexity without proportional benefit.

**Recovery mechanism**:
- Round state stored in Redis HASH `memora:lc:{event_id}:round` with fields: `round_id`, `question_idx`, `phase` (answer/result), `phase_end_ts`.
- On FastAPI startup, scan Active Last Stand events and resume round engines from stored state.
- If `phase_end_ts` has passed during downtime, the engine fast-forwards: evaluate unanswered rounds (hearts deducted for missed answers per FR-013), advance to the correct round.
- The cron task (`live_challenge_transitions.py`) acts as a safety net — if `exam_end_ts` passes with no round engine running, it transitions to Ended and triggers reconciliation.

---

## R-002: Personalized WebSocket Broadcasts at 10k Scale

**Decision**: Extend WebSocket connection tracking to map `(event_id, ws) → player_id`. During round_result, build a per-player message by overlaying personalized fields onto a shared base message. Send using the existing chunked broadcast with per-connection message customization.

**Rationale**: Each player needs to know their own result (heart lost, hearts remaining, eliminated status) without a separate REST call. Broadcasting a single aggregate message would require 10k follow-up REST requests per round — worse than 10k personalized WebSocket sends.

**Alternatives considered**:
- **Broadcast aggregate + REST poll for personal state**: Rejected — creates a thundering herd of 10k simultaneous REST requests after each round_result broadcast. Worse latency and load than personalized WS sends.
- **Encode all player states in broadcast (fat message)**: Rejected — message size grows linearly with player count. At 10k players × ~50 bytes per player = 500KB per message, sent 10k times = 5GB network traffic per round.
- **Redis Streams per player**: Rejected — adds complexity without benefit. WebSocket connections already exist.

**Implementation detail**:
- Modify `register_connection(event_id, ws, player_id)` to store a `dict[WebSocket, str]` mapping (ws → player_id) alongside the existing connection set.
- New method `_broadcast_personalized(event_id, base_msg, player_states)`:
  - `base_msg`: dict with common fields (round_id, question_idx, alive_count, eliminated_this_round)
  - `player_states`: `dict[str, dict]` mapping player_id → personal fields (hearts_remaining, heart_lost, is_eliminated, is_correct)
  - For each connection: merge `base_msg | player_states.get(player_id, {})` → send
  - Eliminated spectators get base_msg only (no personal fields)
- Message size per send: ~200 bytes — negligible at 10k connections.
- Existing chunked send with 2s timeout per connection prevents slow clients from blocking.

---

## R-003: Hearts Data Structure in Redis

**Decision**: Use a single Redis HASH `memora:lc:{event_id}:hearts` mapping `player_id → remaining_hearts` (integer string). Maintain companion SETs for `alive` and `eliminated` players.

**Rationale**: HASH provides O(1) per-player lookup and O(N) full scan for reconciliation. Companion SETs enable O(1) alive count (`SCARD`) and O(1) membership checks without scanning the hearts hash. This mirrors the existing `joined` / `submitted` SET pattern.

**Alternatives considered**:
- **Per-player STRING keys**: Rejected — 10k keys per event creates key sprawl. HASH is more memory-efficient (Redis ziplist encoding for small hashes) and easier to clean up (single DEL).
- **Sorted set (hearts as score)**: Rejected — ZADD/ZSCORE is viable but over-complex for simple decrement. No need for range queries on hearts.
- **Single JSON blob**: Rejected — requires full read-modify-write cycle. HASH allows atomic `HINCRBY -1` per player.

**Atomic operations**:
- Heart deduction: `HINCRBY hearts_key player_id -1` — atomic, returns new value.
- Elimination check: if returned value ≤ 0, `SMOVE alive_key eliminated_key player_id`.
- Combined in Lua script for atomicity: deduct → check → move → return {new_hearts, is_eliminated, alive_count}.

---

## R-004: Early Answer Window Close Detection

**Decision**: Use a polling loop (100ms ticks) checking `HLEN(round_answers) >= SCARD(alive)`. The answer submission endpoint also checks this condition and publishes a Redis notification on match to allow sub-100ms detection.

**Rationale**: FR-010 requires ending the answer window early if all alive players have answered. Two mechanisms ensure responsiveness: (1) the last answering player's submission triggers an immediate check, and (2) the polling loop catches edge cases (e.g., player eliminated between answer and check).

**Alternatives considered**:
- **Pure pub/sub notification**: Rejected as sole mechanism — race condition if notification is lost. Polling is the reliable fallback.
- **Lua script on each answer that checks and signals**: Considered but deferred — adds complexity to the answer path. The 100ms polling worst-case is acceptable (humans won't notice 100ms delay).
- **Redis keyspace notifications**: Rejected — requires CONFIG SET and adds fragile infrastructure dependency.

**Implementation**:
- Answer endpoint: after `HSET round_answers_key player_id data`, check `HLEN >= alive_count`. If true, publish to `memora:lc:{event_id}:round_signal` channel with message "all_answered".
- Round engine: `asyncio.wait` on two conditions — `asyncio.sleep(remaining_time)` and a subscriber on the signal channel. Whichever fires first ends the answer window.

---

## R-005: Reconnection State Delivery

**Decision**: On WebSocket reconnect, send a `player_state` message with the player's current hearts, alive/eliminated status, and current round info. The reconnecting client uses this to rebuild its UI state.

**Rationale**: FR-014 requires alive players to resume at the current round within 3 seconds. The WebSocket handler already distinguishes reconnection scenarios (Active + participant → send current state). Adding a `player_state` message type follows the established pattern.

**Alternatives considered**:
- **REST endpoint for state recovery**: Rejected — adds an extra round-trip. The WebSocket connection already authenticates and can deliver state immediately on accept.
- **Replay missed messages**: Rejected — requires message buffering per player. At 10k players with potential minutes of disconnect, buffer memory grows unboundedly.

**Implementation**:
- On WebSocket connect during Active Last Stand event:
  1. Check if player is in `alive` or `eliminated` set
  2. If alive: send `player_state` with `{hearts, is_alive: true, current_round_id, question_idx, phase, phase_remaining_ms}`
  3. If eliminated: send `player_state` with `{hearts: 0, is_alive: false, eliminated_at_question}`
  4. If in answer phase and alive: also send current question (stripped of correct answer)
- Reconnected player can immediately submit an answer if the answer window is still open.

---

## R-006: Process Crash Recovery

**Decision**: Full round state is persisted to Redis. On FastAPI startup, scan for Active Last Stand events and spawn round engines that resume from stored state.

**Rationale**: FR-022 prohibits DB writes during Active gameplay, so all state is in Redis. Redis persistence (RDB/AOF) ensures survival across Redis restarts. FastAPI process crashes are the primary recovery scenario.

**Implementation**:
- **Redis state contract**: The `memora:lc:{event_id}:round` HASH always reflects the current phase. Fields: `round_id`, `question_idx`, `phase` (answer|result), `phase_end_ts` (Unix epoch).
- **Startup scan**: Query MariaDB for events with `status = 'Active'` and `mode = 'last_stand'`. For each, check Redis for round state. If present, resume. If missing (unlikely — means Redis was also lost), end the event via reconciliation.
- **Fast-forward**: If `phase_end_ts < now`, the engine calculates how many rounds were missed during downtime. For each missed round:
  - All alive players who didn't answer lose a heart (FR-013)
  - Eliminations are processed
  - Engine advances to the correct round
- **Cron safety net**: If FastAPI is down for longer than `exam_end_ts`, the cron task ends the event and reconciles.

---

## R-007: Answer Validation and Anti-Cheat

**Decision**: Use a Lua script for atomic answer submission that validates: (1) event is Active, (2) player is alive, (3) round_id matches current round, (4) answer window is open, (5) player hasn't already answered this round. All five checks in one atomic operation.

**Rationale**: At 10k concurrent players, race conditions between check-and-act are inevitable without atomicity. The existing atomic join Lua script demonstrates this pattern.

**Alternatives considered**:
- **Python-side validation with optimistic locking**: Rejected — check-then-act gap allows double answers or late submissions.
- **Redis transactions (MULTI/EXEC)**: Rejected — WATCH-based optimistic locking retries under contention. Lua is simpler and guaranteed atomic.

**Lua script contract**:
- KEYS: status_key, alive_key, round_key, round_answers_key
- ARGV: player_id, round_id, selected_answer, timestamp
- Returns: 1 (accepted), -1 (not active), -2 (not alive), -3 (wrong round), -4 (window closed), -5 (already answered)

---

## R-008: Ranking Algorithm

**Decision**: Three-tier ranking: score DESC → hearts remaining DESC → avg response time ASC. Standard competition ranking (1, 1, 3 for ties). Matches the existing `compute_ranking` pattern but with additional tiebreakers.

**Rationale**: FR-015 specifies this exact ordering. FR-016 defines score as `correct_answers / total_questions * 100` (percentage of ALL questions, not just questions alive for). FR-017 defines avg response time from answered questions only (server-side timestamps).

**Implementation**:
- Score: `(correct_count / total_questions) * 100` — same formula as exam mode but over all event questions, not just questions the player was alive for.
- Hearts remaining: stored in Redis during gameplay, persisted to Participation during reconciliation.
- Avg response time: computed from per-round answer timestamps (server-side `time.time()` at answer receipt). Only includes rounds where the player submitted an answer (not timeouts).
- Players eliminated earlier have fewer correct answers (lower score) and 0 hearts, so they naturally rank lower.
- All-eliminated-same-round players: same score (if same answers), same hearts (0), tiebreak by avg response time.

---

## R-009: Exam Mode Isolation

**Decision**: Use mode-based branching at the service layer. The round engine only activates for `last_stand` mode. All existing exam mode code paths remain untouched behind `if mode == "exam"` guards (or more precisely, the new code is behind `if mode == "last_stand"` guards).

**Rationale**: FR-021 requires `/submit` to return MODE_NOT_SUPPORTED for Last Stand. US7 requires zero regressions in exam mode. Adding mode checks to the four critical paths (join, submit/answer, WebSocket, reconciliation) isolates the modes cleanly.

**Affected paths**:
1. **Join**: Last Stand rejects join during Active (FR-007). Exam allows it. → Check mode before status validation.
2. **Submit**: Returns MODE_NOT_SUPPORTED for Last Stand (FR-021). → Early return before grading logic.
3. **Answer** (new): Only available for Last Stand. → New endpoint, not a modification.
4. **WebSocket**: Last Stand uses round engine (round_start/round_result messages). Exam uses countdown + exam_start. → Mode branch in connection handler.
5. **Reconciliation**: Last Stand persists hearts/elimination data. Exam persists score only. → Mode branch in reconcile.
6. **Ranking**: Last Stand uses 3-tier. Exam uses score-only. → Mode branch in post-event processing.
7. **Transitions cron**: Last Stand events don't use exam_end_ts for Active→Ended (round engine controls this). → Check mode in transition logic.
