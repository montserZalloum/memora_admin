---
id: content-architecture
title: "بنية المحتوى"
slug: content-architecture
audience: backend
owner: corex
status: current
last_updated: "2026-03-24"
last_verified_commit: "76ef96d"
related_code:
  - memora_admin/memora_admin/doctype/memora_subject/memora_subject.py
  - memora_admin/memora_admin/doctype/memora_lesson/memora_lesson.py
  - memora_admin/memora_admin/api/hierarchy.py
  - memora_admin/memora_admin/api/plan.py
  - memora_admin/memora_admin/api/build.py
  - memora_admin/events/build_trigger.py
  - memora_admin/events/review_item_sync.py
  - memora_admin/events/access_sync.py
  - memora_admin/tasks/build_worker.py
  - memora_admin/tasks/sync.py
related_doctypes:
  - Memora Subject
  - Memora Track
  - Memora Unit
  - Memora Topic
  - Memora Lesson
  - Memora Lesson Stage
  - Memora Lesson Stage Settings
  - Memora Academic Plan
  - Memora Plan Subject
  - Memora Plan Overrider
  - Memora Grade
  - Memora Major
  - Memora Grade Major
  - Memora Subject Applicability
  - Memora Review Item
  - Memora Build Queue
  - Memora Content Report
  - Memora Level Settings
  - Memora Level Title
related_endpoints:
  - "GET get_subject_hierarchy(subject_id)"
  - "GET get_plan_manifest(plan_id)"
  - "POST queue_manual_build(subject_id)"
  - "get_applicable_subjects (whitelisted search filter)"
  - "get_subjects_for_plan (whitelisted search filter)"
  - "get_grade_majors (whitelisted search filter)"
tags: [content, hierarchy, subject, track, unit, topic, lesson, stage, plan, build, review-item, bit-index, grade, major]
---

# بنية المحتوى

> النموذج الأساسي الذي يُبنى عليه كل شيء في ميمورا — من المراجعة اليومية إلى التحدي المباشر.

---

## ملخص سريع

### ما هي بنية المحتوى؟
هرمية من 5 مستويات (مادة → مسار → وحدة → توبيك → درس) مع نظام خطط يجمع المواد لصف/تخصص/فصل محدد، وخط أنابيب بناء تلقائي ينتج ملفات JSON للتطبيق.

### من يستخدمها؟
مشرفو المحتوى ينشئون ويديرون الهرمية عبر Frappe Desk. أربعة أسطح استهلاك تقرأ منها:
المراجعة اليومية، ساحة التدريب، مركز التحدي، والتحدي المباشر.

### ماذا يحصل تلقائياً؟
- عند حفظ أي محتوى: إبطال كاش الهرمية فوراً + بناء JSON مؤجل (debounce دقيقتين)
- عند حفظ درس: استخراج أسئلة المراجعة (Review Items) تلقائياً كل دقيقتين
- عند إنشاء درس: تعيين bit_index فريد تلقائياً (لا يُعاد استخدامه أبداً)

---

## الهرمية

```
المادة (Subject) ← SUBJ-.#####
 └── المسار (Track) ← Track-.#####
      └── الوحدة (Unit) ← UNT-.#####
           └── التوبيك (Topic) ← TPC-.#####
                └── الدرس (Lesson) ← LES-.#####
                     └── المرحلة (Stage) ← جدول فرعي
```

### الحقول المفتاحية لكل مستوى

| المستوى | is_published | is_linear | is_free | حقول خاصة |
|---------|:----------:|:---------:|:-------:|-----------|
| المادة | نعم | نعم | — | `language`, `last_bit_index`, `cdn_url`, `json_hash` |
| المسار | نعم | نعم | — | `is_sold_separately` |
| الوحدة | نعم | نعم | نعم | — |
| التوبيك | نعم | نعم | نعم | — |
| الدرس | نعم | — | — | `base_xp`, `max_hearts`, `is_reviewable`, `bit_index`, `content_hash` |
| المرحلة | — | — | — | `stage_type` (Link → Stage Settings), `is_skippable`, `config_json` |

### أنواع المراحل (Stage Settings)
إعدادات المراحل مخزنة كسجلات في `Memora Lesson Stage Settings` — كل نوع له اسم فريد (`stage_title`) وإعدادات JSON (`payload`).
الأنواع المعروفة:
- **INFORMATION** — مرحلة شرح
- **QUESTION** — مرحلة سؤال (MCQ)
- **MATCHING** — مرحلة مطابقة
- **FILL_BLANK** — مرحلة ملء فراغ

### إعدادات الحقول المفتاحية
- **is_published**: يتحكم بالظهور — المحتوى غير المنشور لا يظهر في الهرمية ولا في التطبيق
- **is_linear**: يتحكم بالتسلسل — إذا مفعّل، يجب إكمال العنصر السابق قبل فتح التالي
- **is_free**: يتحكم بالوصول المجاني — على مستوى الوحدة والتوبيك فقط

---

## نظام الخطط

```
الخطة الأكاديمية (Academic Plan) ← PLAN-.#####
 ├── الصف (Grade) ← GRD-.#####
 │    └── التخصص (Major) ← MJR-.##### (اختياري)
 ├── الفصل (Season) ← مرتبط
 └── مواد الخطة (Plan Subject) ← جدول فرعي
      ├── subject ← رابط للمادة
      ├── alias_title ← عنوان بديل خاص بالخطة
      ├── notes ← ملاحظات
      ├── is_premium ← هل المادة مدفوعة؟
      └── meta_data (JSON) ← يُملأ يدوياً: free_units, free_topics
```

### ربط المواد بالصفوف (Subject Applicability)
كل مادة تحمل جدول فرعي `Memora Subject Applicability` يحدد الصفوف والتخصصات المؤهلة. فلتر البحث `get_applicable_subjects` يستخدم هذا الجدول.

### تجاوزات الخطة (Plan Overrider)
`Memora Plan Overrider` (ترقيم: `OVR-.#####`) يسمح بتعديل سلوك عناصر محددة داخل خطة:
- **ref_doctype**: المادة أو المسار أو الوحدة
- **ref_name**: العنصر المحدد
- **action**: `Hide` (إخفاء من الخطة) أو `Set Free` (جعله مجاني)

---

## خط أنابيب البناء

```
تعديل محتوى / خطة
        │
        ▼
  on_content_updated / on_plan_updated
  ── إبطال كاش الهرمية فوراً (Redis DEL + Pubsub)
  ── فحص debounce (Redis SET NX EX 120s لكل خطة)
        │
        ├── debounce نشط ← تجاهل (بناء معلّق بالفعل)
        │
        └── debounce جديد ← إنشاء سجل Build Queue (Pending)
                                │
                                ▼
                    process_pending_builds (كل دقيقة)
                    ── الأقدم أولاً
                    ── Pending → Processing
                        │
                        ├── generate_plan_json()
                        ├── publish_to_cdn()
                        ├── حذف الملفات اليتيمة
                        ├── تطهير CDN
                        │
                        ├── نجاح → Completed + إشعار Frappe
                        └── فشل → إعادة المحاولة (حتى 3 مرات) أو Failed
```

### حالات Build Queue
- **Pending** → **Processing** → **Completed**
- **Processing** → **Pending** (إعادة محاولة، حتى 3 مرات)
- **Processing** → **Failed** (بعد 3 محاولات أو خطأ فادح)
- تنظيف يومي عند الساعة 04:00

### ماذا يحصل عند حذف محتوى؟
- حذف درس: حذف `lessons/{lesson_id}.json` من التخزين + CDN
- حذف مادة: حذف جميع سجلات `Plan Subject` المرتبطة + إزالة من Redis free_subjects + بناء الخطط المتأثرة
- حذف خطة: إلغاء البناءات المعلقة + حذف `plans/{plan_id}/` + مسح جميع مفاتيح Redis

---

## استخراج أسئلة المراجعة (Review Items)

```
حفظ درس (is_reviewable = 1)
        │
        ▼
  on_lesson_save ← hooks
  ── Redis SADD → dirty_review_items set
        │
        ▼
  sync_dirty_review_items (كل دقيقتين)
  ── Redis SPOP → استخراج الدروس القذرة
  ── لكل درس: استخراج MCQs من المراحل
  ── إنشاء / تحديث سجلات Review Item
        │
        ▼
  ┌─────────────────────────────────────────────────┐
  │         Memora Review Item                       │
  │  item_id (UUID) • subject • track • unit •       │
  │  topic • lesson • stage_id • stage_type •         │
  │  question_text • choice_1-4 • correct_choice •   │
  │  content_json                                     │
  └──┬──────────┬──────────┬──────────┬─────────────┘
     │          │          │          │
     ▼          ▼          ▼          ▼
  المراجعة   ساحة      مركز     التحدي
  اليومية   التدريب    التحدي    المباشر
```

### قواعد خاصة
- **is_reviewable = 0**: حذف جميع Review Items فوراً عند الحفظ
- **حذف درس**: إزالة من dirty set + حذف جميع Review Items
- **التحدي المباشر**: الأسئلة الخاطئة لا تدخل FSRS

---

## رحلة المشرف

### ما الذي ينشئه ويديره؟

| الإجراء | التفاصيل |
|---------|----------|
| إنشاء مادة | تحديد `subject_title`، `language`، `is_published`، `is_linear` + جدول `Applicability` |
| بناء الهرمية | إنشاء مسارات → وحدات → توبيكات → دروس بالترتيب المطلوب (idx) |
| تصميم الدروس | إضافة مراحل (Stages) داخل كل درس: شرح، سؤال، مطابقة، ملء فراغ |
| تحديد المحتوى المجاني | تفعيل `is_free` على الوحدة أو التوبيك |
| إنشاء خطة أكاديمية | ربط الصف + التخصص + الفصل + إضافة مواد مع `meta_data` يدوياً |
| تجاوزات الخطة | إنشاء `Plan Overrider` لإخفاء أو تحرير عناصر محددة |
| بناء يدوي | استدعاء `queue_manual_build(subject_id)` لإعادة بناء JSON |
| مراقبة البناء | مراجعة `Memora Build Queue` لحالة البناء والأخطاء |

### ما يجب التحقق منه
- `is_published` مفعّل على جميع المستويات (العنصر غير المنشور يُخفي كل فروعه)
- `bit_index` مُعيّن تلقائياً لكل درس (لا يجب تغييره يدوياً)
- Build Queue لا يحتوي على سجلات Failed متراكمة
- Review Items موجودة للدروس التي `is_reviewable = 1`

---

## سلوك النظام

### Bit Index (فهرس البت)
- يُعيّن تلقائياً عند إنشاء درس جديد (`before_insert`)
- تسلسلي لكل مادة (يبدأ من 0)
- لا يُعاد استخدامه أبداً حتى لو حُذف الدرس
- يستخدم قفل `SELECT ... FOR UPDATE` على سجل المادة لمنع التعارض
- `last_bit_index` على المادة يحفظ الرقم التالي المتاح
- يُستخدم في خوارزمية التكرار المتباعد (FSRS) لتتبع إتقان كل درس

### إبطال الكاش (Cache Invalidation)
نمط ثنائي المسار عند أي تغيير:
1. **حذف مباشر من Redis** — تأثير فوري
2. **نشر عبر Pubsub** — لإبطال الكاش في FastAPI sidecar

المفاتيح المتأثرة:
- `hierarchy_key(subject_id)` — هرمية المادة
- `catalog_key(plan_id)` — كتالوج الخطة
- `plan_manifest_key(plan_id)` — ملف manifest الخطة
- `plan_free_subjects_key(plan_id)` — المواد المجانية في الخطة
- `build_debounce_key(plan_id)` — حالة debounce

### انتشار المحتوى المجاني
- `is_free` يُحدد على مستوى الوحدة أو التوبيك
- عند تغيير `is_free`: يتم إطلاق `on_unit_free_changed` / `on_topic_free_changed` في `access_sync.py`
- في هرمية API: إذا كان أي توبيك في وحدة `is_free = 1`، الوحدة تُعامل كمجانية أيضاً
- `Plan Subject → meta_data` يحتوي `free_units` و `free_topics` (يُملأ يدوياً)

### مزامنة الخطط (Safety Net)
- كل 6 ساعات: `sync_all_plan_subjects_to_redis` يُعيد مزامنة المواد المجانية لجميع الخطط
- شبكة أمان لالتقاط أي حالة رفض Redis أو تحديث فائت

---

## الحالات الشائعة وحالات الحافة

| الحالة | السبب | ما يجب التحقق منه |
|--------|-------|-------------------|
| المادة لا تظهر في التطبيق | `is_published = 0` على المادة أو أحد مستوياتها | تحقق من `is_published` على كل مستوى في السلسلة |
| الدرس موجود لكن لا أسئلة مراجعة | `is_reviewable = 0` أو الدرس لم يُعالج بعد | تحقق من `is_reviewable` + حالة dirty set |
| البناء فشل بعد 3 محاولات | خطأ في التوليد أو الرفع | تحقق من `error_message` في Build Queue |
| محتوى محدّث لكن التطبيق يعرض النسخة القديمة | الكاش لم يُبطل | تحقق من مفاتيح Redis أو انتظر انتهاء debounce |
| bit_index = 0 لدروس متعددة | استيراد بيانات بدون `bit_index` | أعد تعيين `last_bit_index` على المادة وأعد حفظ الدروس |
| خطة بدون مواد ظاهرة | جميع مواد الخطة `is_premium = 1` بدون اشتراك | تحقق من `is_premium` في Plan Subject |
| Overrider لا يعمل | `ref_doctype` أو `ref_name` غير صحيح | تحقق من أن العنصر موجود وأن `action` صحيح |

---

## استكشاف الأخطاء

| إذا حدث هذا... | تحقق من... |
|----------------|------------|
| البناء عالق في Processing | قد يكون الـ worker معطّل — تحقق من Frappe scheduler وسجلات الأخطاء |
| Review Items غير محدّثة | تحقق من `sync_dirty_review_items` في scheduler + حالة Redis dirty set |
| الهرمية ترجع null | المادة غير موجودة أو `is_published = 0` |
| الكاش قديم بعد التعديل | استدعاء `queue_manual_build` أو انتظار 2 دقيقة (debounce) |
| Plan Subject يتيم بعد حذف المادة | الـ cascade delete يجب أن يحذفها تلقائياً — تحقق من سجلات الأخطاء |
| إشعار بناء لم يصل | تحقق من اتصال Redis pubsub + `build_complete` realtime event |

---

## التفاصيل التقنية

### أنواع المستندات (DocTypes)

**Memora Subject** — الترقيم: `SUBJ-.#####`
`subject_title` `language` `is_published` `is_linear` `last_bit_index` `total_tracks` `total_units` `total_topics` `total_lessons` `json_hash` `json_generated_at` `cdn_url`
جدول فرعي: `Memora Subject Applicability` (`grade`, `major`)

**Memora Track** — الترقيم: `Track-.#####`
`track_title` `subject` `is_sold_separately` `is_published` `is_linear` `total_units` `total_lessons`

**Memora Unit** — الترقيم: `UNT-.#####`
`unit_title` `track` `is_free` `is_published` `is_linear` `subject` (read-only) `total_topics` `total_lessons`

**Memora Topic** — الترقيم: `TPC-.#####`
`topic_title` `unit` `is_free` `is_linear` `is_published` `track` (read-only) `subject` (read-only) `total_lessons`

**Memora Lesson** — الترقيم: `LES-.#####`
`lesson_title` `topic` `base_xp` `max_hearts` `is_reviewable` `content_hash` `bit_index` `unit` `track` `subject` (read-only)
جدول فرعي: `Memora Lesson Stage` (`stage_title`, `stage_type`, `is_skippable`, `config_json`)

**Memora Academic Plan** — الترقيم: `PLAN-.#####`
`plan_name` `grade` `major` `season` `is_published` `total_subjects` `total_lessons` `json_version` `json_hash` `json_generated_at`
جدول فرعي: `Memora Plan Subject` (`subject`, `alias_title`, `notes`, `is_premium`, `meta_data`)

**Memora Plan Overrider** — الترقيم: `OVR-.#####`
`plan` `ref_doctype` (Subject/Track/Unit) `ref_name` `action` (Hide/Set Free)

**Memora Grade** — الترقيم: `GRD-.#####`
`grade_title` `sort_order`
جدول فرعي: `Memora Grade Major` (`major`)

**Memora Review Item** — الترقيم: بحسب `item_id` (UUID)
`item_id` `subject` `track` `unit` `topic` `lesson` `stage_id` `stage_type` `question_text` `choice_1` `choice_2` `choice_3` `choice_4` `correct_choice` `content_json`

**Memora Build Queue** — الترقيم: `BLD-.#####`
`target_type` `target_name` `trigger_reason` `triggered_by` `status` `started_at` `completed_at` `duration_sec` `files_generated` `error_message`

**Memora Content Report** — تقارير أخطاء المحتوى من اللاعبين
`player` `subject` `lesson` `screen_shot` `report_type` `description` `status`

**Memora Level Settings** — Singleton
`quadratic_coefficient` `linear_coefficient` `max_level`
جدول فرعي: `Memora Level Title` (`level_number`, `title_en`, `title_ar`, `icon`)

### واجهات API

| الواجهة | الوصف |
|---------|-------|
| `get_subject_hierarchy(subject_id)` | هرمية كاملة: مسارات → وحدات → توبيكات → دروس مع bit_index و XP و free flags |
| `get_plan_manifest(plan_id)` | بيانات الخطة (من CDN أو توليد فوري) |
| `queue_manual_build(subject_id)` | إطلاق بناء يدوي مع إبطال كاش فوري |

### المهام المجدولة

| الجدول | المهمة | الملف |
|--------|--------|------|
| كل دقيقة | `process_pending_builds` | `tasks/build_worker.py` |
| كل دقيقتين | `sync_dirty_review_items` | `tasks/sync.py` |
| يومياً 04:00 | `cleanup_build_queue` | `tasks/build_cleanup.py` |
| كل 6 ساعات | `sync_all_plan_subjects_to_redis` | `tasks/plan_sync.py` |

### الملفات المصدرية

- `memora_admin/memora_admin/doctype/memora_subject/memora_subject.py` — Subject DocType controller
- `memora_admin/memora_admin/doctype/memora_lesson/memora_lesson.py` — Lesson + bit_index assignment
- `memora_admin/memora_admin/api/hierarchy.py` — get_subject_hierarchy API
- `memora_admin/memora_admin/api/plan.py` — get_plan_manifest API
- `memora_admin/memora_admin/api/build.py` — queue_manual_build API
- `memora_admin/events/build_trigger.py` — Build triggers + cache invalidation + cascade cleanup
- `memora_admin/events/review_item_sync.py` — Review Item dirty-set pattern
- `memora_admin/events/access_sync.py` — Free content sync
- `memora_admin/events/dimension_sync.py` — Analytics dimension refresh
- `memora_admin/tasks/build_worker.py` — Build Queue processor
- `memora_admin/tasks/sync.py` — Review Item consumer
- `memora_admin/hooks.py` — doc_events (lines 156-295), scheduler_events (lines 300-385)

---

## الأماكن الأربعة — مقارنة

| | المراجعة اليومية | ساحة التدريب | مركز التحدي | التحدي المباشر |
|---|---|---|---|---|
| **من يقرر** | النظام | الطالب | تسلسل إجباري | الأدمن ينشئ حدث |
| **الأسئلة** | 10 / مادة + إكمال | اختيار حر: مسارات / وحدات / توبيكات | توبيك واحد بالمرة | يختارها الأدمن |
| **FSRS** | نعم | لا | لا | لا |
| **شرط الفتح** | الدروس المختومة | التوبيكات المختومة | ختم بالمسار + ختم التوبيك السابق | لا شرط (الأدمن يحدد) |
| **الأسئلة الخاطئة** | موجودة بال FSRS | تدخل FSRS | تدخل FSRS | تُسجل فقط |
| **النتيجة** | — | — | 50% لختم التوبيك | ترتيب + XP حسب الرتبة |

---

## مزامنة أبعاد التحليلات (Dimension Sync)

عند تغيير أي من العناصر التالية، يتم تحديث أبعاد التحليلات بشكل غير متزامن:
- **Lesson** → `dimension_sync.on_lesson_changed`
- **Academic Plan** → `dimension_sync.on_plan_changed`
- **Review Item** → `dimension_sync.on_review_item_changed`
- **Season** → `dimension_sync.on_season_changed`
- **Player Profile** → `dimension_sync.on_player_changed`

جميع المعالجات تستخدم `frappe.enqueue(deduplicate=True)` لمنع التكرار.

شبكة أمان: مهمة يومية `reconcile_dimensions` عند 04:15 تُعيد مطابقة جميع الأبعاد.

---

## الصفحات ذات الصلة

- TODO: المراجعة اليومية (Daily Reviews)
- TODO: ساحة التدريب (Practice Arena)
- TODO: مركز التحدي (Challenge Hub)
- TODO: التحدي المباشر (Live Challenge)
- [صلاحية الوصول للفعاليات](event-access)
