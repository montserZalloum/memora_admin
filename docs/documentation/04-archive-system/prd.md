# تصميم نظام الأرشفة — Memora Archive System

---

## 1. الهدف العام

بناء نظام أرشفة منخفض التكلفة لمنتج مبني على
**Frappe / MariaDB**
يبدأ بجدول
`Practice Log`
ويتوسع لاحقاً ليشمل جداول أخرى.

الأرشفة هدفها تاريخي وتقاريري وليست تحليلات لحظية.

---

## 2. المبادئ الأساسية

- البيانات النشطة تبقى بقاعدة بيانات الإنتاج
- عند انتهاء الموسم، بياناته تُأرشف وتُصدّر
- الملفات المُأرشفة تُخزّن محلياً على نفس السيرفر مؤقتاً
- لاحقاً تُنقل لسيرفر تحليلات منفصل
- بعد التأكد من نجاح النقل الكامل، تُحذف النسخة المحلية

---

## 3. المعمارية العامة

### الطبقتان الأساسيتان

**طبقة التحكم** —
`Control Plane`
تعيش داخل
Frappe
ومسؤولة عن:
- تعريف الـ
  `DocType`
  الخاص بالأرشفة
- إنشاء سجلات الأرشفة عند تحقق الشروط
- عرض الحالة للمشرف
- توفير كبسة إعادة المحاولة للمهام الفاشلة

**طبقة التنفيذ** —
`Execution Plane`
سكربت
Python
مستقل بالكامل عن
Frappe runtime
ومسؤول عن:
- سحب المهام المعلقة من قاعدة البيانات مباشرة
- تصدير البيانات إلى ملفات
  `Parquet`
- بناء لقطات الأبعاد
  `dimension snapshots`
- بناء ملف
  `manifest.json`
- تحديث حالة السجل بقاعدة البيانات

### لماذا سكربت مستقل وليس Frappe background job؟

- عزل واضح عن
  Frappe
- لا يزاحم قائمة المهام الخاصة بالنظام
  `worker queue`
- أسهل بالتوسيع لاحقاً
- أسهل بإضافة إعادة المحاولات والسجلات والمراقبة
- يجعل نظام الأرشفة وحدة فرعية مستقلة

### آلية التشغيل

سكربت يعمل مرة واحدة ويخرج، يُشغّل بواسطة
`cron`
يومياً:

```
0 2 * * * /usr/bin/python3 /opt/memora-archive/run.py
```

السكربت في كل تشغيل:
1. يحاول الحصول على قفل ملف
   `file lock`
   — إذا موجود يخرج فوراً
2. يبحث عن مهام بحالة
   `Pending`
3. ينفذها بالتسلسل واحدة تلو الأخرى
4. يحرر القفل ويخرج

---

## 4. جدول الأرشفة — DocType

### الحقول الأساسية

| الحقل | النوع | الوصف |
|---|---|---|
| `source_doctype` | Data | اسم الجدول المصدر |
| `archive_scope` | Data | معرّف النطاق مثل معرّف الموسم |
| `status` | Select | حالة المهمة |
| `priority` | Select | أولوية التنفيذ |
| `schema_version` | Data | رقم نسخة بنية الجدول وقت الأرشفة |

### حقول التنفيذ والتتبع

| الحقل | النوع | الوصف |
|---|---|---|
| `row_count` | Int | عدد السجلات المُأرشفة |
| `file_path` | Data | مسار مجلد الأرشيف |
| `file_checksum` | Data | بصمة الملف بخوارزمية SHA-256 |
| `file_size_bytes` | Int | حجم الملف |
| `started_at` | Datetime | وقت بداية التنفيذ |
| `completed_at` | Datetime | وقت نهاية التنفيذ |
| `claimed_at` | Datetime | وقت حجز المهمة من السكربت |
| `error_log` | Long Text | رسالة الخطأ عند الفشل |
| `retry_count` | Int | عدد المحاولات |

### حقول السلوك

| الحقل | النوع | الوصف |
|---|---|---|
| `post_archive_action` | Select | ماذا نفعل بعد الأرشفة: Delete / Keep / Soft Delete |
| `source_deleted` | Check | هل تم حذف البيانات من المصدر |

### حقل البيانات الوصفية

| الحقل | النوع | الوصف |
|---|---|---|
| `meta` | JSON | تعليمات مخصصة لكل عملية أرشفة |

### مثال على حقل meta

```json
{
  "query_filter": {
    "season_id": "2024-S1"
  },
  "related_tables": [
    "Memora Review Item"
  ],
  "export_columns": [
    "player_id",
    "item_id",
    "first_seen_at",
    "last_seen_at",
    "last_result",
    "attempt_count",
    "correct_count"
  ],
  "schema_snapshot": {
    "columns": [
      {"name": "player_id", "type": "VARCHAR(140)"},
      {"name": "item_id", "type": "VARCHAR(36)"},
      {"name": "first_seen_at", "type": "DATETIME"},
      {"name": "last_seen_at", "type": "DATETIME"},
      {"name": "last_result", "type": "ENUM('Correct','Incorrect')"},
      {"name": "attempt_count", "type": "INT UNSIGNED"},
      {"name": "correct_count", "type": "INT UNSIGNED"}
    ],
    "primary_key": ["player_id", "item_id"]
  },
  "notes": "End of semester archive"
}
```

### ملاحظات مهمة على الجدول

- جميع الحقول
  `read-only`
  من الواجهة
- يُملأ حقل الـ
  `meta`
  برمجياً عند إنشاء السجل
- المشرف لا يستطيع التعديل على القيم

---

## 5. دورة حالة المهمة

```
Pending → Processing → Completed → Purged
                ↓
              Failed
```

### تفاصيل كل حالة

- **Pending**: المهمة جاهزة للتنفيذ
- **Processing**: السكربت حجز المهمة ويعمل عليها
- **Completed**: الأرشفة نجحت والملفات جاهزة
- **Purged**: البيانات حُذفت من المصدر بنجاح
- **Failed**: فشلت بعد استنفاذ كل المحاولات

---

## 6. سير عمل الأرشفة — خطوة بخطوة

### المرحلة الأولى — إنشاء طلب الأرشفة

عند انتهاء الموسم، وظيفة
`cron`
خاصة بالتحقق من المواسم تكتشف الموسم المنتهي وتنشئ سجل أرشفة جديد بحالة
`Pending`
داخل الـ
DocType

كل جدول له محفّز مختلف حسب الحاجة.

### المرحلة الثانية — التنفيذ

1. السكربت يحاول الحصول على
   `file lock`
2. يبحث عن مهام بحالة
   `Pending`
3. لكل مهمة يعمل
   `atomic claim`
   بقاعدة البيانات:

```sql
UPDATE `tabArchive Job`
SET status = 'Processing', claimed_at = NOW()
WHERE status = 'Pending' AND name = '...'
```

4. يسحب بيانات الحقائق
   `fact rows`
   حسب الفلتر المحدد بالـ
   `meta`
5. يستخرج المعرّفات الفريدة المرتبطة من بيانات الحقائق
   `distinct referenced IDs`
6. يبني لقطات الأبعاد فقط للسجلات المرتبطة بهذه الدفعة
7. يصدّر كل شيء إلى ملفات
   `Parquet`
8. يتحقق من الملفات: عدد الصفوف، البنية، البصمة، الحجم
9. ينقل الملفات لمجلد الأرشيف
10. يتحقق من نجاح النقل
11. يبني ملف
    `manifest.json`
12. يحدّث حالة السجل إلى
    `Completed`

---

## 7. صيغة التصدير — Parquet

### لماذا Parquet وليس CSV؟

- حجم أصغر بكثير (5 إلى 10 أضعاف)
- تحفظ أنواع البيانات (تاريخ يبقى تاريخ، رقم يبقى رقم)
- مصممة للتحليلات على ملايين السجلات
- مدعومة من كل أدوات التحليل الحديثة

### طبيعة الملف

`Parquet`
ملف غير قابل للتعديل
`immutable`
— يُكتب مرة ويُقرأ كثيراً.
إذا لزم التعديل، يُقرأ الملف كاملاً ويُكتب ملف جديد.
هذا بالضبط المطلوب للأرشيف.

### أدوات التصدير

```
pyarrow + pandas
```

تُثبّت على سيرفر الإنتاج لاستخدام السكربت المستقل.

---

## 8. بنية ملفات الأرشيف

### هيكلية المجلدات

```
/var/archive/memora/
  └── batch_2024S1_practice_log/
      ├── manifest.json
      ├── fact_practice_log.parquet
      ├── dim_player.parquet
      └── dim_review_item.parquet
```

- مسار التخزين خارج مجلد
  `Frappe/bench`
  عشان لا يتأثر بأي تحديث أو نشر

### مبدأ اللقطات

- كل دفعة أرشفة تنتج مجلد كامل مستقل
- لقطات الأبعاد مقتصرة فقط على السجلات المرتبطة بالدفعة
  `batch-scoped`
- نفس الكيان (مثلاً لاعب) قد يظهر بعدة دفعات — هذا مقبول ومتوقع
- كل دفعة يمكن تحليلها بشكل مستقل بالكامل
- لا غموض في أي لقطة تنتمي لأي دفعة

---

## 9. ملف البيان — manifest.json

```json
{
  "batch_id": "ARCH-00001",
  "source_doctype": "Memora Practice Log",
  "archive_scope": "2024-S1",
  "schema_version": "v1",
  "created_at": "2025-03-07T02:15:00Z",
  "files": [
    {
      "role": "fact",
      "filename": "fact_practice_log.parquet",
      "row_count": 850000,
      "checksum": "sha256:abc123..."
    },
    {
      "role": "dimension",
      "entity": "player",
      "snapshot_schema_version": "v2",
      "scope": "batch_referenced",
      "referenced_by": "fact_practice_log.player_id",
      "filename": "dim_player.parquet",
      "row_count": 1200,
      "checksum": "sha256:def456..."
    },
    {
      "role": "dimension",
      "entity": "review_item",
      "snapshot_schema_version": "v1",
      "scope": "batch_referenced",
      "referenced_by": "fact_practice_log.item_id",
      "filename": "dim_review_item.parquet",
      "row_count": 5000,
      "checksum": "sha256:ghi789..."
    }
  ]
}
```

---

## 10. سجل تعريفات اللقطات — Snapshot Schema Registry

### الموقع

```
memora/
  └── archive/
      ├── schemas/
      │   ├── dimensions/
      │   │   ├── player.v2.yaml
      │   │   └── review_item.v1.yaml
      │   └── archive_types/
      │       └── practice_log.v1.yaml
```

ملفات
`YAML`
داخل المشروع، محفوظة بـ
`git`
وقابلة للمراجعة والمقارنة.

### مبادئ التصميم

- تعريفات الأبعاد عامة لكل كيان وليست مربوطة بنوع أرشفة واحد
  `global per entity`
- مُرقّمة بنسخة
  `versioned`
- نوع الأرشفة يُشير إلى تعريفات الأبعاد يلي بيحتاجها
- عدة أنواع أرشفة ممكن تستخدم نفس تعريف البعد

### مثال — تعريف بُعد

```yaml
# dimensions/player.v2.yaml
entity: player
version: v2
source_table: tabMemora Player
fields:
  - player_id
  - grade
  - track
  - plan_id
  - season_id
```

### مثال — تعريف نوع أرشفة

```yaml
# archive_types/practice_log.v1.yaml
archive_type: practice_log
version: v1
fact_table: tabMemora Practice Log
dimensions:
  - entity: player
    schema_version: v2
  - entity: review_item
    schema_version: v1
```

### مبادئ اختيار حقول اللقطات

لا ننسخ الجدول كاملاً. نأخذ فقط:
- حقول الهوية الثابتة
- حقول مطلوبة للتحليل والتقارير
- حقول تحفظ المعنى التاريخي

التعريفات محددة مسبقاً ومُرقّمة، وليست حرة لكل دفعة.
هذا يمنع التناقض ويسهّل التحليل.

---

## 11. آليات الحماية

### حماية على مستوى العملية — File Lock

```
/var/run/memora-archive.lock
```

يمنع تشغيل نسختين من السكربت بنفس الوقت على نفس السيرفر.

### حماية على مستوى المهمة — DB Claim

```sql
UPDATE `tabArchive Job`
SET status = 'Processing', claimed_at = NOW()
WHERE status = 'Pending' AND name = '...'
```

إذا أثّر التحديث على 0 صف، معناه مهمة أخرى حُجزت بالفعل.

### كشف المهام المعلّقة — Stuck Job Detection

```sql
UPDATE `tabArchive Job`
SET status = 'Failed',
    error_log = 'Job timed out after 1 hour'
WHERE status = 'Processing'
AND claimed_at < NOW() - INTERVAL 1 HOUR
```

### تكرار التصدير — Idempotency

لو نفس الدفعة طُلب تصديرها مرتين:
- إذا حالتها
  `Completed`
  — يتجاهلها
- إذا حالتها
  `Failed`
  — يحذف الملف القديم ويبدأ من جديد

---

## 12. إعادة المحاولة والإشعارات

### آلية إعادة المحاولة

1. المهمة تفشل → ترجع لحالة
   `Pending`
   ويزيد عدد المحاولات
2. تُحاول تلقائياً حتى 3 مرات
3. بعد 3 محاولات → الحالة تصير
   `Failed`
   ويُرسل إشعار للمشرف

### الإشعار

عبر نظام الإشعارات المدمج بـ
`Frappe`
مع إمكانية إرسال بريد إلكتروني.

### كبسة إعادة المحاولة اليدوية

تظهر فقط على المهام بحالة
`Failed`
وهي
`server action button`
بتعمل:

```python
@frappe.whitelist()
def retry_archive_job(job_name):
    job = frappe.get_doc("Archive Job", job_name)
    if job.status != "Failed":
        frappe.throw("Only failed jobs can be retried")
    job.status = "Pending"
    job.retry_count = 0
    job.error_log = ""
    job.save(ignore_permissions=True)
```

---

## 13. مهمة الحذف من المصدر — Purge Job

### مبدأ الفصل

الحذف من المصدر لا يحصل أبداً ضمن عملية الأرشفة نفسها.

هي مهمة منفصلة تعمل لاحقاً بعد التأكد 100% من نجاح الأرشفة.

### آلية العمل

- تبحث عن مهام بحالة
  `Completed`
  والإجراء المطلوب
  `Delete`
- تحذف البيانات من المصدر على دفعات (مثلاً 10,000 سجل بكل دفعة)
- مع فترة راحة بين كل دفعة عشان لا تضغط على قاعدة البيانات
- تتبع التقدم بحقل
  `purge_progress`
  عشان لو وقفت بالنص تكمل من وين وقفت
- بعد الانتهاء تحدّث الحالة إلى
  `Purged`
  وتعلّم
  `source_deleted = 1`

### لماذا الحذف على دفعات؟

حذف ملايين السجلات دفعة واحدة بـ
`MariaDB`
يسبب:
- قفل على الجدول لفترة طويلة
- ضغط على سجل المعاملات
  `transaction log`
- تأثير على أداء الاستعلامات الأخرى

---

## 14. ملاحظات على جدول Practice Log

الجدول منشأ بـ
`raw DDL`
وليس
`DocType`
عادي من
Frappe

- لا يوجد عمود
  `name`
  التقليدي
- المفتاح الأساسي مركّب:
  `player_id + item_id`
- لا يوجد عمود مباشر للموسم — تحديد النطاق يتم عبر المنطق البرمجي

---

## 15. نقاط للمستقبل

- **لوحة مراقبة** — صفحة بسيطة للمشرف تعرض حالة النظام بشكل عام
- **نقل لسيرفر التحليلات** — عندما يجهز المشروع الثاني
- **حذف الملفات المحلية** — بعد التأكد من نجاح النقل للسيرفر الثاني
- **التوسع لجداول أخرى** — بإضافة ملفات
  `YAML`
  جديدة بسجل التعريفات وكتابة المحفّزات المناسبة
- **الانتقال لخدمة دائمة** — إذا كبر حجم الأرشفة، ممكن نتحول من
  `cron`
  إلى
  `daemon / systemd service`
- **تخزين خارجي** — إذا لزم، ممكن ننتقل إلى
  `S3`
  أو
  `MinIO`
  بدل التخزين المحلي