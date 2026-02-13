# Project Research Summary

**Project:** Memora v3.0 - Voucher Management System
**Domain:** Prepaid physical voucher card distribution for gamified educational content
**Researched:** 2026-02-13
**Confidence:** HIGH

## Executive Summary

The voucher system is architecturally a **new entry point into the existing Phase 23 access-grant pipeline**. Physical cards with scratch-off PINs are distributed through 50-100 libraries in Jordan. Students redeem PINs in the mobile app to unlock educational content. The critical insight from research is that voucher redemption creates a `Subscription Transaction` with `payment_method="Voucher"` and `status="Completed"`, which triggers the existing `_handle_approval()` flow. No new access-grant logic is needed. The voucher system reuses the existing Frappe-as-source-of-truth, Redis-as-hot-cache pattern across all 32+ phases.

The recommended approach follows the domain's established patterns: batch generation (telecom VMS pattern), encrypted PIN export for physical card printing, library-level allocation tracking with commission, and atomic redemption with database-level locking. The system requires only ONE new pip dependency (`cryptography>=44.0.0` for Fernet encryption of export files). All other capabilities use Python stdlib (hmac, secrets, csv) or existing Frappe/FastAPI patterns (bulk_insert, background jobs, SELECT FOR UPDATE, rate limiting, FrappeClient HTTP bridge).

The primary risks are **concurrent redemption race conditions** (two requests redeem the same card simultaneously without SELECT FOR UPDATE locking), **HMAC timing attacks** (using `==` instead of `hmac.compare_digest()` for PIN verification), and **batch generation overwhelming Frappe workers** (generating 10K+ cards synchronously in web request context). Prevention requires pessimistic database locking, constant-time cryptographic comparison, background job processing with chunking, and strict Decimal arithmetic for all financial calculations. The research has HIGH confidence because it's based on direct codebase analysis of 17+ existing integration patterns, verified Frappe framework internals, and cross-referenced security literature.

## Key Findings

### Recommended Stack

**Confidence:** HIGH (verified against existing codebase and Frappe v15 internals)

The voucher system extends the existing stack with minimal new dependencies. The core finding is that Memora already has everything needed except file encryption.

**Core technologies:**
- **cryptography (Fernet) >= 44.0.0**: AES-encrypted PIN export files — pyca-maintained, misuse-resistant API, handles IV generation/HMAC verification automatically. This is the ONLY new pip dependency.
- **hmac + hashlib (stdlib)**: HMAC-SHA256 for PIN storage — server-side secret makes DB breach non-catastrophic, constant-time comparison via `hmac.compare_digest()` prevents timing attacks
- **secrets (stdlib)**: Cryptographic random PIN generation — uses os.urandom() CSPRNG, explicitly recommended over `random` module for security-sensitive tokens
- **frappe.db.bulk_insert**: Batch document creation — bypasses ORM overhead for 5K+ cards, 10x faster than individual inserts, requires background job with chunking
- **SELECT FOR UPDATE**: Atomic redemption locking — prevents double-redemption race condition via row-level pessimistic lock during status check-and-update

**Key integration points:**
- Redemption reuses existing `Subscription Transaction -> Player Subscription -> Redis SADD` pipeline (Phase 23)
- Rate limiting reuses existing `RateLimiter` class with Lua script (Phase 8)
- FastAPI endpoints reuse existing `FrappeClient` HTTP bridge and JWT auth (Phase 11)
- Background job processing reuses existing `frappe.enqueue()` infrastructure

**Why NOT other technologies:**
- NOT bcrypt for PINs (12-char system-generated codes with high entropy + server-side secret don't need intentional slowness)
- NOT pandas for CSV (stdlib csv module sufficient for 2-3 columns)
- NOT raw AES-GCM (Fernet provides high-level API that prevents IV reuse and padding bugs)

### Expected Features

**Confidence:** HIGH (well-established domain, cross-referenced with telecom VMS and education access code systems)

The voucher domain has two major patterns: **telecom recharge card systems** (batch generation, dealer allocation, commission, consignment tracking) and **education access code systems** (one-time activation, content unlock, "already redeemed" error handling). Memora vouchers combine both patterns.

**Must have (table stakes):**
- **Batch creation with configurable size** (1K-10K cards) — fundamental unit of work
- **CSPRNG PIN generation + HMAC-SHA256 storage** — predictable PINs = catastrophic breach
- **State machine: Available → Allocated → Redeemed/Void/Expired** — enforced in code, not just UI
- **One-time atomic redemption** — double-redemption is the #1 voucher fraud vector (HackerOne report #759247)
- **Redemption rate limiting** (5/hour per player, 20/hour per IP) — defense-in-depth against brute force
- **Immutable audit log** — compliance and dispute resolution require append-only redemption records
- **CSV export for print vendor** — PINs must be exported (plaintext, encrypted) for physical card printing
- **Preview before confirm** — student MUST see what they will unlock BEFORE PIN is consumed (Pearson/Elsevier pattern)
- **Instant content unlock** — voucher redemption bypasses admin approval, creates Completed transaction immediately
- **Commission tracking** — libraries need to know their margin or won't participate

**Should have (competitive):**
- **QR code on physical card** — scan instead of typing 12 digits, reduces typos for Arabic-speaking students
- **Graceful "already redeemed" response** — show redemption date without revealing WHO (reduces support tickets)
- **"Already owned" guard** — if student already has the content, reject redemption WITHOUT consuming card
- **Automatic commission ledger** — each redemption auto-records library's commission, eliminates monthly reconciliation
- **Return/unallocate flow** — library returns unsold cards back to central inventory (essential for consignment model)

**Defer (v2+):**
- **Library self-service portal** — libraries view inventory, track redemptions, see commission (high complexity, Phase 3+ feature)
- **Digital-only vouchers (SMS/email)** — changes entire distribution model, requires SMS gateway
- **Partial redemption** — cards map to specific products, not monetary value (adds confusing UX)

**Anti-features (explicitly NOT build):**
- Card-to-card transfer (creates secondary market, increases fraud surface)
- Self-service refund/reversal (creates abuse vector: redeem, study, reverse, give card to friend)
- Complex per-library pricing tiers (over-engineering for 50-100 libraries)
- Blockchain/NFT vouchers (adds zero value, infinitely harder to maintain)

### Architecture Approach

**Confidence:** HIGH (based on direct codebase analysis of 17+ existing patterns)

The voucher system spans both Frappe admin (batch creation, allocation, invoicing) and FastAPI sidecar (student-facing preview/redeem endpoints), connected via the existing FrappeClient HTTP bridge. The architecture follows the established pattern: Frappe holds transactional logic with direct MariaDB access, FastAPI is auth/rate-limit/format proxy.

**Major components:**

1. **Voucher Batch (Frappe DocType)** — admin creates batches, defines grant links to Product Grant, triggers background PIN generation. Contains child table of Batch Grants (maps to existing Memora Product Grant).

2. **Voucher Card (Frappe DocType, child of Batch)** — stores individual PIN hash with status lifecycle. Fields: pin_hash (HMAC-SHA256), pin_last4 (admin identification), status (state machine), allocated_to (library link), redeemed_by/redeemed_at (audit).

3. **Voucher Allocation (Frappe DocType)** — tracks which Customer/library received cards, links to Sales Invoice for prepaid model, stores encrypted export file. Contains child table of Allocation Cards (junction to individual cards).

4. **Redemption API (memora_admin/api/voucher.py)** — Frappe whitelisted method with SELECT FOR UPDATE locking. Validates PIN → marks card Redeemed → creates Subscription Transaction (status=Completed) → triggers existing _handle_approval() pipeline → creates Player Subscriptions → Redis SADD via access_sync.py.

5. **FastAPI Proxy (fastapi_app/api/v1/endpoints/voucher.py)** — JWT auth → rate limit → FrappeClient.call() delegation. POST /voucher/preview (read-only, no state change) and POST /voucher/redeem (atomic transaction).

6. **PIN Generation Utility (memora_admin/utils/pin_generator.py)** — secure PIN generation via secrets.choice(), HMAC signing, batch generation with chunking, encrypted export file creation using Fernet.

**Critical architectural decisions:**
- **Core redeem logic lives in Frappe, not FastAPI** — requires SELECT FOR UPDATE (row-level locking) for atomic DB operations, FastAPI has indirect DB connection
- **Subscription Transaction with status=Completed skips approval** — voucher redemption is pre-paid, triggers existing _handle_approval() which creates Player Subscriptions and syncs to Redis
- **HMAC PIN storage (not plaintext, not bcrypt)** — deterministic hash allows WHERE clause lookup, server-side secret provides defense layer even on DB breach
- **No new Redis keys for voucher state** — cards are NOT hot data (max 1 lookup per student per voucher), SELECT FOR UPDATE in MariaDB provides atomicity that Redis cannot
- **Encrypted file export with Fernet** — PIN export files contain plaintext for printing, stored in private/files/ with additional encryption, key from site_config.json

### Critical Pitfalls

**Confidence:** HIGH (verified against existing codebase, Frappe internals, and security literature)

1. **Concurrent redemption race condition (double-spend)** — two requests redeem same card simultaneously without database locking, both create Subscription Transactions. **Avoid:** Use `frappe.db.sql("SELECT ... FOR UPDATE")` to acquire exclusive row lock during redemption, check-then-update pattern is NOT atomic without locking. Add UNIQUE index on pin_hash column.

2. **HMAC timing attack on PIN verification** — using `==` operator to compare HMAC digests leaks information about matching bytes via response time. **Avoid:** Always use `hmac.compare_digest()` for constant-time comparison. Never use `==` for any hash/HMAC values. Attacker can reconstruct HMAC byte-by-byte with ~65K requests.

3. **frappe.db.commit() silently ignored inside doc_events** — the existing `_handle_approval()` calls commit explicitly but Frappe DISABLES manual commit/rollback during doc_events to preserve atomicity. **Avoid:** Create Subscription Transaction with status=Completed directly (not Pending Approval then update), let Frappe's auto-commit handle transaction boundary. Accept that Redis failure during on_subscription_change rolls back entire transaction (card stays Active, player retries).

4. **Batch generation overwhelming Frappe workers** — generating 10K cards synchronously in web request takes 200-500 seconds (well beyond 120s timeout), naming series contention, memory accumulation. **Avoid:** Always use frappe.enqueue() with queue="long" and timeout=1800. Chunk into batches of 500, commit after each chunk. Use bulk_insert or raw SQL INSERT for maximum performance. Track progress via frappe.publish_realtime().

5. **Float-based financial calculations** — commission percentages multiplied using Python float arithmetic, rounding errors accumulate over thousands of cards into visible discrepancies. **Avoid:** Use `decimal.Decimal` for ALL financial math, initialize from strings (not floats), quantize at each step with ROUND_HALF_UP. Store commission rate as string in DocType (Data field, not Float/Percent).

**Secondary pitfalls (moderate severity):**
- Missing `related_grant` on Subscription Transaction breaks _handle_approval()
- State machine enforcement gaps (allow impossible transitions like Redeemed → Active)
- Whitelisted method security (missing allow_guest=False, no role restriction, parameter injection)
- Export file key management (key stored alongside data, no rotation plan, IV reuse in AES)
- Rate limiting bypass via distributed attack (IP-based limiting ineffective against botnets)

## Implications for Roadmap

Based on research, suggested phase structure for v3.0:

### Phase 1: DocType Foundation
**Rationale:** All DocTypes must exist before any API code can reference them. Purely additive to existing 32 DocTypes, no dependencies on other phases.

**Delivers:**
- 6 new DocTypes: Voucher Batch, Voucher Card, Voucher Batch Grant, Voucher Allocation, Voucher Allocation Card, Voucher Redemption Log
- Custom fields on Customer DocType for library metadata
- Database migrations via bench migrate

**Addresses:** Foundation for all table stakes features (TS-1, TS-4, TS-5, TS-22)

**Avoids:** Pitfall #7 (state machine enforcement) by defining valid transitions in DocType class, Pitfall #4 (float financial fields) by using Data fieldtype for commission rates

**Research flag:** SKIP — standard Frappe DocType creation, well-documented patterns

### Phase 2: PIN Generation & Batch Management
**Rationale:** Admin must create batches and generate PINs before any allocation or redemption can happen. Builds on Phase 1 DocTypes.

**Delivers:**
- PIN generation utility (secrets.choice(), HMAC signing)
- Batch.generate_pins() background job with chunking
- HMAC secret configuration in site_config.json
- Batch lifecycle management (Draft → Generated → Allocated → Expired)

**Addresses:** Table stakes TS-1, TS-2, TS-3 (batch creation, CSPRNG PIN generation, HMAC storage)

**Uses:** cryptography (Fernet), hmac (stdlib), secrets (stdlib), frappe.enqueue(), frappe.db.bulk_insert

**Avoids:** Pitfall #2 (timing attack) via hmac.compare_digest(), Pitfall #4 (batch generation timeout) via background job with chunking, Pitfall #5 (float math) via Decimal for denomination

**Research flag:** SKIP — patterns exist in codebase (bulk_insert in memora_lesson.py, background jobs in existing services)

### Phase 3: Allocation & Encrypted Export
**Rationale:** Libraries need to receive cards before students can redeem. Export needed for physical card printing. Depends on Phase 2 (batches with generated PINs).

**Delivers:**
- Allocation controller with SELECT FOR UPDATE allocation logic
- Fernet-encrypted file generation for print vendor
- Frappe File attachment to Allocation doc
- Optional Sales Invoice creation

**Addresses:** Table stakes TS-5, TS-12 (batch allocation, CSV export), TS-23 (prepaid sale model)

**Uses:** Fernet encryption, frappe.utils.file_manager.save_file, is_private=1 for export files

**Avoids:** Pitfall #9 (export key management) by storing per-batch keys in Password field, plaintext PINs never persist after export

**Research flag:** SKIP — Fernet is high-level API, file_manager is standard Frappe pattern

### Phase 4: Core Redemption API (Frappe Side)
**Rationale:** Transactional core must be built and testable via Frappe API before adding FastAPI proxy layer. This is the CRITICAL PATH.

**Delivers:**
- memora_admin/api/voucher.py with preview_voucher() and redeem_voucher()
- SELECT FOR UPDATE race-condition-safe redemption
- Subscription Transaction creation (status=Completed, payment_method=Voucher)
- Voucher Redemption Log creation
- Integration with existing _handle_approval() pipeline

**Addresses:** Table stakes TS-6, TS-13, TS-16 (atomic redemption, PIN entry, instant unlock), differentiators D-2, D-3 (graceful error messages, already-owned guard)

**Avoids:** Pitfall #1 (double-redemption race) via SELECT FOR UPDATE, Pitfall #3 (commit ignored) by creating Completed transaction directly, Pitfall #8 (whitelisted security) via frappe.only_for()

**Research flag:** DEEP DIVE — this phase touches the existing subscription pipeline (memora_subscription_transaction.py lines 36-65), needs integration testing to verify _handle_approval() behavior when status=Completed on insert vs update

### Phase 5: FastAPI Proxy Layer
**Rationale:** Student-facing API must go through FastAPI for JWT auth and rate limiting. Built last because it depends on all previous phases.

**Delivers:**
- fastapi_app/models/voucher.py (Pydantic schemas)
- fastapi_app/services/voucher.py (VoucherService with rate limiting)
- fastapi_app/api/v1/endpoints/voucher.py (POST /voucher/preview, POST /voucher/redeem)
- Dependency injection updates (deps.py, router.py)

**Addresses:** Table stakes TS-10, TS-14, TS-15 (rate limiting, preview before confirm, specific error messages)

**Uses:** Existing RateLimiter with new key prefixes, FrappeClient HTTP bridge, CurrentUser JWT auth

**Avoids:** Pitfall #10 (rate limiting bypass) via dual-key (player + IP) rate limits, Pitfall #8 (parameter injection) by always using JWT sub claim

**Research flag:** SKIP — reuses existing FastAPI patterns (RateLimiter in rate_limit.py, FrappeClient in deps.py)

### Phase 6: Reporting & Admin Features
**Rationale:** Operational quality features after core flow works. No blocking dependencies.

**Delivers:**
- Batch performance report (generated/allocated/redeemed/void counts)
- Sales report (revenue by batch, by library, by date range)
- Consignment reconciliation report
- Security audit report (redemption log with failed attempts)
- Batch list view with status summary
- Individual card lookup by serial

**Addresses:** Table stakes TS-18, TS-19, TS-21 (batch list view, card lookup, reports)

**Avoids:** Pitfall #15 (Frappe Desk UI performance) via database indexes on (card_batch, status) composite

**Research flag:** SKIP — standard Frappe Report Builder, no complex queries

### Phase Ordering Rationale

```
Phase 1 (DocTypes) → Phase 2 (PIN Gen) → Phase 3 (Allocation) → Phase 4 (Redeem) → Phase 5 (FastAPI) → Phase 6 (Reports)
     |                     |                     |                    |                   |                  |
  Foundation           Admin creates         Admin distributes    Testable via       Student-facing    Operational
  (tables exist)       batches with PINs     cards to libraries   Frappe directly    endpoints live    quality
```

**Why this order:**
- Phase 1 must come first (code cannot reference DocTypes that don't exist)
- Phase 2 before Phase 3 (cannot allocate cards that don't exist)
- Phase 3 before Phase 4 (cannot redeem cards that haven't been distributed)
- Phase 4 before Phase 5 (FastAPI proxies to Frappe, Frappe must work standalone first)
- Phase 6 can run parallel to Phase 5 (reporting has no API dependencies)

**Dependency highlights:**
- Phase 4 depends on existing Phase 23 pipeline (Subscription Transaction → Player Subscription → Redis SADD via access_sync.py)
- Phase 5 depends on existing FastAPI infrastructure (JWT auth from Phase 11, RateLimiter from Phase 8, FrappeClient from Phase 15)
- All phases depend on existing Frappe foundation (DocType system, whitelisted methods, background jobs)

**How this avoids pitfalls:**
- Building Frappe API first (Phase 4) before FastAPI proxy (Phase 5) allows testing atomic redemption in isolation, catching race conditions early
- Background job generation (Phase 2) before allocation (Phase 3) ensures batch creation won't timeout when libraries request large quantities
- Financial calculations in Decimal from Phase 2 onward prevents accumulation of float errors across phases

### Research Flags

Phases likely needing deeper research during planning:

- **Phase 4 (Core Redemption API):** Complex integration with existing subscription pipeline. The existing _handle_approval() method (memora_subscription_transaction.py:36-65) assumes certain fields and behaviors. Need integration test to verify: (1) creating transaction with status=Completed on insert fires on_update correctly, (2) missing related_grant is caught gracefully, (3) Redis failure during on_subscription_change rolls back card state update, (4) Player Subscription expiry derives from batch validity not player's season. **Recommendation:** Run /gsd:research-phase before implementing Phase 4 to trace the full hook chain.

- **Phase 3 (Encrypted Export):** Fernet encryption is straightforward, but the export file lifecycle needs clarification: when to generate (at batch creation or allocation?), when to delete (immediate after download or 7-day TTL?), how to handle re-export requests. **Recommendation:** Skip research-phase but add detailed spec in requirements for export workflow edge cases.

Phases with standard patterns (skip research-phase):

- **Phase 1:** DocType creation follows established Frappe patterns, codebase has 32+ DocTypes as templates
- **Phase 2:** Background job + bulk_insert patterns exist in codebase (memora_lesson.py line 33 for SELECT FOR UPDATE, existing background jobs for quiz generation)
- **Phase 5:** FastAPI endpoint creation, rate limiting, JWT auth all follow existing patterns from sessions.py, otp.py, purchase.py
- **Phase 6:** Frappe Report Builder, standard list views, no novel patterns

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | Minimal new dependencies (only cryptography), verified against Frappe v15 source and existing codebase patterns. HMAC, secrets, bulk_insert all confirmed in docs and source. |
| Features | HIGH | Well-established domain (telecom VMS + education access codes), cross-referenced 6+ VMS products (Estel, 6D Tech, Seamless) and 3+ education platforms (Pearson, Elsevier, VitalSource). Feature set aligns with industry norms. |
| Architecture | HIGH | Based on direct codebase analysis of 17+ files. Integration points verified: Subscription Transaction payment_method field already has "Voucher" option, access_sync.py hook exists, FrappeClient and RateLimiter patterns proven. Only uncertainty is _handle_approval() behavior with status=Completed on insert (needs integration test). |
| Pitfalls | HIGH | Race condition verified from HackerOne report #759247 and security literature. Timing attack verified from Python CVE-2022-48566. frappe.db.commit() behavior verified from Frappe source code (database.py). Float precision issues verified from real-world Python financial bugs. Batch generation timeout verified from Frappe forum discussions. |

**Overall confidence:** HIGH

The voucher system follows established patterns in both the domain (telecom VMS, education access codes) and the existing Memora codebase. The only novel aspect is the batch generation scale (5K-10K cards), which is addressed via background jobs with chunking. The redemption integration with Phase 23's subscription pipeline is well-understood from code inspection, though an integration test will confirm hook firing behavior.

### Gaps to Address

**Gap 1: _handle_approval() commit behavior**
- **Issue:** The existing method calls frappe.db.commit() explicitly but this is silently ignored during doc_events. The voucher redemption pathway creates a transaction with status=Completed, which should trigger on_update → _handle_approval(). Need to verify: (a) does on_update fire on insert when status=Completed, or only on subsequent updates? (b) if Redis is down during on_subscription_change, does the entire transaction roll back including the card status update?
- **Resolution:** Add integration test in Phase 4 that simulates Redis failure. Document the atomicity boundary. If needed, modify _handle_approval() to handle voucher-specific flow (remove the dead commit() calls, add voucher payment_method check).

**Gap 2: Export file lifecycle**
- **Issue:** Research does not definitively answer: when is the encrypted export generated (at batch creation, at allocation, or on-demand)? When is it deleted (immediate after download, 7-day TTL, never)?
- **Resolution:** Specify in Phase 3 requirements. Recommendation: generate at allocation time (when library is known), attach to Allocation doc, set 7-day TTL, log all downloads, admin can re-generate if lost.

**Gap 3: ERPNext Sales Invoice availability**
- **Issue:** STACK.md notes the codebase has "options": "Sales Invoice" on the erpnext_invoice field in Subscription Transaction, but NO erpnext imports exist. Is ERPNext installed on the production site?
- **Resolution:** Check during Phase 3 planning. If ERPNext is NOT installed, create a lightweight custom "Memora Invoice" DocType instead (fields: customer, items, total, status). Do not block on ERPNext availability.

**Gap 4: Commission calculation timing**
- **Issue:** FEATURES.md mentions automatic commission ledger entries but does not specify: commission recorded at allocation time (when library receives cards) or at redemption time (when student uses card)? For consignment model, must be at redemption. For prepaid model, could be either.
- **Resolution:** Specify in Phase 3/4 requirements based on business model. Recommendation: always record commission at redemption time (consistent across both prepaid and consignment), prepaid sale records upfront payment separately.

**Gap 5: Partial batch allocation granularity**
- **Issue:** FEATURES.md mentions batch-level vs card-level allocation but research does not conclude which is MVP.
- **Resolution:** Start with batch-level allocation (one batch → one library) in Phase 3. Add card-level allocation as Phase 6+ enhancement if libraries request partial batches. The batch DocType has a library Link field set at allocation time.

## Sources

### Primary (HIGH confidence)

**Codebase Analysis:**
- memora_admin/memora_admin/doctype/memora_subscription_transaction/memora_subscription_transaction.py — Phase 23 approval pipeline, _handle_approval() method
- memora_admin/events/access_sync.py — on_subscription_change hook, Redis SADD for access grants
- fastapi_app/services/purchase.py — FastAPI→Frappe proxy pattern
- fastapi_app/services/rate_limit.py — Reusable RateLimiter with Lua script
- fastapi_app/api/deps.py — Dependency injection patterns
- fastapi_app/core/frappe_client.py — FrappeClient HTTP bridge
- memora_admin/memora_admin/doctype/memora_lesson/memora_lesson.py:33 — SELECT FOR UPDATE precedent

**Official Documentation:**
- [Python hmac module](https://docs.python.org/3/library/hmac.html)
- [Python secrets module](https://docs.python.org/3/library/secrets.html)
- [Python decimal module](https://docs.python.org/3/library/decimal.html)
- [cryptography (Fernet)](https://cryptography.io/en/latest/fernet/)
- [Frappe v15 Database API](https://docs.frappe.io/framework/v15/user/en/api/database)
- [Frappe v15 Background Jobs](https://docs.frappe.io/framework/v15/user/en/api/background_jobs)
- [Frappe file_manager.py source](https://github.com/frappe/frappe/blob/develop/frappe/utils/file_manager.py)
- [Frappe Document class source](https://github.com/frappe/frappe/blob/develop/frappe/model/document.py)

**Security Standards:**
- [OWASP Password Storage Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)
- [CVE-2022-48566 - Python hmac.compare_digest timing flaw](https://www.cve.news/cve-2022-48566/)
- [HackerOne Report #759247 - Race Condition Double Redemption](https://hackerone.com/reports/759247)

### Secondary (MEDIUM confidence)

**Domain Patterns:**
- [F5 Gift Card Cracking Prevention](https://www.f5.com/go/solution/gift-card-cracking) — brute-force enumeration attack patterns
- [DataDome Gift Card Fraud Prevention](https://datadome.co/threats/gift-card-fraud-prevention/) — enumeration detection
- [Estel Telecom VMS](https://www.esteltelecom.com/products/vms-voucher-management-system) — telecom voucher lifecycle
- [6D Technologies Voucher Management](https://www.6dtechnologies.com/fintech/voucher-management-solution/) — distributor tracking
- [Pearson Already-Redeemed Support](https://support.pearson.com/getsupport/s/article/Registration-Access-Code-Already-Redeemed) — education platform error handling
- [Elsevier Access Code Redemption](https://service.elsevier.com/app/answers/detail/a_id/28693/supporthub/evolve/) — education "already redeemed" flow

**Technical Patterns:**
- [Database Locking to Solve Race Condition](https://www.coderbased.com/p/database-locking)
- [Transaction Locking to Prevent Race Conditions](https://sqlfordevs.com/transaction-locking-prevent-race-condition)
- [InnoDB Lock Modes - MariaDB](https://mariadb.com/docs/server/server-usage/storage-engines/innodb/innodb-lock-modes)
- [Deferred Bulk Inserts in Frappe](https://tej.sh/blog/frappe-deferred-bulk/)
- [Timing Attacks against String Comparison in Python](https://sqreen.github.io/DevelopersSecurityBestPractices/timing-attack/python)
- [Python Decimal vs Float: The $10,000 Mistake](https://pranaysuyash.medium.com/how-i-lost-10-000-because-of-a-python-float-and-how-you-can-avoid-my-mistake-3bd2e5b4094d)

### Tertiary (LOW confidence)
- Arizona Lottery Scratch & Scan QR pattern (single source, but QR-on-card concept is straightforward)
- Consignment software commission patterns from Shopify articles (applicable concepts but different domain)

---
*Research completed: 2026-02-13*
*Ready for roadmap: yes*
