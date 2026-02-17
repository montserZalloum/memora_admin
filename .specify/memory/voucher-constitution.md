<!--
SYNC IMPACT REPORT
==================
Version change: N/A → 1.0.0 (initial ratification)
Modified principles: N/A (initial)
Added sections:
  - Core Principles (5): Cryptographic Security, Auditable Lifecycle,
    Financial Precision, Self-Healing Architecture, Test-First Coverage
  - Architecture & Constraints (domain glossary, DocType hierarchy,
    service layer, integration points, hard constraints)
  - Known Gaps & Remediation (10 identified gaps with severity ratings)
  - Governance
Removed sections: N/A (initial)
Templates requiring updates:
  - .specify/templates/plan-template.md — Constitution Check gates
    derivable from principles ✅ (no update needed, gates are filled at
    plan time)
  - .specify/templates/spec-template.md — FR-XXX format compatible ✅
  - .specify/templates/tasks-template.md — phase-based structure
    compatible ✅
Follow-up TODOs: None
-->

# Memora Voucher System Constitution

## Core Principles

### I. Cryptographic Security (NON-NEGOTIABLE)

Every voucher PIN MUST be generated using `secrets.choice()` from a
30-character unambiguous alphabet. The `random` module is FORBIDDEN for
any security-sensitive operation.

- **PIN storage**: HMAC-SHA256 hash only. Plaintext MUST NEVER be
  persisted in the database.
- **PIN verification**: MUST use `hmac.compare_digest()` (timing-safe
  comparison) to prevent timing attacks.
- **Export encryption**: Fernet (AES-128-CBC + HMAC-SHA256) with
  HKDF-SHA256-derived key from `site_config.json` secret. Export
  access requires System Manager role and produces an audit log entry
  in the Batch Export Log child table.
- **HMAC secret**: Stored in `site_config.json` as
  `voucher_hmac_secret`. MUST NOT exist in the database or version
  control.
- **Card locking**: Redemption flow MUST use `SELECT ... FOR UPDATE`
  to prevent race conditions.
- **Serial numbers**: Atomic `tabSeries` reservation with
  `FOR UPDATE` guarantees no gaps under concurrency.

**Rationale**: Voucher PINs are bearer credentials with monetary value.
Any weakness in generation, storage, or verification creates direct
financial exposure.

### II. Auditable Lifecycle

Every Batch, Card, and Allocation MUST follow its defined state machine
exactly. No state transition may occur outside the documented paths.

**Batch states**:
`Draft` -> `Generated` -> `Active` -> `Closed`

**Card states**:
`Available` -> `Allocated` -> `Redeemed` | `Void` | `Expired`
(with `Allocated` -> `Available` on return allocation)

**Allocation states**:
`Draft` -> `Pending Approval` -> `Approved` -> `Completed` | `Rejected`
(auto-approve path: `Draft` -> `Approved` -> `Completed` when library
does not require approval)

- The `Memora Voucher Redemption Log` is IMMUTABLE. Every redemption
  attempt (success or failure) MUST be recorded with its outcome code.
- Terminal card states (`Redeemed`, `Void`, `Expired`) are
  irreversible.
- Batch counters (`available_count`, `allocated_count`,
  `redeemed_count`, `void_count`, `expired_count`) MUST stay
  consistent with actual card states at all times.

**Rationale**: Vouchers represent financial instruments. Missing or
inconsistent audit trails create reconciliation failures and potential
fraud vectors.

### III. Financial Precision

All monetary calculations MUST use `decimal.Decimal` with
`ROUND_HALF_UP` to two decimal places. Float conversion is permitted
ONLY at the ERPNext ORM boundary (`float(result)`).

**Commission priority chain (FIN-03)**:
1. Batch Grant override (per-product commission on child row)
2. Library default (`voucher_commission_type` /
   `voucher_commission_value` on Customer)
3. Zero (no commission; full face value invoiced)

**Invoicing rules**:

| Sale Model     | Invoice Trigger         | Amount Formula                       | Credit Note Trigger          |
|----------------|-------------------------|--------------------------------------|------------------------------|
| **Prepaid**    | Allocation completes    | `(face_value - commission) * qty`    | Return allocation completes  |
| **Consignment**| Card redeemed           | `(face_value - commission) * 1`      | N/A                          |

- All invoices MUST use the `MEMORA-VOUCHER-CARD` item code.
- Sales Invoices MUST be created via Frappe ORM to ensure GL entries,
  tax calculation, and JoFotara e-invoicing hooks fire correctly.

**Rationale**: Jordanian Dinar (JOD) amounts require precise decimal
handling. Float arithmetic introduces rounding drift that compounds
across batch-scale operations.

### IV. Self-Healing Architecture

Redis is a hot cache. MariaDB (via Frappe ORM) is the source of truth.
All Redis-cached data MUST implement the `ensure_hydrated()` pattern:
on cache miss, automatically restore from MariaDB.

- `FrappeClient` MUST be injected via `deps.py` into every service
  that performs hydration. Without it, `ensure_hydrated()` silently
  skips with a `no_frappe_client` warning.
- **Cross-cache invalidation**: When DocType A's data feeds into
  Cache B (e.g., Plan Subject `meta_data` -> hierarchy
  `free_units`/`free_topics`), the event hook for A MUST invalidate
  ALL affected caches using the two-pronged pattern: direct
  `r.delete()` + pubsub publish.
- Denormalized fields (e.g., `free_units`, `free_topics`) MUST be
  populated by ALL producer code paths. Consumer code MUST NOT assume
  these fields are pre-populated.

**Rationale**: Redis data can be lost via FLUSHDB, restart, or eviction.
The system MUST remain correct after any cache loss event without manual
intervention.

### V. Test-First Coverage (NON-NEGOTIABLE)

Every feature MUST have corresponding tests before or alongside
implementation. The project starts from zero test infrastructure.

- Every state transition MUST have both a positive test (valid
  transition succeeds) and a negative test (invalid transition is
  rejected).
- Every error code in the redemption flow MUST have a dedicated test.
- Financial calculations MUST be tested with `Decimal` precision
  assertions (not float comparisons).
- Concurrency scenarios MUST be tested where atomic operations exist
  (serial reservation, card locking during redemption).
- Integration tests MUST follow the full lifecycle:
  `Batch -> Generate -> Allocate -> Redeem -> Invoice`.
- Fixtures MUST create minimal, isolated data. Each test MUST clean
  up after itself.

**Test framework**: Frappe's `frappe.tests.utils.FrappeTestCase` with
pytest runner. Test environment: `x.conanacademy.com`.

**Rationale**: The voucher system handles monetary transactions. Untested
state transitions and financial calculations create direct business risk.

## Architecture & Constraints

### Domain Glossary

| Term              | Definition                                                    | Frappe DocType                      |
|-------------------|---------------------------------------------------------------|-------------------------------------|
| **Batch**         | Generation order: quantity, PIN length, face value, grants    | `Memora Voucher Batch`              |
| **Card**          | Individual voucher with serial number and HMAC-hashed PIN     | `Memora Voucher Card`               |
| **Library**       | B2B customer (bookstore, school, distributor)                 | `Customer` (Frappe core)            |
| **Allocation**    | Formal transfer of cards from batch to library                | `Memora Voucher Allocation`         |
| **Player**        | End-user who redeems a voucher PIN                            | `Memora Player Profile`             |
| **Product Grant** | Digital product/subscription a voucher unlocks                | `Memora Product Grant`              |
| **Season**        | Time-bounded academic period; expired seasons expire cards     | `Memora Season`                     |
| **Redemption Log**| Immutable audit trail of every redemption attempt             | `Memora Voucher Redemption Log`     |
| **Sale Model**    | Payment method: Prepaid (at allocation) or Consignment (at redemption) | Field on Allocation + Card |
| **Commission**    | Library's cut via priority chain                              | Calculated at invoice time          |
| **Face Value**    | Monetary value per card, set at batch level                   | Currency field on Batch             |

### DocType Hierarchy

```
Memora Voucher Batch (parent)
+-- Memora Voucher Batch Grant (child table)
+-- Memora Voucher Batch Export Log (child table)
+-- Memora Voucher Card (linked via batch field)

Memora Voucher Allocation (parent)
+-- Memora Voucher Allocation Card (child table)

Memora Voucher Redemption Log (standalone, immutable)
```

### Service Layer

| Module          | Path                              | Responsibility                            |
|-----------------|-----------------------------------|-------------------------------------------|
| `generator.py`  | `services/voucher/generator.py`   | PIN generation, HMAC, serial reservation  |
| `crypto.py`     | `services/voucher/crypto.py`      | HKDF key derivation, Fernet encrypt/decrypt|
| `commission.py` | `services/voucher/commission.py`  | Commission chain resolution, Decimal math |
| `invoice.py`    | `services/voucher/invoice.py`     | Sales Invoice/Credit Note creation        |

### API Layer

| Module          | Path                | Key Endpoints                                            |
|-----------------|---------------------|----------------------------------------------------------|
| `voucher.py`    | `api/voucher.py`    | `generate_batch`, `export_for_print`, `void_batch`, `void_card`, `preview_voucher`, `redeem_voucher` |
| `allocation.py` | `api/allocation.py` | `fill_cards`, `submit_allocation`, `approve_allocation`, `reject_allocation` |

### Integration Points

| System                 | Direction          | Mechanism                                                |
|------------------------|--------------------|----------------------------------------------------------|
| Subscription Pipeline  | Voucher -> Subscription | `redeem_voucher` creates `Memora Subscription Transaction` (two-step save) |
| ERPNext Accounting     | Voucher -> Sales Invoice | ORM-based creation ensures GL entries + JoFotara hooks |
| Redis                  | Subscription sync  | Access keys pushed via `SADD`                            |
| Frappe Background Jobs | Generation         | `frappe.enqueue` with 600s timeout                       |

### Hard Constraints

- **Tech Stack**: Frappe Framework (Python 3.11+, MariaDB, Redis)
- **Max Batch Size**: 1,000 cards (enforced in `generate_batch`)
- **PIN Lengths**: 12, 14, or 16 characters (Select field)
- **Currency**: JOD (Jordanian Dinar)
- **Invoice Item Code**: `MEMORA-VOUCHER-CARD`
- **Permissions**: System Manager role for all admin operations
- **Test Environment**: x.conanacademy.com
- **Scheduled Jobs**: `expire_season_cards` runs daily at 01:05

## Known Gaps & Remediation

| ID     | Gap                                                              | Severity | Area       |
|--------|------------------------------------------------------------------|----------|------------|
| GAP-01 | Rate limiting referenced in Redemption Log but not implemented   | High     | Security   |
| GAP-02 | Consignment invoicing not implemented (only Prepaid exists)      | High     | Financial  |
| GAP-03 | Batch auto-close logic missing (no auto-transition to Closed)    | Medium   | Lifecycle  |
| GAP-04 | `Memora Voucher Allocation Card` child table DocType not found   | Medium   | Schema     |
| GAP-05 | `sales_invoice` field in invoice.py but not in Card DocType JSON | Medium   | Schema     |
| GAP-06 | Customer commission custom fields assumed but not verified        | Medium   | Schema     |
| GAP-07 | No test infrastructure exists (zero unit or integration tests)   | Critical | QA         |
| GAP-08 | `bulk_insert` comment says max 1000 but code passes 10,000      | Low      | Code       |
| GAP-09 | Season expiration does not update batch counters                 | Medium   | Counters   |
| GAP-10 | Card autoname vs generator serial_no format potential conflict   | Medium   | Schema     |

Each gap MUST be addressed with a specification, implementation, and
test before the voucher system is considered production-ready.
GAP-07 (test infrastructure) is the highest priority as it blocks
validation of all other gap remediations.

## Governance

- This constitution is the authoritative reference for all voucher
  system development. It supersedes ad-hoc decisions and informal
  agreements.
- **Amendments** require: (1) documented rationale, (2) impact
  assessment on existing code, (3) updated version number following
  SemVer (MAJOR for principle removals/redefinitions, MINOR for new
  principles/sections, PATCH for clarifications).
- **Compliance review**: Every PR touching voucher code MUST be
  verified against the relevant principles before merge. The
  Constitution Check gate in `plan-template.md` enforces this at
  design time.
- **Gap tracking**: New gaps discovered during development MUST be
  added to the Known Gaps table with severity rating and area
  classification.
- **Runtime guidance**: See `CLAUDE.md` for development commands,
  Redis key reference, and operational procedures.

**Version**: 1.0.0 | **Ratified**: 2026-02-15 | **Last Amended**: 2026-02-15
