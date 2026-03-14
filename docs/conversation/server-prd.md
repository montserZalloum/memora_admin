# وثيقة عمل — سيرفر الإنتاج: تصدير مجموعات البيانات التحليلية

> **التاريخ:** 2026-03-13
> **الهدف:** تصدير مجموعات بيانات مشتركة كملفات Parquet لسيرفر التحليل
> **نطاق العمل:** سيرفر الإنتاج فقط (Frappe + MariaDB)
> **لا تعدّل أي شيء في التقارير أو DocTypes الموجودة**

---

## 1. فهم المطلوب

سيرفر التحليل يحتاج بيانات من سيرفر الإنتاج عشان يبني تقارير تحليلية. مهمتك هي:

1. افحص كود التصدير الحالي — كيف يُصدَّر الباركيه حالياً (الكرون جوب، مسار الملفات، شكل المانيفست)
2. ابنِ تصدير لكل مجموعة بيانات جديدة بنفس الأسلوب الموجود
3. تأكد أن كل ملف Parquet يُرسل مع manifest.json فيه SHA-256 checksum وعدد الصفوف

**قبل ما تبدأ أي شيء:**
- ابحث في الكود عن نظام التصدير الحالي (ابحث عن parquet, rsync, export, snapshot)
- اعرض لي: وين الكود، كيف يشتغل، وين يحط الملفات، شكل المانيفست
- ابنِ كل مجموعة جديدة بنفس الأسلوب بالضبط

---

## 2. المجموعات المطلوبة — الأساسية (11 مجموعة)

### مجموعات الأبعاد (Dimension Tables) — 5

---

### 2.1 dim_player

**المصدر:** `tabMemora Player Profile`
**معنى السطر:** لاعب واحد مسجّل
**عدد الصفوف الحالي:** 364

| عمود المصدر | عمود التصدير | النوع | ملاحظة |
|---|---|---|---|
| `name` | `player_id` | VARCHAR(140) | PLAYER-XXXXX |
| `display_name` | `display_name` | VARCHAR(140) | |
| `grade` | `grade_id` | VARCHAR(140) | FK → Grade |
| `major` | `major_id` | VARCHAR(140) | FK → Major |
| `season` | `season_id` | VARCHAR(140) | الموسم الحالي |
| `gender` | `gender` | VARCHAR(140) | Male/Female/NULL |
| `preferred_lang` | `language` | VARCHAR(140) | ar/en |
| `creation` | `registered_at` | DATETIME | تاريخ التسجيل |

**لا تُصدَّر:** `mobile`, `password` (بيانات حساسة)

**استعلام التصدير:**
```sql
SELECT
    name AS player_id,
    display_name,
    grade AS grade_id,
    major AS major_id,
    season AS season_id,
    gender,
    preferred_lang AS language,
    creation AS registered_at
FROM `tabMemora Player Profile`;
```

---

### 2.2 dim_content_hierarchy

**المصادر:** `tabMemora Subject`, `tabMemora Track`, `tabMemora Unit`, `tabMemora Topic`, `tabMemora Lesson`, `tabMemora Lesson Stage`
**معنى السطر:** درس واحد مع كامل التسلسل الهرمي (مادة > مسار > وحدة > موضوع > درس)

| عمود التصدير | النوع | المصدر |
|---|---|---|
| `lesson_id` | VARCHAR(140) | `tabMemora Lesson.name` |
| `lesson_title` | VARCHAR(140) | `tabMemora Lesson.lesson_title` |
| `subject_id` | VARCHAR(140) | `tabMemora Lesson.subject` |
| `subject_title` | VARCHAR(140) | `tabMemora Subject.subject_title` |
| `track_id` | VARCHAR(140) | `tabMemora Lesson.track` |
| `track_title` | VARCHAR(140) | `tabMemora Track.track_title` |
| `unit_id` | VARCHAR(140) | `tabMemora Lesson.unit` |
| `unit_title` | VARCHAR(140) | `tabMemora Unit.unit_title` |
| `topic_id` | VARCHAR(140) | `tabMemora Lesson.topic` |
| `topic_title` | VARCHAR(140) | `tabMemora Topic.topic_title` |
| `base_xp` | INT | `tabMemora Lesson.base_xp` |
| `max_hearts` | INT | `tabMemora Lesson.max_hearts` |
| `is_reviewable` | BOOL | `tabMemora Lesson.is_reviewable` |
| `bit_index` | INT | `tabMemora Lesson.bit_index` |
| `stage_count` | INT | عدد المراحل في الدرس |
| `stage_types` | TEXT | أنواع المراحل مفصولة بفاصلة |

**استعلام التصدير:**
```sql
SELECT
    l.name AS lesson_id, l.lesson_title,
    l.subject AS subject_id, sub.subject_title,
    l.track AS track_id, t.track_title,
    l.unit AS unit_id, u.unit_title,
    l.topic AS topic_id, tp.topic_title,
    l.base_xp, l.max_hearts, l.is_reviewable, l.bit_index,
    (SELECT COUNT(*) FROM `tabMemora Lesson Stage` ls WHERE ls.parent = l.name) AS stage_count,
    (SELECT GROUP_CONCAT(DISTINCT ls.stage_type) FROM `tabMemora Lesson Stage` ls WHERE ls.parent = l.name) AS stage_types
FROM `tabMemora Lesson` l
LEFT JOIN `tabMemora Subject` sub ON sub.name = l.subject
LEFT JOIN `tabMemora Track` t ON t.name = l.track
LEFT JOIN `tabMemora Unit` u ON u.name = l.unit
LEFT JOIN `tabMemora Topic` tp ON tp.name = l.topic
WHERE l.is_published = 1;
```

---

### 2.3 dim_review_item

**المصدر:** `tabMemora Review Item`
**معنى السطر:** سؤال واحد قابل للمراجعة
**عدد الصفوف:** 202

| عمود المصدر | عمود التصدير | النوع |
|---|---|---|
| `item_id` | `item_id` | VARCHAR(140) — UUID |
| `subject` | `subject_id` | VARCHAR(140) |
| `topic` | `topic_id` | VARCHAR(140) |
| `lesson` | `lesson_id` | VARCHAR(140) |
| `stage_id` | `stage_id` | VARCHAR(140) |
| `stage_type` | `stage_type` | VARCHAR(140) |
| `question_text` | `question_text` | TEXT |
| `correct_choice` | `correct_choice` | INT (1-4) |

**استعلام التصدير:**
```sql
SELECT
    item_id, subject AS subject_id, topic AS topic_id,
    lesson AS lesson_id, stage_id, stage_type,
    question_text, correct_choice
FROM `tabMemora Review Item`;
```

---

### 2.4 dim_season

**المصدر:** `tabMemora Season`
**معنى السطر:** موسم دراسي واحد
**عدد الصفوف:** 8

| عمود المصدر | عمود التصدير | النوع |
|---|---|---|
| `name` | `season_id` | VARCHAR(140) |
| `season_title` | `season_title` | VARCHAR(140) |
| `season_seq` | `season_seq` | INT |
| `start_date` | `start_date` | DATE |
| `end_date` | `end_date` | DATE |
| `is_published` | `is_published` | BOOL |

**استعلام التصدير:**
```sql
SELECT
    name AS season_id, season_title, season_seq,
    start_date, end_date, is_published
FROM `tabMemora Season`;
```

---

### 2.5 dim_academic_plan

**المصادر:** `tabMemora Academic Plan`, `tabMemora Plan Subject`, `tabMemora Grade`, `tabMemora Major`
**معنى السطر:** خطة أكاديمية واحدة (ربط صف + تخصص + موسم بمواد)
**عدد الصفوف:** 917

| عمود التصدير | النوع | المصدر |
|---|---|---|
| `plan_id` | VARCHAR(140) | `tabMemora Academic Plan.name` |
| `plan_name` | VARCHAR(140) | `tabMemora Academic Plan.plan_name` |
| `grade_id` | VARCHAR(140) | `tabMemora Academic Plan.grade` |
| `grade_title` | VARCHAR(140) | `tabMemora Grade.grade_title` |
| `major_id` | VARCHAR(140) | `tabMemora Academic Plan.major` |
| `major_title` | VARCHAR(140) | `tabMemora Major.major_title` |
| `season_id` | VARCHAR(140) | `tabMemora Academic Plan.season` |
| `is_published` | BOOL | `tabMemora Academic Plan.is_published` |
| `total_subjects` | INT | `tabMemora Academic Plan.total_subjects` |
| `total_lessons` | INT | `tabMemora Academic Plan.total_lessons` |
| `subject_list` | TEXT | قائمة المواد مفصولة بفاصلة |

**استعلام التصدير:**
```sql
SELECT
    ap.name AS plan_id, ap.plan_name,
    ap.grade AS grade_id, g.grade_title,
    ap.major AS major_id, m.major_title,
    ap.season AS season_id, ap.is_published,
    ap.total_subjects, ap.total_lessons,
    (SELECT GROUP_CONCAT(ps.subject) FROM `tabPlan Subject` ps WHERE ps.parent = ap.name) AS subject_list
FROM `tabMemora Academic Plan` ap
LEFT JOIN `tabMemora Grade` g ON g.name = ap.grade
LEFT JOIN `tabMemora Major` m ON m.name = ap.major;
```

---

### مجموعات الحقائق (Fact Tables) — 6

---

### 2.6 fact_interaction

**المصدر:** `tabMemora Interaction Log`
**معنى السطر:** حدث تعليمي واحد (طالب أكمل مرحلة في درس)
**عدد الصفوف:** 10,906 (ينمو يومياً — أكبر جدول حقائق)

| عمود المصدر | عمود التصدير | النوع | ملاحظة |
|---|---|---|---|
| `name` | `event_id` | VARCHAR(140) | LOG-XXXXX |
| `player` | `player_id` | VARCHAR(140) | FK → dim_player |
| `lesson` | `lesson_id` | VARCHAR(140) | FK → dim_content |
| `stage_id` | `stage_id` | VARCHAR(140) | |
| `item_id` | `item_id` | VARCHAR(140) | FK → dim_review_item (nullable) |
| `event_type` | `event_type` | VARCHAR(140) | Started/Completed |
| `time_spent` | `time_spent_sec` | INT | بالثواني |
| `errors_count` | `errors_count` | INT | |
| `timestamp` | `event_ts` | DATETIME(6) | |
| `client_metadata` | `client_metadata` | JSON | بيانات الجهاز |

**استعلام التصدير:**
```sql
SELECT
    name AS event_id, player AS player_id, lesson AS lesson_id,
    stage_id, item_id, event_type,
    time_spent AS time_spent_sec, errors_count,
    timestamp AS event_ts, client_metadata
FROM `tabMemora Interaction Log`
WHERE timestamp BETWEEN :from_date AND :to_date;
```

**ملاحظة:** هذا الجدول كبير وينمو. يُفضّل تصديره بفترات (مثلاً آخر 30 يوم أو حسب الموسم).

---

### 2.7 fact_memory_state

**المصدر:** `tabMemora Memory State`
**معنى السطر:** حالة تذكّر طالب لسؤال معيّن في موسم معيّن
**عدد الصفوف:** 103

| عمود المصدر | عمود التصدير | النوع | ملاحظة |
|---|---|---|---|
| `name` | `ms_id` | BIGINT | |
| `player` | `player_id` | VARCHAR(140) | FK → dim_player |
| `item_id` | `item_id` | BINARY(16) | **⚠️ يجب تحويله** |
| `season_seq` | `season_seq` | INT | مفتاح التقسيم |
| `subject` | `subject_id` | VARCHAR(140) | |
| `lesson` | `lesson_id` | VARCHAR(140) | |
| `stability` | `stability` | DECIMAL → FLOAT64 | **⚠️ يجب تحويله** |
| `difficulty` | `difficulty` | DECIMAL → FLOAT64 | **⚠️ يجب تحويله** |
| `next_review` | `next_review` | DATE | |
| `last_review` | `last_review` | DATETIME(6) | |
| `state` | `fsrs_state` | TINYINT | 0=New, 1=Learning, 2=Review, 3=Relearning |
| `step` | `fsrs_step` | TINYINT | |

**⚠️ تحذيرات تصدير حرجة:**
1. `item_id` هو `BINARY(16)` — يجب تحويله بـ `HEX()` أو `BIN_TO_UUID()` ليصبح UUID نصي في الباركيه
2. `stability` و `difficulty` هما `DECIMAL(21,9)` — يرجعان ككائنات Decimal في Python. يجب تحويلهم لـ float64
3. الجدول مقسّم بـ RANGE PARTITION حسب `season_seq` — **صدّر موسم بموسم**

**استعلام التصدير (لكل موسم):**
```sql
SELECT
    name AS ms_id, player AS player_id,
    BIN_TO_UUID(item_id) AS item_id,
    season_seq, subject AS subject_id, lesson AS lesson_id,
    CAST(stability AS DOUBLE) AS stability,
    CAST(difficulty AS DOUBLE) AS difficulty,
    next_review, last_review,
    state AS fsrs_state, step AS fsrs_step
FROM `tabMemora Memory State`
WHERE season_seq = :target_season_seq;
```

---

### 2.8 fact_practice

**المصدر:** `tabMemora Practice Log`
**معنى السطر:** ملخص تدريب طالب على سؤال معيّن (مجمّع)
**عدد الصفوف:** 20

| عمود المصدر | عمود التصدير | النوع |
|---|---|---|
| `player_id` | `player_id` | VARCHAR(140) |
| `item_id` | `item_id` | VARCHAR(36) |
| `first_seen_at` | `first_seen_at` | DATETIME |
| `last_seen_at` | `last_seen_at` | DATETIME |
| `last_result` | `last_result` | ENUM (Correct/Incorrect) |
| `attempt_count` | `attempt_count` | INT UNSIGNED |
| `correct_count` | `correct_count` | INT UNSIGNED |

**ملاحظة:** جدول مخصص (ليس Frappe عادي) — مفتاح مركّب `(player_id, item_id)`. لا يحتوي على عمود `name`.

**استعلام التصدير:**
```sql
SELECT
    player_id, item_id,
    first_seen_at, last_seen_at, last_result,
    attempt_count, correct_count
FROM `tabMemora Practice Log`;
```

---

### 2.9 fact_subscription

**المصادر:** `tabMemora Player Subscription` + `tabMemora Subscription Transaction`
**معنى السطر:** اشتراك واحد مع تفاصيل الدفع

| عمود التصدير | النوع | المصدر |
|---|---|---|
| `player_id` | VARCHAR(140) | `ps.player` |
| `access_key` | VARCHAR(140) | `ps.access_key` |
| `is_active` | BOOL | `ps.is_active` |
| `expires_at` | DATE | `ps.expires_at` |
| `subscribed_at` | DATETIME | `ps.creation` |
| `payment_method` | VARCHAR(140) | `st.payment_method` |
| `amount_paid` | DECIMAL | `st.amount_paid` |
| `txn_status` | VARCHAR(140) | `st.status` |

**ملاحظة:** `access_key` يكشف نوع الاشتراك — `SUB-SUBJ-XXX` = مادة، `TRK-Track-XXX` = مسار

**استعلام التصدير:**
```sql
SELECT
    ps.player AS player_id, ps.access_key,
    ps.is_active, ps.expires_at,
    ps.creation AS subscribed_at,
    st.payment_method, st.amount_paid, st.status AS txn_status
FROM `tabMemora Player Subscription` ps
LEFT JOIN `tabMemora Subscription Transaction` st
    ON st.player = ps.player AND st.related_grant = ps.name;
```

---

### 2.10 fact_voucher

**المصادر:** `tabMemora Voucher Card` + `tabMemora Voucher Batch` + `tabMemora Voucher Allocation`
**معنى السطر:** بطاقة قسيمة واحدة مع تفاصيل الدفعة والتخصيص
**عدد الصفوف:** 4,872

| عمود التصدير | النوع | المصدر |
|---|---|---|
| `serial_no` | VARCHAR(140) | `vc.serial_no` |
| `batch_id` | VARCHAR(140) | `vc.batch` |
| `batch_name` | VARCHAR(140) | `vb.batch_name` |
| `batch_purpose` | VARCHAR(140) | `vb.batch_purpose` |
| `face_value` | DECIMAL | `vb.face_value` |
| `card_status` | VARCHAR(140) | `vc.status` |
| `library` | VARCHAR(140) | `vc.library` |
| `sale_model` | VARCHAR(140) | `vc.sale_model` |
| `redeemed_by` | VARCHAR(140) | `vc.redeemed_by` |
| `redeemed_at` | DATETIME | `vc.redeemed_at` |
| `allocation_date` | DATE | `va.allocation_date` |
| `allocated_to` | VARCHAR(140) | `va.customer` |

**استعلام التصدير:**
```sql
SELECT
    vc.serial_no, vc.batch AS batch_id,
    vb.batch_name, vb.batch_purpose, vb.face_value,
    vc.status AS card_status, vc.library, vc.sale_model,
    vc.redeemed_by, vc.redeemed_at,
    va.allocation_date, va.customer AS allocated_to
FROM `tabMemora Voucher Card` vc
JOIN `tabMemora Voucher Batch` vb ON vb.name = vc.batch
LEFT JOIN `tabMemora Voucher Allocation` va ON va.name = vc.allocation;
```

---

### 2.11 fact_challenge

**المصادر:** `tabMemora Challenge Attempt` + `tabMemora Challenge Attempt Detail`
**معنى السطر:** محاولة تحدي واحدة
**عدد الصفوف:** 30 محاولة + 95 تفصيل

**ملف 1 — على مستوى المحاولة (fact_challenge_attempt):**

| عمود التصدير | النوع | المصدر |
|---|---|---|
| `attempt_id` | VARCHAR(140) | `ca.name` |
| `player_id` | VARCHAR(140) | `ca.player` |
| `topic_id` | VARCHAR(140) | `ca.topic` |
| `subject_id` | VARCHAR(140) | `ca.subject` |
| `season_id` | VARCHAR(140) | `ca.season` |
| `attempt_number` | INT | `ca.attempt_number` |
| `total_questions` | INT | `ca.total_questions` |
| `correct_count` | INT | `ca.correct_count` |
| `score_pct` | DECIMAL | `ca.score_pct` |
| `passed` | BOOL | `ca.passed` |
| `time_spent_sec` | INT | `ca.time_spent` |
| `xp_earned` | INT | `ca.xp_earned` |
| `submitted_at` | DATETIME | `ca.submitted_at` |

**ملف 2 — على مستوى السؤال (fact_challenge_detail):**

| عمود التصدير | النوع | المصدر |
|---|---|---|
| `attempt_id` | VARCHAR(140) | `cad.parent` |
| `item_id` | VARCHAR(140) | `cad.item_id` |
| `is_correct` | BOOL | `cad.correct` |
| `time_spent_sec` | INT | `cad.time_spent` |
| `chosen_answer` | INT | `cad.chosen_answer` |

**استعلامات التصدير:**
```sql
-- المحاولات
SELECT
    name AS attempt_id, player AS player_id,
    topic AS topic_id, subject AS subject_id, season AS season_id,
    attempt_number, total_questions, correct_count,
    score_pct, passed, time_spent AS time_spent_sec,
    xp_earned, submitted_at
FROM `tabMemora Challenge Attempt`;

-- التفاصيل
SELECT
    parent AS attempt_id, item_id,
    correct AS is_correct, time_spent AS time_spent_sec, chosen_answer
FROM `tabMemora Challenge Attempt Detail`;
```

---

## 3. المجموعات الإضافية المطلوبة

هذه مجموعات لم تكن في القائمة الأصلية لكن بعض التقارير تحتاجها:

---

### 3.1 fact_structure_progress

**المصدر:** `tabMemora Structure Progress`
**معنى السطر:** تقدم طالب في مادة معيّنة
**عدد الصفوف:** 124
**مطلوبة لتقرير:** B2 (تقدم إكمال المواد)

| عمود المصدر | عمود التصدير | النوع |
|---|---|---|
| `player` | `player_id` | VARCHAR(140) |
| `subject` | `subject_id` | VARCHAR(140) |
| `completion_percentage` | `completion_pct` | DECIMAL |
| `passed_lessons_bitset` | `passed_lessons_bitset` | LONGTEXT |

```sql
SELECT
    player AS player_id, subject AS subject_id,
    completion_percentage AS completion_pct, passed_lessons_bitset
FROM `tabMemora Structure Progress`;
```

---

### 3.2 fact_player_wallet

**المصدر:** `tabMemora Player Wallet`
**معنى السطر:** ملخص نشاط طالب (XP, دروس, وقت, سلسلة متتالية)
**عدد الصفوف:** 330
**مطلوبة لتقرير:** B4 (سرعة التعلم)

| عمود المصدر | عمود التصدير | النوع |
|---|---|---|
| `player` | `player_id` | VARCHAR(140) |
| `total_xp` | `total_xp` | INT |
| `total_lessons` | `total_lessons` | INT |
| `total_time_min` | `total_time_min` | INT |
| `current_streak` | `current_streak` | INT |
| `daily_xp_json` | `daily_xp_json` | JSON |
| `last_sync_at` | `last_sync_at` | DATETIME |

```sql
SELECT
    player AS player_id, total_xp, total_lessons,
    total_time_min, current_streak, daily_xp_json, last_sync_at
FROM `tabMemora Player Wallet`;
```

---

### 3.3 dim_lesson_stage

**المصادر:** `tabMemora Lesson Stage` + `tabMemora Lesson Stage Settings`
**معنى السطر:** مرحلة واحدة في درس
**مطلوبة لتقرير:** C3 (فعالية أنواع المراحل)

| عمود التصدير | النوع | المصدر |
|---|---|---|
| `stage_id` | VARCHAR(140) | `ls.stage_id` |
| `lesson_id` | VARCHAR(140) | `ls.parent` |
| `stage_type` | VARCHAR(140) | `ls.stage_type` |
| `is_skippable` | BOOL | `ls.is_skippable` |
| `default_stage_time` | INT | `lss.default_stage_time` |
| `is_time_calculated` | BOOL | `lss.is_time_calculated` |

```sql
SELECT
    ls.stage_id, ls.parent AS lesson_id,
    ls.stage_type, ls.is_skippable,
    lss.default_stage_time, lss.is_time_calculated
FROM `tabMemora Lesson Stage` ls
LEFT JOIN `tabMemora Lesson Stage Settings` lss
    ON lss.stage_title = ls.stage_type;
```

---

### 3.4 fact_content_report

**المصدر:** `tabMemora Content Report`
**معنى السطر:** بلاغ واحد من طالب عن محتوى
**عدد الصفوف:** 6
**مطلوبة لتقرير:** C2 (بلاغات المحتوى)

| عمود المصدر | عمود التصدير | النوع |
|---|---|---|
| `player` | `player_id` | VARCHAR(140) |
| `subject` | `subject_id` | VARCHAR(140) |
| `lesson` | `lesson_id` | VARCHAR(140) |
| `report_type` | `report_type` | VARCHAR(140) |
| `description` | `description` | TEXT |
| `status` | `status` | VARCHAR(140) |
| `creation` | `created_at` | DATETIME |
| `modified` | `resolved_at` | DATETIME |

```sql
SELECT
    player AS player_id, subject AS subject_id,
    lesson AS lesson_id, report_type, description,
    status, creation AS created_at, modified AS resolved_at
FROM `tabMemora Content Report`;
```

---

### 3.5 fact_live_challenge

**المصادر:** `tabMemora Live Challenge Event` + `tabMemora Live Challenge Participation` + `tabMemora Live Challenge Question`
**معنى السطر:** حدث تحدي مباشر واحد
**عدد الصفوف:** حدثان فقط
**مطلوبة لتقرير:** D2 (تحليل الأحداث الحية)

**ملف 1 — الأحداث:**

| عمود التصدير | النوع | المصدر |
|---|---|---|
| `event_id` | VARCHAR(140) | `lce.name` |
| `event_name` | VARCHAR(140) | `lce.event_name` |
| `status` | VARCHAR(140) | `lce.status` |
| `scheduled_start` | DATETIME | `lce.scheduled_start` |
| `exam_duration` | INT | `lce.exam_duration` |
| `capacity` | INT | `lce.capacity` |
| `participant_count` | INT | `lce.participant_count` |
| `submitted_count` | INT | `lce.submitted_count` |
| `is_paid` | BOOL | `lce.is_paid` |

**ملف 2 — المشاركات:**

| عمود التصدير | النوع | المصدر |
|---|---|---|
| `event_id` | VARCHAR(140) | `lcp.event` |
| `player_id` | VARCHAR(140) | `lcp.player` |
| `joined_at` | DATETIME | `lcp.joined_at` |
| `submitted_at` | DATETIME | `lcp.submitted_at` |
| `score` | INT | `lcp.score` |
| `rank` | INT | `lcp.rank` |
| `xp_awarded` | INT | `lcp.xp_awarded` |

---

### 3.6 fact_archive_job

**المصدر:** `tabMemora Archive Job`
**معنى السطر:** مهمة أرشفة واحدة
**عدد الصفوف:** 60
**مطلوبة لتقرير:** F1 (صحة خط الأرشفة)

| عمود التصدير | النوع | المصدر |
|---|---|---|
| `job_id` | VARCHAR(140) | `name` |
| `source_doctype` | VARCHAR(140) | `source_doctype` |
| `status` | VARCHAR(140) | `status` |
| `archive_scope` | VARCHAR(140) | `archive_scope` |
| `started_at` | DATETIME | `started_at` |
| `completed_at` | DATETIME | `completed_at` |
| `duration_seconds` | INT | `duration_seconds` |
| `row_count` | INT | `row_count` |
| `file_size_bytes` | BIGINT | `file_size_bytes` |
| `retry_count` | INT | `retry_count` |
| `error_log` | TEXT | `error_log` |

---

### 3.7 fact_task_run

**المصادر:** `tabMemora Task Run Log` + `tabMemora Build Queue`
**مطلوبة لتقرير:** F2 (صحة المهام الخلفية)

**ملف 1 — سجل المهام (1,735 صف):**

| عمود التصدير | النوع | المصدر |
|---|---|---|
| `task_name` | VARCHAR(140) | `task_name` |
| `run_date` | DATE | `run_date` |
| `started_at` | DATETIME | `started_at` |
| `completed_at` | DATETIME | `completed_at` |
| `duration_sec` | INT | `duration_sec` |
| `status` | VARCHAR(140) | `status` |
| `triggered_by` | VARCHAR(140) | `triggered_by` |
| `processed_count` | INT | `processed_count` |
| `failed_count` | INT | `failed_count` |
| `error_message` | TEXT | `error_message` |

**ملف 2 — طابور البناء (215 صف):**

| عمود التصدير | النوع | المصدر |
|---|---|---|
| `target_type` | VARCHAR(140) | `target_type` |
| `target_name` | VARCHAR(140) | `target_name` |
| `status` | VARCHAR(140) | `status` |
| `started_at` | DATETIME | `started_at` |
| `completed_at` | DATETIME | `completed_at` |
| `duration_sec` | INT | `duration_sec` |
| `files_generated` | INT | `files_generated` |
| `trigger_reason` | VARCHAR(140) | `trigger_reason` |

---

## 4. ملخص — قائمة كل الملفات المطلوب تصديرها

| # | اسم المجموعة | dataset_key | عدد الصفوف | ملفات |
|---|---|---|---|---|
| 1 | dim_player | `dim_player` | 364 | `dim_player.parquet` |
| 2 | dim_content_hierarchy | `dim_content_hierarchy` | ~47 | `dim_content_hierarchy.parquet` |
| 3 | dim_review_item | `dim_review_item` | 202 | `dim_review_item.parquet` |
| 4 | dim_season | `dim_season` | 8 | `dim_season.parquet` |
| 5 | dim_academic_plan | `dim_academic_plan` | 917 | `dim_academic_plan.parquet` |
| 6 | fact_interaction | `fact_interaction` | 10,906+ | `fact_interaction.parquet` |
| 7 | fact_memory_state | `fact_memory_state` | 103 | `fact_memory_state.parquet` (موسم بموسم) |
| 8 | fact_practice | `fact_practice` | 20 | `fact_practice.parquet` |
| 9 | fact_subscription | `fact_subscription` | ~73 | `fact_subscription.parquet` |
| 10 | fact_voucher | `fact_voucher` | 4,872 | `fact_voucher.parquet` |
| 11 | fact_challenge | `fact_challenge` | 30+95 | `fact_challenge_attempt.parquet` + `fact_challenge_detail.parquet` |
| 12 | fact_structure_progress | `fact_structure_progress` | 124 | `fact_structure_progress.parquet` |
| 13 | fact_player_wallet | `fact_player_wallet` | 330 | `fact_player_wallet.parquet` |
| 14 | dim_lesson_stage | `dim_lesson_stage` | ~222 | `dim_lesson_stage.parquet` |
| 15 | fact_content_report | `fact_content_report` | 6 | `fact_content_report.parquet` |
| 16 | fact_live_challenge | `fact_live_challenge` | 2+1 | `fact_live_challenge_event.parquet` + `fact_live_challenge_participation.parquet` |
| 17 | fact_archive_job | `fact_archive_job` | 60 | `fact_archive_job.parquet` |
| 18 | fact_task_run | `fact_task_run` | 1,735+215 | `fact_task_run_log.parquet` + `fact_build_queue.parquet` |

**المجموع:** 18 مجموعة بيانات تُنتج ~22 ملف Parquet

---

## 5. تعليمات التنفيذ

1. **أولاً:** افحص كود التصدير الحالي — اعرض لي الملفات والأسلوب المستخدم قبل ما تبدأ
2. **ثانياً:** ابنِ كل مجموعة جديدة بنفس الأسلوب بالضبط (نفس بنية المجلدات، نفس شكل المانيفست، نفس طريقة الإرسال)
3. **ثالثاً:** تأكد من التحويلات الحرجة:
   - `item_id` في Memory State: `BIN_TO_UUID()` قبل التصدير
   - `stability` و `difficulty`: تحويل من Decimal لـ float64
   - Memory State: تصدير موسم بموسم
4. **رابعاً:** أضف كل المجموعات الجديدة للكرون جوب الموجود
5. **خامساً:** اختبر أن كل ملف يُصدَّر بشكل صحيح مع manifest.json صالح