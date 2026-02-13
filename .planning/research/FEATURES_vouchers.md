# Feature Landscape: Voucher Management System

**Domain:** Prepaid voucher/recharge card distribution for educational content
**Researched:** 2026-02-13
**Context:** Physical scratch cards distributed via library network in Jordan, redeemed in mobile app to unlock learning content
**Overall Confidence:** HIGH (well-established domain across telecom and education, cross-referenced with existing codebase)

---

## Table Stakes

Features users (admin, library, student) expect. Missing any of these = system feels broken or untrustworthy.

### Core Card Lifecycle

| # | Feature | Why Expected | Complexity | Depends On | Notes |
|---|---------|--------------|------------|------------|-------|
| TS-1 | **Batch creation with configurable size** | Every VMS generates cards in batches, not individually. Telecom systems generate millions per batch; we need 1K-10K. | Medium | DocType schema | PRD covers this. Batch creation is the fundamental unit of work. |
| TS-2 | **CSPRNG PIN generation** | Telecom standard. Predictable PINs = catastrophic breach. PIN guessing is the #1 attack vector per F5 Security. | Low | Python `secrets` module | PRD specifies HMAC-SHA256 storage. Use `secrets.randbelow(10**12)` for 12-digit PINs -- NOT `random.randint()`. |
| TS-3 | **PIN hashing (HMAC-SHA256) with per-card salt** | Industry standard -- plaintext PINs in DB are a non-starter. Constant-time comparison prevents timing attacks. | Low | `hmac` stdlib | PRD covers this. Use `hmac.compare_digest()` for verification. Per-card salt prevents rainbow table attacks across the entire card database. |
| TS-4 | **State machine: Available -> Allocated -> Redeemed / Void / Expired** | Core lifecycle every card system uses. A card must be in exactly one state at all times. | Medium | DocType workflow field | PRD covers this. State transitions must be enforced in code (not just UI). |
| TS-5 | **Batch allocation to libraries** | Distribution channel management is the entire point of the system. | Medium | Library DocType | PRD covers this. Track which library received which batch, with allocation date. |
| TS-6 | **One-time atomic redemption** | Double-redemption is the #1 voucher fraud vector. HackerOne reports show race conditions allowing multiple concurrent redemptions of the same code. | High | Redis atomic lock or DB `SELECT...FOR UPDATE` | PRD implies this but does NOT specify the atomicity mechanism. See Gap Analysis. |
| TS-7 | **Expiration enforcement** | Legal and business requirement. Students and libraries expect cards to have clear, enforced expiry dates. | Low | Check-time validation + scheduled task | PRD covers this. Must check at redemption time (not just cron). A card that expired 5 seconds ago must fail immediately. |
| TS-8 | **Void individual card** | Admin must immediately kill a compromised, disputed, or erroneously created card. | Low | State transition | PRD covers this (Void state). |
| TS-9 | **Void entire batch** | If a batch is stolen during shipping or a library is compromised, void all cards at once. | Low | Batch-level bulk operation | PRD implies this but should be an explicit single-click action, not card-by-card. |
| TS-10 | **Redemption rate limiting** | Brute-force PIN guessing is the #1 attack vector. At 12 digits, 10^12 combinations makes random guessing impractical, but rate limiting adds defense in depth. | Medium | Existing rate limit infrastructure | PRD covers this. Reuse OTP-style rate limiting pattern: 3-5 attempts per player per 10 minutes. |
| TS-11 | **Immutable redemption audit log** | Compliance and dispute resolution. Every VMS has an append-only log of all redemption attempts (success and failure). | Medium | Append-only log table or DocType | PRD covers this. NEVER delete or update redemption records. |
| TS-12 | **CSV export for print vendor** | Physical cards must be printed. Print vendors need data files with serial numbers, PINs (plaintext), and product info. | Low | Export function | PRD covers this. This is the ONLY time plaintext PINs exist outside memory. |

### Redemption Flow (Student-Facing)

| # | Feature | Why Expected | Complexity | Depends On | Notes |
|---|---------|--------------|------------|------------|-------|
| TS-13 | **PIN entry screen** | The core student interaction. Simple numeric input for 12-digit PIN. | Low | FastAPI endpoint | PRD covers this. Numeric-only keyboard on mobile avoids Arabic/English switching issues. |
| TS-14 | **Preview before confirm** | Student MUST see what they will unlock BEFORE the PIN is consumed. Pearson, Elsevier, VitalSource all use this pattern. | Medium | Product catalog lookup from card's product grant | PRD covers preview+confirm flow. Show: product name, subjects included, duration. Critical for trust -- students need to verify they are redeeming the right card for the right product. |
| TS-15 | **Specific error messages** | Students will call library/support when redemption fails. Support needs to know exactly WHY. Generic "error" is unacceptable. | Low | Error code enum | PRD mentions rate limiting but does not define error taxonomy. Define: `INVALID_PIN`, `ALREADY_REDEEMED`, `EXPIRED_CARD`, `VOIDED_CARD`, `RATE_LIMITED`, `PRODUCT_UNAVAILABLE`. |
| TS-16 | **Instant content unlock** | Student expects immediate access after redemption, not "wait for admin approval." This is the key difference from the manual purchase flow. | High | Existing subscription pipeline | PRD covers this. Redemption bypasses admin approval: creates `Subscription Transaction` with `status="Completed"` and `payment_method="Voucher"`, triggering the existing `on_subscription_change` -> Redis SADD pipeline. |
| TS-17 | **Redemption receipt** | Student needs proof of successful redemption for their records and potential disputes. | Low | Response payload | Show: product name, subjects unlocked, transaction ID, redemption date. Mobile app persists locally. |

### Admin Management

| # | Feature | Why Expected | Complexity | Depends On | Notes |
|---|---------|--------------|------------|------------|-------|
| TS-18 | **Batch list view with status summary** | Admin needs to see batch health at a glance: total, allocated, redeemed, void, expired counts. | Medium | Frappe List View + virtual fields | PRD covers reports. Aggregate counts per batch. |
| TS-19 | **Individual card lookup by serial** | Support agents look up specific cards for customer complaints ("I bought a card and it doesn't work"). | Low | Indexed serial_number field | Index `serial_number` for fast lookup. Show: state, batch, library, redemption info if redeemed. |
| TS-20 | **Library allocation tracking** | Admin must know which library has which cards and their status. | Medium | Allocation records | PRD covers allocation flow. |
| TS-21 | **Sales and redemption reports** | Business needs revenue tracking and channel performance metrics. | Medium | Frappe Report Builder or Script Report | PRD covers 4 report types: Sales, Batch Performance, Consignment Reconciliation, Security Audit. |

### Library/Distributor Management

| # | Feature | Why Expected | Complexity | Depends On | Notes |
|---|---------|--------------|------------|------------|-------|
| TS-22 | **Library entity with contact info** | Must track who receives card allocations. | Low | New DocType | PRD implies a library entity. Fields: name, location, contact_person, phone, email, default_commission_rate, sale_model (Prepaid/Consignment), is_active. |
| TS-23 | **Prepaid sale model** | Standard wholesale distribution: library buys cards upfront at discount, sells at retail. | Low | Transaction/payment tracking | PRD covers this. Library pays before receiving cards. |
| TS-24 | **Consignment model** | Library receives cards without upfront payment, pays after students redeem. Common for onboarding new libraries. | Medium | Reconciliation logic | PRD covers this. Must track: consigned, sold (redeemed), unsold, returned. |
| TS-25 | **Commission tracking** | Libraries need to know their margin. Without this, no library will participate. | Medium | Per-product override or library default rate | PRD covers this (product-level override -> library default fallback). |

---

## Differentiators

Features that set the system apart. Not expected by default, but add significant value for this specific domain (education + Jordan market + library network at 50-100 library scale).

### High-Value Differentiators

| # | Feature | Value Proposition | Complexity | Depends On | Notes |
|---|---------|-------------------|------------|------------|-------|
| D-1 | **QR code on physical card** | Faster redemption -- scan instead of typing 12 digits. Arizona Lottery uses this exact pattern (scan QR, enter PIN). Reduces typos for young Arabic-speaking students. | Low | QR generation at CSV export time | NOT in PRD. QR encodes `memora://redeem?s={serial}` -- deep link that opens app and pre-fills serial. Student only types the PIN. Print vendor adds QR to card layout alongside scratch-off PIN area. |
| D-2 | **Graceful "already redeemed" response** | When a student enters an already-redeemed PIN, show "This card was redeemed on {date}" without revealing WHO. Pearson and Elsevier use this pattern. Reduces support tickets dramatically. | Low | Lookup `redeemed_at` timestamp on card | NOT in PRD. Students frequently buy "used" cards from classmates or find cards they already redeemed. Clear messaging prevents confusion. |
| D-3 | **"Already owned" guard** | If student redeems a card for a product they already own, reject the redemption WITHOUT consuming the card. Card stays Available/Allocated. Student can give it to someone else. | Medium | Check existing subscriptions before redemption | NOT explicitly in PRD. Prevents waste -- a student who already has Math access should not burn a Math card. Return `ALREADY_OWNED` error with friendly message. |
| D-4 | **Automatic commission ledger** | On each redemption, auto-calculate and record the library's commission. No manual spreadsheet reconciliation. | Medium | Commission rate + redemption event hook | PRD mentions commission calculation but not auto-recording. Eliminates monthly reconciliation effort. Each redemption creates a commission ledger entry: library, amount, card, date. |
| D-5 | **Card activation delay** | Cards exist in "Printed" state after export. Become "Available" only when admin activates the batch. Prevents theft-during-shipping fraud. | Low | Additional state in lifecycle | NOT in PRD. Common telecom pattern. If cards are stolen before activation, they are useless. Admin activates batch only after confirming delivery to library. State machine becomes: Printed -> Available -> Allocated -> Redeemed/Void/Expired. |
| D-6 | **Return/unallocate flow** | Library returns unsold cards back to central inventory. State changes from Allocated -> Available. Essential for consignment model. | Low | State transition + validation | NOT explicitly in PRD. Required for consignment reconciliation. Only non-redeemed cards can be returned. Log the return event for audit. |

### Nice-to-Have Differentiators

| # | Feature | Value Proposition | Complexity | Depends On | Notes |
|---|---------|-------------------|------------|------------|-------|
| D-7 | **Library self-service portal** | Libraries view their allocated inventory, track redemptions, see commission balance. Reduces admin support load. | High | Frappe "Library Manager" role + workspace | NOT in PRD. Create a read-only Frappe Desk view scoped to the library's data. Phase 3+ feature. |
| D-8 | **Real-time batch dashboard** | Admin watches batch redemption progress in real time during school distribution events. | Medium | Redis counter + SSE/polling | NOT in PRD. Simple Redis INCR on batch redemption count. |
| D-9 | **Near-expiry alerts for libraries** | Notify libraries when allocated cards are approaching expiry so they can push sales or return unsold stock. | Medium | Scheduled task + notification | NOT in PRD. Prevents dead stock and lost revenue. |
| D-10 | **Redemption time-to-redeem analytics** | Track how long after allocation cards are redeemed. Identifies high-performing vs stagnant libraries. | Medium | Analytics queries | PRD covers Batch Performance report; add time-to-redeem as a metric. |
| D-11 | **PIN reveal with admin OTP** | Admin can reveal a card's PIN for phone support, but only after entering their own OTP/password. Prevents insider theft. | Medium | Admin re-authentication | NOT in PRD. Defense-in-depth against insider threat. |
| D-12 | **Partial batch allocation** | Allocate 500 of a 1000-card batch to Library A, 300 to Library B, keep 200 in reserve. | Low | Card-level allocation tracking | PRD may imply batch-level allocation only. Card-level is more flexible. |

---

## Anti-Features

Features to explicitly NOT build. Building these adds complexity without proportional value for this scale (thousands of cards/year, 50-100 libraries) and market (Jordan education).

| # | Anti-Feature | Why Avoid | What to Do Instead |
|---|--------------|-----------|-------------------|
| AF-1 | **Digital-only vouchers (SMS/email delivery)** | Physical cards ARE the distribution model for Jordan's library network. Students trust tangible cards with scratch-off PINs. Digital delivery requires SMS gateway, different distribution chain, and changes the business model entirely. | Keep physical cards as the primary and only channel. Digital delivery can be a future enhancement (v3+) once the physical flow is proven. |
| AF-2 | **Partial redemption / split value** | Cards map to specific products (content packages), not monetary value. A card either unlocks a product or it does not. Partial redemption creates confusing UX ("you have 3 JOD remaining on this card") and complex accounting. | One card = one product. If smaller packages are needed, create smaller products in the Product Grant system. |
| AF-3 | **Card-to-card transfer** | Students should not transfer a card's activated value to another student. Creates a secondary market, increases fraud surface, and generates support headaches. | Redemption is final and tied to the redeeming player's account. An unredeemed card can be given to anyone (it is a bearer instrument until redeemed). |
| AF-4 | **Self-service refund/reversal** | Once redeemed, content is unlocked and accessible. Allowing self-service reversal creates abuse vectors (redeem, study, reverse, give card to friend) and requires complex state rollback across Subscription Transaction, Player Subscription, and Redis access grants. | Admin-only void with full audit trail. Refunds are a manual business process (library handles returns, admin credits replacement). |
| AF-5 | **Complex per-library pricing tiers** | Different prices per library based on volume, loyalty tier, or geographic region. Over-engineering for 50-100 libraries. Adds negotiation complexity and custom pricing logic. | Single commission rate per library with optional product-level override. Renegotiate manually when needed. The Product Grant's Item Price already handles product pricing. |
| AF-6 | **Blockchain/NFT-based vouchers** | Adds zero value for this use case. Physical cards + HMAC hashing is simpler, more robust, and infinitely easier to maintain. No student or library benefits from blockchain. | Standard HMAC-SHA256 hashed PINs with MariaDB as source of truth. |
| AF-7 | **Multi-currency support** | Jordan uses JOD exclusively for this market. No international distribution is planned. Currency conversion adds complexity with no business need. | Single currency (JOD). Set once in system configuration. |
| AF-8 | **Online voucher marketplace** | Libraries sell physical cards in person. Building an e-commerce storefront changes the entire distribution model, requires payment gateway integration, and competes with the library network. | Physical distribution through library network only. |
| AF-9 | **Automatic reorder/replenishment** | At the initial scale of thousands of cards per year and 50-100 libraries, automated reorder triggers are premature optimization. Admin creates batches manually based on demand. | Admin creates batches on demand. Add reorder alerts only if scale exceeds 100K cards/year. |
| AF-10 | **Voucher gifting between players** | "Send this voucher code to a friend" feature in the app. Creates confusion about card ownership and complicates the physical distribution model. | Cards are physical bearer instruments. Give the physical card to a friend. The app does not need to facilitate transfers. |

---

## Feature Dependencies

```
DocType Foundation (build first):
  Memora Library (TS-22) -----> Commission Config (TS-25)
  Memora Voucher Batch (TS-1) -> PIN Generation Service (TS-2, TS-3)
  Memora Voucher Card (TS-4) --> Batch (parent-child relationship)

Batch Operations:
  Batch + Cards -> CSV Export for Print (TS-12)
  Batch + Library -> Batch Allocation (TS-5)
  Allocation -> Card state: Available -> Allocated (TS-4)

Redemption Flow (CRITICAL PATH):
  PIN Entry (TS-13) -> PIN Validation (TS-3)
  PIN Validation -> Rate Limit Check (TS-10)
  Rate Limit Pass -> Preview (TS-14): lookup card -> product grant -> show products
  Preview + Confirm -> Atomic Redemption (TS-6): Redis lock + state change
  Atomic Redemption -> Subscription Transaction (status=Completed, payment_method=Voucher)
  ^^^^^ THIS MUST USE THE EXISTING PIPELINE ^^^^^
  Subscription Transaction -> Player Subscription (EXISTING: access_sync.py)
  Player Subscription -> Redis SADD (EXISTING: on_subscription_change hook)
  Redemption -> Audit Log Entry (TS-11)
  Redemption -> Commission Ledger Entry (D-4, if consignment)

Reporting:
  Audit Log -> Security Audit Report (TS-21)
  Batch + Cards -> Batch Performance Report (TS-21)
  Allocations + Redemptions -> Sales Report (TS-21)
  Consignment + Commission Ledger -> Reconciliation Report (TS-21)

Expiration:
  Scheduled Task -> Mark Expired Cards (TS-7)
  Redemption Time Check -> Reject Expired (TS-7)
```

**Critical dependency**: The redemption flow MUST terminate into the existing `Subscription Transaction -> Player Subscription -> Redis SADD` pipeline via the `access_sync.py` event hooks. Do NOT create a parallel content-unlock mechanism. The existing `payment_method` field on `Memora Subscription Transaction` already has a "Voucher" option.

---

## MVP Recommendation

### Must Build (Phase 1 -- Core flow works end to end)

Priority: Get a card from creation to redemption working in a single path.

1. **TS-22** Library DocType (simple: name, contact, commission rate, sale model)
2. **TS-1** Voucher Batch DocType with configurable size
3. **TS-2 + TS-3** CSPRNG PIN generation + HMAC-SHA256 hashing
4. **TS-4** Voucher Card DocType with state machine
5. **TS-12** CSV export for print (serial + plaintext PIN + product info)
6. **TS-5** Batch allocation to library
7. **TS-13 + TS-14** FastAPI redemption endpoint: PIN entry + preview + confirm
8. **TS-6** Atomic redemption (Redis SETNX lock to prevent double-redeem)
9. **TS-16** Wire redemption into existing subscription pipeline (Completed transaction)
10. **TS-10** Rate limiting on redemption attempts
11. **TS-11** Immutable redemption audit log
12. **TS-15** Specific error codes for all failure modes
13. **TS-17** Redemption receipt in response
14. **TS-18 + TS-19** Admin list views (batch summary, card lookup)

### Should Build (Phase 2 -- Operational quality)

15. **TS-7** Expiration enforcement (check at redemption + nightly scheduled task)
16. **TS-8 + TS-9** Void individual card + void entire batch
17. **TS-24** Consignment tracking (consigned/sold/returned counters)
18. **D-4** Automatic commission ledger entries on redemption
19. **D-1** QR code generation on CSV export
20. **D-2** Graceful "already redeemed" response with date
21. **D-3** "Already owned" guard (reject without consuming card)
22. **D-6** Return/unallocate flow for unsold cards
23. **TS-21** Reports (Sales, Batch Performance, Consignment Reconciliation, Security Audit)
24. **TS-23** Prepaid sale tracking

### Defer (Phase 3+ -- Nice to have)

25. **D-5** Card activation delay (Printed -> Available)
26. **D-7** Library self-service portal
27. **D-8** Real-time batch dashboard
28. **D-9** Near-expiry alerts
29. **D-10** Time-to-redeem analytics
30. **D-11** PIN reveal with admin OTP
31. **D-12** Partial batch allocation (card-level)

---

## Gap Analysis: What the PRD Might Be Missing

### Critical Gaps (could cause rework if not addressed)

| # | Gap | Risk | Recommendation |
|---|-----|------|----------------|
| G-1 | **Atomicity of redemption** | PRD does not specify how to prevent double-redemption race conditions. Two concurrent requests with the same PIN could both pass the "is available?" check before either marks it redeemed. This is the #1 voucher vulnerability per HackerOne (report #759247) and security researchers. | Use Redis `SET {pin_lock_key} NX EX 30` as a distributed lock before DB state change. If SETNX fails, another request is processing the same card -- return "try again." After DB commit, release lock. This fits the existing Redis-first architecture. Alternative: MariaDB `SELECT ... FOR UPDATE` within a transaction, but Redis lock is faster and matches existing patterns. |
| G-2 | **PIN plaintext lifecycle** | PRD says PINs are HMAC-hashed for storage, but does not address how plaintext PINs reach the CSV export for the print vendor. Plaintext must exist exactly ONCE: in memory during batch creation, streamed to export, then discarded. | Generate batch -> stream PINs to in-memory buffer -> write CSV -> hash for DB storage -> return CSV as download response -> NEVER persist plaintext to disk. The CSV download IS the only plaintext copy. Log who downloaded it and when. Consider password-protecting the CSV/ZIP. |
| G-3 | **Redemption-to-subscription pipeline integration** | PRD describes redemption but does not explicitly state that it reuses the existing `Subscription Transaction -> Player Subscription -> Redis access sync` pipeline. A separate content-unlock mechanism would be a major architectural mistake and maintenance burden. | Redemption creates a `Memora Subscription Transaction` with `payment_method="Voucher"`, `status="Completed"` (skipping "Pending Approval"), and `related_grant` pointing to the card's product grant. The existing `on_subscription_change` hook in `access_sync.py` handles Redis SADD. The `payment_method` field already includes "Voucher" as an option. |
| G-4 | **Error taxonomy for students** | PRD mentions rate limiting but does not define the specific error codes a student sees when redemption fails. Students will contact libraries or support -- clear errors prevent confusion and reduce support load. | Define error enum: `INVALID_PIN` (no card found), `ALREADY_REDEEMED` (used by someone, show date), `EXPIRED_CARD` (past expiry), `VOIDED_CARD` (admin voided), `RATE_LIMITED` (too many attempts), `PRODUCT_UNAVAILABLE` (product unpublished), `ALREADY_OWNED` (student already has this content). |
| G-5 | **Batch export file security** | The CSV with plaintext PINs is the crown jewel for attackers. PRD does not address export file lifecycle, access logging, or encryption. | Generate CSV as a streaming HTTP response (never write to disk). Log: who exported, when, batch ID, IP address. Consider ZIP with password. Require admin authentication (already have it via Frappe session). The export endpoint should be rate-limited to 1 per minute per admin to prevent mass scraping. |

### Moderate Gaps (could cause confusion or rework)

| # | Gap | Risk | Recommendation |
|---|-----|------|----------------|
| G-6 | **Card-level vs batch-level allocation** | PRD says "Allocate to Libraries" but does not specify granularity. If allocation is batch-level only, you cannot split a batch across multiple libraries. | Start with batch-level allocation for Phase 1 (simpler: one batch -> one library). Add card-level allocation in Phase 2 if libraries request partial batches. The batch DocType should have a `library` Link field set at allocation time. |
| G-7 | **Return flow for unsold cards** | PRD mentions consignment but not what happens when a library returns unsold cards at end of semester. Without this, consignment reconciliation cannot close out. | Add Allocated -> Available state transition for "returned" cards. Only non-redeemed cards can be returned. Create a return log entry for audit. Update consignment counters. |
| G-8 | **Commission payment tracking** | PRD mentions commission calculation but not whether commissions are tracked as paid/unpaid. Finance team needs to know what they owe each library. | Add a simple `is_paid` flag and `paid_date` on commission ledger entries. Full payment gateway integration is overkill -- finance pays via bank transfer and marks entries as paid. |
| G-9 | **Library onboarding workflow** | PRD does not describe how a new library joins the system: who creates the record, what approval is needed, what defaults are set. | Library DocType created by admin in Frappe Desk. Required fields: name, contact_person, phone, location, default_commission_rate (percentage), sale_model (Select: Prepaid/Consignment), is_active (Check). No self-registration for libraries. |
| G-10 | **Product unavailable after card creation** | PRD says cards map to products, but does not address what happens if a product grant is unpublished after cards are already printed and distributed. | Redemption checks `product_grant.is_published` at redemption time. If unpublished, return `PRODUCT_UNAVAILABLE` error. The card is NOT consumed (stays in current state). This is a recoverable situation -- admin can republish the product or arrange a replacement card. |

### Minor Gaps (good to document, not blocking)

| # | Gap | Risk | Recommendation |
|---|-----|------|----------------|
| G-11 | **Serial number format** | PRD mentions serial numbers but not the format. Without a standard, cards may have confusing or collision-prone identifiers. | Use human-readable format: `MEM-{BATCHSEQ}-{CARDSEQ}` (e.g., `MEM-2603001-00042`). Avoid ambiguous characters (0/O, 1/I/l). Serial is printed on the card exterior (visible without scratching). |
| G-12 | **PIN length and character set** | PRD does not specify PIN length or whether PINs are numeric-only or alphanumeric. | 12-digit numeric PIN. Long enough to resist brute force (10^12 = 1 trillion combinations). Numeric-only avoids Arabic/English keyboard switching on mobile. Formatted as 4-4-4 groups for readability: `1234-5678-9012`. |
| G-13 | **Batch naming convention** | No specified format for batch identifiers. | `VBATCH-{YYYYMM}-{SEQUENCE}` (e.g., `VBATCH-202603-001`). Encodes creation month for easy identification. Frappe autoname handles uniqueness. |
| G-14 | **Timezone handling for expiry** | Jordan is UTC+2 (UTC+3 in summer). Expiry dates must be unambiguous. | Use date-only (not datetime) for expiry. A card expiring "2026-06-30" expires at end of that day in Jordan local time. Store as naive Date (Frappe convention). Check `expiry_date < today()` at redemption. |
| G-15 | **Redemption from wrong plan context** | What if a card is linked to a Product Grant for Plan A, but the student is on Plan B? | The card's product grant has a `plan` field. If the student's plan does not match, still allow redemption -- the product grant's content components are what matter, not plan membership. The subscription pipeline handles access grants based on components, not plans. |

---

## Comparison with Similar Systems

| Aspect | Telecom Recharge VMS | Gift Card Systems | Education Access Codes (Pearson, Elsevier) | Memora Vouchers |
|--------|---------------------|-------------------|-------------------------------------------|-----------------|
| **Value type** | Monetary (top-up balance) | Monetary (store credit) | Content access (license key) | Content access (subscription grant) |
| **Partial use** | Yes (remaining balance) | Yes (remaining balance) | No (one-time activation) | No (one-time activation) |
| **PIN length** | 14-16 digits | 16-25 alphanumeric | 10-20 alphanumeric | 12 digits numeric (recommended) |
| **Distribution** | Retail shops, ATMs, dealers | Retail stores, online | Bookstores, campus stores, online | Libraries (physical, in-person) |
| **Expiry** | 30-365 days | 5+ years (US legal requirement) | 1-4 years | Semester-aligned (4-6 months) |
| **Double-use risk** | HIGH (direct monetary loss) | HIGH (direct monetary loss) | MEDIUM (content leak) | MEDIUM (content leak) |
| **Primary fraud vector** | PIN guessing, dealer theft | Enumeration, card cloning | Code sharing, resale | PIN guessing, batch theft during shipping |
| **Scale** | Millions/month | Millions/month | Thousands/semester | Thousands/year (growing to tens of thousands) |
| **Admin approval** | No (instant top-up) | No (instant credit) | No (instant access) | No (instant access -- differs from manual purchase flow) |
| **Distributor tracking** | Yes (dealer/sub-dealer hierarchy) | Minimal | Minimal | Yes (library allocation + commission) |

**Key insight**: Memora vouchers are closest to **education access codes** (Pearson, Elsevier, VitalSource) in behavior (one-time activation, content unlock, "already redeemed" error patterns) but distributed via a **telecom-style dealer/library network** (batch generation, dealer allocation, commission, consignment tracking). The technical architecture should follow education patterns while the business logic follows telecom patterns.

---

## Existing Codebase Integration Points

The voucher system must connect to several existing components. Verified by reading the current codebase:

| Component | File | Integration Point |
|-----------|------|-------------------|
| **Subscription Transaction** | `memora_subscription_transaction.json` | `payment_method` already has "Voucher" option. Redemption creates a transaction with `status="Completed"`. |
| **Product Grant** | `memora_product_grant.json` | Cards link to a Product Grant (plan + item_code + grant_components). The grant defines what content is unlocked. |
| **Player Subscription** | `memora_player_subscription.json` | Created by the subscription approval pipeline. Links player to access_key with expiry. |
| **Access Sync** | `events/access_sync.py` | `on_subscription_change` hook syncs grants to Redis. Redemption triggers this via the Subscription Transaction -> Player Subscription chain. |
| **Purchase API** | `api/purchase.py` | Existing `create_purchase_request` creates "Pending Approval" transactions. Voucher redemption creates "Completed" transactions directly -- different path but same destination. |
| **Catalog Service** | `endpoints/catalog.py` | Product discovery for students. Voucher preview can reuse catalog data to show what a card unlocks. |
| **Rate Limiting** | Existing Lua-based rate limiter | Reuse for redemption attempt limiting. Same pattern as OTP and login rate limiting. |

---

## Sources

### HIGH Confidence (Official Documentation, Security Standards, Codebase Verification)
- [F5 Gift Card Cracking Prevention](https://www.f5.com/go/solution/gift-card-cracking) - Brute-force enumeration attack patterns and mitigation
- [DataDome Gift Card Fraud Prevention 2025](https://datadome.co/threats/gift-card-fraud-prevention/) - Enumeration attack detection and prevention
- [HackerOne Report #759247 - Race Condition Double Redemption](https://hackerone.com/reports/759247) - Real-world race condition allowing multiple redemptions
- [Ian.nl - Race Conditions in Coupons](https://ian.nl/blog/race-conditions-coupons) - Technical analysis of concurrent redemption vulnerabilities
- [Pearson Already-Redeemed Support](https://support.pearson.com/getsupport/s/article/Registration-Access-Code-Already-Redeemed) - Education platform error handling patterns
- [Elsevier Access Code Redemption](https://service.elsevier.com/app/answers/detail/a_id/28693/supporthub/evolve/) - Education platform "already redeemed" flow
- Existing codebase: `access_sync.py`, `purchase.py`, `catalog.py`, `memora_subscription_transaction.json`, `memora_product_grant.json`

### MEDIUM Confidence (Multiple Sources Agree)
- [Estel Telecom VMS](https://www.esteltelecom.com/products/vms-voucher-management-system) - Telecom voucher lifecycle management patterns
- [6D Technologies Voucher Management](https://www.6dtechnologies.com/fintech/voucher-management-solution/) - Telecom VMS features, distributor tracking
- [Seamless VMS](https://seamless.se/vms/) - Physical+digital voucher lifecycle, distributor management
- [Comviva VMS](https://www.comviva.com/products-solutions/digitech/pretups-voucher-management/) - Distribution and redemption patterns
- [PassKit Gift Voucher Features](https://passkit.com/blog/gift-voucher-management-system/) - Essential VMS feature list
- [Voucherify Gift Cards UX](https://www.voucherify.io/blog/gift-cards-ux-and-ui-best-practices) - Redemption UX best practices
- [SDK.finance Redeem Prepaid Flow](https://sdk.finance/knowledge-base/redeem-prepaid/) - Redemption flow architecture

### LOW Confidence (Single Source, Needs Validation)
- Arizona Lottery Scratch & Scan QR pattern (single source, but the QR-on-card concept is straightforward)
- Consignment software commission patterns from Shopify/consignment POS articles (applicable concepts but different domain)

---

*Research complete. Feature landscape mapped with 25 table stakes, 12 differentiators, 10 anti-features, and 15 gap items. Ready for requirements definition.*
