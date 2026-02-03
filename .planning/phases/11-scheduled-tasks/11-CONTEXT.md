# Phase 11: Scheduled Tasks - Context

**Gathered:** 2026-02-03
**Status:** Ready for planning

<domain>
## Phase Boundary

Automated background jobs for maintenance operations: streak resets at midnight Asia/Amman, expired session cleanup (hourly), and daily leaderboard archival. All tasks must be idempotent. Users don't interact directly — this is infrastructure.

</domain>

<decisions>
## Implementation Decisions

### Failure handling
- Fail fast on errors, no automatic retries
- On failure: log error (CRITICAL level) AND send Frappe notification to admin users
- Partial failures (some users fail, others succeed): continue processing all users, log individual failures
- Failed task details stored in a Frappe DocType for review and potential re-run

### Timing precision
- Streak resets at exactly midnight Asia/Amman (00:00:00), no tolerance window
- Weekly leaderboard resets Friday midnight Asia/Amman (Islamic week: week ends Thursday night)
- If server was down at scheduled time, run task immediately on recovery (catch-up)
- Idempotency: task logic is inherently safe to repeat + track last successful run timestamp for observability

### Observability
- Detailed logging: task name, start/end time, success/fail, count processed, plus per-user results for debugging
- Dedicated Frappe page for task history dashboard (past runs, statuses, durations, failures)
- Dashboard shows history only, not upcoming schedule
- Emit Prometheus-style metrics: counters and histograms for external monitoring

### Manual control
- Admins can manually trigger tasks from Frappe UI with confirmation dialog ("Are you sure?")
- Re-run is full task only, not per-user granularity
- No pause/disable capability — tasks always run on schedule
- Custom "Task Admin" role for task operations (trigger, view history, re-run)

### Claude's Discretion
- Exact metrics naming and labels
- DocType schema for failed task tracking
- Dashboard layout and UX
- Prometheus client library choice

</decisions>

<specifics>
## Specific Ideas

- Islamic week calendar: Friday is first day of week for leaderboard cycles
- Catch-up on missed tasks prevents users from unfairly losing streaks due to server issues
- Fail fast + Frappe notification ensures admins know immediately when something breaks

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 11-scheduled-tasks*
*Context gathered: 2026-02-03*
