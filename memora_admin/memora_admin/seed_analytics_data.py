"""Seed ~140,000 realistic records across 18 tables for analytics reports.

Usage:
    bench --site x.conanacademy.com execute memora_admin.memora_admin.seed_analytics_data.run

Two phases:
  1. Inspect — query reference data, print report
  2. Generate — 12 wave functions, each with guard/skip-if-seeded logic

Safe to re-run: each wave checks current count and skips if threshold met.
Does NOT modify or delete existing data (INSERT only).
"""

import binascii
import hashlib
import hmac as hmac_module
import json
import random
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import frappe

# ─── Constants ─────────────────────────────────────────────────────────────────

SEED = 42
NOW = None  # set in run()
OWNER = "Administrator"

# Targets
T_PLAYERS = 2_600
T_INTERACTIONS = 90_000
T_MEMORY_STATE = 15_000
T_PRACTICE_LOG = 8_000
T_PROGRESS = 5_000
T_ATTEMPTS = 2_000
T_LIVE_EVENTS = 10
T_LIVE_PARTS = 500
T_SUBS = 1_500
T_VOUCHER_BATCHES = 30
T_VOUCHER_CARDS = 5_000
T_VOUCHER_ALLOCS = 300
T_VOUCHER_RDMPTS = 2_000
T_PLAN_HIST = 500
T_REPORTS = 200

# Distributions  {value: weight}
D_EVENT_TYPE = {"Started": 30, "Completed": 40, "Failed": 20, "Skipped": 10}
D_PLATFORM = {"android": 55, "ios": 40, "web": 5}
D_REPORT_TYPE = {"Bug": 25, "Content Error": 40, "Suggestion": 25, "Other": 10}
D_REPORT_STATUS = {"Open": 40, "In Progress": 25, "Resolved": 25, "Closed": 10}
D_PLAN_REASON = {"Season Expired": 70, "Voluntary Change": 30}
D_VOUCHER_PURPOSE = {"Sale": 60, "Scholarship": 20, "Gift": 10, "Promotion": 10}
D_REDEMPTION_STATUS = {
    "Success": 60, "Invalid PIN": 15, "Already Redeemed": 5, "Expired": 5,
    "Void": 3, "All Grants Owned": 5, "Rate Limited": 2, "Error": 5,
}
D_PAYMENT_METHOD = {
    "Payment Gateway": 40, "Manual-Admin": 20, "Voucher": 30,
    "Scholarship": 5, "Gift": 5,
}
D_TXN_STATUS = {
    "Completed": 70, "Pending Approval": 10, "Failed": 10,
    "Cancelled": 5, "Rejected": 5,
}

APP_VERSIONS = ["1.2.0", "1.3.0", "1.4.0", "2.0.0"]

ARABIC_FIRST = [
    "أحمد", "محمد", "علي", "حسن", "عمر", "خالد", "يوسف", "إبراهيم", "سعد", "طارق",
    "فاطمة", "عائشة", "مريم", "نور", "هدى", "سارة", "لينا", "رنا", "دانا", "ريم",
]
ARABIC_LAST = [
    "الأحمد", "العلي", "الحسن", "الخالد", "السعيد", "المحمود", "الناصر", "العمري",
    "الشامي", "الزعبي", "الطراونة", "العبادي", "الرواشدة", "القاضي", "الصمادي",
]
REPORT_DESC = [
    "يوجد خطأ في صياغة السؤال الثالث",
    "الإجابة الصحيحة غير صحيحة في هذا السؤال",
    "النص غير واضح ويحتاج إلى تعديل",
    "هناك خطأ إملائي في الخيار الثاني",
    "الصورة المرفقة غير واضحة",
    "السؤال مكرر مع سؤال سابق",
    "المعلومة المذكورة قديمة وتحتاج تحديث",
    "التنسيق غير مناسب للقراءة",
    "يوجد خطأ في ترتيب الأسئلة",
    "اقتراح: إضافة شرح مفصل للإجابة",
    "المحتوى لا يتوافق مع المنهج الحالي",
    "يوجد خطأ في الأرقام المذكورة",
    "الدرس يحتاج أمثلة إضافية",
    "التسجيل الصوتي غير واضح",
    "اقتراح: إضافة فيديو توضيحي",
]

PIN_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


# ─── SeedContext ───────────────────────────────────────────────────────────────

@dataclass
class Ctx:
    """Reference data collected during inspect phase."""
    seasons: list = field(default_factory=list)
    plans: list = field(default_factory=list)
    plan_subjects: dict = field(default_factory=dict)       # plan → [subject, ...]
    subjects_with_lessons: list = field(default_factory=list)
    lessons: dict = field(default_factory=dict)              # subject → [lesson_name, ...]
    stages: dict = field(default_factory=dict)               # lesson → [stage_id, ...]
    review_items: dict = field(default_factory=dict)         # subject → [{item_id, topic, lesson}]
    all_review_items: list = field(default_factory=list)
    topics: dict = field(default_factory=dict)               # subject → [{name, topic_title}]
    product_grants: list = field(default_factory=list)
    customers: list = field(default_factory=list)
    existing_mobiles: set = field(default_factory=set)
    hmac_secret: str = ""
    new_player_ids: list = field(default_factory=list)
    player_plans: dict = field(default_factory=dict)
    player_seasons: dict = field(default_factory=dict)
    player_grades: dict = field(default_factory=dict)
    player_majors: dict = field(default_factory=dict)
    player_subjects: dict = field(default_factory=dict)     # player → [subject, ...]


# ─── Utilities ─────────────────────────────────────────────────────────────────

def _reserve(series_name: str, count: int) -> int:
    """Reserve contiguous block from tabSeries. Returns start number."""
    row = frappe.db.sql(
        "SELECT current FROM tabSeries WHERE name=%s FOR UPDATE",
        (series_name,), as_dict=True,
    )
    if row:
        start = row[0]["current"] + 1
        frappe.db.sql(
            "UPDATE tabSeries SET current=%s WHERE name=%s",
            (row[0]["current"] + count, series_name),
        )
    else:
        start = 1
        frappe.db.sql(
            "INSERT INTO tabSeries (name, current) VALUES (%s, %s)",
            (series_name, count),
        )
    return start


def _wchoice(rng, dist: dict):
    """Weighted random choice from {value: weight} dict."""
    return rng.choices(list(dist.keys()), weights=list(dist.values()), k=1)[0]


def _rdt(rng, lo: datetime, hi: datetime) -> datetime:
    """Random datetime in [lo, hi]."""
    secs = max(int((hi - lo).total_seconds()), 1)
    return lo + timedelta(seconds=rng.randint(0, secs))


def _compute_hmac(pin: str, secret: str) -> str:
    return hmac_module.new(
        secret.encode(), pin.encode(), hashlib.sha256,
    ).hexdigest()


def _gen_pin(rng, length=12) -> str:
    return "".join(rng.choice(PIN_ALPHABET) for _ in range(length))


def _uuid_to_bin(uuid_str: str) -> bytes:
    """Convert UUID string to 16-byte binary (same as UUID_TO_BIN polyfill)."""
    return binascii.unhexlify(uuid_str.replace("-", ""))


def _ms_name(prefix: str, n: int) -> int:
    """Deterministic BIGINT name for Memory State via uuid5."""
    return int.from_bytes(
        uuid.uuid5(uuid.NAMESPACE_DNS, f"seed-ms-{prefix}-{n}").bytes[:7], "big",
    ) + 1


def _count(table: str) -> int:
    if not table.startswith("tab"):
        raise ValueError(f"Invalid table name: {table}")
    return frappe.db.sql(f"SELECT COUNT(*) FROM `{table}`")[0][0]


# ─── Inspect ───────────────────────────────────────────────────────────────────

def inspect() -> Ctx:
    """Query reference data, print report, return context."""
    ctx = Ctx()

    # Seasons
    ctx.seasons = frappe.db.sql(
        "SELECT name, season_seq, start_date, end_date, is_published "
        "FROM `tabMemora Season` WHERE is_published=1 ORDER BY season_seq",
        as_dict=True,
    )
    print(f"\n{'=' * 60}")
    print("ANALYTICS SEED DATA — INSPECTION")
    print(f"{'=' * 60}")
    print(f"\nPublished Seasons: {len(ctx.seasons)}")
    for s in ctx.seasons:
        print(f"  {s.name} (seq {s.season_seq}): {s.start_date} → {s.end_date}")
    if not ctx.seasons:
        raise RuntimeError("No published seasons — cannot seed")

    # Plans
    snames = [s.name for s in ctx.seasons]
    ctx.plans = frappe.db.sql(
        "SELECT name, grade, major, season FROM `tabMemora Academic Plan` "
        "WHERE season IN %s ORDER BY name",
        (snames,), as_dict=True,
    )
    print(f"\nAcademic Plans (published seasons): {len(ctx.plans)}")
    if not ctx.plans:
        raise RuntimeError("No academic plans for published seasons")

    # Plan subjects
    pnames = [p.name for p in ctx.plans]
    for row in frappe.db.sql(
        "SELECT parent, subject FROM `tabMemora Plan Subject` "
        "WHERE parent IN %s ORDER BY parent, idx",
        (pnames,), as_dict=True,
    ):
        ctx.plan_subjects.setdefault(row.parent, []).append(row.subject)

    # Subjects with lessons
    ctx.subjects_with_lessons = frappe.db.sql(
        "SELECT s.name, s.subject_title, "
        "(SELECT COUNT(*) FROM `tabMemora Lesson` l WHERE l.subject=s.name) AS lc, "
        "(SELECT COUNT(*) FROM `tabMemora Review Item` ri WHERE ri.subject=s.name) AS rc "
        "FROM `tabMemora Subject` s HAVING lc > 0 ORDER BY lc DESC",
        as_dict=True,
    )
    print(f"\nSubjects with lessons: {len(ctx.subjects_with_lessons)}")
    for s in ctx.subjects_with_lessons:
        print(f"  {s.name} ({s.subject_title}): {s.lc} lessons, {s.rc} RIs")
    if not ctx.subjects_with_lessons:
        raise RuntimeError("No subjects with lessons")

    # Lessons, stages, review items, topics
    for subj in ctx.subjects_with_lessons:
        lessons = frappe.db.sql(
            "SELECT name FROM `tabMemora Lesson` WHERE subject=%s",
            (subj.name,), as_dict=True,
        )
        ctx.lessons[subj.name] = [l.name for l in lessons]

    all_lesson_names = []
    for v in ctx.lessons.values():
        all_lesson_names.extend(v)
    if all_lesson_names:
        for row in frappe.db.sql(
            "SELECT parent, stage_id FROM `tabMemora Lesson Stage` WHERE parent IN %s",
            (all_lesson_names,), as_dict=True,
        ):
            ctx.stages.setdefault(row.parent, []).append(row.stage_id)

    subj_names = [s.name for s in ctx.subjects_with_lessons]
    for row in frappe.db.sql(
        "SELECT item_id, subject, topic, lesson "
        "FROM `tabMemora Review Item` WHERE subject IN %s",
        (subj_names,), as_dict=True,
    ):
        ctx.review_items.setdefault(row.subject, []).append(row)
        ctx.all_review_items.append(row)
    print(f"Total review items: {len(ctx.all_review_items)}")

    for row in frappe.db.sql(
        "SELECT name, topic_title, subject FROM `tabMemora Topic` WHERE subject IN %s",
        (subj_names,), as_dict=True,
    ):
        ctx.topics.setdefault(row.subject, []).append(row)

    # Product grants, customers
    ctx.product_grants = frappe.db.sql(
        "SELECT name, plan FROM `tabMemora Product Grant` WHERE is_published=1",
        as_dict=True,
    )
    ctx.customers = frappe.db.sql("SELECT name FROM `tabCustomer`", as_dict=True)
    print(f"Product Grants: {len(ctx.product_grants)}")
    print(f"Customers: {len(ctx.customers)}")

    # Existing mobiles
    ctx.existing_mobiles = {
        r[0] for r in frappe.db.sql(
            "SELECT mobile FROM `tabMemora Player Profile` WHERE mobile IS NOT NULL"
        )
    }

    # HMAC secret
    ctx.hmac_secret = frappe.conf.get("voucher_hmac_secret", "")
    if not ctx.hmac_secret:
        print("WARNING: voucher_hmac_secret not set — using 'memora'")
        ctx.hmac_secret = "memora"

    # Current counts
    print(f"\n{'─' * 60}")
    print("CURRENT TABLE COUNTS:")
    for label, tbl in [
        ("Player Profile", "tabMemora Player Profile"),
        ("Player Wallet", "tabMemora Player Wallet"),
        ("Interaction Log", "tabMemora Interaction Log"),
        ("Memory State", "tabMemora Memory State"),
        ("Practice Log", "tabMemora Practice Log"),
        ("Structure Progress", "tabMemora Structure Progress"),
        ("Challenge Attempt", "tabMemora Challenge Attempt"),
        ("Challenge Detail", "tabMemora Challenge Attempt Detail"),
        ("Live Challenge Event", "tabMemora Live Challenge Event"),
        ("Live Participation", "tabMemora Live Challenge Participation"),
        ("Player Subscription", "tabMemora Player Subscription"),
        ("Subscription Txn", "tabMemora Subscription Transaction"),
        ("Voucher Batch", "tabMemora Voucher Batch"),
        ("Voucher Card", "tabMemora Voucher Card"),
        ("Voucher Allocation", "tabMemora Voucher Allocation"),
        ("Voucher Redemption", "tabMemora Voucher Redemption Log"),
        ("Plan History", "tabMemora Player Plan History"),
        ("Content Report", "tabMemora Content Report"),
    ]:
        print(f"  {label:30s}: {_count(tbl):>8,d}")

    print(f"\n{'=' * 60}\n")
    return ctx


# ─── Wave 01: Players ─────────────────────────────────────────────────────────

def wave_01_players(ctx: Ctx, rng):
    if _count("tabMemora Player Profile") >= 2_900:
        print("Wave 01: Players — SKIPPED")
        return
    print("Wave 01: Generating Player Profiles...")

    start = _reserve("PLAYER-", T_PLAYERS)

    # Unique mobiles
    mobiles: set[str] = set()
    while len(mobiles) < T_PLAYERS:
        m = f"079{rng.randint(1_000_000, 9_999_999)}"
        if m not in ctx.existing_mobiles and m not in mobiles:
            mobiles.add(m)
    mobiles_list = sorted(mobiles)

    # Season → plans mapping
    season_plans: dict[str, list] = {}
    for p in ctx.plans:
        season_plans.setdefault(p.season, []).append(p)

    sorted_seasons = sorted(ctx.seasons, key=lambda s: s.season_seq, reverse=True)
    available = [s.name for s in sorted_seasons if s.name in season_plans]
    if not available:
        raise RuntimeError("No seasons with plans")

    weights = []
    for i, sn in enumerate(available):
        weights.append(70 if i == 0 else (20 if i == 1 else 10))

    fields = [
        "name", "mobile", "display_name", "plan", "avatar",
        "grade", "major", "season", "preferred_lang",
        "owner", "modified_by", "creation", "modified", "docstatus", "idx",
    ]

    rows = []
    for i in range(T_PLAYERS):
        pname = f"PLAYER-{start + i:05d}"
        season = rng.choices(available, weights=weights, k=1)[0]
        plan = rng.choice(season_plans[season])
        display = f"{rng.choice(ARABIC_FIRST)} {rng.choice(ARABIC_LAST)}"
        creation = _rdt(rng, NOW - timedelta(days=180), NOW - timedelta(days=1))

        rows.append((
            pname, mobiles_list[i], display, plan.name, "pre",
            plan.grade, plan.major, season, "ar",
            OWNER, OWNER, creation, creation, 0, 0,
        ))

        ctx.new_player_ids.append(pname)
        ctx.player_plans[pname] = plan.name
        ctx.player_seasons[pname] = season
        ctx.player_grades[pname] = plan.grade
        ctx.player_majors[pname] = plan.major

        # Assign subjects
        if plan.name in ctx.plan_subjects:
            ctx.player_subjects[pname] = list(ctx.plan_subjects[plan.name])
        else:
            n = min(rng.randint(1, 3), len(ctx.subjects_with_lessons))
            ctx.player_subjects[pname] = [
                s.name for s in rng.sample(ctx.subjects_with_lessons, n)
            ]

    frappe.db.bulk_insert("Memora Player Profile", fields, rows, chunk_size=5_000)
    frappe.db.commit()
    print(f"  → {len(rows):,d} Player Profiles")


# ─── Wave 02: Wallets ──────────────────────────────────────────────────────────

def wave_02_wallets(ctx: Ctx, rng):
    if _count("tabMemora Player Wallet") >= 2_900:
        print("Wave 02: Wallets — SKIPPED")
        return
    print("Wave 02: Generating Player Wallets...")

    existing = {
        r[0] for r in frappe.db.sql("SELECT player FROM `tabMemora Player Wallet`")
    }
    need = [p for p in ctx.new_player_ids if p not in existing]
    if not need:
        print("  → all players already have wallets")
        return

    start = _reserve("WALT-", len(need))

    fields = [
        "name", "player", "total_xp", "current_streak", "dirty_flag",
        "status", "total_lessons", "total_time_min",
        "owner", "modified_by", "creation", "modified", "docstatus", "idx",
    ]

    rows = []
    for i, pid in enumerate(need):
        wname = f"WALT-{start + i:05d}"
        creation = _rdt(rng, NOW - timedelta(days=180), NOW)
        xp = rng.choices([0, rng.randint(10, 500), rng.randint(500, 5000),
                          rng.randint(5000, 50000)], weights=[20, 30, 35, 15])[0]
        streak = rng.choices([0, rng.randint(1, 7), rng.randint(7, 30),
                              rng.randint(30, 100)], weights=[30, 40, 20, 10])[0]
        lessons = rng.choices([0, rng.randint(1, 20), rng.randint(20, 100),
                               rng.randint(100, 500)], weights=[20, 30, 35, 15])[0]
        tmin = rng.choices([0, rng.randint(1, 60), rng.randint(60, 500),
                            rng.randint(500, 3000)], weights=[20, 30, 35, 15])[0]

        rows.append((
            wname, pid, xp, streak, 0, "Active", lessons, tmin,
            OWNER, OWNER, creation, creation, 0, 0,
        ))

    frappe.db.bulk_insert("Memora Player Wallet", fields, rows, chunk_size=5_000)
    frappe.db.commit()
    print(f"  → {len(rows):,d} Wallets")


# ─── Wave 03: Interaction Logs ─────────────────────────────────────────────────

def wave_03_interactions(ctx: Ctx, rng):
    if _count("tabMemora Interaction Log") >= 95_000:
        print("Wave 03: Interaction Logs — SKIPPED")
        return
    print("Wave 03: Generating Interaction Logs...")

    start = _reserve("LOG-", T_INTERACTIONS)

    fields = [
        "name", "player", "lesson", "stage_type", "item_id", "event_type",
        "time_spent", "errors_count", "timestamp", "client_metadata",
        "owner", "modified_by", "creation", "modified", "docstatus", "idx",
    ]

    BATCH = 10_000
    total = 0

    for b0 in range(0, T_INTERACTIONS, BATCH):
        b1 = min(b0 + BATCH, T_INTERACTIONS)
        rows = []
        for i in range(b0, b1):
            lname = f"LOG-{start + i:05d}"
            player = rng.choice(ctx.new_player_ids)

            # Subject the player knows
            psubjs = ctx.player_subjects.get(player, [])
            usable = [s for s in psubjs if s in ctx.lessons and ctx.lessons[s]]
            if not usable:
                usable = [s.name for s in ctx.subjects_with_lessons]
            subj = rng.choice(usable)
            lesson = rng.choice(ctx.lessons[subj])

            lstages = ctx.stages.get(lesson, [])
            stage = rng.choice(lstages) if lstages else f"stg{rng.randint(1000, 9999)}"

            ris = ctx.review_items.get(subj, [])
            item_id = rng.choice(ris).item_id if ris else None

            etype = _wchoice(rng, D_EVENT_TYPE)
            tspent = rng.randint(5, 600) if etype in ("Completed", "Failed") else 0
            errs = rng.randint(0, 5) if etype == "Failed" else 0

            ts = _rdt(rng, NOW - timedelta(days=90), NOW)
            meta = json.dumps({
                "platform": _wchoice(rng, D_PLATFORM),
                "app_version": rng.choice(APP_VERSIONS),
            })

            rows.append((
                lname, player, lesson, stage, item_id, etype,
                tspent, errs, ts, meta,
                OWNER, OWNER, ts, ts, 0, 0,
            ))

        frappe.db.bulk_insert("Memora Interaction Log", fields, rows)
        frappe.db.commit()
        total += len(rows)
        print(f"  → batch {b0 // BATCH + 1}: {len(rows):,d} (total {total:,d})")

    print(f"  → {total:,d} Interaction Logs total")


# ─── Wave 04: Memory State ────────────────────────────────────────────────────

def wave_04_memory_state(ctx: Ctx, rng):
    if _count("tabMemora Memory State") >= 15_000:
        print("Wave 04: Memory State — SKIPPED")
        return
    print("Wave 04: Generating Memory State rows...")

    valid_seqs = [s.season_seq for s in ctx.seasons]
    if not valid_seqs:
        valid_seqs = [1]

    subjects_with_ri = [s for s in ctx.subjects_with_lessons if s.name in ctx.review_items]
    if not subjects_with_ri:
        print("  → no review items — skipping")
        return

    BATCH = 2_000
    total = 0
    used = set()
    params_batch = []

    # Build rows
    attempts = 0
    max_attempts = T_MEMORY_STATE * 3

    while total + len(params_batch) < T_MEMORY_STATE and attempts < max_attempts:
        attempts += 1
        player = rng.choice(ctx.new_player_ids)
        subj = rng.choice(subjects_with_ri)
        ri = rng.choice(ctx.review_items[subj.name])
        seq = rng.choice(valid_seqs)

        key = (player, ri.item_id, seq)
        if key in used:
            continue
        used.add(key)

        n = _ms_name(f"{player}-{ri.item_id}", total + len(params_batch))
        stability = round(rng.uniform(0.1, 365.0), 9)
        difficulty = round(rng.uniform(0.1, 10.0), 9)
        state = rng.choices([0, 1, 2, 3], weights=[10, 20, 50, 20])[0]
        step = rng.randint(0, 3) if state in (1, 3) else 0
        next_rev = (NOW + timedelta(days=rng.randint(-30, 90))).date()
        last_rev = _rdt(rng, NOW - timedelta(days=90), NOW)
        item_bin = _uuid_to_bin(ri.item_id)

        params_batch.append((
            n, seq, player, subj.name, item_bin,
            ri.lesson or "",
            stability, difficulty, next_rev, state, step, last_rev,
            last_rev, last_rev, OWNER, OWNER, 0, 0,
        ))

        if len(params_batch) >= BATCH:
            _insert_ms_batch(params_batch)
            total += len(params_batch)
            print(f"  → batch: {len(params_batch):,d} (total {total:,d})")
            params_batch = []

    if params_batch:
        _insert_ms_batch(params_batch)
        total += len(params_batch)

    frappe.db.commit()
    print(f"  → {total:,d} Memory State rows")


def _insert_ms_batch(params_batch):
    """Batch-insert Memory State rows via raw SQL with parameterized values."""
    placeholders = ", ".join(
        ["(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"]
        * len(params_batch)
    )
    flat = []
    for row in params_batch:
        flat.extend(row)

    frappe.db.sql(
        f"INSERT INTO `tabMemora Memory State` "
        f"(name, season_seq, player, subject, item_id, lesson, "
        f"stability, difficulty, next_review, state, step, last_review, "
        f"creation, modified, modified_by, owner, docstatus, idx) "
        f"VALUES {placeholders}",
        tuple(flat),
    )


# ─── Wave 05: Practice Log ────────────────────────────────────────────────────

def wave_05_practice_log(ctx: Ctx, rng):
    if _count("tabMemora Practice Log") >= 8_000:
        print("Wave 05: Practice Logs — SKIPPED")
        return
    print("Wave 05: Generating Practice Logs...")

    if not ctx.all_review_items:
        print("  → no review items — skipping")
        return

    BATCH = 2_000
    total = 0
    used = set()
    params = []

    attempts = 0
    max_attempts = T_PRACTICE_LOG * 3

    while total + len(params) < T_PRACTICE_LOG and attempts < max_attempts:
        attempts += 1
        player = rng.choice(ctx.new_player_ids)
        ri = rng.choice(ctx.all_review_items)

        key = (player, ri.item_id)
        if key in used:
            continue
        used.add(key)

        first_seen = _rdt(rng, NOW - timedelta(days=90), NOW - timedelta(days=1))
        last_seen = _rdt(rng, first_seen, NOW)
        last_result = rng.choice(["Correct", "Incorrect"])
        att_count = rng.randint(1, 50)
        corr_count = rng.randint(0, att_count)

        params.append((
            player, ri.item_id,
            first_seen.strftime("%Y-%m-%d %H:%M:%S"),
            last_seen.strftime("%Y-%m-%d %H:%M:%S"),
            last_result, att_count, corr_count,
        ))

        if len(params) >= BATCH:
            _insert_pl_batch(params)
            total += len(params)
            print(f"  → batch: {len(params):,d} (total {total:,d})")
            params = []

    if params:
        _insert_pl_batch(params)
        total += len(params)

    frappe.db.commit()
    print(f"  → {total:,d} Practice Logs")


def _insert_pl_batch(params):
    placeholders = ", ".join(["(%s,%s,%s,%s,%s,%s,%s)"] * len(params))
    flat = []
    for row in params:
        flat.extend(row)
    frappe.db.sql(
        f"INSERT IGNORE INTO `tabMemora Practice Log` "
        f"(player_id, item_id, first_seen_at, last_seen_at, "
        f"last_result, attempt_count, correct_count) VALUES {placeholders}",
        tuple(flat),
    )


# ─── Wave 06: Structure Progress ──────────────────────────────────────────────

def wave_06_progress(ctx: Ctx, rng):
    if _count("tabMemora Structure Progress") >= 5_000:
        print("Wave 06: Structure Progress — SKIPPED")
        return
    print("Wave 06: Generating Structure Progress...")

    existing = {
        (r[0], r[1]) for r in frappe.db.sql(
            "SELECT player, subject FROM `tabMemora Structure Progress`"
        )
    }

    start = _reserve("PROG-", T_PROGRESS)
    fields = [
        "name", "player", "subject", "passed_lessons_bitset", "completion_percentage",
        "owner", "modified_by", "creation", "modified", "docstatus", "idx",
    ]

    rows = []
    used = set(existing)
    idx = 0

    for player in ctx.new_player_ids:
        if idx >= T_PROGRESS:
            break
        psubjs = ctx.player_subjects.get(player, [])
        for sn in psubjs:
            if idx >= T_PROGRESS:
                break
            key = (player, sn)
            if key in used:
                continue
            used.add(key)

            si = next((s for s in ctx.subjects_with_lessons if s.name == sn), None)
            total_l = si.lc if si else rng.randint(5, 30)

            if total_l > 0:
                passed = rng.randint(0, total_l)
                bits = [1] * passed + [0] * (total_l - passed)
                rng.shuffle(bits)
                bitstr = "".join(str(b) for b in bits)
                while len(bitstr) % 4:
                    bitstr += "0"
                hexs = format(int(bitstr, 2), "X") if "1" in bitstr else "0"
                pct = round(sum(bits) / total_l * 100, 2)
            else:
                hexs, pct = "0", 0.0

            creation = _rdt(rng, NOW - timedelta(days=90), NOW)
            rows.append((
                f"PROG-{start + idx:05d}", player, sn, hexs, pct,
                OWNER, OWNER, creation, creation, 0, 0,
            ))
            idx += 1

    if rows:
        frappe.db.bulk_insert("Memora Structure Progress", fields, rows, chunk_size=5_000)
        frappe.db.commit()
    print(f"  → {len(rows):,d} Structure Progress")


# ─── Wave 07: Challenge Attempts + Details ─────────────────────────────────────

def wave_07_challenges(ctx: Ctx, rng):
    if _count("tabMemora Challenge Attempt") >= 2_000:
        print("Wave 07: Challenges — SKIPPED")
        return
    print("Wave 07: Generating Challenge Attempts + Details...")

    subjs_with_topics = [sn for sn in ctx.topics if ctx.topics[sn]]
    if not subjs_with_topics:
        print("  → no topics — skipping")
        return

    a_start = _reserve("CHA-", T_ATTEMPTS)

    a_fields = [
        "name", "naming_series", "player", "topic", "subject", "season",
        "attempt_number", "total_questions", "correct_count", "score_pct",
        "passed", "time_spent", "xp_earned", "submitted_at",
        "owner", "modified_by", "creation", "modified", "docstatus", "idx",
    ]
    d_fields = [
        "name", "parent", "parentfield", "parenttype",
        "item_id", "correct", "time_spent", "chosen_answer",
        "owner", "modified_by", "creation", "modified", "docstatus", "idx",
    ]

    a_rows, d_rows = [], []
    for i in range(T_ATTEMPTS):
        aname = f"CHA-{a_start + i:05d}"
        player = rng.choice(ctx.new_player_ids)
        sn = rng.choice(subjs_with_topics)
        topic = rng.choice(ctx.topics[sn])
        season = ctx.player_seasons.get(player, ctx.seasons[0].name)

        total_q = rng.randint(3, 6)
        correct = rng.randint(0, total_q)
        pct = round(correct / total_q * 100, 2)
        passed = 1 if pct >= 60 else 0
        tspent = rng.randint(30, 300)
        xp = rng.randint(5, 50) if passed else 0
        ts = _rdt(rng, NOW - timedelta(days=90), NOW)

        a_rows.append((
            aname, "CHA-.#####", player, topic.name, sn, season,
            rng.randint(1, 5), total_q, correct, pct,
            passed, tspent, xp, ts,
            OWNER, OWNER, ts, ts, 0, 0,
        ))

        ris = ctx.review_items.get(sn, [])
        for j in range(total_q):
            dname = frappe.generate_hash("", 10)
            ri = rng.choice(ris) if ris else None
            d_rows.append((
                dname, aname, "details", "Memora Challenge Attempt",
                ri.item_id if ri else f"item-{i}-{j}",
                1 if j < correct else 0,
                rng.randint(5, 60), rng.randint(1, 4),
                OWNER, OWNER, ts, ts, 0, j + 1,
            ))

    frappe.db.bulk_insert("Memora Challenge Attempt", a_fields, a_rows, chunk_size=5_000)
    frappe.db.bulk_insert("Memora Challenge Attempt Detail", d_fields, d_rows, chunk_size=5_000)
    frappe.db.commit()
    print(f"  → {len(a_rows):,d} Attempts + {len(d_rows):,d} Details")


# ─── Wave 08: Live Challenges ─────────────────────────────────────────────────

def wave_08_live_challenges(ctx: Ctx, rng):
    current = _count("tabMemora Live Challenge Event")
    if current >= 12:
        print("Wave 08: Live Challenges — SKIPPED")
        return
    print("Wave 08: Generating Live Challenge Events...")

    ris = ctx.all_review_items or []
    plan_names = [p.name for p in ctx.plans[:10]]
    event_names = []
    base = NOW - timedelta(days=70)

    for i in range(T_LIVE_EVENTS):
        sched = (base + timedelta(days=7 * i)).replace(
            hour=17, minute=0, second=0, microsecond=0,
        )
        wait_dur = rng.randint(60, 300)
        exam_dur = rng.randint(3, 15)
        exam_start = sched + timedelta(seconds=wait_dur)
        exam_end = exam_start + timedelta(minutes=exam_dur)

        doc = frappe.new_doc("Memora Live Challenge Event")
        doc.event_name = f"تحدي أسبوعي {i + 1}"
        doc.scheduled_start = sched
        doc.waiting_room_duration = wait_dur
        doc.exam_duration = exam_dur
        doc.capacity = rng.randint(50, 200)
        doc.participation_xp = rng.randint(5, 20)
        doc.first_place_xp = rng.randint(50, 100)
        doc.second_place_xp = rng.randint(30, 50)
        doc.third_place_xp = rng.randint(10, 30)
        doc.default_xp = rng.randint(5, 10)

        for q in range(rng.randint(3, 5)):
            ri = rng.choice(ris) if ris else None
            doc.append("questions", {
                "question_text": f"سؤال {q + 1}",
                "option_a": "أ", "option_b": "ب",
                "option_c": "ج", "option_d": "د",
                "correct_answer": rng.choice(["A", "B", "C", "D"]),
            })
        for pn in rng.sample(plan_names, min(3, len(plan_names))):
            doc.append("eligible_plans", {"plan": pn})

        doc.flags.ignore_links = True
        doc.insert(ignore_permissions=True)

        # Set to Ended directly (bypass FSM)
        frappe.db.sql(
            "UPDATE `tabMemora Live Challenge Event` SET status='Ended', "
            "exam_start_ts=%s, exam_end_ts=%s, "
            "participant_count=%s, submitted_count=%s WHERE name=%s",
            (exam_start, exam_end, rng.randint(20, 100), rng.randint(10, 80), doc.name),
        )
        event_names.append(doc.name)

    frappe.db.commit()
    print(f"  → {len(event_names)} Events")

    # Participations
    if not event_names:
        return
    print("  → Generating Participations...")

    p_fields = [
        "name", "event", "player", "joined_at", "submitted_at",
        "score", "rank", "xp_awarded", "answers_json",
        "owner", "modified_by", "creation", "modified", "docstatus", "idx",
    ]
    p_rows = []
    used = set()

    for _ in range(T_LIVE_PARTS):
        ev = rng.choice(event_names)
        pl = rng.choice(ctx.new_player_ids)
        if (ev, pl) in used:
            continue
        used.add((ev, pl))

        joined = _rdt(rng, NOW - timedelta(days=70), NOW)
        submitted = joined + timedelta(minutes=rng.randint(1, 15))
        p_rows.append((
            frappe.generate_hash("", 10), ev, pl, joined, submitted,
            round(rng.uniform(0, 100), 2), rng.randint(1, 100),
            rng.randint(0, 50), "[]",
            OWNER, OWNER, joined, joined, 0, 0,
        ))

    frappe.db.bulk_insert("Memora Live Challenge Participation", p_fields, p_rows, chunk_size=5_000)
    frappe.db.commit()
    print(f"  → {len(p_rows):,d} Participations")


# ─── Wave 09: Subscriptions + Transactions ─────────────────────────────────────

def wave_09_subscriptions(ctx: Ctx, rng):
    if _count("tabMemora Player Subscription") >= 1_500:
        print("Wave 09: Subscriptions — SKIPPED")
        return
    print("Wave 09: Generating Subscriptions + Transactions...")

    existing_sub = {
        r[0] for r in frappe.db.sql("SELECT player FROM `tabMemora Player Subscription`")
    }
    avail = [p for p in ctx.new_player_ids if p not in existing_sub]
    n = min(T_SUBS, len(avail))
    players = rng.sample(avail, n)

    s_start = _reserve("PSUB-", n)
    t_start = _reserve("TRX-", n)

    snames = [s.name for s in ctx.subjects_with_lessons]

    s_fields = [
        "name", "player", "access_key", "expires_at", "is_active",
        "owner", "modified_by", "creation", "modified", "docstatus", "idx",
    ]
    t_fields = [
        "name", "player", "payment_method", "status", "transaction_id", "amount_paid",
        "owner", "modified_by", "creation", "modified", "docstatus", "idx",
    ]

    s_rows, t_rows = [], []
    for i, pid in enumerate(players):
        sname = f"PSUB-{s_start + i:05d}"
        tname = f"TRX-{t_start + i:05d}"

        subj = rng.choice(snames)
        creation = _rdt(rng, NOW - timedelta(days=180), NOW)
        exp = (creation + timedelta(days=rng.choice([30, 90, 180, 365]))).date()
        active = 1 if exp >= date.today() else 0

        s_rows.append((
            sname, pid, f"SUB-{subj}", exp, active,
            OWNER, OWNER, creation, creation, 0, 0,
        ))

        pm = _wchoice(rng, D_PAYMENT_METHOD)
        st = _wchoice(rng, D_TXN_STATUS)
        amt = rng.choice([5, 10, 15, 20, 25, 30, 50]) if pm != "Scholarship" else 0
        t_rows.append((
            tname, pid, pm, st, f"TXN-{rng.randint(100_000, 999_999)}", amt,
            OWNER, OWNER, creation, creation, 0, 0,
        ))

    frappe.db.bulk_insert("Memora Player Subscription", s_fields, s_rows, chunk_size=5_000)
    frappe.db.bulk_insert("Memora Subscription Transaction", t_fields, t_rows, chunk_size=5_000)
    frappe.db.commit()
    print(f"  → {len(s_rows):,d} Subscriptions + {len(t_rows):,d} Transactions")


# ─── Wave 10: Vouchers ────────────────────────────────────────────────────────

def wave_10_vouchers(ctx: Ctx, rng):
    if _count("tabMemora Voucher Batch") >= 600:
        print("Wave 10: Vouchers — SKIPPED")
        return
    print("Wave 10: Generating Voucher Pipeline...")

    # --- Batches (30) via ORM ---
    batch_names = []
    batch_purposes = []

    for i in range(T_VOUCHER_BATCHES):
        purpose = _wchoice(rng, D_VOUCHER_PURPOSE)
        fv = rng.choice([5, 10, 15, 20, 25]) if purpose == "Sale" else 0

        doc = frappe.new_doc("Memora Voucher Batch")
        doc.batch_name = f"Analytics Batch {i + 1:03d}"
        doc.batch_purpose = purpose
        doc.quantity = rng.randint(50, 500)
        doc.pin_length = "12"
        doc.face_value = fv

        grants = rng.sample(
            ctx.product_grants, min(rng.randint(1, 3), len(ctx.product_grants)),
        )
        for g in grants:
            doc.append("batch_grants", {"product_grant": g.name})

        doc.flags.ignore_links = True
        doc.insert(ignore_permissions=True)
        frappe.db.set_value(
            "Memora Voucher Batch", doc.name, "status", "Active",
            update_modified=False,
        )
        batch_names.append(doc.name)
        batch_purposes.append(purpose)

    frappe.db.commit()
    print(f"  → {len(batch_names)} Batches")

    # --- Cards (5,000) via bulk_insert ---
    serial_row = frappe.db.sql(
        "SELECT current FROM tabSeries WHERE name='VCH-SERIAL' FOR UPDATE",
        as_dict=True,
    )
    ser_start = (serial_row[0]["current"] + 1) if serial_row else 1
    frappe.db.sql(
        "UPDATE tabSeries SET current=%s WHERE name='VCH-SERIAL'",
        (ser_start + T_VOUCHER_CARDS - 1,),
    )

    c_fields = [
        "name", "serial_no", "pin_hmac", "batch", "batch_purpose", "status",
        "owner", "modified_by", "creation", "modified", "docstatus", "idx",
    ]

    c_rows = []
    cards_by_batch: dict[str, list[str]] = {bn: [] for bn in batch_names}
    per_batch = T_VOUCHER_CARDS // len(batch_names)

    for bi, bn in enumerate(batch_names):
        n_cards = per_batch if bi < len(batch_names) - 1 else \
                  T_VOUCHER_CARDS - per_batch * (len(batch_names) - 1)
        for c in range(n_cards):
            sn = f"VCH-{ser_start + len(c_rows):06d}"
            pin = _gen_pin(rng)
            hmac_val = _compute_hmac(pin, ctx.hmac_secret)
            status = rng.choices(
                ["Available", "Allocated", "Redeemed"], weights=[60, 25, 15],
            )[0]
            creation = _rdt(rng, NOW - timedelta(days=90), NOW)

            c_rows.append((
                sn, sn, hmac_val, bn, batch_purposes[bi], status,
                OWNER, OWNER, creation, creation, 0, 0,
            ))
            cards_by_batch[bn].append(sn)

    frappe.db.bulk_insert("Memora Voucher Card", c_fields, c_rows, chunk_size=5_000)
    frappe.db.commit()
    print(f"  → {len(c_rows):,d} Cards")

    # --- Allocations (300) via ORM ---
    if ctx.customers:
        alloc_n = 0
        for _ in range(T_VOUCHER_ALLOCS):
            bn = rng.choice(batch_names)
            cust = rng.choice(ctx.customers)

            doc = frappe.new_doc("Memora Voucher Allocation")
            doc.allocation_type = "Allocate"
            doc.batch = bn
            doc.customer = cust.name
            doc.sale_model = rng.choice(["Prepaid", "Consignment"])
            doc.allocation_date = (NOW - timedelta(days=rng.randint(1, 60))).date()
            doc.status = "Draft"

            bcards = cards_by_batch.get(bn, [])
            nc = min(rng.randint(1, 5), len(bcards))
            if nc == 0:
                continue
            chosen = rng.sample(bcards, nc)
            for cn in chosen:
                doc.append("allocation_cards", {
                    "voucher_card": cn, "serial_no": cn, "card_status": "Available",
                })

            doc.flags.ignore_links = True
            try:
                doc.insert(ignore_permissions=True)
                frappe.db.set_value(
                    "Memora Voucher Allocation", doc.name, "status", "Completed",
                    update_modified=False,
                )
                alloc_n += 1
            except Exception:
                frappe.db.rollback()
                frappe.db.commit()

            if alloc_n % 50 == 0 and alloc_n > 0:
                frappe.db.commit()

        frappe.db.commit()
        print(f"  → {alloc_n} Allocations")

    # --- Redemption Logs (2,000) via bulk_insert ---
    r_start = _reserve("VRLOG-", T_VOUCHER_RDMPTS)
    r_fields = [
        "name", "player", "pin_masked", "card", "batch",
        "status", "failure_reason", "ip_address", "timestamp",
        "owner", "modified_by", "creation", "modified", "docstatus", "idx",
    ]

    r_rows = []
    for i in range(T_VOUCHER_RDMPTS):
        rname = f"VRLOG-{r_start + i:05d}"
        player = rng.choice(ctx.new_player_ids)
        status = _wchoice(rng, D_REDEMPTION_STATUS)
        card = c_rows[rng.randint(0, len(c_rows) - 1)][0] if c_rows else None
        batch = rng.choice(batch_names)
        masked = f"****{''.join(rng.choices('ABCD2345', k=4))}"
        fail = None if status == "Success" else status
        ip = f"192.168.{rng.randint(1, 10)}.{rng.randint(1, 254)}"
        ts = _rdt(rng, NOW - timedelta(days=60), NOW)

        r_rows.append((
            rname, player, masked, card, batch,
            status, fail, ip, ts,
            OWNER, OWNER, ts, ts, 0, 0,
        ))

    frappe.db.bulk_insert("Memora Voucher Redemption Log", r_fields, r_rows, chunk_size=5_000)
    frappe.db.commit()
    print(f"  → {len(r_rows):,d} Redemption Logs")


# ─── Wave 11: Plan History ────────────────────────────────────────────────────

def wave_11_plan_history(ctx: Ctx, rng):
    if _count("tabMemora Player Plan History") >= 500:
        print("Wave 11: Plan History — SKIPPED")
        return
    print("Wave 11: Generating Plan History...")

    start = _reserve("PLHIST-", T_PLAN_HIST)
    fields = [
        "name", "player", "trigger_reason", "changed_at",
        "previous_plan", "previous_grade", "previous_major", "previous_season",
        "new_plan", "new_grade", "new_major", "new_season",
        "snapshot_total_xp", "snapshot_current_streak",
        "snapshot_total_lessons", "snapshot_total_time_min", "snapshot_memory_states",
        "owner", "modified_by", "creation", "modified", "docstatus", "idx",
    ]

    rows = []
    for i in range(T_PLAN_HIST):
        hname = f"PLHIST-{start + i:05d}"
        player = rng.choice(ctx.new_player_ids)
        reason = _wchoice(rng, D_PLAN_REASON)
        changed = _rdt(rng, NOW - timedelta(days=180), NOW)

        prev = rng.choice(ctx.plans)
        new = rng.choice(ctx.plans)

        rows.append((
            hname, player, reason, changed,
            prev.name, prev.grade, prev.major, prev.season,
            new.name, new.grade, new.major, new.season,
            rng.randint(0, 10_000), rng.randint(0, 50),
            rng.randint(0, 200), rng.randint(0, 1_000), rng.randint(0, 500),
            OWNER, OWNER, changed, changed, 0, 0,
        ))

    frappe.db.bulk_insert("Memora Player Plan History", fields, rows, chunk_size=5_000)
    frappe.db.commit()
    print(f"  → {len(rows):,d} Plan History")


# ─── Wave 12: Content Reports ─────────────────────────────────────────────────

def wave_12_reports(ctx: Ctx, rng):
    if _count("tabMemora Content Report") >= 200:
        print("Wave 12: Content Reports — SKIPPED")
        return
    print("Wave 12: Generating Content Reports...")

    start = _reserve("RPT-", T_REPORTS)
    fields = [
        "name", "player", "subject", "lesson", "report_type", "description", "status",
        "owner", "modified_by", "creation", "modified", "docstatus", "idx",
    ]

    snames = [s.name for s in ctx.subjects_with_lessons]
    rows = []
    for i in range(T_REPORTS):
        rname = f"RPT-{start + i:05d}"
        player = rng.choice(ctx.new_player_ids)
        sn = rng.choice(snames)
        ls = ctx.lessons.get(sn, [])
        lesson = rng.choice(ls) if ls else None

        creation = _rdt(rng, NOW - timedelta(days=60), NOW)
        rows.append((
            rname, player, sn, lesson,
            _wchoice(rng, D_REPORT_TYPE), rng.choice(REPORT_DESC),
            _wchoice(rng, D_REPORT_STATUS),
            OWNER, OWNER, creation, creation, 0, 0,
        ))

    frappe.db.bulk_insert("Memora Content Report", fields, rows, chunk_size=5_000)
    frappe.db.commit()
    print(f"  → {len(rows):,d} Content Reports")


# ─── Verification ──────────────────────────────────────────────────────────────

def verify():
    """Print final counts and distribution checks."""
    print(f"\n{'=' * 60}")
    print("VERIFICATION")
    print(f"{'=' * 60}\n")

    targets = [
        ("Player Profile", "tabMemora Player Profile", 2_900),
        ("Player Wallet", "tabMemora Player Wallet", 2_900),
        ("Interaction Log", "tabMemora Interaction Log", 95_000),
        ("Memory State", "tabMemora Memory State", 10_000),
        ("Practice Log", "tabMemora Practice Log", 5_000),
        ("Structure Progress", "tabMemora Structure Progress", 3_000),
        ("Challenge Attempt", "tabMemora Challenge Attempt", 2_000),
        ("Challenge Detail", "tabMemora Challenge Attempt Detail", 5_000),
        ("Live Event", "tabMemora Live Challenge Event", 10),
        ("Live Participation", "tabMemora Live Challenge Participation", 400),
        ("Subscription", "tabMemora Player Subscription", 1_400),
        ("Subscription Txn", "tabMemora Subscription Transaction", 1_400),
        ("Voucher Batch", "tabMemora Voucher Batch", 590),
        ("Voucher Card", "tabMemora Voucher Card", 9_000),
        ("Voucher Allocation", "tabMemora Voucher Allocation", 400),
        ("Voucher Redemption", "tabMemora Voucher Redemption Log", 2_000),
        ("Plan History", "tabMemora Player Plan History", 500),
        ("Content Report", "tabMemora Content Report", 200),
    ]

    print(f"{'Table':30s} {'Actual':>10s} {'Min':>10s} {'OK?':>6s}")
    print("─" * 60)
    for label, tbl, mn in targets:
        cnt = _count(tbl)
        ok = "OK" if cnt >= mn else "LOW"
        print(f"{label:30s} {cnt:>10,d} {mn:>10,d} {ok:>6s}")

    # Distributions
    print(f"\n{'─' * 60}")
    print("DISTRIBUTIONS:\n")

    print("Interaction Log — event_type:")
    for r in frappe.db.sql(
        "SELECT event_type, COUNT(*) c FROM `tabMemora Interaction Log` "
        "GROUP BY event_type ORDER BY c DESC"
    ):
        print(f"  {r[0]:15s}: {r[1]:>8,d}")

    print("\nMemory State — state:")
    sn = {0: "New", 1: "Learning", 2: "Review", 3: "Relearning"}
    for r in frappe.db.sql(
        "SELECT state, COUNT(*) c FROM `tabMemora Memory State` GROUP BY state ORDER BY state"
    ):
        print(f"  {sn.get(r[0], str(r[0])):15s}: {r[1]:>8,d}")

    print("\nVoucher Card — status:")
    for r in frappe.db.sql(
        "SELECT status, COUNT(*) c FROM `tabMemora Voucher Card` "
        "GROUP BY status ORDER BY c DESC"
    ):
        print(f"  {r[0]:15s}: {r[1]:>8,d}")

    print("\nSubscription Txn — status:")
    for r in frappe.db.sql(
        "SELECT status, COUNT(*) c FROM `tabMemora Subscription Transaction` "
        "GROUP BY status ORDER BY c DESC"
    ):
        print(f"  {r[0]:15s}: {r[1]:>8,d}")

    print(f"\n{'=' * 60}")
    print("SEED COMPLETE")
    print(f"{'=' * 60}\n")


# ─── Entry Point ───────────────────────────────────────────────────────────────

def run():
    """Inspect → 12 waves → verify."""
    global NOW
    NOW = datetime.now()
    rng = random.Random(SEED)
    ctx = inspect()

    for fn in [
        wave_01_players, wave_02_wallets, wave_03_interactions,
        wave_04_memory_state, wave_05_practice_log, wave_06_progress,
        wave_07_challenges, wave_08_live_challenges, wave_09_subscriptions,
        wave_10_vouchers, wave_11_plan_history, wave_12_reports,
    ]:
        try:
            fn(ctx, rng)
        except Exception as e:
            frappe.db.rollback()
            print(f"\nERROR in {fn.__name__}: {e}")
            import traceback
            traceback.print_exc()
            raise

    verify()
