# وثيقة عمل — سيرفر التحليل: بناء التقارير التحليلية

> **التاريخ:** 2026-03-13
> **الهدف:** بناء 15 تقرير تحليلي كـ Frappe Script Reports تقرأ من DuckDB
> **نطاق العمل:** سيرفر التحليل فقط (Frappe + DuckDB)
> **المتطلب:** سيرفر الإنتاج سيصدّر 18 مجموعة بيانات كملفات Parquet (انظر القسم 2)

---

## 1. فهم المطلوب

سيرفر الإنتاج سيُصدّر مجموعات بيانات جديدة كملفات Parquet. مهمتك هي:

1. **افحص الوضع الحالي** — قبل أي شيء:
   - نفّذ استعلام في DuckDB لعرض كل الجداول والـ views الموجودة
   - افحص الكود في `backend/ingestion/` لفهم الـ pipelines الموجودة
   - افحص هل في تقارير Frappe (Script Reports) موجودة على هذا السيرفر
   - اعرض لي النتائج قبل ما تبدأ

2. **ابنِ ingestion pipeline** لكل مجموعة بيانات جديدة بنفس أسلوب الموجود (raw → curated → mart)

3. **ابنِ 15 Frappe Script Report** بنفس أسلوب التقارير الخمسة الموجودة على سيرفر الإنتاج:
   - Batch Performance
   - Consignment Reconciliation
   - Sales by Library
   - Scholarship Gift Grants
   - Security Audit

   كل تقرير يحتاج: ملف Python + ملف JS + DocType في Frappe، مع filters و Report Summary.

---

## 2. المجموعات المتوقع وصولها من سيرفر الإنتاج

ستصل 18 مجموعة بيانات كملفات Parquet إلى `/data/analytics/incoming/`:

### مجموعات الأبعاد (7):

| dataset_key | الوصف | معنى السطر | صفوف تقريبية |
|---|---|---|---|
| `dim_player` | بيانات اللاعبين | لاعب مسجّل | 364 |
| `dim_content_hierarchy` | التسلسل الهرمي للمحتوى | درس واحد مع مادة/مسار/وحدة/موضوع | ~47 |
| `dim_review_item` | الأسئلة القابلة للمراجعة | سؤال واحد | 202 |
| `dim_season` | المواسم الدراسية | موسم واحد | 8 |
| `dim_academic_plan` | الخطط الأكاديمية | خطة واحدة (صف+تخصص+موسم) | 917 |
| `dim_lesson_stage` | مراحل الدروس | مرحلة واحدة في درس | ~222 |
| `fact_content_report` | بلاغات المحتوى | بلاغ واحد | 6 |

### مجموعات الحقائق (11):

| dataset_key | الوصف | معنى السطر | صفوف تقريبية |
|---|---|---|---|
| `fact_interaction` | أحداث التعلم | حدث تعليمي واحد | 10,906+ |
| `fact_memory_state` | حالة التذكّر (FSRS) | (طالب, سؤال, موسم) | 103 |
| `fact_practice` | سجل التدريب | (طالب, سؤال) مجمّع | 20 |
| `fact_subscription` | الاشتراكات + الدفعات | اشتراك واحد | ~73 |
| `fact_voucher` | القسائم | بطاقة قسيمة واحدة | 4,872 |
| `fact_challenge_attempt` | محاولات التحدي | محاولة واحدة | 30 |
| `fact_challenge_detail` | تفاصيل التحدي | إجابة سؤال واحد في محاولة | 95 |
| `fact_structure_progress` | تقدم إكمال المواد | (طالب, مادة) | 124 |
| `fact_player_wallet` | محفظة الطالب | لاعب واحد | 330 |
| `fact_live_challenge_event` | أحداث التحدي المباشر | حدث واحد | 2 |
| `fact_live_challenge_participation` | مشاركات التحدي المباشر | مشاركة واحدة | 1 |
| `fact_archive_job` | مهام الأرشفة | مهمة واحدة | 60 |
| `fact_task_run_log` | سجل المهام الخلفية | تشغيل مهمة واحد | 1,735 |
| `fact_build_queue` | طابور البناء | عملية بناء واحدة | 215 |

---

## 3. أعمدة كل مجموعة — مرجع سريع

### dim_player
| عمود | النوع | ملاحظة |
|---|---|---|
| `player_id` | VARCHAR(140) | PLAYER-XXXXX (المفتاح) |
| `display_name` | VARCHAR(140) | |
| `grade_id` | VARCHAR(140) | FK → Grade |
| `major_id` | VARCHAR(140) | FK → Major |
| `season_id` | VARCHAR(140) | الموسم الحالي |
| `gender` | VARCHAR(140) | Male/Female/NULL |
| `language` | VARCHAR(140) | ar/en |
| `registered_at` | DATETIME | تاريخ التسجيل |

### dim_content_hierarchy
| عمود | النوع |
|---|---|
| `lesson_id` | VARCHAR(140) (المفتاح) |
| `lesson_title` | VARCHAR(140) |
| `subject_id` | VARCHAR(140) |
| `subject_title` | VARCHAR(140) |
| `track_id` | VARCHAR(140) |
| `track_title` | VARCHAR(140) |
| `unit_id` | VARCHAR(140) |
| `unit_title` | VARCHAR(140) |
| `topic_id` | VARCHAR(140) |
| `topic_title` | VARCHAR(140) |
| `base_xp` | INT |
| `max_hearts` | INT |
| `is_reviewable` | BOOL |
| `bit_index` | INT |
| `stage_count` | INT |
| `stage_types` | TEXT |

### dim_review_item
| عمود | النوع |
|---|---|
| `item_id` | VARCHAR(140) — UUID (المفتاح) |
| `subject_id` | VARCHAR(140) |
| `topic_id` | VARCHAR(140) |
| `lesson_id` | VARCHAR(140) |
| `stage_id` | VARCHAR(140) |
| `stage_type` | VARCHAR(140) |
| `question_text` | TEXT |
| `correct_choice` | INT (1-4) |

### dim_season
| عمود | النوع |
|---|---|
| `season_id` | VARCHAR(140) (المفتاح) |
| `season_title` | VARCHAR(140) |
| `season_seq` | INT (فريد — ترتيب زمني) |
| `start_date` | DATE |
| `end_date` | DATE |
| `is_published` | BOOL |

### dim_academic_plan
| عمود | النوع |
|---|---|
| `plan_id` | VARCHAR(140) (المفتاح) |
| `plan_name` | VARCHAR(140) |
| `grade_id` | VARCHAR(140) |
| `grade_title` | VARCHAR(140) |
| `major_id` | VARCHAR(140) |
| `major_title` | VARCHAR(140) |
| `season_id` | VARCHAR(140) |
| `is_published` | BOOL |
| `total_subjects` | INT |
| `total_lessons` | INT |
| `subject_list` | TEXT |

### dim_lesson_stage
| عمود | النوع |
|---|---|
| `stage_id` | VARCHAR(140) (المفتاح) |
| `lesson_id` | VARCHAR(140) |
| `stage_type` | VARCHAR(140) |
| `is_skippable` | BOOL |
| `default_stage_time` | INT |
| `is_time_calculated` | BOOL |

### fact_interaction
| عمود | النوع | ملاحظة |
|---|---|---|
| `event_id` | VARCHAR(140) | المفتاح — LOG-XXXXX |
| `player_id` | VARCHAR(140) | FK → dim_player |
| `lesson_id` | VARCHAR(140) | FK → dim_content_hierarchy |
| `stage_id` | VARCHAR(140) | |
| `item_id` | VARCHAR(140) | FK → dim_review_item (nullable) |
| `event_type` | VARCHAR(140) | Started/Completed |
| `time_spent_sec` | INT | بالثواني |
| `errors_count` | INT | |
| `event_ts` | DATETIME(6) | |
| `client_metadata` | JSON | بيانات الجهاز |

### fact_memory_state
| عمود | النوع | ملاحظة |
|---|---|---|
| `ms_id` | BIGINT | المفتاح |
| `player_id` | VARCHAR(140) | FK → dim_player |
| `item_id` | VARCHAR(140) | UUID نصي (محوّل من BINARY) |
| `season_seq` | INT | مفتاح التقسيم |
| `subject_id` | VARCHAR(140) | |
| `lesson_id` | VARCHAR(140) | |
| `stability` | FLOAT64 | قوة الذاكرة (أيام) |
| `difficulty` | FLOAT64 | صعوبة السؤال (0-10) |
| `next_review` | DATE | موعد المراجعة القادم |
| `last_review` | DATETIME(6) | آخر مراجعة |
| `fsrs_state` | TINYINT | 0=New, 1=Learning, 2=Review, 3=Relearning |
| `fsrs_step` | TINYINT | |

### fact_practice
| عمود | النوع |
|---|---|
| `player_id` | VARCHAR(140) |
| `item_id` | VARCHAR(36) |
| `first_seen_at` | DATETIME |
| `last_seen_at` | DATETIME |
| `last_result` | VARCHAR (Correct/Incorrect) |
| `attempt_count` | INT |
| `correct_count` | INT |

### fact_subscription
| عمود | النوع |
|---|---|
| `player_id` | VARCHAR(140) |
| `access_key` | VARCHAR(140) |
| `is_active` | BOOL |
| `expires_at` | DATE |
| `subscribed_at` | DATETIME |
| `payment_method` | VARCHAR(140) |
| `amount_paid` | DECIMAL |
| `txn_status` | VARCHAR(140) |

### fact_voucher
| عمود | النوع |
|---|---|
| `serial_no` | VARCHAR(140) |
| `batch_id` | VARCHAR(140) |
| `batch_name` | VARCHAR(140) |
| `batch_purpose` | VARCHAR(140) |
| `face_value` | DECIMAL |
| `card_status` | VARCHAR(140) |
| `library` | VARCHAR(140) |
| `sale_model` | VARCHAR(140) |
| `redeemed_by` | VARCHAR(140) |
| `redeemed_at` | DATETIME |
| `allocation_date` | DATE |
| `allocated_to` | VARCHAR(140) |

### fact_structure_progress
| عمود | النوع |
|---|---|
| `player_id` | VARCHAR(140) |
| `subject_id` | VARCHAR(140) |
| `completion_pct` | DECIMAL |
| `passed_lessons_bitset` | TEXT |

### fact_player_wallet
| عمود | النوع |
|---|---|
| `player_id` | VARCHAR(140) |
| `total_xp` | INT |
| `total_lessons` | INT |
| `total_time_min` | INT |
| `current_streak` | INT |
| `daily_xp_json` | JSON |
| `last_sync_at` | DATETIME |

---

## 4. التقارير الـ 15 — تفاصيل كاملة

> **كل تقرير يُبنى كـ Frappe Script Report**
> **كل تقرير يقرأ من DuckDB عبر الـ views الحالية (`*_current`)**
> **اتبع نفس أسلوب التقارير الخمسة الموجودة على سيرفر الإنتاج**

---

### الفئة A: سلوك الطالب التعليمي

---

### تقرير A1: الطلاب النشطين (يومي / أسبوعي / شهري)

**الغرض:** أهم مقياس لصحة المنتج — كم طالب يستخدم النظام

**المصادر في DuckDB:** `fact_interaction_current`, `dim_player_current`

**المقاييس:**
- `dau` = عدد اللاعبين المختلفين في يوم معيّن
- `wau` = عدد اللاعبين المختلفين في آخر 7 أيام
- `mau` = عدد اللاعبين المختلفين في آخر 30 يوم
- `stickiness` = `dau / mau * 100`
- `new_users` = اللاعبين الذين `registered_at` = تاريخ التقرير
- `returning_users` = `dau - new_users`

**الأبعاد:** التاريخ، الصف، التخصص، الموسم، الجنس

**الفلاتر:**
- `from_date` (Date — مطلوب)
- `to_date` (Date — مطلوب)
- `grade` (Link — اختياري)
- `season` (Link — اختياري)

**ملخص التقرير (Report Summary):**
- إجمالي النشطين اليوم (أخضر)
- إجمالي النشطين هذا الشهر (أزرق)
- نسبة الالتصاق (رمادي)

**استعلام DuckDB:**
```sql
SELECT
    CAST(fi.event_ts AS DATE) AS report_date,
    dp.grade_id,
    dp.season_id,
    COUNT(DISTINCT fi.player_id) AS dau,
    COUNT(DISTINCT CASE
        WHEN CAST(dp.registered_at AS DATE) = CAST(fi.event_ts AS DATE)
        THEN fi.player_id END) AS new_users,
    COUNT(DISTINCT CASE
        WHEN CAST(dp.registered_at AS DATE) < CAST(fi.event_ts AS DATE)
        THEN fi.player_id END) AS returning_users
FROM fact_interaction_current fi
JOIN dim_player_current dp ON dp.player_id = fi.player_id
WHERE fi.event_ts BETWEEN :from_date AND :to_date
GROUP BY CAST(fi.event_ts AS DATE), dp.grade_id, dp.season_id
ORDER BY report_date;
```

---

### تقرير A2: تحليل الجلسات

**الغرض:** أنماط الاستخدام — متى يدرسون، كم يقضون، كم جلسة في اليوم

**المصادر:** `fact_interaction_current`, `dim_player_current`

**طريقة بناء الجلسات:** لا يوجد جدول جلسات. تُبنى من الأحداث — إذا مر أكثر من 30 دقيقة بين حدثين لنفس الطالب، تبدأ جلسة جديدة.

**المقاييس:**
- `session_duration_min` = مجموع `time_spent_sec / 60` لكل جلسة
- `events_per_session` = عدد الأحداث في الجلسة
- `sessions_per_day` = عدد الجلسات في اليوم
- `bounce_rate` = نسبة الجلسات بحدث واحد فقط
- `peak_hour` = الساعة الأكثر نشاطاً

**الفلاتر:** `from_date`, `to_date`, `grade`, `season`

**استعلام DuckDB:**
```sql
WITH ordered_events AS (
    SELECT
        player_id, event_ts, time_spent_sec, lesson_id,
        LAG(event_ts) OVER (PARTITION BY player_id ORDER BY event_ts) AS prev_ts
    FROM fact_interaction_current
    WHERE event_ts BETWEEN :from_date AND :to_date
),
session_starts AS (
    SELECT *,
        SUM(CASE
            WHEN prev_ts IS NULL
              OR EXTRACT(EPOCH FROM (event_ts - prev_ts)) / 60 > 30
            THEN 1 ELSE 0
        END) OVER (PARTITION BY player_id ORDER BY event_ts) AS session_id
    FROM ordered_events
)
SELECT
    player_id, session_id,
    CAST(MIN(event_ts) AS DATE) AS session_date,
    EXTRACT(HOUR FROM MIN(event_ts)) AS start_hour,
    COUNT(*) AS events_in_session,
    SUM(time_spent_sec) / 60.0 AS duration_min,
    COUNT(DISTINCT lesson_id) AS lessons_touched
FROM session_starts
GROUP BY player_id, session_id;
```

---

### تقرير A3: دقة تدريب الطالب

**الغرض:** أداء الطلاب في التدريب — أي أسئلة يجيبون صح وأيها يغلطون

**المصادر:** `fact_practice_current`, `dim_review_item_current`

**المقاييس:**
- `accuracy_rate` = `correct_count / attempt_count * 100`
- `avg_attempts` = متوسط المحاولات لكل سؤال
- `perfect_items` = أسئلة بدقة 100%
- `struggling_items` = أسئلة بدقة أقل من 50%

**الفلاتر:** `subject`, `topic`, `lesson`, `stage_type`

**استعلام DuckDB:**
```sql
SELECT
    ri.subject_id, ri.topic_id, ri.lesson_id, ri.stage_type,
    COUNT(DISTINCT fp.player_id) AS unique_players,
    COUNT(*) AS items_practiced,
    SUM(fp.attempt_count) AS total_attempts,
    SUM(fp.correct_count) AS total_correct,
    ROUND(SUM(fp.correct_count) * 100.0 / SUM(fp.attempt_count), 1) AS accuracy_pct,
    ROUND(AVG(fp.attempt_count), 1) AS avg_attempts_per_item,
    SUM(CASE WHEN fp.correct_count = fp.attempt_count THEN 1 ELSE 0 END) AS perfect_items,
    SUM(CASE WHEN fp.correct_count * 1.0 / fp.attempt_count < 0.5 THEN 1 ELSE 0 END) AS struggling_items
FROM fact_practice_current fp
JOIN dim_review_item_current ri ON ri.item_id = fp.item_id
GROUP BY ri.subject_id, ri.topic_id, ri.lesson_id, ri.stage_type
ORDER BY accuracy_pct ASC;
```

---

### تقرير A4: قمع إكمال الدروس

**الغرض:** أي دروس يكملها الطلاب وأيها يفشلون فيها

**المصادر:** `fact_interaction_current`, `dim_content_hierarchy_current`

**المقاييس:**
- `total_completions` = عدد مرات الإكمال
- `unique_players` = عدد الطلاب المختلفين
- `avg_time_sec` = متوسط الوقت
- `avg_errors` = متوسط الأخطاء
- `perfect_completions` = بدون أخطاء
- `failed_attempts` = `errors_count >= max_hearts` (فشل)

**الفلاتر:** `from_date`, `to_date`, `subject`, `topic`

**ملخص التقرير:** إجمالي الإكمالات (أخضر)، متوسط الأخطاء (برتقالي)، نسبة النجاح (أزرق)

**استعلام DuckDB:**
```sql
SELECT
    ch.lesson_id, ch.lesson_title,
    ch.subject_id, ch.subject_title, ch.topic_id,
    COUNT(DISTINCT fi.player_id) AS unique_players,
    COUNT(*) AS total_completions,
    ROUND(AVG(fi.time_spent_sec), 0) AS avg_time_sec,
    ROUND(AVG(fi.errors_count), 1) AS avg_errors,
    SUM(CASE WHEN fi.errors_count = 0 THEN 1 ELSE 0 END) AS perfect_completions,
    SUM(CASE WHEN fi.errors_count >= ch.max_hearts THEN 1 ELSE 0 END) AS failed_attempts
FROM fact_interaction_current fi
JOIN dim_content_hierarchy_current ch ON ch.lesson_id = fi.lesson_id
WHERE fi.event_type = 'Completed'
  AND fi.event_ts BETWEEN :from_date AND :to_date
GROUP BY ch.lesson_id, ch.lesson_title, ch.subject_id, ch.subject_title, ch.topic_id
ORDER BY avg_errors DESC;
```

---

### الفئة B: تقدم التعلم والاحتفاظ

---

### تقرير B1: فعالية التكرار المتباعد

**الغرض:** هل الطلاب يتذكرون ما تعلموه — قياس مخرجات خوارزمية FSRS

**المصادر:** `fact_memory_state_current`, `dim_season_current`, `dim_player_current`

**شرح أعمدة FSRS:**
- `stability`: أيام حتى تنخفض احتمالية التذكر لـ 10%. أعلى = ذاكرة أقوى
- `difficulty`: صعوبة السؤال (0-10). أعلى = أصعب
- `fsrs_state`: 0=جديد, 1=يتعلم, 2=مراجعة, 3=يعيد التعلم

**المقاييس:**
- `avg_stability` = متوسط قوة الذاكرة
- `avg_difficulty` = متوسط الصعوبة
- `overdue_items` = أسئلة `next_review <= اليوم`
- `mastered_items` = أسئلة `fsrs_state = 2 AND stability > 30`
- `mastery_pct` = نسبة الإتقان

**الفلاتر:** `season_seq` (مطلوب دائماً), `subject`, `grade`

**استعلام DuckDB:**
```sql
SELECT
    ms.subject_id, ms.season_seq, ds.season_title, dp.grade_id,
    COUNT(*) AS total_items,
    AVG(ms.stability) AS avg_stability,
    AVG(ms.difficulty) AS avg_difficulty,
    SUM(CASE WHEN ms.fsrs_state = 0 THEN 1 ELSE 0 END) AS new_items,
    SUM(CASE WHEN ms.fsrs_state = 1 THEN 1 ELSE 0 END) AS learning_items,
    SUM(CASE WHEN ms.fsrs_state = 2 THEN 1 ELSE 0 END) AS review_items,
    SUM(CASE WHEN ms.fsrs_state = 3 THEN 1 ELSE 0 END) AS relearning_items,
    SUM(CASE WHEN ms.next_review <= CURRENT_DATE THEN 1 ELSE 0 END) AS overdue_items,
    SUM(CASE WHEN ms.fsrs_state = 2 AND ms.stability > 30 THEN 1 ELSE 0 END) AS mastered_items,
    ROUND(SUM(CASE WHEN ms.fsrs_state = 2 AND ms.stability > 30 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) AS mastery_pct
FROM fact_memory_state_current ms
JOIN dim_season_current ds ON ds.season_seq = ms.season_seq
JOIN dim_player_current dp ON dp.player_id = ms.player_id
WHERE ms.season_seq = :target_season_seq
GROUP BY ms.subject_id, ms.season_seq, ds.season_title, dp.grade_id
ORDER BY avg_stability DESC;
```

---

### تقرير B2: تقدم إكمال المواد

**الغرض:** أي مواد يكملها الطلاب وأيها يتركونها

**المصادر:** `fact_structure_progress_current`, `dim_content_hierarchy_current`, `dim_player_current`

**المقاييس:**
- `avg_completion_pct` = متوسط نسبة الإكمال
- `students_at_100` = أكملوا 100%
- `completion_distribution` = 4 شرائح (0-25%, 25-50%, 50-75%, 75-100%)

**ملاحظة:** استثنِ المواد التي ليس لها دروس (`total_lessons = 0` — 57 من 65 مادة)

**الفلاتر:** `grade`, `season`, `subject`

**استعلام DuckDB:**
```sql
SELECT
    sp.subject_id,
    dp.grade_id, dp.season_id,
    COUNT(DISTINCT sp.player_id) AS active_students,
    ROUND(AVG(sp.completion_pct), 1) AS avg_completion_pct,
    SUM(CASE WHEN sp.completion_pct < 25 THEN 1 ELSE 0 END) AS bucket_0_25,
    SUM(CASE WHEN sp.completion_pct >= 25 AND sp.completion_pct < 50 THEN 1 ELSE 0 END) AS bucket_25_50,
    SUM(CASE WHEN sp.completion_pct >= 50 AND sp.completion_pct < 75 THEN 1 ELSE 0 END) AS bucket_50_75,
    SUM(CASE WHEN sp.completion_pct >= 75 THEN 1 ELSE 0 END) AS bucket_75_100,
    SUM(CASE WHEN sp.completion_pct >= 100 THEN 1 ELSE 0 END) AS completed_students
FROM fact_structure_progress_current sp
JOIN dim_player_current dp ON dp.player_id = sp.player_id
GROUP BY sp.subject_id, dp.grade_id, dp.season_id
ORDER BY avg_completion_pct ASC;
```

---

### تقرير B3: احتفاظ الطلاب حسب الموسم

**الغرض:** كم طالب نشط في موسم ورجع في الموسم التالي

**المصادر:** `fact_interaction_current`, `dim_season_current`

**المقاييس:**
- `active_in_season`, `returned_next_season`, `retention_rate`, `churn_rate`

**الفلاتر:** لا شيء — يعرض كل المواسم

**استعلام DuckDB:**
```sql
WITH season_activity AS (
    SELECT DISTINCT
        fi.player_id, ds.season_id, ds.season_seq
    FROM fact_interaction_current fi
    JOIN dim_season_current ds
        ON fi.event_ts >= ds.start_date
       AND fi.event_ts < ds.end_date + INTERVAL 1 DAY
    WHERE ds.is_published = true
)
SELECT
    curr.season_id, curr.season_seq,
    COUNT(DISTINCT curr.player_id) AS active_students,
    COUNT(DISTINCT nxt.player_id) AS returned_students,
    ROUND(COUNT(DISTINCT nxt.player_id) * 100.0
        / NULLIF(COUNT(DISTINCT curr.player_id), 0), 1) AS retention_pct
FROM season_activity curr
LEFT JOIN season_activity nxt
    ON nxt.player_id = curr.player_id
   AND nxt.season_seq = curr.season_seq + 1
GROUP BY curr.season_id, curr.season_seq
ORDER BY curr.season_seq;
```

---

### تقرير B4: سرعة التعلم

**الغرض:** سرعة تقدم الطلاب — XP يومياً، دروس أسبوعياً، وقت لكل درس

**المصادر:** `fact_player_wallet_current`, `dim_player_current`

**المقاييس:**
- `xp_per_day` = `total_xp / أيام منذ التسجيل`
- `lessons_per_week` = `total_lessons / (أيام / 7)`
- `avg_time_per_lesson` = `total_time_min / total_lessons`
- `streak_distribution` = توزيع السلاسل (0, 1-7, >7)

**الفلاتر:** `grade`, `season`

**استعلام DuckDB:**
```sql
SELECT
    dp.grade_id, dp.season_id,
    COUNT(*) AS total_players,
    ROUND(AVG(pw.total_xp), 0) AS avg_total_xp,
    ROUND(AVG(pw.total_lessons), 1) AS avg_lessons,
    ROUND(AVG(pw.total_time_min), 0) AS avg_time_min,
    ROUND(AVG(pw.current_streak), 1) AS avg_streak,
    ROUND(AVG(pw.total_xp / NULLIF(DATEDIFF('day', dp.registered_at, CURRENT_DATE), 0)), 1) AS avg_xp_per_day,
    ROUND(AVG(pw.total_time_min / NULLIF(pw.total_lessons, 0)), 1) AS avg_min_per_lesson,
    SUM(CASE WHEN pw.current_streak = 0 THEN 1 ELSE 0 END) AS streak_zero,
    SUM(CASE WHEN pw.current_streak BETWEEN 1 AND 7 THEN 1 ELSE 0 END) AS streak_1_7,
    SUM(CASE WHEN pw.current_streak > 7 THEN 1 ELSE 0 END) AS streak_gt7
FROM fact_player_wallet_current pw
JOIN dim_player_current dp ON dp.player_id = pw.player_id
GROUP BY dp.grade_id, dp.season_id
ORDER BY avg_xp_per_day DESC;
```

---

### الفئة C: جودة المحتوى

---

### تقرير C1: تحليل صعوبة الأسئلة

**الغرض:** أي أسئلة صعبة أو سهلة أو مكسورة — لتحسين المحتوى

**المصادر:** `dim_review_item_current`, `fact_practice_current`, `fact_memory_state_current`, `fact_challenge_detail_current`

**ثلاثة مصادر للصعوبة:**
1. من التدريب: `practice_accuracy = correct_count / attempt_count`
2. من FSRS: `fsrs_difficulty = AVG(difficulty)`
3. من التحديات: `challenge_accuracy = AVG(is_correct)`

**الفلاتر:** `subject`, `topic`, `lesson`, `stage_type`

**استعلام DuckDB:**
```sql
SELECT
    ri.item_id, ri.subject_id, ri.topic_id, ri.lesson_id, ri.stage_type,
    LEFT(ri.question_text, 80) AS question_preview,
    -- من التدريب
    SUM(fp.attempt_count) AS total_practice_attempts,
    ROUND(SUM(fp.correct_count) * 100.0 / NULLIF(SUM(fp.attempt_count), 0), 1) AS practice_accuracy_pct,
    COUNT(DISTINCT fp.player_id) AS players_practiced,
    -- من FSRS
    ms_agg.avg_fsrs_difficulty,
    ms_agg.avg_stability,
    -- من التحديات
    ch_agg.challenge_attempts,
    ch_agg.challenge_accuracy_pct
FROM dim_review_item_current ri
LEFT JOIN fact_practice_current fp ON fp.item_id = ri.item_id
LEFT JOIN (
    SELECT item_id,
        ROUND(AVG(difficulty), 3) AS avg_fsrs_difficulty,
        ROUND(AVG(stability), 1) AS avg_stability
    FROM fact_memory_state_current
    GROUP BY item_id
) ms_agg ON ms_agg.item_id = ri.item_id
LEFT JOIN (
    SELECT item_id,
        COUNT(*) AS challenge_attempts,
        ROUND(AVG(CAST(is_correct AS INT)) * 100, 1) AS challenge_accuracy_pct
    FROM fact_challenge_detail_current
    GROUP BY item_id
) ch_agg ON ch_agg.item_id = ri.item_id
GROUP BY ri.item_id, ri.subject_id, ri.topic_id, ri.lesson_id, ri.stage_type,
         ri.question_text, ms_agg.avg_fsrs_difficulty, ms_agg.avg_stability,
         ch_agg.challenge_attempts, ch_agg.challenge_accuracy_pct
ORDER BY practice_accuracy_pct ASC
LIMIT 50;
```

---

### تقرير C2: تتبع بلاغات المحتوى

**الغرض:** متابعة بلاغات الأخطاء والاقتراحات

**المصادر:** `fact_content_report_current`

**المقاييس:**
- `reports_by_type` = حسب النوع (Bug / Content Error / Suggestion / Other)
- `reports_per_lesson`
- `open_reports`
- `avg_resolution_days`

**الفلاتر:** `from_date`, `to_date`, `status`, `report_type`

---

### تقرير C3: فعالية أنواع المراحل

**الغرض:** أي نوع مرحلة يتعلم منه الطالب أفضل

**المصادر:** `fact_interaction_current`, `dim_lesson_stage_current`

**المقاييس:**
- `avg_time_by_stage_type`
- `error_rate_by_stage_type`
- `time_ratio` = الوقت الفعلي / الوقت الافتراضي

**الفلاتر:** `from_date`, `to_date`

**استعلام DuckDB:**
```sql
SELECT
    ls.stage_type, ls.default_stage_time, ls.is_skippable,
    COUNT(*) AS total_events,
    COUNT(DISTINCT fi.player_id) AS unique_players,
    ROUND(AVG(fi.time_spent_sec), 1) AS avg_time_sec,
    ROUND(AVG(fi.errors_count), 2) AS avg_errors,
    ROUND(AVG(fi.time_spent_sec) * 1.0 / NULLIF(ls.default_stage_time, 0), 2) AS time_ratio
FROM fact_interaction_current fi
JOIN dim_lesson_stage_current ls
    ON ls.stage_id = fi.stage_id AND ls.lesson_id = fi.lesson_id
WHERE fi.event_type = 'Completed'
  AND fi.event_ts BETWEEN :from_date AND :to_date
GROUP BY ls.stage_type, ls.default_stage_time, ls.is_skippable
ORDER BY avg_errors DESC;
```

---

### الفئة D: التحديات والمنافسة

---

### تقرير D1: لوحة أداء التحديات

**الغرض:** أداء الطلاب في التحديات — نسب النجاح، أصعب الأسئلة

**المصادر:** `fact_challenge_attempt_current`, `fact_challenge_detail_current`, `dim_review_item_current`

**المقاييس:**
- `avg_score_pct`, `pass_rate`, `avg_attempts_to_pass`, `hardest_questions`

**الفلاتر:** `topic`, `subject`, `season`

**استعلامات DuckDB:**
```sql
-- ملخص حسب الموضوع
SELECT
    ca.topic_id, ca.subject_id, ca.season_id,
    COUNT(DISTINCT ca.player_id) AS unique_players,
    COUNT(*) AS total_attempts,
    ROUND(AVG(ca.score_pct), 1) AS avg_score_pct,
    ROUND(SUM(CAST(ca.passed AS INT)) * 100.0 / COUNT(*), 1) AS pass_rate,
    ROUND(AVG(ca.time_spent_sec), 0) AS avg_time_sec,
    SUM(ca.xp_earned) AS total_xp
FROM fact_challenge_attempt_current ca
GROUP BY ca.topic_id, ca.subject_id, ca.season_id;

-- أصعب الأسئلة
SELECT
    cd.item_id, ri.question_text,
    COUNT(*) AS times_asked,
    SUM(CAST(cd.is_correct AS INT)) AS times_correct,
    ROUND(AVG(CAST(cd.is_correct AS INT)) * 100, 1) AS accuracy_pct
FROM fact_challenge_detail_current cd
JOIN dim_review_item_current ri ON ri.item_id = cd.item_id
GROUP BY cd.item_id, ri.question_text
ORDER BY accuracy_pct ASC;
```

---

### تقرير D2: تحليل الأحداث الحية

**الغرض:** تحليل أحداث التحدي المباشر

**المصادر:** `fact_live_challenge_event_current`, `fact_live_challenge_participation_current`

**المقاييس:**
- `participation_rate` = المشاركين / السعة
- `submission_rate` = المسلّمين / المشاركين
- `avg_score`

**ملاحظة:** حدثان فقط حالياً. التقرير يكبر مع الاستخدام.

---

### الفئة E: الاشتراكات والإيرادات

---

### تقرير E1: تحليل الإيرادات

**الغرض:** اتجاهات الإيرادات الشهرية ومتوسط الإيراد لكل مستخدم

**المصادر:** `fact_subscription_current`, `dim_player_current`

**المقاييس:**
- `revenue_total`, `arpu`, `monthly_revenue`, مقارنة طرق الدفع

**الفلاتر:** `from_date`, `to_date`, `grade`, `payment_method`

**استعلام DuckDB:**
```sql
SELECT
    DATE_TRUNC('month', fs.subscribed_at) AS month,
    dp.grade_id, fs.payment_method,
    COUNT(*) AS transactions,
    COUNT(DISTINCT fs.player_id) AS paying_players,
    SUM(fs.amount_paid) AS total_revenue,
    ROUND(SUM(fs.amount_paid) / NULLIF(COUNT(DISTINCT fs.player_id), 0), 2) AS arpu
FROM fact_subscription_current fs
JOIN dim_player_current dp ON dp.player_id = fs.player_id
WHERE fs.txn_status = 'Completed'
  AND fs.subscribed_at BETWEEN :from_date AND :to_date
GROUP BY DATE_TRUNC('month', fs.subscribed_at), dp.grade_id, fs.payment_method
ORDER BY month;
```

---

### تقرير E2: دورة حياة الاشتراكات

**الغرض:** حالة الاشتراكات — نشطة، منتهية، قريبة من الانتهاء

**المصادر:** `fact_subscription_current`, `dim_player_current`

**المقاييس:**
- `active_subscriptions`, `expired_subscriptions`, `expiring_soon` (خلال 30 يوم)
- `avg_subscription_duration`, `subject_popularity`

**ملاحظة:** `access_key` يكشف النوع — `SUB-SUBJ-XXX` = مادة، `TRK-Track-XXX` = مسار

**الفلاتر:** لا شيء — يعرض كل الاشتراكات

**استعلام DuckDB:**
```sql
SELECT
    fs.access_key,
    CASE
        WHEN fs.access_key LIKE 'SUB-SUBJ-%' THEN 'Subject'
        WHEN fs.access_key LIKE 'TRK-Track-%' THEN 'Track'
        ELSE 'Other'
    END AS subscription_type,
    COUNT(*) AS total_subscriptions,
    SUM(CAST(fs.is_active AS INT)) AS active_count,
    SUM(CASE WHEN fs.expires_at < CURRENT_DATE THEN 1 ELSE 0 END) AS expired_count,
    SUM(CASE WHEN fs.expires_at BETWEEN CURRENT_DATE AND CURRENT_DATE + INTERVAL 30 DAY THEN 1 ELSE 0 END) AS expiring_soon,
    ROUND(AVG(DATEDIFF('day', fs.subscribed_at, fs.expires_at)), 0) AS avg_duration_days
FROM fact_subscription_current fs
GROUP BY fs.access_key
ORDER BY total_subscriptions DESC;
```

---

### تقرير E3: قمع القسائم

**الغرض:** رحلة القسيمة من التوليد حتى الاستخدام — أين تضيع

**المصادر:** `fact_voucher_current`

**المقاييس:**
- القمع: مولّدة → مخصصة → مستخدمة = نسبة التفعيل
- `wastage_rate` = (ملغاة + منتهية) / الإجمالي
- `avg_days_to_redeem`
- `library_efficiency`

**الفلاتر:** `library`, `batch_purpose`, `sale_model`

**استعلام DuckDB:**
```sql
SELECT
    fv.library, fv.batch_purpose, fv.sale_model,
    COUNT(*) AS total_cards,
    SUM(CASE WHEN fv.card_status = 'Available' THEN 1 ELSE 0 END) AS available,
    SUM(CASE WHEN fv.card_status = 'Allocated' THEN 1 ELSE 0 END) AS allocated,
    SUM(CASE WHEN fv.card_status = 'Redeemed' THEN 1 ELSE 0 END) AS redeemed,
    SUM(CASE WHEN fv.card_status IN ('Void', 'Expired') THEN 1 ELSE 0 END) AS wasted,
    ROUND(SUM(CASE WHEN fv.card_status = 'Redeemed' THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) AS redemption_pct,
    ROUND(AVG(CASE WHEN fv.card_status = 'Redeemed'
        THEN DATEDIFF('day', fv.allocation_date, fv.redeemed_at) END), 1) AS avg_days_to_redeem
FROM fact_voucher_current fv
GROUP BY fv.library, fv.batch_purpose, fv.sale_model
ORDER BY redemption_pct ASC;
```

---

### الفئة F: صحة النظام

---

### تقرير F1: صحة خط الأرشفة

**الغرض:** متابعة مهام الأرشفة

**المصادر:** `fact_archive_job_current`

**المقاييس:**
- توزيع حسب الحالة (Pending, Completed, Purged)
- `avg_duration_sec`, `error_rate`, `total_rows_archived`

**الفلاتر:** `from_date`, `to_date`, `source_doctype`, `status`

---

### تقرير F2: صحة المهام الخلفية

**الغرض:** متابعة المهام المجدولة

**المصادر:** `fact_task_run_log_current`, `fact_build_queue_current`

**المقاييس:**
- `tasks_per_day`, `failure_rate`, `avg_duration`, `total_processed`

**الفلاتر:** `from_date`, `to_date`, `task_name`, `status`

---

## 5. ترتيب التنفيذ المقترح

### المرحلة 1 — البنية التحتية
1. فحص الوضع الحالي (جداول + pipelines + تقارير موجودة)
2. بناء ingestion pipeline لكل مجموعة بيانات جديدة (بنفس أسلوب الموجود)
3. بناء transform scripts (raw → curated → mart) لكل مجموعة
4. التحقق أن كل الـ `*_current` views تعمل

### المرحلة 2 — التقارير الأساسية
5. C1: تحليل صعوبة الأسئلة
6. A1: الطلاب النشطين
7. B1: فعالية التكرار المتباعد
8. A4: قمع إكمال الدروس

### المرحلة 3 — التقارير التكميلية
9. B2: تقدم إكمال المواد
10. B4: سرعة التعلم
11. A3: دقة التدريب
12. A2: تحليل الجلسات

### المرحلة 4 — التقارير المالية والتشغيلية
13. E1: تحليل الإيرادات
14. E2: دورة الاشتراكات
15. E3: قمع القسائم
16. D1: أداء التحديات
17. B3: الاحتفاظ بالطلاب
18. D2: الأحداث الحية
19. F1: صحة الأرشفة
20. F2: صحة المهام