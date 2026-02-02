# Phase 5: Wallet & Gamification - Context

**Gathered:** 2026-02-02
**Status:** Ready for planning

<domain>
## Phase Boundary

Players earn XP and maintain streaks on lesson completion. XP accumulates in a Redis-backed wallet, streaks track consecutive days of activity. Leaderboards and achievements are separate phases (v2).

</domain>

<decisions>
## Implementation Decisions

### XP Award Amounts
- Base XP from `Memora Settings.base_lesson_xp` (system default)
- Lesson-level override via `Lesson.base_xp` (if > 0, use it; else fallback to system default)
- Streak multiplier: linear +1% per day, capped at configurable max
- Max multiplier cap stored in `Memora Settings` (admin-adjustable)

### Streak Rules
- Timezone: All players use `Asia/Amman` (single timezone, no per-player config)
- Daily requirement: 1 lesson completion maintains streak
- Missed day: Streak resets to 0 immediately (no grace period, no freeze)
- Replay policy: Replays do NOT count toward maintaining streak (only fresh completions)

### Replay Experience
- Replay XP: Fixed amount from `Memora Settings.replay_xp` (not a percentage)
- Streak multiplier DOES apply to replay XP
- No daily cap on replay XP
- API response includes `is_replay: true` flag for replay completions

### Wallet Display
- Wallet returns: XP total + current streak only (minimal)
- No streak_date in response (client doesn't need it)
- Access model: Player views own wallet, admin can view any player's wallet
  - `GET /wallet` — authenticated player's wallet
  - `GET /wallet/{player_id}` — admin access to any player

### Claude's Discretion
- Whether completion endpoint returns wallet snapshot or requires separate call
- Redis hash field naming for wallet data
- Exact multiplier calculation (floor/round for fractional XP)

</decisions>

<specifics>
## Specific Ideas

- Memora Settings already has `base_lesson_xp` and `replay_xp` fields — use these directly
- Lesson doctype has `base_xp` for per-lesson override
- Single timezone (Asia/Amman) simplifies streak boundary calculation significantly

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 05-wallet-gamification*
*Context gathered: 2026-02-02*
