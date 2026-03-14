# Memora Analytics — Schema Analysis Report

> **Date:** 2026-03-13
> **Branch:** `047-analytics-dataset-export`
> **Purpose:** Analyze the production schema to identify existing reports, potential analytics reports, and shared datasets for a future analytics platform.
> **Scope:** Read-only analysis. No data modified.

---

## Table of Contents

1. [Existing Reports](#1-existing-reports)
2. [Potential Reports — Full Details](#2-potential-reports)
   - [A: Student Learning Behavior](#category-a-student-learning-behavior)
   - [B: Learning Progress & Retention](#category-b-learning-progress--retention)
   - [C: Content Quality](#category-c-content-quality)
   - [D: Challenges & Competition](#category-d-challenges--competition)
   - [E: Monetization & Subscriptions](#category-e-monetization--subscriptions)
   - [F: Operational Health](#category-f-operational-health)
3. [Shared Datasets for Analytics](#3-shared-datasets-for-analytics)
4. [Gap Summary & Priorities](#4-gap-summary--priorities)

---

## Data Landscape — Quick Numbers

| Entity | Table | Row Count |
|---|---|---|
| Players | `tabMemora Player Profile` | 364 |
| Interaction Events | `tabMemora Interaction Log` | 10,906 |
| Memory States | `tabMemora Memory State` | 103 |
| Practice Logs | `tabMemora Practice Log` | 20 |
| Challenge Attempts | `tabMemora Challenge Attempt` | 30 |
| Challenge Attempt Details | `tabMemora Challenge Attempt Detail` | 95 |
| Structure Progress | `tabMemora Structure Progress` | 124 |
| Subscriptions | `tabMemora Player Subscription` | 73 |
| Subscription Transactions | `tabMemora Subscription Transaction` | 77 |
| Voucher Cards | `tabMemora Voucher Card` | 4,872 |
| Voucher Batches | `tabMemora Voucher Batch` | 573 |
| Voucher Allocations | `tabMemora Voucher Allocation` | 269 |
| Redemption Logs | `tabMemora Voucher Redemption Log` | 169 |
| Review Items | `tabMemora Review Item` | 202 |
| Lessons | `tabMemora Lesson` | 47 |
| Subjects | `tabMemora Subject` | 65 |
| Topics | `tabMemora Topic` | 12 |
| Seasons | `tabMemora Season` | 8 |
| Academic Plans | `tabMemora Academic Plan` | 917 |
| Player Wallets | `tabMemora Player Wallet` | 330 |
| Player Plan History | `tabMemora Player Plan History` | 2 |
| Content Reports | `tabMemora Content Report` | 6 |
| Live Challenge Events | `tabMemora Live Challenge Event` | 2 |
| Live Participations | `tabMemora Live Challenge Participation` | 1 |
| Archive Jobs | `tabMemora Archive Job` | 60 |
| Task Run Logs | `tabMemora Task Run Log` | 1,735 |
| Build Queue | `tabMemora Build Queue` | 215 |
| Analytics Aggregates | `tabMemora Analytics Aggregate` | 0 |

**Interaction Log event types:** Completed (10,899), Started (7)

**Voucher Card statuses:** Available (2,670), Allocated (1,601), Redeemed (336), Void (237), Expired (28)

**Redemption Log statuses:** Success (72), Already Owned (22), Invalid PIN (15), Not Allocated (13), Grant Not In Batch (13), others

**Subscription payment methods:** Voucher (73), Manual-Admin (4)

**Interaction Log date range:** 2026-02-12 to 2026-03-13 (1 month)

---

## 1. Existing Reports

The system has **5 Script Reports**, all in the voucher/sales domain. There are **zero reports** covering learning behavior, student progress, or content quality.

---

### 1.1 Batch Performance

| | |
|---|---|
| **Source tables** | `tabMemora Voucher Batch`, `tabMemora Voucher Card`, `tabMemora Voucher Batch Grant`, `tabMemora Product Grant`, `tabMemora Academic Plan`, `tabMemora Season` |
| **Metrics** | total_cards, available, allocated, redeemed, voided, expired, redemption_rate (%) |
| **Dimensions** | batch, batch_status (Generated/Active/Closed), season_end, days_until_end |
| **Filters** | batch (Link), status (Select: Generated/Active/Closed) |
| **Report Summary** | Total Cards (Grey), Total Redeemed (Green), Avg Redemption Rate (Blue) |
| **Purpose** | Monitor voucher batch inventory and lifecycle. Identify underperforming batches or approaching expirations. |

### 1.2 Consignment Reconciliation

| | |
|---|---|
| **Source tables** | `tabMemora Voucher Card` (filtered by `sale_model='Consignment'`), `tabMemora Voucher Batch` |
| **Metrics** | allocated_cards, redeemed_cards, uninvoiced_cards, total_redeemed_value, commission_per_card, amount_due |
| **Dimensions** | library (Customer), date_range |
| **Filters** | from_date (required), to_date (required), library (Link to Customer) |
| **Report Summary** | Total Allocated (Grey), Total Redeemed (Green), Total Uninvoiced (Orange), Total Amount Due (Blue, JOD) |
| **Purpose** | Reconcile consignment-model sales per library, calculate commissions owed. |

### 1.3 Sales by Library

| | |
|---|---|
| **Source tables** | `tabMemora Voucher Card` (filtered by `status='Redeemed'`), `tabMemora Voucher Batch` |
| **Metrics** | redeemed_cards, total_face_value, total_commission, net_revenue |
| **Dimensions** | library, sale_model (Prepaid/Consignment), invoice_status, date_range |
| **Filters** | from_date, to_date, library (Link), sale_model (Select) |
| **Report Summary** | Total Redeemed (Green), Total Net Revenue (Blue, JOD), Total Commission (Grey, JOD) |
| **Purpose** | Revenue analysis per library. Compare sale models and identify top-performing distribution partners. |

### 1.4 Scholarship Gift Grants

| | |
|---|---|
| **Source tables** | `tabMemora Voucher Batch` (filtered by `batch_purpose != 'Sale'`), `tabMemora Voucher Card`, `tabMemora Voucher Batch Grant` |
| **Metrics** | total_cards, activated, redeemed, voided, remaining, redemption_rate |
| **Dimensions** | purpose (Scholarship/Gift/Promotion), product_grant, date_range |
| **Filters** | batch_purpose (Select), from_date, to_date, product_grant (Link) |
| **Report Summary** | Total Cards (Grey), Total Redeemed (Green), Avg Redemption Rate (Blue) |
| **Purpose** | Track non-sale voucher programs (scholarships, gifts, promotions) separately from commercial sales. |

### 1.5 Security Audit

| | |
|---|---|
| **Source tables** | `tabMemora Voucher Redemption Log` (filtered by `status != 'Success'`) |
| **Metrics** | failed_attempts, unique_players, unique_ips |
| **Dimensions** | player, ip_address, failure_type (Invalid PIN, Already Redeemed, Expired, Void, Not Allocated, Batch Inactive, Season Inactive, Rate Limited), date_range |
| **Filters** | from_date (default: 7 days ago), to_date (default: today), player (Link), failure_type (Select) |
| **Report Summary** | Total Failed Attempts (Red), Unique Players (Orange), Unique IPs (Grey) |
| **Purpose** | Detect brute-force attacks, fraud patterns, and suspicious redemption behavior. |

---

## 2. Potential Reports

### Category A: Student Learning Behavior

---

#### A1: Daily Active Students (DAU / WAU / MAU)

**Source tables and columns used:**

| Table | Columns Used | Why |
|---|---|---|
| `tabMemora Interaction Log` | `player`, `timestamp` | Count distinct players per day/week/month |
| `tabMemora Player Profile` | `name`, `grade`, `major`, `season`, `gender`, `creation` | Segmentation dimensions + registration date |

**Metrics with formulas:**

- `dau` = `COUNT(DISTINCT il.player)` WHERE `DATE(il.timestamp) = :target_date`
- `wau` = `COUNT(DISTINCT il.player)` WHERE `il.timestamp >= :target_date - INTERVAL 6 DAY`
- `mau` = `COUNT(DISTINCT il.player)` WHERE `il.timestamp >= :target_date - INTERVAL 29 DAY`
- `stickiness` = `dau / mau * 100`
- `new_users` = count of players WHERE `DATE(pp.creation) = :target_date`
- `returning_users` = `dau - new_users`

**Dimensions:** date, grade, major, season, gender

**Approximate query:**

```sql
SELECT
    DATE(il.timestamp)                          AS report_date,
    pp.grade,
    pp.season,
    COUNT(DISTINCT il.player)                   AS dau,
    COUNT(DISTINCT CASE
        WHEN DATE(pp.creation) = DATE(il.timestamp)
        THEN il.player END)                     AS new_users,
    COUNT(DISTINCT CASE
        WHEN DATE(pp.creation) < DATE(il.timestamp)
        THEN il.player END)                     AS returning_users
FROM `tabMemora Interaction Log` il
JOIN `tabMemora Player Profile` pp ON pp.name = il.player
WHERE il.timestamp BETWEEN :from_date AND :to_date
GROUP BY DATE(il.timestamp), pp.grade, pp.season
ORDER BY report_date;
```

**Memora-specific notes:**

- `event_type` in Interaction Log currently contains `Completed` (10,899) and `Started` (7). Most events are lesson completions. DAU here measures "how many students completed at least one lesson today".
- Data volume: 10,906 interactions from 364 players. Average ~30 interactions/player. Date range: 2026-02-12 to 2026-03-13 (one month).

---

#### A2: Session Analysis

**Source tables and columns used:**

| Table | Columns Used | Why |
|---|---|---|
| `tabMemora Interaction Log` | `player`, `timestamp`, `time_spent`, `lesson`, `event_type` | Build sessions from time gaps |
| `tabMemora Player Profile` | `name`, `grade`, `season` | Segmentation dimensions |
| `tabMemora Lesson` | `name`, `subject` | Link lesson to subject |

**How sessions are built:**

There is no sessions table. Sessions are computed from `timestamp` by grouping consecutive events per player. If more than 30 minutes pass between two events from the same player, a new session begins.

**Metrics:**

- `session_duration_min` = `SUM(time_spent) / 60` per session (`time_spent` is in seconds)
- `events_per_session` = `COUNT(*)` events in the session
- `sessions_per_day` = number of sessions per day
- `bounce_rate` = percentage of sessions with only one event
- `peak_hour` = hour with the most sessions

**Approximate query (session construction):**

```sql
WITH ordered_events AS (
    SELECT
        player,
        timestamp,
        time_spent,
        lesson,
        LAG(timestamp) OVER (
            PARTITION BY player ORDER BY timestamp
        ) AS prev_timestamp
    FROM `tabMemora Interaction Log`
    WHERE timestamp BETWEEN :from_date AND :to_date
),
session_starts AS (
    SELECT *,
        SUM(CASE
            WHEN prev_timestamp IS NULL
              OR TIMESTAMPDIFF(MINUTE, prev_timestamp, timestamp) > 30
            THEN 1 ELSE 0
        END) OVER (PARTITION BY player ORDER BY timestamp) AS session_id
    FROM ordered_events
)
SELECT
    player,
    session_id,
    DATE(MIN(timestamp))                        AS session_date,
    HOUR(MIN(timestamp))                        AS start_hour,
    COUNT(*)                                    AS events_in_session,
    SUM(time_spent) / 60.0                      AS duration_min,
    COUNT(DISTINCT lesson)                      AS lessons_touched
FROM session_starts
GROUP BY player, session_id;
```

**Memora-specific notes:**

- `time_spent` stores time in seconds as INT. This is the actual time the student spent in the stage.
- `client_metadata` contains JSON from the app — may contain additional device/platform info.

---

#### A3: Student Practice Accuracy

**Source tables and columns used:**

| Table | Columns Used | Why |
|---|---|---|
| `tabMemora Practice Log` | `player_id`, `item_id`, `attempt_count`, `correct_count`, `first_seen_at`, `last_seen_at`, `last_result` | Core practice data |
| `tabMemora Review Item` | `item_id`, `subject`, `topic`, `lesson`, `stage_type`, `question_text` | Content context for each question |
| `tabMemora Player Profile` | `name`, `grade`, `major`, `season` | Player dimensions |

**Metrics with formulas:**

- `accuracy_rate` = `pl.correct_count / pl.attempt_count * 100`
- `avg_attempts` = `AVG(pl.attempt_count)`
- `perfect_items` = `COUNT(*)` WHERE `pl.correct_count = pl.attempt_count`
- `struggling_items` = `COUNT(*)` WHERE `pl.correct_count / pl.attempt_count < 0.5`
- `practice_span_days` = `DATEDIFF(pl.last_seen_at, pl.first_seen_at)`

**Approximate query (report at subject/topic level):**

```sql
SELECT
    ri.subject,
    ri.topic,
    ri.lesson,
    ri.stage_type,
    COUNT(DISTINCT pl.player_id)                  AS unique_players,
    COUNT(*)                                      AS items_practiced,
    SUM(pl.attempt_count)                         AS total_attempts,
    SUM(pl.correct_count)                         AS total_correct,
    ROUND(SUM(pl.correct_count) / SUM(pl.attempt_count) * 100, 1)
                                                  AS accuracy_pct,
    ROUND(AVG(pl.attempt_count), 1)               AS avg_attempts_per_item,
    SUM(CASE WHEN pl.correct_count = pl.attempt_count
             THEN 1 ELSE 0 END)                   AS perfect_items,
    SUM(CASE WHEN pl.correct_count * 1.0 / pl.attempt_count < 0.5
             THEN 1 ELSE 0 END)                   AS struggling_items
FROM `tabMemora Practice Log` pl
JOIN `tabMemora Review Item` ri ON ri.item_id = pl.item_id
GROUP BY ri.subject, ri.topic, ri.lesson, ri.stage_type
ORDER BY accuracy_pct ASC;
```

**Memora-specific notes:**

- `tabMemora Practice Log` is a custom non-Frappe table. Composite PK `(player_id, item_id)`. No `name` column or standard Frappe columns.
- `item_id` in Practice Log is `VARCHAR(36)` (UUID as text), while in Memory State it is `BINARY(16)`. Be careful when joining.
- `last_result` is `ENUM('Correct','Incorrect')` — stores only the latest attempt result.
- Join with `tabMemora Review Item` via `ri.item_id = pl.item_id` since Review Item has a unique index on `item_id`.

---

#### A4: Lesson Completion Funnel

**Source tables and columns used:**

| Table | Columns Used | Why |
|---|---|---|
| `tabMemora Interaction Log` | `player`, `lesson`, `stage_id`, `event_type`, `time_spent`, `errors_count`, `timestamp` | Start and completion events |
| `tabMemora Lesson` | `name`, `lesson_title`, `subject`, `topic`, `track`, `unit`, `base_xp`, `max_hearts` | Lesson data |
| `tabMemora Lesson Stage` | `parent` (FK to Lesson), `stage_id`, `stage_type`, `is_skippable` | Lesson stages |

**Metrics with formulas:**

- `started_count` = `COUNT(*)` WHERE `event_type = 'Started'`
- `completed_count` = `COUNT(*)` WHERE `event_type = 'Completed'`
- `completion_rate` = `completed_count / started_count * 100` (currently: 10,899 / 7 — skewed ratio, `Started` is likely not always recorded)
- `avg_time_to_complete` = `AVG(time_spent)` for completed events
- `avg_errors` = `AVG(errors_count)` for completed events
- `hardest_lessons` = lessons with highest `avg_errors`

**Approximate query:**

```sql
SELECT
    l.name                                        AS lesson,
    l.lesson_title,
    l.subject,
    l.topic,
    COUNT(DISTINCT il.player)                     AS unique_players,
    COUNT(*)                                      AS total_completions,
    ROUND(AVG(il.time_spent), 0)                  AS avg_time_sec,
    ROUND(AVG(il.errors_count), 1)                AS avg_errors,
    SUM(CASE WHEN il.errors_count = 0
             THEN 1 ELSE 0 END)                   AS perfect_completions,
    SUM(CASE WHEN il.errors_count >= l.max_hearts
             THEN 1 ELSE 0 END)                   AS failed_attempts
FROM `tabMemora Interaction Log` il
JOIN `tabMemora Lesson` l ON l.name = il.lesson
WHERE il.event_type = 'Completed'
  AND il.timestamp BETWEEN :from_date AND :to_date
GROUP BY l.name, l.lesson_title, l.subject, l.topic
ORDER BY avg_errors DESC;
```

**Memora-specific notes:**

- `max_hearts` in Lesson defines the number of allowed errors before failure. If `errors_count >= max_hearts`, the student failed the lesson.
- `stage_id` in Interaction Log links to a specific stage within the lesson. Can analyze which stages cause the most errors.
- Index `idx_event_creation` on `(event_type, creation)` accelerates this query.

---

### Category B: Learning Progress & Retention

---

#### B1: Spaced Repetition Effectiveness

**Source tables and columns used:**

| Table | Columns Used | Why |
|---|---|---|
| `tabMemora Memory State` | `player`, `item_id`, `season_seq`, `subject`, `lesson`, `stability`, `difficulty`, `next_review`, `last_review`, `state`, `step` | Memory state per item |
| `tabMemora Review Item` | `item_id`, `subject`, `topic`, `lesson`, `stage_type` | Content context |
| `tabMemora Season` | `name`, `season_seq`, `season_title`, `start_date`, `end_date` | Season dimensions |
| `tabMemora Player Profile` | `name`, `grade`, `season` | Player dimensions |

**FSRS column details in Memory State:**

- `stability` (DECIMAL 21,9): Number of days until recall probability drops to 10%. Higher = stronger memory.
- `difficulty` (DECIMAL 21,9): Item difficulty (roughly 0-10). Higher = harder.
- `state` (TINYINT): FSRS state — 0=New, 1=Learning, 2=Review, 3=Relearning
- `step` (TINYINT): Current step within learning phase
- `next_review` (DATE): Next review date
- `last_review` (DATETIME): Last review date

**Metrics with formulas:**

- `avg_stability` = `AVG(ms.stability)` — average memory strength
- `avg_difficulty` = `AVG(ms.difficulty)` — average difficulty
- `items_due` = `COUNT(*)` WHERE `ms.next_review <= CURDATE()` — overdue review items
- `items_mastered` = `COUNT(*)` WHERE `ms.state = 2 AND ms.stability > 30` — items with strong memory (>30 days)
- `new_items` = `COUNT(*)` WHERE `ms.state = 0`
- `overdue_ratio` = `items_due / total_items * 100`
- `retention_estimate` = based on `stability` and `DATEDIFF(CURDATE(), last_review)`

**Approximate query:**

```sql
SELECT
    ms.subject,
    ms.season_seq,
    s.season_title,
    pp.grade,
    COUNT(*)                                              AS total_items,
    AVG(ms.stability)                                     AS avg_stability,
    AVG(ms.difficulty)                                    AS avg_difficulty,
    SUM(CASE WHEN ms.state = 0 THEN 1 ELSE 0 END)        AS new_items,
    SUM(CASE WHEN ms.state = 1 THEN 1 ELSE 0 END)        AS learning_items,
    SUM(CASE WHEN ms.state = 2 THEN 1 ELSE 0 END)        AS review_items,
    SUM(CASE WHEN ms.state = 3 THEN 1 ELSE 0 END)        AS relearning_items,
    SUM(CASE WHEN ms.next_review <= CURDATE()
             THEN 1 ELSE 0 END)                           AS overdue_items,
    SUM(CASE WHEN ms.state = 2 AND ms.stability > 30
             THEN 1 ELSE 0 END)                           AS mastered_items,
    ROUND(SUM(CASE WHEN ms.state = 2 AND ms.stability > 30
                   THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1)
                                                          AS mastery_pct
FROM `tabMemora Memory State` ms
JOIN `tabMemora Season` s ON s.season_seq = ms.season_seq
JOIN `tabMemora Player Profile` pp ON pp.name = ms.player
WHERE ms.season_seq = :target_season_seq
GROUP BY ms.subject, ms.season_seq, s.season_title, pp.grade
ORDER BY avg_stability DESC;
```

**Critical Memora-specific notes:**

- Table is RANGE PARTITIONED by `season_seq`. **Always** include `season_seq` in WHERE clause for partition pruning.
- `item_id` is `BINARY(16)`. To join with Review Item, use `BIN_TO_UUID(ms.item_id)` to convert to text.
- `stability` and `difficulty` return `Decimal` objects in Python — must handle when exporting to Parquet.
- Currently 103 rows across 3 seasons. Will be the largest table as the platform scales.

---

#### B2: Subject Completion Progress

**Source tables and columns used:**

| Table | Columns Used | Why |
|---|---|---|
| `tabMemora Structure Progress` | `player`, `subject`, `completion_percentage`, `passed_lessons_bitset` | Completion per (player, subject) |
| `tabMemora Subject` | `name`, `subject_title`, `total_lessons`, `total_tracks` | Subject metadata |
| `tabMemora Player Profile` | `name`, `grade`, `major`, `season` | Player dimensions |

**About `passed_lessons_bitset`:**

This is a `LONGTEXT` column storing a bitset where each bit represents a lesson. Lesson N is identified by the `bit_index` column in `tabMemora Lesson`. If bit = 1, the student completed the lesson.

**Metrics:**

- `avg_completion_pct` = `AVG(sp.completion_percentage)`
- `students_not_started` = players without a Structure Progress record for a given subject
- `students_at_100` = `COUNT(*)` WHERE `completion_percentage >= 100`
- `completion_distribution` = distribution of percentages (0-25%, 25-50%, 50-75%, 75-100%)

**Approximate query:**

```sql
SELECT
    sub.name                                      AS subject,
    sub.subject_title,
    sub.total_lessons,
    pp.grade,
    pp.season,
    COUNT(DISTINCT sp.player)                     AS active_students,
    ROUND(AVG(sp.completion_percentage), 1)       AS avg_completion_pct,
    SUM(CASE WHEN sp.completion_percentage < 25
             THEN 1 ELSE 0 END)                   AS bucket_0_25,
    SUM(CASE WHEN sp.completion_percentage >= 25
              AND sp.completion_percentage < 50
             THEN 1 ELSE 0 END)                   AS bucket_25_50,
    SUM(CASE WHEN sp.completion_percentage >= 50
              AND sp.completion_percentage < 75
             THEN 1 ELSE 0 END)                   AS bucket_50_75,
    SUM(CASE WHEN sp.completion_percentage >= 75
             THEN 1 ELSE 0 END)                   AS bucket_75_100,
    SUM(CASE WHEN sp.completion_percentage >= 100
             THEN 1 ELSE 0 END)                   AS completed_students
FROM `tabMemora Structure Progress` sp
JOIN `tabMemora Subject` sub ON sub.name = sp.subject
JOIN `tabMemora Player Profile` pp ON pp.name = sp.player
GROUP BY sub.name, sub.subject_title, sub.total_lessons, pp.grade, pp.season
ORDER BY avg_completion_pct ASC;
```

**Memora-specific notes:**

- Currently 124 rows. Index `idx_player_subject` on `(player, subject)` accelerates queries.
- The bitset allows computing completed lessons precisely even if `completion_percentage` hasn't been updated yet.
- Subjects with `total_lessons = 0` (57 of 65 published subjects) should be excluded — they are defined without content yet.

---

#### B3: Student Retention by Season

**Source tables and columns used:**

| Table | Columns Used | Why |
|---|---|---|
| `tabMemora Player Plan History` | `player`, `changed_at`, `previous_season`, `new_season`, `previous_grade`, `new_grade`, `trigger_reason`, `snapshot_total_xp`, `snapshot_current_streak`, `snapshot_total_lessons` | Track student transitions between seasons |
| `tabMemora Interaction Log` | `player`, `timestamp` | Determine actual activity per season |
| `tabMemora Player Profile` | `name`, `grade`, `season` | Current season for each student |
| `tabMemora Season` | `name`, `season_seq`, `start_date`, `end_date` | Season date boundaries |

**Metrics:**

- `active_in_season` = count of players with >= 1 interaction within the season date range
- `returned_next_season` = count of players active in both season S and season S+1
- `retention_rate` = `returned_next_season / active_in_season * 100`
- `churn_rate` = `100 - retention_rate`
- `grade_change_rate` = count of players whose grade changed (from Player Plan History)

**Approximate query:**

```sql
WITH season_activity AS (
    SELECT DISTINCT
        il.player,
        s.name AS season_id,
        s.season_seq
    FROM `tabMemora Interaction Log` il
    JOIN `tabMemora Season` s
        ON il.timestamp >= s.start_date
       AND il.timestamp <  DATE_ADD(s.end_date, INTERVAL 1 DAY)
    WHERE s.is_published = 1
)
SELECT
    curr.season_id                                AS current_season,
    curr.season_seq,
    COUNT(DISTINCT curr.player)                   AS active_students,
    COUNT(DISTINCT nxt.player)                    AS returned_students,
    ROUND(COUNT(DISTINCT nxt.player) * 100.0
        / NULLIF(COUNT(DISTINCT curr.player), 0), 1)
                                                  AS retention_pct
FROM season_activity curr
LEFT JOIN season_activity nxt
    ON nxt.player = curr.player
   AND nxt.season_seq = curr.season_seq + 1
GROUP BY curr.season_id, curr.season_seq
ORDER BY curr.season_seq;
```

**Memora-specific notes:**

- Player Plan History currently has only 2 rows. But the schema is rich — it stores full snapshots (`snapshot_total_xp`, `snapshot_total_lessons`, `snapshot_progress_json`, `snapshot_memory_states`). This allows comparing student level at transition time.
- `trigger_reason` documents the reason for the change (manual upgrade, season end, etc.).
- 8 seasons currently. `season_seq` is used for chronological ordering.

---

#### B4: Learning Velocity

**Source tables and columns used:**

| Table | Columns Used | Why |
|---|---|---|
| `tabMemora Player Wallet` | `player`, `total_xp`, `total_lessons`, `total_time_min`, `current_streak`, `daily_xp_json`, `last_sync_at` | Player progress summary |
| `tabMemora Player Profile` | `name`, `grade`, `major`, `season`, `creation` | Dimensions + registration date |

**About `daily_xp_json`:**

Contains a JSON array of daily XP values. This allows computing daily trends without aggregating from the Interaction Log.

**Metrics:**

- `xp_per_day` = `total_xp / DATEDIFF(CURDATE(), pp.creation)` (daily average since registration)
- `lessons_per_week` = `total_lessons / (DATEDIFF(CURDATE(), pp.creation) / 7.0)`
- `avg_time_per_lesson` = `total_time_min / NULLIF(total_lessons, 0)`
- `streak_distribution` = distribution of `current_streak` across players
- `velocity_trend` = from `daily_xp_json` — compare last 7 days with previous 7

**Approximate query:**

```sql
SELECT
    pp.grade,
    pp.season,
    COUNT(*)                                                AS total_players,
    ROUND(AVG(pw.total_xp), 0)                              AS avg_total_xp,
    ROUND(AVG(pw.total_lessons), 1)                          AS avg_lessons,
    ROUND(AVG(pw.total_time_min), 0)                         AS avg_time_min,
    ROUND(AVG(pw.current_streak), 1)                         AS avg_streak,
    ROUND(AVG(pw.total_xp /
        NULLIF(DATEDIFF(CURDATE(), pp.creation), 0)), 1)     AS avg_xp_per_day,
    ROUND(AVG(pw.total_time_min /
        NULLIF(pw.total_lessons, 0)), 1)                     AS avg_min_per_lesson,
    SUM(CASE WHEN pw.current_streak = 0 THEN 1 ELSE 0 END)  AS streak_zero,
    SUM(CASE WHEN pw.current_streak BETWEEN 1 AND 7
             THEN 1 ELSE 0 END)                              AS streak_1_7,
    SUM(CASE WHEN pw.current_streak > 7
             THEN 1 ELSE 0 END)                              AS streak_gt7
FROM `tabMemora Player Wallet` pw
JOIN `tabMemora Player Profile` pp ON pp.name = pw.player
GROUP BY pp.grade, pp.season
ORDER BY avg_xp_per_day DESC;
```

**Memora-specific notes:**

- 330 wallets out of 364 players. The rest have no wallet = never interacted.
- `dirty_flag` indicates the wallet needs syncing — not used in reports.
- `last_sync_at` is useful for identifying inactive players (last sync > 1 week ago).

---

### Category C: Content Quality

---

#### C1: Item Difficulty Analysis

**Source tables and columns used:**

| Table | Columns Used | Why |
|---|---|---|
| `tabMemora Review Item` | `item_id`, `name`, `subject`, `topic`, `lesson`, `stage_id`, `stage_type`, `question_text` | Question and context |
| `tabMemora Practice Log` | `item_id`, `attempt_count`, `correct_count` | Accuracy from practice |
| `tabMemora Memory State` | `item_id`, `difficulty`, `stability`, `state` | FSRS-computed difficulty |
| `tabMemora Challenge Attempt Detail` | `item_id`, `correct`, `time_spent` | Performance in challenges |

**Three sources of difficulty:**

1. **From Practice Log**: `practice_accuracy` = `SUM(correct_count) / SUM(attempt_count)` — practice accuracy
2. **From Memory State**: `fsrs_difficulty` = `AVG(ms.difficulty)` — algorithmically computed FSRS difficulty
3. **From Challenge Detail**: `challenge_accuracy` = `AVG(cad.correct)` — challenge test accuracy

**Approximate query (combined report):**

```sql
SELECT
    ri.item_id,
    ri.subject,
    ri.topic,
    ri.lesson,
    ri.stage_type,
    LEFT(ri.question_text, 80)                              AS question_preview,

    -- From Practice Log
    SUM(pl.attempt_count)                                   AS total_practice_attempts,
    ROUND(SUM(pl.correct_count) * 100.0
        / NULLIF(SUM(pl.attempt_count), 0), 1)              AS practice_accuracy_pct,
    COUNT(DISTINCT pl.player_id)                            AS players_practiced,

    -- From Memory State (needs item_id conversion)
    ms_agg.avg_fsrs_difficulty,
    ms_agg.avg_stability,

    -- From Challenge Attempt Detail
    ch_agg.challenge_attempts,
    ch_agg.challenge_accuracy_pct
FROM `tabMemora Review Item` ri

LEFT JOIN `tabMemora Practice Log` pl
    ON pl.item_id = ri.item_id

LEFT JOIN (
    SELECT
        BIN_TO_UUID(item_id) AS item_uuid,
        ROUND(AVG(difficulty), 3) AS avg_fsrs_difficulty,
        ROUND(AVG(stability), 1)  AS avg_stability
    FROM `tabMemora Memory State`
    GROUP BY BIN_TO_UUID(item_id)
) ms_agg ON ms_agg.item_uuid = ri.item_id

LEFT JOIN (
    SELECT
        item_id,
        COUNT(*)                                AS challenge_attempts,
        ROUND(AVG(correct) * 100, 1)            AS challenge_accuracy_pct
    FROM `tabMemora Challenge Attempt Detail`
    GROUP BY item_id
) ch_agg ON ch_agg.item_id = ri.item_id

GROUP BY ri.item_id, ri.subject, ri.topic, ri.lesson,
         ri.stage_type, ri.question_text,
         ms_agg.avg_fsrs_difficulty, ms_agg.avg_stability,
         ch_agg.challenge_attempts, ch_agg.challenge_accuracy_pct
ORDER BY practice_accuracy_pct ASC
LIMIT 50;
```

**Critical Memora-specific notes — `item_id` encoding differs across tables:**

| Table | Column Type | Format |
|---|---|---|
| `Review Item` | `VARCHAR(140)` | UUID as text |
| `Practice Log` | `VARCHAR(36)` | UUID as text |
| `Memory State` | `BINARY(16)` | UUID bytes, needs `BIN_TO_UUID()` |
| `Challenge Attempt Detail` | `VARCHAR(140)` | UUID as text |

202 review items total. 179 of them are in subject SUBJ-00704 (the main subject with actual data).

---

#### C2: Content Error/Report Tracker

**Source tables and columns:**

| Table | Columns | Why |
|---|---|---|
| `tabMemora Content Report` | `player`, `subject`, `lesson`, `report_type`, `description`, `status`, `creation` | Bug reports |
| `tabMemora Lesson` | `name`, `lesson_title`, `subject`, `topic` | Lesson context |

**Metrics:**

- `reports_by_type` = `COUNT(*) GROUP BY report_type` (Bug / Content Error / Suggestion / Other)
- `reports_per_lesson` = `COUNT(*) GROUP BY lesson`
- `open_reports` = `COUNT(*)` WHERE `status IN ('Open', 'In Progress')`
- `avg_resolution_days` = `AVG(DATEDIFF(modified, creation))` WHERE `status IN ('Resolved', 'Closed')`

**Note:** Only 6 reports currently. This report becomes important as the user base grows.

---

#### C3: Stage Type Effectiveness

**Source tables and columns:**

| Table | Columns | Why |
|---|---|---|
| `tabMemora Interaction Log` | `stage_id`, `time_spent`, `errors_count`, `event_type`, `lesson` | Performance data |
| `tabMemora Lesson Stage` | `stage_id`, `stage_type`, `is_skippable`, `parent` (-> Lesson) | Stage type |
| `tabMemora Lesson Stage Settings` | `stage_title`, `default_stage_time`, `is_time_calculated` | Type settings |

**Metrics:**

- `avg_time_by_stage_type` = `AVG(il.time_spent)` grouped by `ls.stage_type`
- `error_rate_by_stage_type` = `AVG(il.errors_count)`
- `skip_rate` = percentage of skippable stages that were skipped (needs `event_type = 'Skipped'`)
- `time_efficiency` = `AVG(il.time_spent) / lss.default_stage_time` — did the student take more or less than expected

**Approximate query:**

```sql
SELECT
    ls.stage_type,
    lss.default_stage_time,
    ls.is_skippable,
    COUNT(*)                                      AS total_events,
    COUNT(DISTINCT il.player)                     AS unique_players,
    ROUND(AVG(il.time_spent), 1)                  AS avg_time_sec,
    ROUND(AVG(il.errors_count), 2)                AS avg_errors,
    ROUND(AVG(il.time_spent) * 1.0
        / NULLIF(lss.default_stage_time, 0), 2)   AS time_ratio
FROM `tabMemora Interaction Log` il
JOIN `tabMemora Lesson Stage` ls
    ON ls.stage_id = il.stage_id AND ls.parent = il.lesson
JOIN `tabMemora Lesson Stage Settings` lss
    ON lss.stage_title = ls.stage_type
WHERE il.event_type = 'Completed'
GROUP BY ls.stage_type, lss.default_stage_time, ls.is_skippable
ORDER BY avg_errors DESC;
```

**Memora-specific notes:**

- 8 stage types defined in `Lesson Stage Settings`, each with a different default time.
- 222 stages in `Lesson Stage` across 47 lessons.

---

### Category D: Challenges & Competition

---

#### D1: Challenge Performance Dashboard

**Source tables and columns:**

| Table | Columns | Why |
|---|---|---|
| `tabMemora Challenge Attempt` | `player`, `topic`, `subject`, `season`, `attempt_number`, `total_questions`, `correct_count`, `score_pct`, `passed`, `time_spent`, `xp_earned`, `submitted_at` | Attempts |
| `tabMemora Challenge Attempt Detail` | `parent`, `item_id`, `correct`, `time_spent`, `chosen_answer` | Per-question answers |
| `tabMemora Challenge Progress` | `player`, `topic`, `subject`, `season`, `stamped`, `best_score_pct`, `attempt_count`, `total_xp_earned` | Progress summary |

**Metrics:**

- `avg_score_pct` = `AVG(ca.score_pct)`
- `pass_rate` = `SUM(ca.passed) / COUNT(*) * 100`
- `avg_attempts_to_pass` = `AVG(ca.attempt_number)` WHERE `ca.passed = 1` (first successful attempt)
- `avg_time_per_attempt` = `AVG(ca.time_spent)`
- `hardest_questions` = questions with lowest `AVG(cad.correct)`
- `xp_total` = `SUM(ca.xp_earned)`

**Approximate queries:**

```sql
-- Summary at topic level
SELECT
    ca.topic,
    ca.subject,
    ca.season,
    COUNT(DISTINCT ca.player)                     AS unique_players,
    COUNT(*)                                      AS total_attempts,
    ROUND(AVG(ca.score_pct), 1)                   AS avg_score_pct,
    ROUND(SUM(ca.passed) * 100.0 / COUNT(*), 1)   AS pass_rate,
    ROUND(AVG(ca.time_spent), 0)                  AS avg_time_sec,
    SUM(ca.xp_earned)                             AS total_xp,
    MAX(ca.attempt_number)                        AS max_attempts
FROM `tabMemora Challenge Attempt` ca
GROUP BY ca.topic, ca.subject, ca.season;

-- Hardest questions
SELECT
    cad.item_id,
    ri.question_text,
    COUNT(*)                                      AS times_asked,
    SUM(cad.correct)                              AS times_correct,
    ROUND(AVG(cad.correct) * 100, 1)              AS accuracy_pct,
    ROUND(AVG(cad.time_spent), 0)                 AS avg_time_sec
FROM `tabMemora Challenge Attempt Detail` cad
JOIN `tabMemora Review Item` ri ON ri.item_id = cad.item_id
GROUP BY cad.item_id, ri.question_text
ORDER BY accuracy_pct ASC;
```

**Memora-specific notes:**

- 30 attempts with 95 question-level details. Index `idx_ch_attempt_dedup` on `(player, topic, attempt_number, submitted_at)` prevents duplicates.
- `stamped` in Challenge Progress means the student earned the completion stamp for the topic.
- `chosen_answer` is INT referring to answer number (1-4). `correct_choice` in Review Item defines the correct answer.

---

#### D2: Live Challenge Event Analytics

**Source tables and columns:**

| Table | Columns | Why |
|---|---|---|
| `tabMemora Live Challenge Event` | `name`, `event_name`, `status`, `scheduled_start`, `exam_duration`, `capacity`, `participant_count`, `submitted_count`, `leaderboard_json`, `is_paid`, XP fields | Event data |
| `tabMemora Live Challenge Participation` | `event`, `player`, `joined_at`, `submitted_at`, `score`, `rank`, `xp_awarded`, `answers_json` | Player participation |
| `tabMemora Live Challenge Question` | `parent` (-> Event), `question_text`, `option_a`-`d`, `correct_answer`, `source_review_item` | Questions |

**Metrics:**

- `participation_rate` = `participant_count / capacity * 100`
- `submission_rate` = `submitted_count / participant_count * 100`
- `avg_score` = `AVG(lcp.score)`
- `per_question_accuracy` = from `answers_json` — requires JSON parsing
- `xp_distribution` = distribution of `lcp.xp_awarded`
- `time_to_submit` = `AVG(TIMESTAMPDIFF(SECOND, lce.exam_start_ts, lcp.submitted_at))`

**Memora-specific notes:**

- Only 2 events currently. `leaderboard_json` contains full participant ranking as JSON.
- `answers_json` in Participation contains each question's answer per participant — very rich data for question analysis.
- `is_paid` field indicates paid challenges — future revenue opportunity.

---

### Category E: Monetization & Subscriptions

---

#### E1: Revenue Cohort Analysis

**Source tables and columns:**

| Table | Columns | Why |
|---|---|---|
| `tabMemora Subscription Transaction` | `player`, `payment_method`, `status`, `amount_paid`, `creation`, `related_grant` | Financial transactions |
| `tabMemora Player Profile` | `name`, `grade`, `season`, `creation` | Player dimensions + registration date |
| `tabMemora Voucher Card` | `redeemed_by`, `redeemed_at`, `batch` | Link redeemed vouchers |
| `tabMemora Voucher Batch` | `name`, `face_value`, `batch_purpose` | Voucher value |

**Metrics:**

- `revenue_total` = `SUM(st.amount_paid)` WHERE `st.status = 'Completed'`
- `arpu` = `revenue_total / COUNT(DISTINCT st.player)` — average revenue per user
- `transactions_per_player` = `COUNT(*) / COUNT(DISTINCT st.player)`
- `voucher_revenue` = revenue from `payment_method = 'Voucher'` (73 transactions)
- `manual_revenue` = revenue from `payment_method = 'Manual-Admin'` (4 transactions)
- `monthly_revenue` = grouped by `DATE_FORMAT(st.creation, '%Y-%m')`

**Approximate query:**

```sql
SELECT
    DATE_FORMAT(st.creation, '%Y-%m')             AS month,
    pp.grade,
    st.payment_method,
    COUNT(*)                                      AS transactions,
    COUNT(DISTINCT st.player)                     AS paying_players,
    SUM(st.amount_paid)                           AS total_revenue,
    ROUND(SUM(st.amount_paid)
        / NULLIF(COUNT(DISTINCT st.player), 0), 2) AS arpu
FROM `tabMemora Subscription Transaction` st
JOIN `tabMemora Player Profile` pp ON pp.name = st.player
WHERE st.status = 'Completed'
GROUP BY DATE_FORMAT(st.creation, '%Y-%m'), pp.grade, st.payment_method
ORDER BY month;
```

---

#### E2: Subscription Lifecycle

**Source tables and columns:**

| Table | Columns | Why |
|---|---|---|
| `tabMemora Player Subscription` | `player`, `access_key`, `expires_at`, `is_active`, `creation` | Subscriptions |
| `tabMemora Player Profile` | `name`, `grade`, `season` | Player dimensions |

**About `access_key`:**

Follows two patterns:

- `SUB-SUBJ-XXXXX` -> subscription to a Subject
- `TRK-Track-XXXXX` -> subscription to a Track

This determines what content the subscription unlocks.

**Metrics:**

- `active_subscriptions` = `COUNT(*)` WHERE `is_active = 1`
- `expired_subscriptions` = `COUNT(*)` WHERE `is_active = 0 OR expires_at < CURDATE()`
- `avg_subscription_duration` = `AVG(DATEDIFF(expires_at, creation))`
- `subscriptions_per_player` = `COUNT(*) / COUNT(DISTINCT player)`
- `subject_popularity` = subscription count per `access_key`
- `expiring_soon` = subscriptions expiring within 30 days

**Approximate query:**

```sql
SELECT
    ps.access_key,
    CASE
        WHEN ps.access_key LIKE 'SUB-SUBJ-%' THEN 'Subject'
        WHEN ps.access_key LIKE 'TRK-Track-%' THEN 'Track'
        ELSE 'Other'
    END                                           AS subscription_type,
    COUNT(*)                                      AS total_subscriptions,
    SUM(ps.is_active)                             AS active_count,
    SUM(CASE WHEN ps.expires_at < CURDATE()
             THEN 1 ELSE 0 END)                   AS expired_count,
    SUM(CASE WHEN ps.expires_at BETWEEN CURDATE()
                  AND DATE_ADD(CURDATE(), INTERVAL 30 DAY)
             THEN 1 ELSE 0 END)                   AS expiring_soon,
    ROUND(AVG(DATEDIFF(ps.expires_at, ps.creation)), 0)
                                                  AS avg_duration_days
FROM `tabMemora Player Subscription` ps
GROUP BY ps.access_key
ORDER BY total_subscriptions DESC;
```

**Memora-specific note:** SUB-SUBJ-00712 is most popular (11 subscriptions), followed by SUB-SUBJ-00704 (7 subscriptions) — this is the same subject with 179 review items and 76 memory states. The main subject in the system.

---

#### E3: Voucher-to-Activation Funnel

**Source tables and columns:**

| Table | Columns | Why |
|---|---|---|
| `tabMemora Voucher Card` | `name`, `serial_no`, `batch`, `status`, `library`, `allocation`, `sale_model`, `redeemed_by`, `redeemed_at`, `batch_purpose` | Card status |
| `tabMemora Voucher Allocation` | `name`, `batch`, `customer`, `allocation_date`, `sale_model`, `quantity` | Card distribution |
| `tabMemora Voucher Batch` | `name`, `face_value`, `quantity`, `batch_purpose` | Batch data |

**Metrics:**

- `allocation_to_redemption_days` = `AVG(DATEDIFF(vc.redeemed_at, va.allocation_date))` — time from distribution to redemption
- `funnel`: Generated (4,872) -> Allocated (1,601) -> Redeemed (336) — final activation rate **6.9%**
- `wastage_rate` = `(voided + expired) / total * 100` = `(237 + 28) / 4,872 = 5.4%`
- `library_efficiency` = redemption rate per library

**Approximate query:**

```sql
SELECT
    vc.library,
    vb.batch_purpose,
    vc.sale_model,
    COUNT(*)                                      AS total_cards,
    SUM(CASE WHEN vc.status = 'Available'
             THEN 1 ELSE 0 END)                   AS available,
    SUM(CASE WHEN vc.status = 'Allocated'
             THEN 1 ELSE 0 END)                   AS allocated,
    SUM(CASE WHEN vc.status = 'Redeemed'
             THEN 1 ELSE 0 END)                   AS redeemed,
    SUM(CASE WHEN vc.status IN ('Void', 'Expired')
             THEN 1 ELSE 0 END)                   AS wasted,
    ROUND(SUM(CASE WHEN vc.status = 'Redeemed'
                   THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1)
                                                  AS redemption_pct,
    ROUND(AVG(CASE WHEN vc.status = 'Redeemed'
        THEN DATEDIFF(vc.redeemed_at, va.allocation_date)
        END), 1)                                  AS avg_days_to_redeem
FROM `tabMemora Voucher Card` vc
JOIN `tabMemora Voucher Batch` vb ON vb.name = vc.batch
LEFT JOIN `tabMemora Voucher Allocation` va ON va.name = vc.allocation
GROUP BY vc.library, vb.batch_purpose, vc.sale_model
ORDER BY redemption_pct ASC;
```

---

### Category F: Operational Health

---

#### F1: Archive Pipeline Health

**Source tables and columns:**

| Table | Columns | Why |
|---|---|---|
| `tabMemora Archive Job` | `name`, `source_doctype`, `status`, `archive_scope`, `execution_stage`, `started_at`, `completed_at`, `duration_seconds`, `row_count`, `file_size_bytes`, `retry_count`, `error_log` | Archive jobs |
| `archive_delete_audit_log` | `job_id`, `rows_deleted`, `duration_ms`, `status`, `batch_size`, `num_batches` | Purge audit trail |
| `tabMemora Task Log Archive Batch` | `status`, `row_count`, `exported_at`, `purged_at`, `retry_count` | Task log archive batches |

**Metrics:**

- `jobs_by_status`: Pending (12), Completed (8), Purged (40) — current distribution
- `avg_duration_sec` = `AVG(duration_seconds)` WHERE `status = 'Completed'`
- `total_rows_archived` = `SUM(row_count)` WHERE `status IN ('Completed', 'Purged')`
- `error_rate` = `SUM(retry_count > 0) / COUNT(*)`
- `purge_throughput` = from `archive_delete_audit_log`: `SUM(rows_deleted) / SUM(duration_ms) * 1000` rows/sec

**Current distribution:**

| Source DocType | Status | Count |
|---|---|---|
| Memora Interaction Log | Pending | 5 |
| Memora Memory State | Completed | 3 |
| Memora Memory State | Pending | 2 |
| Memora Practice Log | Completed | 5 |
| Memora Task Run Log | Pending | 5 |
| Memora Task Run Log | Purged | 40 |

---

#### F2: Background Task Health

**Source tables and columns:**

| Table | Columns | Why |
|---|---|---|
| `tabMemora Task Run Log` | `task_name`, `run_date`, `started_at`, `completed_at`, `duration_sec`, `status`, `triggered_by`, `processed_count`, `failed_count`, `error_message` | Task log |
| `tabMemora Build Queue` | `target_type`, `target_name`, `status`, `started_at`, `completed_at`, `duration_sec`, `files_generated`, `trigger_reason` | Build queue |

**Metrics:**

- `tasks_per_day` = `COUNT(*) GROUP BY run_date`
- `failure_rate` = `SUM(status='Failed') / COUNT(*) * 100`
- `avg_duration` = `AVG(duration_sec)` per `task_name`
- `p95_duration` = 95th percentile of execution duration
- `total_processed` = `SUM(processed_count)`
- `total_failed` = `SUM(failed_count)`

**Note:** 1,735 task run logs and 215 build queue items. `triggered_by` distinguishes between scheduled (`Scheduler`), manual (`Manual`), and catch-up (`Catch-up`) tasks.

---

## 3. Shared Datasets for Analytics

### Why shared datasets matter

Instead of each report joining 5-6 tables every time, we export common entities once as Parquet files. Analytics queries then join lightweight dimension tables against fact tables.

---

### 3.1 `dim_player`

**Source:** `tabMemora Player Profile`

**Grain:** One row per registered player.

| Source Column | Export Column | Type | Note |
|---|---|---|---|
| `name` | `player_id` | VARCHAR(140) | Unique ID (PLAYER-XXXXX) |
| `display_name` | `display_name` | VARCHAR(140) | |
| `grade` | `grade_id` | VARCHAR(140) | FK -> Grade (GRD-XXXXX) |
| `major` | `major_id` | VARCHAR(140) | FK -> Major (MJR-XXXXX) |
| `season` | `season_id` | VARCHAR(140) | Current season |
| `gender` | `gender` | VARCHAR(140) | Male (342) / Female (2) / NULL (20) |
| `preferred_lang` | `language` | VARCHAR(140) | ar / en |
| `creation` | `registered_at` | DATETIME | Registration date |
| `mobile` | — | **NOT EXPORTED** | Sensitive data |
| `password` | — | **NOT EXPORTED** | Sensitive data |

**Why shared:** Almost every report needs player dimension for segmentation by grade/major/season/gender. 364 rows — small, fully loaded in memory.

**Used by:** A1, A2, A3, A4, B2, B3, B4, D1, E1, E2 — **10 of 15 reports**.

---

### 3.2 `dim_content_hierarchy`

**Sources:** `tabMemora Subject`, `tabMemora Track`, `tabMemora Unit`, `tabMemora Topic`, `tabMemora Lesson`, `tabMemora Lesson Stage`

**Grain:** One row per lesson (denormalized with full hierarchy).

| Source | Export Column | Type |
|---|---|---|
| `tabMemora Lesson.name` | `lesson_id` | VARCHAR(140) |
| `tabMemora Lesson.lesson_title` | `lesson_title` | VARCHAR(140) |
| `tabMemora Lesson.subject` | `subject_id` | VARCHAR(140) |
| `tabMemora Subject.subject_title` | `subject_title` | VARCHAR(140) |
| `tabMemora Lesson.track` | `track_id` | VARCHAR(140) |
| `tabMemora Track.track_title` | `track_title` | VARCHAR(140) |
| `tabMemora Lesson.unit` | `unit_id` | VARCHAR(140) |
| `tabMemora Unit.unit_title` | `unit_title` | VARCHAR(140) |
| `tabMemora Lesson.topic` | `topic_id` | VARCHAR(140) |
| `tabMemora Topic.topic_title` | `topic_title` | VARCHAR(140) |
| `tabMemora Lesson.base_xp` | `base_xp` | INT |
| `tabMemora Lesson.max_hearts` | `max_hearts` | INT |
| `tabMemora Lesson.is_reviewable` | `is_reviewable` | BOOL |
| `tabMemora Lesson.bit_index` | `bit_index` | INT |
| COUNT(stages) | `stage_count` | INT |
| GROUP_CONCAT(stage_types) | `stage_types` | TEXT |

**Build query:**

```sql
SELECT
    l.name AS lesson_id, l.lesson_title,
    l.subject AS subject_id, sub.subject_title,
    l.track AS track_id, t.track_title,
    l.unit AS unit_id, u.unit_title,
    l.topic AS topic_id, tp.topic_title,
    l.base_xp, l.max_hearts, l.is_reviewable, l.bit_index,
    (SELECT COUNT(*) FROM `tabMemora Lesson Stage` ls
     WHERE ls.parent = l.name) AS stage_count,
    (SELECT GROUP_CONCAT(DISTINCT ls.stage_type)
     FROM `tabMemora Lesson Stage` ls
     WHERE ls.parent = l.name) AS stage_types
FROM `tabMemora Lesson` l
LEFT JOIN `tabMemora Subject` sub ON sub.name = l.subject
LEFT JOIN `tabMemora Track` t ON t.name = l.track
LEFT JOIN `tabMemora Unit` u ON u.name = l.unit
LEFT JOIN `tabMemora Topic` tp ON tp.name = l.topic
WHERE l.is_published = 1;
```

**Why shared:** The hierarchy Subject > Track > Unit > Topic > Lesson is the universal content dimension. Denormalizing once avoids repeating 5 JOINs in every report.

**Used by:** A3, A4, B2, C1, C2, C3, D1 — **7 reports**.

---

### 3.3 `dim_review_item`

**Source:** `tabMemora Review Item`

**Grain:** One row per reviewable item (question).

| Source Column | Export Column | Type |
|---|---|---|
| `item_id` | `item_id` | VARCHAR(140) — UUID |
| `subject` | `subject_id` | VARCHAR(140) |
| `topic` | `topic_id` | VARCHAR(140) |
| `lesson` | `lesson_id` | VARCHAR(140) |
| `stage_id` | `stage_id` | VARCHAR(140) |
| `stage_type` | `stage_type` | VARCHAR(140) |
| `question_text` | `question_text` | TEXT |
| `correct_choice` | `correct_choice` | INT (1-4) |

**Why shared:** This is the join key between Practice Log, Memory State, and Challenge Attempt Detail. 202 items. These are the atomic learning units.

**Used by:** A3, B1, C1, D1 — **4 reports**.

---

### 3.4 `dim_season`

**Source:** `tabMemora Season`

**Grain:** One row per season.

| Source Column | Export Column | Type |
|---|---|---|
| `name` | `season_id` | VARCHAR(140) |
| `season_title` | `season_title` | VARCHAR(140) |
| `season_seq` | `season_seq` | INT (unique) |
| `start_date` | `start_date` | DATE |
| `end_date` | `end_date` | DATE |
| `is_published` | `is_published` | BOOL |

**Why shared:** Most learning and subscription data is scoped to a season. 8 rows. Memory State is partitioned by `season_seq`.

**Used by:** B1, B3, D1, E2, A1 — **5 reports**.

---

### 3.5 `dim_academic_plan`

**Sources:** `tabMemora Academic Plan`, `tabMemora Plan Subject`, `tabMemora Grade`, `tabMemora Major`

**Grain:** One row per plan (denormalized with grade, major, season, subjects).

| Source | Export Column | Type |
|---|---|---|
| `tabMemora Academic Plan.name` | `plan_id` | VARCHAR(140) |
| `tabMemora Academic Plan.plan_name` | `plan_name` | VARCHAR(140) |
| `tabMemora Academic Plan.grade` | `grade_id` | VARCHAR(140) |
| `tabMemora Grade.grade_title` | `grade_title` | VARCHAR(140) |
| `tabMemora Academic Plan.major` | `major_id` | VARCHAR(140) |
| `tabMemora Major.major_title` | `major_title` | VARCHAR(140) |
| `tabMemora Academic Plan.season` | `season_id` | VARCHAR(140) |
| `tabMemora Academic Plan.is_published` | `is_published` | BOOL |
| `tabMemora Academic Plan.total_subjects` | `total_subjects` | INT |
| `tabMemora Academic Plan.total_lessons` | `total_lessons` | INT |
| GROUP_CONCAT(subjects) | `subject_list` | TEXT |

**Why shared:** 917 academic plans define what content each student group sees. Links grades/majors to subjects.

**Used by:** B2, B3, E2 — **3 reports**.

---

### 3.6 `fact_interaction`

**Source:** `tabMemora Interaction Log`

**Grain:** One row per learning event.

| Source Column | Export Column | Type | Note |
|---|---|---|---|
| `name` | `event_id` | VARCHAR(140) | LOG-XXXXX |
| `player` | `player_id` | VARCHAR(140) | FK -> dim_player |
| `lesson` | `lesson_id` | VARCHAR(140) | FK -> dim_content |
| `stage_id` | `stage_id` | VARCHAR(140) | |
| `item_id` | `item_id` | VARCHAR(140) | FK -> dim_review_item (nullable) |
| `event_type` | `event_type` | VARCHAR(140) | Started/Completed/Failed/Skipped |
| `time_spent` | `time_spent_sec` | INT | In seconds |
| `errors_count` | `errors_count` | INT | |
| `timestamp` | `event_ts` | DATETIME(6) | |
| `client_metadata` | `client_metadata` | JSON | Device data |

**Why shared:** Highest-volume fact table (10,906 rows, growing daily). **Every** behavioral report depends on it. Should be partitioned by date in the analytics layer.

**Used by:** A1, A2, A4, B3, C3 — **5 reports**.

---

### 3.7 `fact_memory_state`

**Source:** `tabMemora Memory State`

**Grain:** One row per (player, item, season).

| Source Column | Export Column | Type | Note |
|---|---|---|---|
| `name` | `ms_id` | BIGINT | |
| `player` | `player_id` | VARCHAR(140) | FK -> dim_player |
| `item_id` | `item_id` | BINARY(16) -> HEX | Must convert with `BIN_TO_UUID()` |
| `season_seq` | `season_seq` | INT | Partition key |
| `subject` | `subject_id` | VARCHAR(140) | |
| `lesson` | `lesson_id` | VARCHAR(140) | |
| `stability` | `stability` | DECIMAL -> FLOAT64 | Must convert from Decimal |
| `difficulty` | `difficulty` | DECIMAL -> FLOAT64 | Must convert from Decimal |
| `next_review` | `next_review` | DATE | |
| `last_review` | `last_review` | DATETIME(6) | |
| `state` | `fsrs_state` | TINYINT | 0=New, 1=Learning, 2=Review, 3=Relearning |
| `step` | `fsrs_step` | TINYINT | |

**Export warnings:**

- `item_id` is `BINARY(16)` — must convert with `HEX()` or `BIN_TO_UUID()` for Parquet
- `stability` and `difficulty` are `DECIMAL(21,9)` — return `Decimal` objects in Python. Must convert to `float64` for Parquet
- Table is RANGE PARTITIONED by `season_seq` — must export season-by-season

**Used by:** B1, C1 — the two most important reports for measuring actual learning.

---

### 3.8 `fact_practice`

**Source:** `tabMemora Practice Log`

**Grain:** One row per (player, item).

| Source Column | Export Column | Type | Note |
|---|---|---|---|
| `player_id` | `player_id` | VARCHAR(140) | FK -> dim_player |
| `item_id` | `item_id` | VARCHAR(36) | FK -> dim_review_item |
| `first_seen_at` | `first_seen_at` | DATETIME | |
| `last_seen_at` | `last_seen_at` | DATETIME | |
| `last_result` | `last_result` | ENUM | Correct / Incorrect |
| `attempt_count` | `attempt_count` | INT UNSIGNED | |
| `correct_count` | `correct_count` | INT UNSIGNED | |

**Why shared:** Aggregated practice data per item. Custom non-Frappe table with composite PK `(player_id, item_id)`.

**Used by:** A3, C1 — **2 reports**.

---

### 3.9 `fact_subscription`

**Sources:** `tabMemora Player Subscription`, `tabMemora Subscription Transaction`

**Grain:** One row per subscription (joined with transaction).

| Source Column | Export Column | Type |
|---|---|---|
| `ps.player` | `player_id` | VARCHAR(140) |
| `ps.access_key` | `access_key` | VARCHAR(140) |
| `ps.is_active` | `is_active` | BOOL |
| `ps.expires_at` | `expires_at` | DATE |
| `ps.creation` | `subscribed_at` | DATETIME |
| `st.payment_method` | `payment_method` | VARCHAR(140) |
| `st.amount_paid` | `amount_paid` | DECIMAL |
| `st.status` | `txn_status` | VARCHAR(140) |

**Why shared:** 73 subscriptions + 77 transactions = complete monetization picture. `access_key` encodes what content was purchased (subject or track).

**Used by:** E1, E2 — **2 reports**.

---

### 3.10 `fact_voucher`

**Sources:** `tabMemora Voucher Card`, `tabMemora Voucher Batch`, `tabMemora Voucher Allocation`

**Grain:** One row per voucher card (denormalized with batch and allocation).

| Source | Export Column | Type |
|---|---|---|
| `vc.serial_no` | `serial_no` | VARCHAR(140) |
| `vc.batch` | `batch_id` | VARCHAR(140) |
| `vb.batch_name` | `batch_name` | VARCHAR(140) |
| `vb.batch_purpose` | `batch_purpose` | VARCHAR(140) |
| `vb.face_value` | `face_value` | DECIMAL |
| `vc.status` | `card_status` | VARCHAR(140) |
| `vc.library` | `library` | VARCHAR(140) |
| `vc.sale_model` | `sale_model` | VARCHAR(140) |
| `vc.redeemed_by` | `redeemed_by` | VARCHAR(140) |
| `vc.redeemed_at` | `redeemed_at` | DATETIME |
| `va.allocation_date` | `allocation_date` | DATE |
| `va.customer` | `allocated_to` | VARCHAR(140) |

**Why shared:** 4,872 voucher cards are the main monetization pipeline. All 5 existing reports use this data. Denormalizing once simplifies analytics queries.

**Used by:** E1, E3, and all 5 existing reports — **7+ reports**.

---

### 3.11 `fact_challenge`

**Sources:** `tabMemora Challenge Attempt`, `tabMemora Challenge Attempt Detail`

**Grain:** One row per challenge attempt (with nested detail).

**Attempt-level columns:**

| Source Column | Export Column | Type |
|---|---|---|
| `ca.name` | `attempt_id` | VARCHAR(140) |
| `ca.player` | `player_id` | VARCHAR(140) |
| `ca.topic` | `topic_id` | VARCHAR(140) |
| `ca.subject` | `subject_id` | VARCHAR(140) |
| `ca.season` | `season_id` | VARCHAR(140) |
| `ca.attempt_number` | `attempt_number` | INT |
| `ca.total_questions` | `total_questions` | INT |
| `ca.correct_count` | `correct_count` | INT |
| `ca.score_pct` | `score_pct` | DECIMAL |
| `ca.passed` | `passed` | BOOL |
| `ca.time_spent` | `time_spent_sec` | INT |
| `ca.xp_earned` | `xp_earned` | INT |
| `ca.submitted_at` | `submitted_at` | DATETIME |

**Detail-level columns (exported separately, one row per question per attempt):**

| Source Column | Export Column | Type |
|---|---|---|
| `cad.parent` | `attempt_id` | VARCHAR(140) |
| `cad.item_id` | `item_id` | VARCHAR(140) |
| `cad.correct` | `is_correct` | BOOL |
| `cad.time_spent` | `time_spent_sec` | INT |
| `cad.chosen_answer` | `chosen_answer` | INT |

**Why shared:** 30 attempts with 95 question-level detail rows. The only assessment data that provides objective learning measurement.

**Used by:** D1, D2, C1 — **3 reports**.

---

## 4. Gap Summary & Priorities

### Coverage Map

| Domain | Existing Reports | Potential Reports | Key Datasets |
|---|---|---|---|
| **Voucher/Sales** | 5 | 1 (E3) | `fact_voucher` |
| **Learning Behavior** | 0 | 4 (A1-A4) | `fact_interaction`, `fact_practice` |
| **Learning Progress** | 0 | 4 (B1-B4) | `fact_memory_state`, `dim_player` |
| **Content Quality** | 0 | 3 (C1-C3) | `dim_review_item`, `dim_content_hierarchy` |
| **Challenges** | 0 | 2 (D1-D2) | `fact_challenge` |
| **Monetization** | 0 (beyond vouchers) | 2 (E1-E2) | `fact_subscription` |
| **Ops/System** | 0 | 2 (F1-F2) | Archive Job, Task Run Log |

### The Three Biggest Gaps

| Gap | Impact | Proposed Reports |
|---|---|---|
| **No learning reports at all** | Cannot measure whether students are actually learning | B1 (Spaced Repetition), A4 (Lesson Funnel) |
| **No engagement measurement** | Cannot know how many students are active daily | A1 (DAU/MAU) |
| **No content analysis** | Cannot identify broken or poorly-designed questions | C1 (Item Difficulty) |

### Recommended Priority Order

**Phase 1 — Core metrics (answer: are students engaged? learning? is content working?)**

1. **A1: DAU/MAU** — the single most important product health metric
2. **B1: Spaced Repetition Effectiveness** — measures the core algorithm's output
3. **C1: Item Difficulty Analysis** — identifies content quality issues

**Phase 2 — Depth (answer: where are problems? how to improve?)**

4. **A4: Lesson Completion Funnel** — find where students drop off
5. **B2: Subject Completion Progress** — which subjects get finished vs abandoned
6. **B4: Learning Velocity** — how fast students progress

**Phase 3 — Business (answer: is the business growing?)**

7. **E1: Revenue Cohort Analysis** — revenue trends
8. **E2: Subscription Lifecycle** — subscription health
9. **E3: Voucher Funnel** — activation optimization

**Phase 4 — Advanced**

10. **D1: Challenge Performance** — assessment analytics
11. **B3: Student Retention** — cross-season retention
12. **A2: Session Analysis** — usage patterns
