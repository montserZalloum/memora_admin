Memora Archive System
│
├── 1) الهدف العام
│   ├── بناء نظام أرشفة منخفض التكلفة
│   ├── مبني على Frappe / MariaDB
│   ├── يبدأ من Practice Log
│   ├── يتوسع لاحقاً لجداول أخرى
│   └── الهدف: تاريخي وتقاريري وليس تحليلات لحظية
│
├── 2) المبادئ الأساسية
│   ├── البيانات النشطة تبقى في Production DB
│   ├── عند انتهاء الموسم يتم إنشاء أرشيف
│   ├── الأرشيف يُخزّن محلياً مؤقتاً
│   ├── لاحقاً يُنقل إلى Analytics Server
│   └── بعد نجاح النقل الكامل تُحذف النسخة المحلية
│
├── 3) المعمارية العامة
│   ├── Control Plane
│   │   ├── داخل Frappe
│   │   ├── تعريف DocType للأرشفة
│   │   ├── إنشاء سجلات الأرشفة
│   │   ├── عرض الحالة للمشرف
│   │   └── إعادة المحاولة للمهام الفاشلة
│   │
│   ├── Execution Plane
│   │   ├── Python script مستقل
│   │   ├── لا يعتمد على Frappe runtime
│   │   ├── يسحب Pending jobs من DB مباشرة
│   │   ├── يصدّر Parquet
│   │   ├── يبني Dimension Snapshots
│   │   ├── يبني manifest.json
│   │   └── يحدّث حالة المهمة
│   │
│   ├── لماذا Python script مستقل؟
│   │   ├── عزل عن Frappe
│   │   ├── لا يزاحم worker queue
│   │   ├── أسهل في التوسعة
│   │   ├── أسهل في retries / logging / monitoring
│   │   └── يجعل الأرشفة وحدة مستقلة
│   │
│   └── آلية التشغيل
│       ├── cron يومي
│       │   └── 0 2 * * * /usr/bin/python3 /opt/memora-archive/run.py
│       └── كل تشغيل:
│           ├── الحصول على file lock
│           ├── البحث عن Pending jobs
│           ├── تنفيذها بالتسلسل
│           └── تحرير القفل والخروج
│
├── 4) Archive Job DocType
│   ├── الحقول الأساسية
│   │   ├── source_doctype
│   │   ├── archive_scope
│   │   ├── status
│   │   ├── priority
│   │   └── schema_version
│   │
│   ├── حقول التنفيذ والتتبع
│   │   ├── row_count
│   │   ├── file_path
│   │   ├── file_checksum
│   │   ├── file_size_bytes
│   │   ├── started_at
│   │   ├── completed_at
│   │   ├── claimed_at
│   │   ├── error_log
│   │   └── retry_count
│   │
│   ├── حقول السلوك
│   │   ├── post_archive_action
│   │   └── source_deleted
│   │
│   ├── metadata
│   │   └── meta (JSON)
│   │       ├── query_filter
│   │       ├── related_tables
│   │       ├── export_columns
│   │       ├── schema_snapshot
│   │       └── notes
│   │
│   └── ملاحظات
│       ├── جميع الحقول read-only
│       ├── meta يُملأ برمجياً
│       └── المشرف لا يعدّل القيم
│
├── 5) دورة حالة المهمة
│   └── Pending
│       └── Processing
│           ├── Completed
│           │   └── Purged
│           └── Failed
│
├── 6) سير عمل الأرشفة
│   ├── المرحلة الأولى: إنشاء طلب الأرشفة
│   │   ├── cron يكتشف الموسم المنتهي
│   │   ├── ينشئ Archive Job
│   │   └── الحالة = Pending
│   │
│   └── المرحلة الثانية: التنفيذ
│       ├── file lock
│       ├── البحث عن Pending jobs
│       ├── atomic DB claim
│       ├── سحب fact rows
│       ├── استخراج distinct referenced IDs
│       ├── بناء dimension snapshots
│       ├── التصدير إلى Parquet
│       ├── التحقق من الملفات
│       ├── نقل الملفات لمجلد الأرشيف
│       ├── التحقق من نجاح النقل
│       ├── بناء manifest.json
│       └── تحديث الحالة إلى Completed
│
├── 7) صيغة التصدير
│   ├── Parquet
│   │   ├── أصغر من CSV
│   │   ├── يحفظ أنواع البيانات
│   │   ├── مناسب للتحليلات الكبيرة
│   │   └── immutable
│   └── الأدوات
│       └── pyarrow + pandas
│
├── 8) بنية ملفات الأرشيف
│   ├── المسار
│   │   └── /var/archive/memora/
│   │       └── batch_2024S1_practice_log/
│   │           ├── manifest.json
│   │           ├── fact_practice_log.parquet
│   │           ├── dim_player.parquet
│   │           └── dim_review_item.parquet
│   │
│   └── مبدأ اللقطات
│       ├── كل batch مستقل
│       ├── dimensions مرتبطة فقط بهذه الدفعة
│       ├── نفس الكيان قد يتكرر بين الدفعات
│       ├── كل دفعة قابلة للتحليل مستقلة
│       └── لا يوجد غموض في الانتماء
│
├── 9) manifest.json
│   ├── batch_id
│   ├── source_doctype
│   ├── archive_scope
│   ├── schema_version
│   ├── created_at
│   └── files[]
│       ├── fact file
│       └── dimension files
│           ├── player
│           └── review_item
│
├── 10) Snapshot Schema Registry
│   ├── location
│   │   └── memora/archive/schemas/
│   │       ├── dimensions/
│   │       │   ├── player.v2.yaml
│   │       │   └── review_item.v1.yaml
│   │       └── archive_types/
│   │           └── practice_log.v1.yaml
│   │
│   ├── مبادئ التصميم
│   │   ├── dimensions عامة لكل entity
│   │   ├── versioned
│   │   ├── archive type يشير لما يحتاجه
│   │   └── أكثر من archive type قد يستخدم نفس dimension
│   │
│   ├── مثال dimension schema
│   │   └── player.v2.yaml
│   │       ├── entity: player
│   │       ├── version: v2
│   │       ├── source_table
│   │       └── fields
│   │
│   ├── مثال archive type schema
│   │   └── practice_log.v1.yaml
│   │       ├── archive_type: practice_log
│   │       ├── version: v1
│   │       ├── fact_table
│   │       └── dimensions
│   │
│   └── مبادئ اختيار الحقول
│       ├── لا ننسخ الجدول كاملاً
│       ├── نأخذ حقول الهوية
│       ├── نأخذ حقول التحليل والتقارير
│       ├── نأخذ ما يحفظ المعنى التاريخي
│       └── التعريفات ثابتة ومُرقّمة
│
├── 11) آليات الحماية
│   ├── File Lock
│   │   └── /var/run/memora-archive.lock
│   │
│   ├── DB Claim
│   │   └── UPDATE tabArchive Job ... WHERE status = 'Pending'
│   │
│   ├── Stuck Job Detection
│   │   └── Processing + claimed_at older than 1 hour → Failed
│   │
│   └── Idempotency
│       ├── Completed → تجاهل
│       └── Failed → حذف القديم وإعادة التنفيذ
│
├── 12) إعادة المحاولة والإشعارات
│   ├── Retry Flow
│   │   ├── الفشل → Pending
│   │   ├── زيادة retry_count
│   │   ├── حتى 3 محاولات
│   │   └── بعدها → Failed
│   │
│   ├── Notification
│   │   ├── Frappe notifications
│   │   └── Email optional
│   │
│   └── Manual Retry Button
│       └── يظهر فقط عند Failed
│
├── 13) Purge Job
│   ├── مبدأ الفصل
│   │   └── الحذف لا يتم داخل عملية الأرشفة نفسها
│   │
│   ├── الشروط
│   │   ├── status = Completed
│   │   └── post_archive_action = Delete
│   │
│   ├── آلية التنفيذ
│   │   ├── حذف على دفعات
│   │   ├── sleep بين الدفعات
│   │   ├── تتبع purge_progress
│   │   ├── استكمال من آخر نقطة عند الانقطاع
│   │   └── عند الانتهاء:
│   │       ├── status = Purged
│   │       └── source_deleted = 1
│   │
│   └── لماذا الحذف على دفعات؟
│       ├── تقليل القفل الطويل
│       ├── تقليل ضغط transaction log
│       └── حماية أداء الاستعلامات الأخرى
│
├── 14) ملاحظات على Practice Log
│   ├── منشأ بـ raw DDL
│   ├── ليس DocType عادي
│   ├── لا يوجد name
│   ├── primary key مركب
│   │   └── player_id + item_id
│   └── لا يوجد season column مباشر
│
└── 15) نقاط للمستقبل
    ├── Dashboard للمراقبة
    ├── نقل إلى Analytics Server
    ├── حذف الملفات المحلية بعد نجاح النقل
    ├── التوسع لجداول أخرى
    ├── التحول من cron إلى daemon / systemd
    └── استخدام S3 / MinIO مستقبلاً