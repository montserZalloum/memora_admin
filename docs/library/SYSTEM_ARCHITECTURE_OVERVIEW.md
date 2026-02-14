# Memora Voucher System - Architecture Overview

## How All 6 Phases Work Together

---

## The Big Picture: From Idea to Revenue

```
YOU (ADMIN)              THE SYSTEM                  STUDENTS              LIBRARIES
─────────────────────────────────────────────────────────────────────────────────

Create Batch              [Phase 33: Foundation]
  │                       • Database schema setup
  │                       • DocTypes created
  │
Generate Cards ────→ [Phase 34: Batch Generation]
  │                       • Secure PIN generation
  │                       • Cards created in DB
  │                       • Export file for printing
  │
Print Cards               (Physical card printing)
  │
Allocate to Libraries ──→ [Phase 35: Allocation]
  │                       • Fill Cards with library inventory
  │                       • Approve workflow
  │                       • Invoice created (Prepaid)
  │
                      [Phase 36: Redemption API]
                             │
                    Student enters PIN ──→ Student has access
                             │
                    Every attempt logged
                             │
                         [Subscription created
                          Content unlocked]
  │                          │
  │◄─────────────────────────┘
  │
Track Money & Revenue ──→ [Phase 37: Financial]
  │                       • Invoice for prepaid cards
  │                       • Credit notes for returns
  │                       • Monthly invoices for consignment
  │                       • Commission calculations
  │
Review Performance ────→ [Phase 38: Reports]
  │                       • Sales by Library
  │                       • Batch Performance
  │                       • Consignment Reconciliation
  │                       • Security Audit
  │
Automatic Cleanup ────→ Season Expiration
                         • Cards auto-expire
                         • New season starts
```

---

## Data Flow Architecture

### Phase 33: The Foundation (Database Schema)

```
                    ┌─────────────────────────────────────┐
                    │    VOUCHER BATCH                    │
                    │  (Container for 1000+ cards)        │
                    │  - batch_name                       │
                    │  - quantity                         │
                    │  - pin_length (12/14/16)           │
                    │  - face_value ($10)                 │
                    │  - status (Draft→Generated→Active)  │
                    └────────────────┬────────────────────┘
                                     │
                    ┌────────────────┴────────────────┐
                    │                                 │
         [Batch Grants Child]              [Cards created]
         ├─ Product Grant A                   │
         │  └─ Commission override          Many
         ├─ Product Grant B                 VCH-000001
         │  └─ Commission override          VCH-000002
         └─ Product Grant C                 ...
                                           VCH-001000

          ┌─────────────────────────────────────┐
          │    CUSTOMER (Library)                │
          │  - name (Cairo Library)              │
          │  - voucher_requires_approval (Y/N)   │
          │  - commission_type (%)               │
          │  - commission_value (10%)            │
          └─────────────────────────────────────┘
```

**Phase 33 creates the filing system. Nothing happens yet — just structure.**

---

### Phase 34: Batch Generation (PIN Creation)

```
ADMIN CREATES BATCH              BACKGROUND JOB RUNS
                                        │
┌─────────────────────┐          ┌──────────────────┐
│ BATCH (Draft)       │    →     │ CREATE CARDS     │
├─────────────────────┤          │  For each i=1:qty│
│ qty: 1000           │          │ · Generate PIN   │
│ pin_length: 14      │          │   using secrets  │
│ face_value: $10     │          │ · Hash with HMAC │
│ status: Draft       │          │ · Create record  │
└─────────────────────┘          │ · Bulk insert    │
         │                       └──────────────────┘
         │                              │
         │◄─────────────────────────────┘
         │
         └──→ Create Encrypted Export
                ├─ All plain PINs in CSV
                ├─ Encrypt with Fernet
                ├─ Store file in Frappe
                └─ Auto-delete after 30 days

RESULT: Status changes Draft → Generated
        1000 Voucher Cards created
        Encrypted export ready to download

CARD LIFECYCLE BEGINS:
VCH-000001: Status = Available (in warehouse)
VCH-000002: Status = Available (in warehouse)
... (all 1000 cards available for allocation)
```

**Phase 34 is where PINs are created and secured. Cards sit in "Available" status.**

---

### Phase 35: Allocation & Distribution

```
                    ┌──────────────────────────┐
                    │  VOUCHER ALLOCATION      │
                    │  - allocation_type       │
                    │  - batch (VBATCH-001)    │
                    │  - customer (Cairo Lib)  │
                    │  - sale_model (Prepaid)  │
                    │  - status (Draft→...)    │
                    └──────────────┬───────────┘
                                   │
                    ┌──────────────┴────────────┐
                    │                           │
         [Admin fills 100 cards]    [Auto-fills Available]
              Manual add/remove      VCH-000001
              Edit child table        VCH-000002
                                     ...
                                     VCH-000100
                                   │
                        ┌──────────┴─────────┐
                        │                    │
                  [Auto-Approve]      [Requires Approval]
                  (library config)    (library config)
                        │                    │
              Status: Completed      Status: Pending
              Cards: Allocated       Cards: Still Available
              Invoice: Created       Invoice: Not yet
                        │                    │
                        │         ┌──────────┘
                        │         │
                        │    [Admin clicks Approve]
                        │         │
                        │    Status: Completed
                        │    Cards: Allocated
                        │    Invoice: Created
                        │         │
                        └─────────┴──────┐
                                         │
                                    ✓ RESULT
                                    100 cards in Cairo Lib
                                    Invoice: 100 × $10 × 90% = $900
```

**Phase 35 moves cards from "Available" to "Allocated". Invoices created (Prepaid) or queued (Consignment).**

---

### Phase 36: Student Redemption

```
STUDENT ENTERS PIN IN APP          BACKEND PROCESSING

┌──────────────────────┐    ┌─────────────────────────────┐
│ PIN: 4a7f2k9x8b3q   │    │ API: /api/v1/voucher/preview│
│ [Submit]            │───→│ 1. Hash PIN with HMAC-SHA256 │
└──────────────────────┘    │ 2. Compare with DB          │
         │                  │ 3. Check card status         │
         │                  │ 4. Filter owned grants       │
         │                  │ 5. Return available products │
         │                  └────────────┬──────────────────┘
         │                               │
         │                    ┌──────────┴─────────┐
         │                    │                    │
         │            Response: OK            Response: ERROR
         │       "Premium Plan available"    "INVALID_PIN" / etc
         │                    │                    │
         │                    │        ┌───────────┘
         │                    │        │
         │         ┌──────────┘        │ Log failure attempt
         │         │                   │ Increment rate limit
         │         │                   │ Return error message
         │     [Student clicks                  │
         │      Redeem]
         │         │
         ├────────→├─ API: /api/v1/voucher/redeem
         │         │   1. Lock card (SELECT FOR UPDATE)
         │         │   2. Mark status = Redeemed
         │         │   3. Create Subscription Transaction
         │         │   4. Run existing Phase 23 pipeline
         │         │   5. Add content to student account
         │         │
         │         └─→ ✓ SUCCESS: Content visible
         │
         │    Log entry created (audit trail)
         │    VCH-000001 | ahmed_2024 | Success | ****2k9x | Cairo | Feb 10 2:30 PM


CARD STATE TRANSITIONS:
Allocated → Redeemed (terminal)    ✓ Student used card
Allocated → ALREADY_OWNED ✗        ✗ Card NOT consumed (try another product)
Available → INVALID_PIN ✗          ✗ Card doesn't exist
Allocated → ALREADY_REDEEMED ✗     ✗ Someone else used it
... (9 error codes total)
```

**Phase 36 is where cards are actually used. Student sees content unlock. Everything is logged.**

---

### Phase 37: Financial Integration

```
PREPAID MODEL (Invoice Immediately)

Allocation Created + Submitted
            │
            ├─ Sale Model = Prepaid?  YES
            │
            ├─→ Call: _create_prepaid_invoice()
            │
            ├─→ Create Sales Invoice
            │   ├─ Customer: Cairo Library
            │   ├─ 100 line items (one per card)
            │   ├─ Commission calculated:
            │   │  · Level 1: Batch grant override? NO
            │   │  · Level 2: Library default? YES (10%)
            │   │  · Use 10%
            │   ├─ Total: 100 × $10 × (100% - 10%) = $900
            │   └─ Submit invoice
            │
            └─→ Invoice awaiting payment


CONSIGNMENT MODEL (Invoice Monthly)

Allocation Created + Submitted (NO INVOICE YET)
            │
            ├─ Sale Model = Consignment
            │
            └─→ Cards go to library, awaiting redemptions
                    │
        [Time passes, students redeem cards]
                    │
            [1st of next month, 2:00 AM]
                    │
        generate_monthly_invoices() RUNS
                    │
                    ├─ Query: Redeemed cards from last month
                    ├─ Group by library
                    ├─ For each library:
                    │  ├─ Count redeemed cards by batch
                    │  ├─ Calculate commission per batch
                    │  ├─ Create one invoice with multi-line items
                    │  └─ Submit invoice
                    │
                    └─→ Invoices created for February redeemed cards


COMMISSION CALCULATION (Smart 3-Level Priority)

For each card:
   Level 1: Check Batch Grant
         ├─ Does this batch have commission override?
         │  YES → Use it (e.g., 15%)
         │  NO  → Go to Level 2
         │
   Level 2: Check Library (Customer) Default
         ├─ Does library have commission_type set?
         │  YES → Use it (e.g., 10%)
         │  NO  → Go to Level 3
         │
   Level 3: Zero
         ├─ Use 0% (admin keeps 100%)


EXAMPLE COMMISSION CHAIN FOR ONE CARD

Batch VBATCH-001, Product "Premium Plan", Library "Cairo"

Level 1 Check:
  ├─ Batch Grant: commission_type = NULL (not set)
  └─ Result: NOT OVERRIDDEN, go to Level 2

Level 2 Check:
  ├─ Customer "Cairo Library":
  │  ├─ voucher_commission_type = "Percentage"
  │  ├─ voucher_commission_value = "10%"
  └─ Result: USE 10%

Calculation:
  Face Value: $10
  Commission: 10% = $1
  Library Gets: $1
  Admin Gets: $9
```

**Phase 37 calculates money owed and creates invoices. Prepaid is immediate, Consignment is monthly.**

---

### Phase 38: Reports & Monitoring

```
┌─────────────────────────────────────────────────────────┐
│             REPORTING LAYER                             │
│                                                         │
│  All reports query from: Voucher Card + Redemption Log │
│  All reports cached for 5 minutes                       │
└─────────────┬──────────────────────────────────────────┘
              │
              ├─ SALES BY LIBRARY (RPT-01)
              │  ├─ Query: Redeemed cards per library
              │  ├─ Calculate revenue
              │  ├─ Filter: Date range, library, model
              │  └─ Display: [Library | Redeemed | Revenue]
              │
              ├─ BATCH PERFORMANCE (RPT-02)
              │  ├─ Query: Card status distribution per batch
              │  ├─ Calculate redemption rate %
              │  ├─ Calculate days until season expires
              │  └─ Display: [Batch | Total | Available | Allocated | Redeemed | Expired | %]
              │
              ├─ CONSIGNMENT RECONCILIATION (RPT-03)
              │  ├─ Query: Consignment cards per library
              │  ├─ Count: Allocated, Redeemed, Uninvoiced
              │  ├─ Calculate: Amount due (with commission)
              │  └─ Display: [Library | Allocated | Redeemed | Uninvoiced | Amount Due]
              │
              └─ SECURITY AUDIT (RPT-04)
                 ├─ Query: Failed redemption attempts
                 ├─ Group by: Player, IP, failure reason
                 ├─ Filter: Date range, player, type
                 └─ Display: [Player | IP | Failure | Attempts | First Try | Last Try]


AUTOMATIC CLEANUP LAYER

Every Day at 1:05 AM:
  ├─ expire_season_cards()
  │  ├─ Find batches linked to ended seasons
  │  ├─ Mark Available/Allocated cards as Expired
  │  └─ Keep Redeemed cards (finalized)
  │
  └─ cleanup_expired_exports()
     ├─ Find encrypted CSV exports older than 30 days
     └─ Delete from file system
```

**Phase 38 provides visibility (reports) and automatic cleanup (expiration).**

---

## Complete Card Lifecycle (State Machine)

```
                    CREATION → ALLOCATION → REDEMPTION → TERMINAL
                       │           │            │           │
                       ▼           ▼            ▼           ▼

VCH-000001: Available ────► Allocated ────► Redeemed ════════ (FINAL)
VCH-000002: Available ────► Allocated ────► Void       ════════ (FINAL)
VCH-000003: Available ────► Void           ════════════════════ (FINAL)
VCH-000004: Available ────► Allocated ────► ALREADY_OWNED (card not consumed)
VCH-000005: Available ────► (expires) ────► Expired    ════════ (FINAL)


STATE MACHINE RULES:

Available   → Can go to: Allocated, Void, Expired
Allocated   → Can go to: Redeemed, Void, Expired, Available (return)
Redeemed    → TERMINAL (final, cannot change)
Void        → TERMINAL (final, cannot change)
Expired     → TERMINAL (final, cannot change)


IMPORTANT: Once in terminal state, card cannot be modified
           (Redeemed = used, Void = broken, Expired = season ended)
```

---

## Integration Points (How Phases Connect)

### Phase 33 ↔ Phase 34
```
Phase 33 creates: Voucher Batch, Voucher Card (empty), Batch Grants
Phase 34 uses:    Batch schema to store generated cards
              ↓
Creates:         1000 Voucher Card records with HMAC hashes
```

### Phase 34 ↔ Phase 35
```
Phase 34 creates: Cards with status=Available
Phase 35 uses:    Fill Cards → Queries Available status
              ↓
Transitions:     Available → Allocated
Creates:         Voucher Allocation records linking cards to library
```

### Phase 35 ↔ Phase 37
```
Phase 35 creates: Voucher Allocation (Prepaid)
Phase 37 uses:    on_allocation_completed event
              ↓
Creates:         Sales Invoice immediately (Prepaid)
                 OR queues for monthly job (Consignment)
```

### Phase 35 ↔ Phase 36
```
Phase 35 creates: Allocated cards at library
Phase 36 uses:    Student enters PIN
              ↓
Finds:           Card with matching HMAC
Transitions:     Allocated → Redeemed
Creates:         Subscription Transaction + Redemption Log entry
```

### Phase 36 ↔ Phase 37
```
Phase 36 creates: Redemption Log entries + status transitions
Phase 37 uses:    Monthly job queries Redeemed cards from previous month
              ↓
For Consignment: Creates Sales Invoice for redeemed cards
```

### Phase 37 ↔ Phase 38
```
Phase 37 creates: Sales Invoices
Phase 38 uses:    Reports query against invoice data + redemption logs
              ↓
Reports show:    Revenue per library, commission due, consignment status
```

### All Phases ↔ Phase 38 (Monitoring)
```
Phase 38 provides visibility into all previous phases:
  · Batch Performance (Phase 34 health)
  · Allocation status (Phase 35 status)
  · Redemption logs (Phase 36 activity)
  · Sales by library (Phase 37 revenue)
  · Security patterns (Phase 36 fraud detection)
```

---

## Data Consistency Guarantees

### Transaction Isolation
```
When card is being redeemed:
  1. SELECT card FOR UPDATE (lock row)
  2. Check all conditions (status, batch, season, etc.)
  3. If all pass:
     a. Mark card Redeemed
     b. Create Subscription Transaction
     c. COMMIT
  4. If any fail:
     a. Do NOT mark card Redeemed
     b. ROLLBACK
     c. Return error to student

Result: Exactly one student per card (no double-redemption)
```

### Audit Trail (Write-Once)
```
Voucher Redemption Log is READ-ONLY after creation
  · Cannot edit entries (no write permission)
  · Cannot delete entries (no delete permission)
  · Tamper-proof for accounting compliance
  · PINs masked as ****last4 (security)
  · Every attempt logged (success and failure)
```

### Financial Accuracy
```
Invoice amounts calculated with Decimal precision:
  · No float arithmetic (avoids rounding errors)
  · Quantize at every step (0.01 precision for currency)
  · Commission priority chain enforced (product > library > zero)

Result: Invoices always match card counts (100 cards @ $10 × 90% = $900)
```

---

## Security Architecture

### PIN Protection (Multi-Layer)
```
Layer 1: Generation Security
  · Use secrets.choice() (cryptographically secure)
  · NOT random.choice() (predictable)

Layer 2: Storage Security
  · Store HMAC-SHA256 hash (one-way encryption)
  · Never store plaintext PIN
  · Hash uses site_config voucher_hmac_secret key

Layer 3: Transmission Security
  · PIN hashed in FastAPI before sending to Frappe
  · Frappe compares using hmac.compare_digest() (timing-safe)
  · Prevents timing attacks (attacker can't guess by response time)

Layer 4: Export Security
  · Encrypted CSV using Fernet (AES)
  · Auto-delete after 30 days
  · HMAC secret required to decrypt

Result: Even if database hacked, PINs cannot be extracted
```

### Fraud Detection
```
Rate Limiting (Failed Attempts Only):
  · 5 attempts/hour per player
  · 20 attempts/hour per IP
  · Prevents brute force (guessing PINs)
  · Auto-expires via Redis TTL

Logging:
  · Every attempt logged to Redemption Log
  · IP address recorded (fraud detection)
  · Patterns visible in Security Audit report

Response:
  · RATE_LIMITED error after threshold
  · Admin can block IP or investigate account
```

---

## Performance Characteristics

### Database Operations

| Operation | Time | Scale |
|-----------|------|-------|
| Generate 1000 cards | 10-30s | Bulk insert with transaction |
| Allocate 100 cards | <1s | SQL UPDATE with WHERE clause |
| Process redemption | <50ms | SELECT FOR UPDATE + 2 inserts |
| Run monthly invoicing job | 5-10s | 50,000+ cards processed by library |
| Sales by Library report | <2s | Aggregation over 100,000+ records |

### Caching

```
Reports:       Cached 5 minutes (refresh page to update)
Rate limits:   Redis with auto-expiry (1 hour TTL)
Exports:       Encrypted file auto-deleted after 30 days
Scheduled jobs: Queued asynchronously, no blocking
```

---

## Disaster Recovery

### What Happens If...

**Database corrupted?**
  · All data replicated from source of truth (MariaDB)
  · Redemption Log is append-only (cannot corrupt)
  · Invoices are submitted (cannot be edited)

**Card generated with wrong PIN length?**
  · Void batch → All cards become Void
  · Batch status → Closed
  · Create new batch with correct PIN length

**Student redeemed card but didn't get content?**
  · Check Redemption Log for status=Success
  · Check Subscription Transaction created
  · If missing: Contact dev for manual recovery

**Invoice amount wrong?**
  · Create Credit Note to reverse
  · Create new Sales Invoice to adjust
  · Maintain audit trail of corrections

**Season expiration too aggressive?**
  · Expired cards cannot be un-expired
  · Plan new season before current one ends
  · Consider longer seasons to avoid gaps

---

## Admin Decision Tree

```
START
  │
  ├─ New voucher program?
  │  └─ Go to Phase 33-34 (batch creation)
  │
  ├─ Distribute to library?
  │  └─ Go to Phase 35 (allocation)
  │
  ├─ Student reporting card error?
  │  └─ Go to Phase 36 (redemption logs)
  │
  ├─ Money issues?
  │  └─ Go to Phase 37 (invoicing)
  │
  ├─ Need to monitor performance?
  │  └─ Go to Phase 38 (reports)
  │
  ├─ Detect fraud?
  │  └─ Go to Phase 38 (security audit)
  │
  └─ Card damaged/need to cancel?
     └─ Go to Phase 34 (void card)
```

---

## Summary: Why Each Phase Exists

| Phase | Creates | Purpose | Without It |
|-------|---------|---------|-----------|
| **33** | Database schema | Store vouchers safely | No way to track cards or students |
| **34** | Secure PIN cards | Generate cards for printing | Cannot create or distribute cards |
| **35** | Allocation workflow | Get cards to libraries | No way to distribute or track allocation |
| **36** | Student redemption API | Let students unlock content | Cards exist but students can't use them |
| **37** | Financial tracking | Calculate commissions, invoices | No money tracking or payment system |
| **38** | Reports + automation | Monitor performance, expire old cards | No visibility into business health |

**All 6 phases together = Complete voucher system**

---

**Last Updated:** February 14, 2026
**For questions:** Refer to ADMIN_GUIDE_PHASES_33-38.md or ADMIN_QUICK_REFERENCE.md
