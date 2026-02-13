# Technology Stack: Voucher Management System (v3.0)

**Project:** Memora - Voucher/Recharge Card Distribution
**Researched:** 2026-02-13
**Overall Confidence:** HIGH

## Context

This is a SUBSEQUENT MILESTONE stack research. The core stack (Frappe v15, FastAPI, Redis, MariaDB, PyJWT) is already validated and running in production. This document covers ONLY the new capabilities needed for the voucher system.

---

## New Dependencies Required

### 1. cryptography (Fernet for Encrypted Export Files)

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| cryptography | >=44.0.0 | AES-encrypted PIN export files | Fernet provides authenticated encryption (AES-128-CBC + HMAC-SHA256) with a simple, misuse-resistant API. Single function call to encrypt/decrypt. No IV management, no padding bugs, no mode selection errors. | HIGH |

**Why Fernet over raw AES-GCM:**
- Fernet is the "high-level recipes" layer of the cryptography library -- designed to prevent misuse
- Handles IV generation, HMAC verification, and padding automatically
- AES-GCM requires manual nonce management where nonce reuse is catastrophic
- The export file is a one-shot operation (encrypt at batch creation, decrypt on download) -- no streaming needed
- Fernet's AES-128 is more than sufficient for this use case (protecting PIN lists that have a limited validity period)

**Why NOT PyCryptodome:**
- The `cryptography` library is the pyca (Python Cryptographic Authority) maintained package
- Better maintained, more widely audited, and recommended by the Python community
- PyCryptodome is fine but adds a parallel dependency when `cryptography` covers all needs

**Usage Pattern (Encrypted Export File):**
```python
from cryptography.fernet import Fernet

# At batch creation time -- generate key, encrypt PIN list
def create_encrypted_export(pins: list[dict], batch_name: str) -> tuple[bytes, bytes]:
    """Generate encrypted file containing plaintext PINs.

    Returns (encrypted_content, fernet_key) -- key stored in site_config.json
    or per-batch in a secure field.
    """
    key = Fernet.generate_key()  # 32-byte URL-safe base64-encoded key
    f = Fernet(key)

    # Build CSV content with serial_number, pin columns
    csv_content = "serial_number,pin\n"
    for pin_data in pins:
        csv_content += f"{pin_data['serial']},{pin_data['pin']}\n"

    encrypted = f.encrypt(csv_content.encode("utf-8"))
    return encrypted, key

# At download time -- admin provides batch password/key
def decrypt_export(encrypted_content: bytes, key: bytes) -> bytes:
    f = Fernet(key)
    return f.decrypt(encrypted_content)  # Returns plaintext CSV bytes
```

**Key Management:**
- Store the Fernet key in `site_config.json` as `voucher_export_encryption_key` (single key for all exports)
- OR store per-batch keys in a Password-type field on the Voucher Batch DocType (Frappe encrypts Password fields at rest)
- Recommendation: Single key in `site_config.json` -- simpler, and the threat model is protecting at-rest files, not per-batch isolation

**Installation:**
```bash
pip install "cryptography>=44.0.0"
```

**Version Verification:** Latest release is 44.0.0 (February 2026 per PyPI). Supports Python 3.8+.

**Sources:**
- [Fernet Documentation](https://cryptography.io/en/latest/fernet/) (HIGH confidence)
- [cryptography on PyPI](https://pypi.org/project/cryptography/) (HIGH confidence)

---

### 2. No New Dependencies Required (Python stdlib)

The following capabilities are provided by Python 3.10+ standard library modules. No pip installs needed.

#### 2a. hmac + hashlib (HMAC-SHA256 for PIN Storage)

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| hmac (stdlib) | Python 3.10+ | Keyed hash for PIN storage | PRD specifies HMAC-SHA256. Server-side secret means even full DB dump cannot reverse PINs without the key. Constant-time comparison via `hmac.compare_digest()`. | HIGH |
| hashlib (stdlib) | Python 3.10+ | SHA-256 digest function | Used as the hash function parameter for hmac. | HIGH |

**Why HMAC-SHA256 and NOT bcrypt for voucher PINs:**

This is a deliberate, defensible design choice for THIS specific use case. The security analysis:

1. **PINs are system-generated with high entropy** -- 12-character alphanumeric codes (62^12 = ~2.27 x 10^21 combinations), NOT user-chosen 4-digit PINs. Brute-force is computationally infeasible regardless of hash speed.

2. **Server-side secret key adds a defense layer** -- HMAC requires knowledge of the secret key stored in `site_config.json`. Even with full database access, an attacker cannot compute valid HMACs without the key. This is equivalent to a "pepper" in password hashing.

3. **Performance at batch generation** -- Generating 5,000 PINs per batch requires 5,000 HMAC operations. HMAC-SHA256 runs in microseconds per operation. bcrypt at cost=12 takes ~250ms per hash = 20+ minutes for a single batch. This matters for the background job timeout.

4. **Performance at redemption** -- Single HMAC computation + constant-time comparison. No bcrypt's intentional slowness when the rate limiter already provides brute-force protection (5/hour per player, 20/hour per IP).

5. **The PRD explicitly specifies HMAC-SHA256** -- this is an architectural decision already made.

**IMPORTANT SECURITY NOTE:** This analysis ONLY applies because PINs are high-entropy system-generated codes with server-side key protection. For user-chosen passwords, ALWAYS use bcrypt/argon2/PBKDF2.

**Usage Pattern:**
```python
import hmac
import hashlib

# Secret key from site_config.json
HMAC_SECRET = frappe.conf.get("voucher_hmac_secret")

def hash_pin(pin: str) -> str:
    """Compute HMAC-SHA256 of a PIN using server-side secret."""
    return hmac.new(
        HMAC_SECRET.encode("utf-8"),
        pin.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

def verify_pin(pin: str, stored_hash: str) -> bool:
    """Constant-time comparison to prevent timing attacks."""
    computed = hash_pin(pin)
    return hmac.compare_digest(computed, stored_hash)
```

**Key Management:**
- Store HMAC secret in `site_config.json` as `voucher_hmac_secret`
- Access via `frappe.conf.get("voucher_hmac_secret")` in Frappe context
- Access via environment variable `VOUCHER_HMAC_SECRET` in FastAPI context (add to `.env`)
- Generate with: `python3 -c "import secrets; print(secrets.token_hex(32))"`
- MUST be at least 32 bytes (256 bits) of entropy

**Sources:**
- [Python hmac module docs](https://docs.python.org/3/library/hmac.html) (HIGH confidence)
- [OWASP Password Storage Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html) (HIGH confidence -- for understanding when HMAC is vs is not appropriate)

#### 2b. secrets (Cryptographic Random PIN Generation)

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| secrets (stdlib) | Python 3.10+ | Generate cryptographically secure random PINs | Purpose-built for security-sensitive random generation. Uses OS entropy source. Explicitly recommended over `random` module for tokens/PINs. | HIGH |

**Why NOT `random` module:** The `random` module uses Mersenne Twister PRNG which is predictable. `secrets` uses `os.urandom()` which draws from the OS CSPRNG. For voucher PINs that represent monetary value, cryptographic randomness is mandatory.

**Usage Pattern:**
```python
import secrets
import string

# PIN alphabet: uppercase + digits, excluding ambiguous chars (0/O, 1/I/L)
PIN_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # 30 chars
PIN_LENGTH = 12

def generate_pin() -> str:
    """Generate a 12-character cryptographically random PIN.

    Entropy: log2(30^12) = ~58.6 bits per PIN.
    With 5,000 PINs per batch, collision probability is negligible.
    """
    return "".join(secrets.choice(PIN_ALPHABET) for _ in range(PIN_LENGTH))
```

**Sources:**
- [Python secrets module docs](https://docs.python.org/3/library/secrets.html) (HIGH confidence)

#### 2c. csv (CSV File Generation)

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| csv (stdlib) | Python 3.10+ | Generate CSV content for PIN export | Standard library, no edge cases with encoding. Used for the plaintext content that gets encrypted by Fernet. | HIGH |

**Why NOT pandas:** Overkill for simple tabular data with 2-3 columns and 5,000 rows. csv module is zero-dependency and handles the quoting/escaping correctly.

#### 2d. io (In-Memory File Handling)

| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| io.StringIO / io.BytesIO (stdlib) | Python 3.10+ | In-memory file buffer for CSV generation | Build CSV in memory before encrypting. No temp file needed on disk. | HIGH |

---

## Frappe-Specific Patterns (No New Dependencies)

### 3. Batch Document Creation with frappe.db.bulk_insert

**Confidence:** HIGH -- Verified in Frappe v15 source code.

For generating 500-5,000 Voucher Card documents per batch, use `frappe.db.bulk_insert()` instead of individual `doc.insert()` calls.

| Pattern | Performance | When to Use |
|---------|-------------|-------------|
| `doc.insert()` loop | ~50-100 docs/sec | When hooks/validation needed per doc |
| `frappe.db.bulk_insert()` | ~5,000-10,000 docs/sec | When bypassing hooks is acceptable |
| Raw SQL INSERT | ~10,000+ docs/sec | When maximum performance needed |

**Recommended: `frappe.db.bulk_insert()` in a background job.**

**API Signature (Frappe v15):**
```python
frappe.db.bulk_insert(
    doctype: str,
    fields: list[str],
    values: Iterable[Sequence[Any]],
    ignore_duplicates: bool = False,
    *,
    chunk_size: int = 1000,
)
```

**Usage Pattern for Voucher Card Generation:**
```python
import frappe
import secrets
import hmac
import hashlib
from datetime import datetime

def generate_voucher_cards(batch_name: str, quantity: int):
    """Generate voucher cards in bulk using frappe.db.bulk_insert.

    Called from a background job (frappe.enqueue) triggered by
    Voucher Batch on_submit or a button action.
    """
    batch = frappe.get_doc("Memora Voucher Batch", batch_name)
    hmac_secret = frappe.conf.get("voucher_hmac_secret")

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
    pins_for_export = []

    fields = [
        "name", "batch", "serial_number", "pin_hash",
        "status", "owner", "creation", "modified",
        "modified_by", "docstatus",
    ]

    def generate_values():
        for i in range(quantity):
            serial = f"VCH-{batch.serial_prefix}-{i+1:06d}"
            pin = generate_pin()
            pin_hash = hmac.new(
                hmac_secret.encode(), pin.encode(), hashlib.sha256
            ).hexdigest()

            pins_for_export.append({"serial": serial, "pin": pin})

            yield (
                serial,              # name (= serial_number for this doctype)
                batch_name,          # batch (Link)
                serial,              # serial_number
                pin_hash,            # pin_hash
                "Available",         # status
                "Administrator",     # owner
                now,                 # creation
                now,                 # modified
                "Administrator",     # modified_by
                0,                   # docstatus
            )

    frappe.db.bulk_insert(
        "Memora Voucher Card",
        fields,
        generate_values(),
        chunk_size=1000,
    )

    # Generate encrypted export file with plaintext PINs
    create_encrypted_export_file(batch_name, pins_for_export)

    frappe.db.commit()
```

**Critical Notes:**
- `bulk_insert` bypasses all document hooks (`validate`, `before_insert`, `after_insert`)
- Must manually set `name`, `owner`, `creation`, `modified`, `modified_by`, `docstatus`
- Must run inside `frappe.enqueue()` for batches > 100 cards to avoid request timeout
- Use `chunk_size=1000` (default) -- good balance of memory and DB performance
- The generator pattern (`yield`) avoids holding all 5,000 records in memory at once

**Sources:**
- [Frappe Database API](https://docs.frappe.io/framework/v15/user/en/api/database) (HIGH confidence)
- [Frappe bulk_insert source](https://github.com/frappe/frappe/blob/develop/frappe/database/database.py) (HIGH confidence)
- [Deferred Bulk Inserts in Frappe](https://tej.sh/blog/frappe-deferred-bulk/) (MEDIUM confidence)

---

### 4. Background Job Enqueuing with frappe.enqueue

**Confidence:** HIGH -- Pattern already used in Frappe ecosystem, verified against v15 docs.

Batch creation (PIN generation + encryption + bulk insert) MUST run as a background job because:
- 5,000 PIN generation + HMAC: ~50ms (fast)
- 5,000 bulk_insert: ~1-2 seconds
- Fernet encryption of export file: ~10ms
- File save: ~50ms
- Total: ~2-3 seconds -- acceptable in background, too slow for HTTP request

**Usage Pattern:**
```python
@frappe.whitelist()
def generate_batch(batch_name: str):
    """Trigger batch generation as background job."""
    batch = frappe.get_doc("Memora Voucher Batch", batch_name)

    if batch.generation_status == "Completed":
        frappe.throw("Batch already generated")

    batch.db_set("generation_status", "Generating")

    frappe.enqueue(
        "memora_admin.memora_admin.services.voucher.generator.generate_voucher_cards",
        batch_name=batch_name,
        queue="default",           # 300s timeout, sufficient for 5K cards
        job_name=f"voucher_gen_{batch_name}",
        enqueue_after_commit=True,  # Ensure batch status is committed first
    )

    return {"status": "queued", "batch": batch_name}
```

**Queue Selection:**
| Queue | Timeout | Use When |
|-------|---------|----------|
| short | 300s | Batches < 1,000 cards |
| default | 300s | Batches 1,000-5,000 cards |
| long | 1,500s | Batches > 5,000 cards (unlikely) |

**Sources:**
- [Frappe Background Jobs v15](https://docs.frappe.io/framework/v15/user/en/api/background_jobs) (HIGH confidence)

---

### 5. Row-Level Locking with for_update (Atomic Redemption)

**Confidence:** HIGH -- Pattern already used in this codebase (`memora_lesson.py:33`).

The voucher redemption flow MUST use `SELECT ... FOR UPDATE` to prevent double-redemption race conditions. Frappe supports this via the `for_update` parameter on `frappe.get_doc()`.

**How it works (verified from Frappe source):**
```python
# frappe/model/document.py - load_from_db():
if self.flags.for_update and frappe.db.db_type != "sqlite":
    for_update = "FOR UPDATE"
# Generates: SELECT * FROM `tabMemora Voucher Card` WHERE `name` = %s FOR UPDATE
```

This acquires an exclusive row lock in MariaDB/InnoDB. The lock is held until the transaction commits or rolls back. Other transactions attempting to read the same row with `FOR UPDATE` will block until the lock is released.

**Usage Pattern (Redemption Whitelisted Method):**
```python
@frappe.whitelist(allow_guest=False)
def redeem_voucher(pin: str, player_id: str) -> dict:
    """Atomic voucher redemption with row-level locking.

    Called by FastAPI via Frappe API (POST /api/method/memora_admin.api.voucher.redeem_voucher).
    FastAPI handles JWT auth and rate limiting before proxying here.
    """
    # 1. Compute HMAC of submitted PIN
    hmac_secret = frappe.conf.get("voucher_hmac_secret")
    pin_hash = hmac.new(
        hmac_secret.encode(), pin.encode(), hashlib.sha256
    ).hexdigest()

    # 2. Find card by pin_hash (index lookup)
    card_name = frappe.db.get_value(
        "Memora Voucher Card",
        {"pin_hash": pin_hash, "status": "Allocated"},
        "name",
    )

    if not card_name:
        # Log failed attempt to Voucher Redemption Log
        log_redemption_attempt(pin_hash, player_id, success=False, reason="invalid_pin")
        frappe.throw("Invalid or unavailable PIN", frappe.ValidationError)

    # 3. Lock the card row -- prevents double redemption
    card = frappe.get_doc("Memora Voucher Card", card_name, for_update=True)

    # 4. Re-check status under lock (another request may have redeemed it)
    if card.status != "Allocated":
        log_redemption_attempt(pin_hash, player_id, success=False, reason="already_redeemed")
        frappe.throw("Card has already been redeemed", frappe.ValidationError)

    # 5. Transition state and link to player
    card.status = "Redeemed"
    card.redeemed_by = player_id
    card.redeemed_at = frappe.utils.now()
    card.save(ignore_permissions=True)

    # 6. Create Subscription Transaction (reuses existing Phase 23 pipeline)
    # This triggers the doc_event hook -> Player Subscription -> Redis SADD
    grants = get_batch_grants(card.batch)
    for grant in grants:
        create_subscription_transaction(player_id, grant, card)

    # 7. Log successful redemption
    log_redemption_attempt(pin_hash, player_id, success=True, card_name=card.name)

    frappe.db.commit()

    return {"status": "redeemed", "grants": [g.name for g in grants]}
```

**Critical: Index on pin_hash column.** The lookup `{"pin_hash": pin_hash}` MUST be backed by a database index. Add this in the DocType JSON or via a migration:
```python
# In after_migrate hook or DocType definition
frappe.db.add_index("Memora Voucher Card", ["pin_hash"])
```

**Why NOT raw SQL for locking:** `frappe.get_doc(..., for_update=True)` is the idiomatic Frappe pattern. It loads the full document (needed for `.save()`) and acquires the lock in one query. Raw `frappe.db.sql("SELECT ... FOR UPDATE")` would require a separate load step.

**Existing Codebase Precedent:**
```python
# memora_admin/doctype/memora_lesson/memora_lesson.py:33
subject = frappe.get_doc("Memora Subject", self.subject, for_update=True)
```

**Sources:**
- [Frappe Document class source](https://github.com/frappe/frappe/blob/develop/frappe/model/document.py) (HIGH confidence -- verified `for_update` generates SQL `FOR UPDATE`)
- Existing codebase usage in `memora_lesson.py` (HIGH confidence)

---

### 6. File Storage with frappe.utils.file_manager.save_file

**Confidence:** HIGH -- Verified from Frappe source code.

The encrypted export file needs to be saved as a private Frappe File document attached to the Voucher Batch.

**API Signature:**
```python
frappe.utils.file_manager.save_file(
    fname: str,           # Filename (e.g., "VCH-BATCH-001_export.enc")
    content: bytes,       # File content (encrypted bytes)
    dt: str,              # Parent DocType ("Memora Voucher Batch")
    dn: str,              # Parent document name ("VCH-BATCH-001")
    folder: str = None,   # Optional folder path
    decode: bool = False, # Whether to base64-decode content
    is_private: int = 0,  # 1 = store in /private/files/
    df: str = None,       # Field name on parent document
)
```

**Usage Pattern:**
```python
from frappe.utils.file_manager import save_file

def create_encrypted_export_file(batch_name: str, pins_for_export: list[dict]):
    """Create encrypted CSV and attach as private file to batch."""
    from cryptography.fernet import Fernet
    import csv
    import io

    # 1. Build CSV in memory
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["serial_number", "pin"])
    for pin_data in pins_for_export:
        writer.writerow([pin_data["serial"], pin_data["pin"]])
    csv_bytes = buffer.getvalue().encode("utf-8")

    # 2. Encrypt with Fernet
    key = frappe.conf.get("voucher_export_encryption_key").encode()
    f = Fernet(key)
    encrypted = f.encrypt(csv_bytes)

    # 3. Save as private file attached to batch
    file_doc = save_file(
        fname=f"{batch_name}_pins.enc",
        content=encrypted,
        dt="Memora Voucher Batch",
        dn=batch_name,
        is_private=1,  # CRITICAL: Store in /private/files/, not /files/
    )

    # 4. Update batch with file reference
    frappe.db.set_value(
        "Memora Voucher Batch", batch_name,
        "encrypted_export_file", file_doc.file_url,
    )
```

**Private Files:**
- `is_private=1` stores files in `sites/{site}/private/files/` -- NOT publicly accessible via URL
- Access requires Frappe authentication + permission check on the parent document
- Download URL: `/api/method/frappe.utils.file_manager.download_file?file_url=/private/files/VCH-BATCH-001_pins.enc`

**Sources:**
- [Frappe file_manager.py source](https://github.com/frappe/frappe/blob/develop/frappe/utils/file_manager.py) (HIGH confidence)

---

### 7. Sales Invoice Programmatic Creation

**Confidence:** MEDIUM -- ERPNext Sales Invoice DocType exists (referenced in `memora_subscription_transaction.json`), but no ERPNext import exists in the current codebase. Need to verify ERPNext is installed.

Sales Invoice creation uses standard Frappe `get_doc()` pattern. ERPNext's Sales Invoice is a standard DocType with specific required fields.

**Usage Pattern (Prepaid Invoice on Allocation Approval):**
```python
def create_prepaid_invoice(allocation_doc):
    """Create Sales Invoice for prepaid voucher allocation.

    Called when Voucher Allocation is approved.
    """
    batch = frappe.get_doc("Memora Voucher Batch", allocation_doc.batch)
    library = allocation_doc.library  # Customer docname

    # Get allocated card count and unit price
    card_count = allocation_doc.quantity
    unit_price = batch.unit_price  # Price per card

    invoice = frappe.get_doc({
        "doctype": "Sales Invoice",
        "customer": library,
        "posting_date": frappe.utils.today(),
        "due_date": frappe.utils.add_days(frappe.utils.today(), 30),
        "items": [{
            "item_code": batch.item_code,  # Link to ERPNext Item
            "qty": card_count,
            "rate": unit_price,
            "description": f"Voucher Cards - Batch {batch.name}",
        }],
        # Custom fields for traceability
        "voucher_allocation": allocation_doc.name,
    })
    invoice.insert(ignore_permissions=True)
    invoice.submit()  # Auto-submit for prepaid

    return invoice.name
```

**Required ERPNext Configuration:**
- Customer DocType must have entries for each library
- Item DocType must have voucher card item(s)
- Company, default accounts, and tax templates must be configured
- If ERPNext is NOT installed, use a custom "Memora Invoice" DocType instead

**IMPORTANT NOTE:** The existing codebase has `"options": "Sales Invoice"` on the `erpnext_invoice` field in Subscription Transaction, implying ERPNext is expected. However, NO `from erpnext` imports exist anywhere in the codebase. This needs verification during implementation:
- If ERPNext is installed: use Sales Invoice directly
- If ERPNext is NOT installed: create a lightweight custom "Memora Invoice" DocType with the minimum fields needed (customer, items, total, status)

**Sources:**
- [Frappe Forum: Creating Sales Invoices Programmatically](https://discuss.frappe.io/t/creating-sales-invoices-programmatically/20562) (MEDIUM confidence)
- [GitHub Gist: Frappe Client API for Sales Invoices](https://gist.github.com/dawoodjee/6952205776dc678f61b0a1fb7773c5fb) (MEDIUM confidence)
- Existing codebase: `memora_subscription_transaction.json` field `erpnext_invoice` links to Sales Invoice (HIGH confidence)

---

### 8. Redis Rate Limiting for Voucher Endpoints

**Confidence:** HIGH -- Pattern already exists and is battle-tested in this codebase.

The existing `RateLimiter` class in `fastapi_app/services/rate_limit.py` uses a Lua script for atomic increment-with-TTL. The voucher system reuses this exact pattern with different limits.

**Existing Pattern (verified from codebase):**
```python
# fastapi_app/services/rate_limit.py -- already exists
RATE_LIMIT_SCRIPT = """
local count = redis.call("INCR", KEYS[1])
if count == 1 then
    redis.call("EXPIRE", KEYS[1], ARGV[1])
end
return count
"""

class RateLimiter:
    def __init__(self, redis_client, key_prefix="memora:ratelimit:",
                 ip_limit=10, account_limit=5, window_seconds=60):
        ...
```

**Voucher-Specific Limits (from PRD):**
- Preview endpoint: 5 attempts/hour per player, 20 attempts/hour per IP
- Redeem endpoint: 5 attempts/hour per player, 20 attempts/hour per IP

**New RateLimiter Instance for Vouchers:**
```python
# In voucher endpoint -- reuse existing RateLimiter with different params
voucher_limiter = RateLimiter(
    redis_client=redis,
    key_prefix="memora:ratelimit:voucher:",
    ip_limit=20,          # 20/hour per IP
    account_limit=5,      # 5/hour per player
    window_seconds=3600,  # 1-hour window
)
```

**No new code needed** -- just instantiate `RateLimiter` with voucher-specific parameters. The existing Lua script and dual-key (IP + account) pattern handles everything.

**Sources:**
- Existing codebase: `fastapi_app/services/rate_limit.py` (HIGH confidence)
- Existing codebase: `fastapi_app/services/otp.py` uses same pattern (HIGH confidence)

---

## Configuration Additions

### New site_config.json Keys

```json
{
    "voucher_hmac_secret": "64-char-hex-string-generated-with-secrets.token_hex(32)",
    "voucher_export_encryption_key": "fernet-key-from-Fernet.generate_key()"
}
```

**Generation commands:**
```bash
# HMAC secret (64 hex chars = 32 bytes = 256 bits)
python3 -c "import secrets; print(secrets.token_hex(32))"

# Fernet key (44 base64 chars = 32 bytes)
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### New .env Keys (FastAPI)

```bash
# Only needed if FastAPI validates PINs directly (preview endpoint)
VOUCHER_HMAC_SECRET=same-value-as-site_config
```

### New Settings in config.py

```python
class Settings(BaseSettings):
    # ... existing settings ...

    # Voucher Configuration
    voucher_hmac_secret: str = ""  # Optional -- only needed for FastAPI preview
```

---

## What NOT to Add

| Library | Why NOT |
|---------|---------|
| PyCryptodome | Redundant -- `cryptography` covers all needs with better API |
| bcrypt/argon2 for PINs | Wrong tool -- PINs are high-entropy system-generated codes, not user passwords. HMAC with server-side key is correct here. |
| pandas | Overkill for simple CSV generation. stdlib `csv` module is sufficient. |
| celery | Frappe has its own background job system (`frappe.enqueue`) backed by Redis/RQ. Adding Celery would create infrastructure complexity for no benefit. |
| SQLAlchemy | Frappe ORM handles all database operations. Adding SQLAlchemy would bypass Frappe's permission and hook system. |
| python-barcode / qrcode | Barcode/QR generation for physical cards is a printing concern, NOT a backend concern. The backend provides serial numbers and PINs; printing is handled by the card manufacturer. |
| passlib | Password hashing not needed for voucher PINs (HMAC is the correct pattern here). Already using Frappe's built-in PBKDF2-SHA256 for player passwords. |

---

## Complete requirements.txt Additions

```
# NEW for v3.0 Voucher System -- only ONE new pip dependency
cryptography>=44.0.0
```

That is it. One new dependency. Everything else uses Python stdlib or existing Frappe/FastAPI patterns.

---

## Integration Points with Existing Stack

### FastAPI Side (Preview + Redeem Proxy)

| Component | Integration |
|-----------|-------------|
| Rate Limiting | Reuse `RateLimiter` from `fastapi_app/services/rate_limit.py` with voucher-specific params |
| JWT Auth | Reuse existing `get_current_player` dependency -- voucher endpoints are authenticated |
| Frappe Proxy | Reuse existing `httpx`-based `FrappeClient` from `fastapi_app/core/frappe_client.py` to call whitelisted redeem method |
| Redis | Reuse existing Redis pool from `app.state.redis` |

### Frappe Side (Admin + Core Logic)

| Component | Integration |
|-----------|-------------|
| Background Jobs | `frappe.enqueue()` for batch generation (existing infrastructure) |
| File Storage | `save_file()` for encrypted export (existing Frappe file system) |
| DocType Hooks | `doc_events` in `hooks.py` for voucher state transitions |
| Scheduled Tasks | `scheduler_events` in `hooks.py` for season-end expiration and consignment billing |
| Whitelisted Methods | `@frappe.whitelist()` for redeem endpoint (existing pattern used 30+ times) |
| Row Locking | `for_update=True` on `frappe.get_doc()` (existing pattern in `memora_lesson.py`) |
| Subscription Pipeline | Reuse `create_subscription()` from `memora_admin/api/subscriptions.py` -- voucher redemption creates the same Subscription Transaction that triggers the existing Phase 23 hook chain |

### Database Indexes Required

```sql
-- Essential for PIN lookup at redemption (O(1) instead of full table scan)
CREATE INDEX idx_voucher_card_pin_hash ON `tabMemora Voucher Card` (pin_hash);

-- Essential for batch-level queries (allocation, reporting)
CREATE INDEX idx_voucher_card_batch_status ON `tabMemora Voucher Card` (batch, status);

-- Essential for finding cards allocated to a library
CREATE INDEX idx_voucher_card_allocation ON `tabMemora Voucher Card` (allocation, status);
```

These should be defined in the DocType JSON `search_fields` / `index` properties or added via `after_migrate` hook.

---

## Confidence Assessment

| Component | Level | Reasoning |
|-----------|-------|-----------|
| hmac + hashlib (stdlib) | HIGH | Python standard library, verified docs, existing HMAC usage patterns in crypto community |
| secrets (stdlib) | HIGH | Python standard library, purpose-built for this exact use case |
| cryptography (Fernet) | HIGH | PyPI verified v44.0.0, pyca-maintained, widely audited, simple API |
| frappe.db.bulk_insert | HIGH | Verified in Frappe v15 source, documented API with chunk_size parameter |
| frappe.enqueue | HIGH | Verified in Frappe v15 docs, standard pattern for background jobs |
| for_update locking | HIGH | Already used in codebase (memora_lesson.py:33), verified from Frappe source generates SQL FOR UPDATE |
| save_file (private) | HIGH | Verified from Frappe file_manager.py source, is_private=1 stores in /private/files/ |
| Sales Invoice creation | MEDIUM | ERPNext Sales Invoice is standard, but ERPNext installation status unverified. Fallback plan needed. |
| RateLimiter reuse | HIGH | Existing codebase pattern, no modifications needed, just different constructor params |

---

## Sources

### Official Documentation (HIGH confidence)
- [Python hmac module](https://docs.python.org/3/library/hmac.html)
- [Python secrets module](https://docs.python.org/3/library/secrets.html)
- [Python hashlib module](https://docs.python.org/3/library/hashlib.html)
- [Python csv module](https://docs.python.org/3/library/csv.html)
- [cryptography (Fernet)](https://cryptography.io/en/latest/fernet/)
- [cryptography on PyPI](https://pypi.org/project/cryptography/)
- [Frappe v15 Database API](https://docs.frappe.io/framework/v15/user/en/api/database)
- [Frappe v15 Background Jobs](https://docs.frappe.io/framework/v15/user/en/api/background_jobs)
- [Frappe file_manager.py source](https://github.com/frappe/frappe/blob/develop/frappe/utils/file_manager.py)
- [Frappe Document class source](https://github.com/frappe/frappe/blob/develop/frappe/model/document.py)

### Codebase Precedents (HIGH confidence)
- `fastapi_app/services/rate_limit.py` -- RateLimiter with Lua script
- `fastapi_app/services/otp.py` -- Same rate limiting pattern
- `memora_admin/memora_admin/doctype/memora_lesson/memora_lesson.py:33` -- `for_update=True` usage
- `memora_admin/api/subscriptions.py` -- Subscription creation whitelisted method
- `memora_admin/events/purchase_sync.py` -- Doc event hook pattern
- `memora_admin/hooks.py` -- scheduler_events and doc_events patterns
- `memora_admin/memora_admin/services/build/storage/local.py` -- File storage with atomic writes

### Community Sources (MEDIUM confidence)
- [Frappe Forum: Creating Sales Invoices Programmatically](https://discuss.frappe.io/t/creating-sales-invoices-programmatically/20562)
- [Frappe Forum: Bulk Insert Records](https://discuss.frappe.io/t/bulk-insert-records/115125)
- [Deferred Bulk Inserts in Frappe](https://tej.sh/blog/frappe-deferred-bulk/)
- [OWASP Password Storage Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)

---

*Stack research completed: 2026-02-13*
*Verdict: ONE new pip dependency (`cryptography>=44.0.0`). Everything else is Python stdlib or existing Frappe/FastAPI patterns.*
