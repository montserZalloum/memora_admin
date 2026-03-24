---
id: event-access
title: "صلاحية الوصول للفعاليات"
slug: event-access
audience: backend
owner: corex
status: current
last_updated: "2026-03-24"
last_verified_commit: "76ef96d"
related_code:
  - fastapi_app/services/event_access.py
  - fastapi_app/api/v1/endpoints/event_access.py
  - memora_admin/memora_admin/services/premium/event_purchase.py
  - memora_admin/memora_admin/api/premium.py
  - memora_admin/memora_admin/events/event_access_sync.py
related_doctypes:
  - Memora Live Event Access
  - Memora Live Event Purchase
  - Memora Live Challenge Event
related_endpoints:
  - "POST /events/{event_id}/purchase"
  - "GET /events/{event_id}/access-state"
  - "POST grant_event_access (whitelisted)"
  - "POST revoke_event_access (whitelisted)"
  - "POST refund_event_purchase (whitelisted)"
tags: [access, premium, purchase, voucher, live-event, paid-event]
---

# صلاحية الوصول للفعاليات

> تتحكم هذه الميزة بمن يمكنه الانضمام إلى فعاليات التحدي المباشر — سواء كانت مجانية أو مدفوعة.

---

## ملخص سريع

### ماذا تفعل هذه الميزة؟
تحدد ما إذا كان الطالب مؤهلاً للانضمام إلى فعالية تحدي مباشر بناءً على نوع الفعالية (مجانية/مدفوعة) وحالة الاشتراك والشراء.

### متى تكون مهمة؟
عند وجود فعالية مدفوعة (`is_paid = 1`) — عندها يجب التحقق من وجود صلاحية وصول قبل السماح بالانضمام.

### ما الذي يمنع الوصول عادةً؟
- لا يوجد اشتراك بريميوم فعّال
- لم يتم شراء تذكرة
- لم يتم استخدام قسيمة
- لم يمنح المشرف صلاحية يدوية

---

## مسار القرار البصري

```
هل الفعالية مجانية؟
├── نعم ← الجميع يستطيع الانضمام (access_type = free)
└── لا (مدفوعة) ←
    هل لدى الطالب اشتراك بريميوم فعّال؟
    ├── نعم ← الانضمام مجاناً (access_type = premium)
    └── لا ←
        هل يوجد سجل وصول فعّال (LEA)؟
        ├── نعم ← الانضمام (access_type = purchase | voucher | admin)
        └── لا ← لا يمكن الانضمام
            ├── شراء تذكرة (صلاحية 30 دقيقة)
            ├── استخدام قسيمة
            └── طلب صلاحية من المشرف
```

---

## رحلة المشرف

### ما الذي ينشئه ويديره المشرف؟

| الإجراء | التفاصيل |
|---------|----------|
| إنشاء فعالية مدفوعة | تفعيل `is_paid`، تحديد `price` و `currency` (JOD افتراضياً) |
| تحديد الخطط المؤهلة | إضافة الخطط في `eligible_plans` (الجدول الفرعي) |
| منح صلاحية يدوياً | استدعاء `grant_event_access(player, event)` |
| إلغاء صلاحية | استدعاء `revoke_event_access(access_id)` |
| معالجة استرداد | استدعاء `refund_event_purchase(purchase_id)` — يلغي الصلاحية وينشئ إشعار ائتمان تلقائياً |

### ما الذي يجب التحقق منه؟
- سجل LEA موجود بحالة `active`
- سجل LEP يعرض الحالة الصحيحة
- استجابة endpoint `/access-state` متسقة مع البيانات

---

## رحلة الطالب

### عند وجود صلاحية وصول
- يرى الطالب زر "الانضمام" فعّالاً
- يدخل مباشرة إلى الفعالية

### عند عدم وجود صلاحية وصول
- يرى الطالب السعر وزر "شراء تذكرة"
- بعد الشراء (خلال 30 دقيقة) يتم إنشاء سجل وصول تلقائياً
- أو يمكنه استخدام قسيمة إن توفرت

---

## الحالات الشائعة وحالات الحافة

| الحالة | السبب | ما يجب التحقق منه |
|--------|-------|-------------------|
| مدفوعة ولا يمكن الانضمام | لا يوجد سجل LEA فعّال | تحقق من `Memora Live Event Access` |
| بريميوم موجود لكن غير معتبر | الخطة غير مدرجة في `eligible_plans` أو البريميوم منتهي | تحقق من `eligible_plans` على الفعالية و `is_plan_premium_usable` |
| صلاحية ملغاة | المشرف ألغى الصلاحية أو تم الاسترداد | تحقق من حقل `status` في LEA (revoked/refunded) |
| الفعالية غير قابلة للانضمام | الفعالية ليست في حالة Active | تحقق من حقل `status` على الفعالية |
| شراء مزدوج محظور | يوجد شراء معلق أو صلاحية فعّالة أو بريميوم يغطي | رمز الخطأ: `PENDING_PURCHASE` أو `ALREADY_HAS_ACCESS` أو `COVERED_BY_PREMIUM` |
| الشراء انتهت صلاحيته | مرّت 30 دقيقة بدون إتمام الدفع | تحقق من `expires_at` في LEP |
| تتالي الاسترداد | الاسترداد يلغي LEP و LEA وينشئ إشعار ائتمان | كل شيء ذري — إما ينجح كله أو يفشل كله |

---

## صندوق استكشاف الأخطاء

| إذا حدث هذا... | تحقق من... |
|----------------|------------|
| الطالب لا يستطيع الانضمام لفعالية مدفوعة | حالة LEA في Frappe + استجابة `/access-state` |
| البريميوم يجب أن يغطي لكنه لا يغطي | `eligible_plans` على الفعالية + `is_plan_premium_usable` |
| الشراء عالق في pending | حقل `expires_at` + التحقق من وصول webhook الدفع |
| الكاش يعرض حالة خاطئة | استدعاء `invalidate_event_access_cache(player, event)` أو انتظار 5 دقائق (NEGATIVE_CACHE_TTL) |
| خطأ CONCURRENT_REQUEST | إعادة المحاولة بعد ثوانٍ قليلة — قفل Redis مؤقت (10 ثوانٍ) |

---

## التفاصيل التقنية

### أنواع المستندات (DocTypes)

**Memora Live Event Access (LEA)**
- الترقيم: `LEA-.#####`
- الحقول: `player`, `event`, `status` (active/revoked/refunded), `access_type` (purchase/voucher/admin), `purchase_ref`, `voucher_ref`, `granted_by`, `revoked_at`, `revoked_by`

**Memora Live Event Purchase (LEP)**
- الترقيم: `LEP-.#####`
- الحقول: `player`, `event`, `status` (pending/paid/failed/cancelled/refunded), `amount`, `currency`, `expires_at`, `payment_gateway`, `payment_reference`, `erpnext_invoice`, `event_access_ref`, `paid_at`, `refunded_at`

**Memora Live Challenge Event**
- الحقول المتعلقة: `is_paid`, `price`, `currency`, `eligible_plans` (جدول فرعي)

### آلية التخزين المؤقت (3 طبقات)

1. **ذاكرة العملية** — TTL: 60 ثانية، الحد الأقصى: 10,000 مدخل
2. **Redis HASH** — المفتاح: `memora:event_access:{player}:{event}` — يحتوي: `has_access`, `access_type`, `access_id`
3. **Frappe API** — مصدر الحقيقة النهائي

- الكاش السلبي: 300 ثانية (يمنع الرفض الدائم الخاطئ)
- مفتاح القفل: `memora:lock:event_access:{player}:{event}` — TTL: 10 ثوانٍ
- المزامنة: عبر Frappe hooks في `event_access_sync.py` (after_insert, on_update)

### النقاط الطرفية (Endpoints)

| النقطة | الوصف |
|--------|-------|
| `POST /events/{event_id}/purchase` | إنشاء شراء تذكرة — الحراسات: فحص بريميوم، وصول موجود، شراء معلق، قفل Redis |
| `GET /events/{event_id}/access-state` | حالة الوصول الكاملة للواجهة الأمامية |
| `grant_event_access` (whitelisted) | منح صلاحية يدوية — مشرفون فقط |
| `revoke_event_access` (whitelisted) | إلغاء صلاحية — مشرفون فقط |
| `refund_event_purchase` (whitelisted) | استرداد مع تتالي ذري (LEP + LEA + إشعار ائتمان) |

### آلات الحالة

**دورة حياة الشراء:**
```
pending → paid → refunded
pending → failed
pending → cancelled (تلقائي بعد 30 دقيقة أو يدوي)
```

**دورة حياة الوصول:**
```
active → revoked (إلغاء من المشرف)
active → refunded (تتالي من استرداد الشراء)
```

---

## الصفحات ذات الصلة

- TODO: إدارة الاشتراكات البريميوم
- TODO: نظام القسائم
- TODO: فعاليات التحدي المباشر
- TODO: الفواتير وإشعارات الائتمان
