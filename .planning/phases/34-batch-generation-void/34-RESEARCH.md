# Phase 34: Batch Generation & Void - Research

**Researched:** 2026-02-14
**Domain:** Frappe background jobs, cryptographic PIN generation, Fernet file encryption, batch void operations
**Confidence:** HIGH

## Summary

Phase 34 implements the core batch generation workflow: an admin clicks "Generate" on a Draft batch, a background job creates up to 1,000 cards with cryptographically secure PINs and HMAC hashes, produces a Fernet-encrypted CSV export file at generation time, and transitions the batch to Generated status. The phase also implements batch and individual card void operations with required reason text.

The existing codebase provides all the building blocks. The Voucher Batch and Voucher Card DocTypes from Phase 33 already have status fields, state transition validation, and the correct autoname patterns. The `cryptography` library (v3.4.8) is already installed system-wide -- no new pip dependency is needed. HKDF key derivation from `voucher_hmac_secret` to produce a Fernet key is verified working. Background job patterns exist in `build_worker.py` and `purchase_sync.py`. The `frappe.publish_progress` API provides built-in progress bar UI during generation.

**Primary recommendation:** Use `frappe.enqueue` with `queue="default"` (300s timeout, sufficient for 1,000 cards), generate all cards in a single transaction with rollback on failure, encrypt the CSV at generation time when plaintext PINs are in memory, and use `frappe.publish_progress` for the built-in Frappe progress dialog.

<user_constraints>

## User Constraints (from CONTEXT.md)

### Locked Decisions

#### Generation job behavior
- Maximum batch size: up to 1,000 cards -- no need for complex chunking strategies
- If generation fails midway, rollback all partially created cards -- batch stays Draft, admin retries from scratch
- No resume/partial-generation support needed

#### Export & print security
- Fernet encryption key derived from existing `voucher_hmac_secret` in site_config -- one secret to manage (use KDF to derive Fernet key from HMAC secret)
- Encrypted export file auto-deleted after a configurable period (e.g., 30 days) -- reduces risk window
- Only System Manager role can export (download decrypted CSV)
- Unlimited re-exports allowed -- every export logged in append-only export_log child table for full audit trail
- When a batch is voided, its encrypted export file is deleted immediately (PINs are worthless)

#### Void operations
- Batch void: voids ALL non-final cards (Available AND Allocated) -- batch becomes Closed
- Void is permanent -- no undo/un-void capability
- Void reason is free text (no dropdown) -- required for both batch and individual card void
- Voiding a batch also deletes the encrypted export file

#### Serial number scheme
- Globally unique sequential numbers across all batches -- VCH-000001 format
- Fixed VCH- prefix (not configurable)
- 6-digit zero-padded (supports up to 999,999 total cards)
- Serial numbers never reused -- voided serials leave gaps, every number is unique across all time

### Claude's Discretion
- Progress reporting mechanism during generation (frappe.publish_progress vs status field polling)
- Export file timing (generate during card creation when PINs are in memory, vs on-demand)
- Auto-delete period for encrypted export files (exact number of days)
- Background job implementation details (frappe.enqueue patterns)

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope

</user_constraints>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| cryptography (Fernet + HKDF) | 3.4.8 (already installed) | Encrypt export CSV, derive Fernet key from HMAC secret | Already on system, pyca-maintained, misuse-resistant API |
| hmac + hashlib (stdlib) | Python 3.10+ | HMAC-SHA256 for PIN storage | Deterministic hash for WHERE clause lookup, server-side secret |
| secrets (stdlib) | Python 3.10+ | Cryptographic random PIN generation | Uses os.urandom() CSPRNG, purpose-built for security tokens |
| csv + io (stdlib) | Python 3.10+ | Build CSV in memory before encrypting | Zero-dependency, handles quoting/escaping correctly |

### Frappe APIs
| API | Purpose | When to Use |
|-----|---------|-------------|
| `frappe.enqueue()` | Run generation as background job | Always -- never generate cards in web request context |
| `frappe.publish_progress()` | Show progress dialog to admin | During generation loop, update every N cards |
| `frappe.db.bulk_insert()` | Insert 1,000 cards efficiently | Bypass ORM overhead for bulk creation |
| `frappe.utils.file_manager.save_file()` | Store encrypted export as private Frappe File | Attach to batch with `is_private=1` |
| `frappe.publish_realtime()` | Notify admin of completion/failure | After generation completes or fails |

### No New Dependencies Required

The `cryptography` library (v3.4.8) is **already installed** on the system. Verified working:
- `cryptography.fernet.Fernet` -- encryption/decryption
- `cryptography.hazmat.primitives.kdf.hkdf.HKDF` -- key derivation from HMAC secret
- Full roundtrip test passed with existing installation

**No changes to `requirements.txt` or `pyproject.toml` needed.**

## Architecture Patterns

### Recommended Project Structure
```
memora_admin/
  memora_admin/
    doctype/
      memora_voucher_batch/
        memora_voucher_batch.py      # Add generate/void methods
        memora_voucher_batch.js      # Add Generate/Void/Export buttons
        memora_voucher_batch.json    # Add export_log, encrypted_file_url fields
      memora_voucher_batch_export_log/  # NEW child table DocType
        memora_voucher_batch_export_log.json
        memora_voucher_batch_export_log.py
      memora_voucher_card/
        memora_voucher_card.py       # Add void method
        memora_voucher_card.js       # Add Void button
    services/
      voucher/                       # NEW service module
        __init__.py
        generator.py                 # PIN generation, HMAC, bulk insert
        crypto.py                    # Fernet encryption, HKDF key derivation
        export.py                    # CSV building, file management
        void.py                      # Batch and card void operations
    api/
      voucher.py                     # NEW whitelisted methods (generate, export, void)
```

### Pattern 1: Background Job with Progress Reporting

**What:** Use `frappe.enqueue` to run card generation in background, report progress via `frappe.publish_progress`.

**When to use:** Always for batch generation (even 100 cards should use background job for consistency).

**Recommendation: `frappe.publish_progress`** over status field polling.

Rationale:
- `frappe.publish_progress(percent, title, description)` shows a native Frappe progress dialog automatically
- No polling needed -- uses socket.io realtime
- Already used pattern in codebase (`build_worker.py:234` uses `frappe.publish_realtime`)
- For 1,000 cards, update every 100 cards (10 updates total) -- avoids socket flood

```python
# Source: Frappe v15 realtime API docs
@frappe.whitelist()
def generate_batch(batch_name: str):
    """Trigger batch generation as background job."""
    batch = frappe.get_doc("Memora Voucher Batch", batch_name)
    if batch.status != "Draft":
        frappe.throw("Can only generate cards for Draft batches")

    frappe.enqueue(
        "memora_admin.api.voucher.generate_cards_job",
        batch_name=batch_name,
        queue="default",        # 300s timeout, plenty for 1K cards
        timeout=600,            # 10 min explicit timeout (safety margin)
        job_name=f"voucher_gen_{batch_name}",
        enqueue_after_commit=True,
    )
    frappe.msgprint("Card generation started. You will see a progress bar.", alert=True)

def generate_cards_job(batch_name: str):
    """Background job: generate all cards for a batch."""
    batch = frappe.get_doc("Memora Voucher Batch", batch_name)
    quantity = batch.quantity
    pin_length = int(batch.pin_length)
    hmac_secret = frappe.conf.get("voucher_hmac_secret")

    if not hmac_secret:
        frappe.throw("voucher_hmac_secret not configured in site_config.json")

    pins_for_export = []  # Collect plaintext PINs for export file
    try:
        # ... generate cards, build export, bulk insert ...
        for i in range(quantity):
            # Generate PIN, compute HMAC, collect for bulk insert
            if (i + 1) % 100 == 0 or (i + 1) == quantity:
                frappe.publish_progress(
                    percent=int((i + 1) / quantity * 100),
                    title=f"Generating Cards for {batch_name}",
                    description=f"{i + 1} of {quantity} cards created",
                )
        # Bulk insert all cards
        # Create encrypted export file
        # Update batch status to Generated
        frappe.db.commit()
    except Exception:
        frappe.db.rollback()
        # Batch stays Draft -- admin retries
        raise
```

### Pattern 2: Serial Number Generation (Global Sequential)

**What:** Globally unique `VCH-000001` format serial numbers across all batches.

**When to use:** For the `serial_no` field on each Voucher Card.

**Key insight:** The Voucher Card `name` (Frappe primary key) uses autoname `VCH-.#####.` which generates `VCH-00001` etc. But the `serial_no` field needs the `VCH-000001` format (6-digit). Two options:

**Option A (Recommended): Use Frappe's naming series for the serial_no field directly.**

Since `serial_no` has `unique: 1` in the DocType JSON, and the requirement is for globally unique sequential numbers, use Frappe's `tabSeries` counter with custom formatting:

```python
import frappe

def get_next_serial_numbers(count: int) -> list[str]:
    """Reserve a block of sequential serial numbers atomically.

    Uses Frappe's naming series infrastructure for global uniqueness.
    """
    serials = []
    for _ in range(count):
        # Atomically increment the VCH series counter
        current = frappe.db.sql(
            "SELECT current FROM tabSeries WHERE name = 'VCH' FOR UPDATE"
        )
        if current:
            next_val = int(current[0][0]) + 1
            frappe.db.sql("UPDATE tabSeries SET current = %s WHERE name = 'VCH'", next_val)
        else:
            next_val = 1
            frappe.db.sql("INSERT INTO tabSeries (name, current) VALUES ('VCH', 1)")
        serials.append(f"VCH-{next_val:06d}")
    return serials
```

**Option B (Better for bulk): Reserve a block of serial numbers in one query.**

```python
def reserve_serial_block(count: int) -> list[str]:
    """Reserve `count` sequential serial numbers atomically.

    Single lock acquisition for the entire block -- avoids per-card locking.
    """
    result = frappe.db.sql(
        "SELECT current FROM tabSeries WHERE name = 'VCH-SERIAL' FOR UPDATE"
    )
    if result:
        start = int(result[0][0]) + 1
        frappe.db.sql(
            "UPDATE tabSeries SET current = %s WHERE name = 'VCH-SERIAL'",
            start + count - 1,
        )
    else:
        start = 1
        frappe.db.sql(
            "INSERT INTO tabSeries (name, current) VALUES ('VCH-SERIAL', %s)",
            count,
        )
    return [f"VCH-{i:06d}" for i in range(start, start + count)]
```

This acquires the series lock once, reserves the entire block, then releases. Generates all serial numbers without per-card contention.

### Pattern 3: Fernet Key Derivation from HMAC Secret (HKDF)

**What:** Derive a Fernet-compatible encryption key from the existing `voucher_hmac_secret` using HKDF.

**When to use:** Every time encryption/decryption of export files is needed.

**Why HKDF (not PBKDF2):** The `voucher_hmac_secret` is already a 256-bit cryptographic secret (generated with `secrets.token_hex(32)`). HKDF is designed for deriving keys from high-entropy key material. PBKDF2 adds expensive iterations to protect weak passwords -- unnecessary and wasteful when the input is already strong.

```python
# Source: cryptography.io HKDF docs + verified on system (cryptography 3.4.8)
import base64
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

# Fixed salt and info for deterministic key derivation
# These are not secret -- they provide domain separation
HKDF_SALT = b"memora-voucher-export-v1"
HKDF_INFO = b"fernet-encryption-key"

def get_fernet_key(hmac_secret: str) -> bytes:
    """Derive a Fernet key from the HMAC secret using HKDF.

    The same HMAC secret always produces the same Fernet key
    (deterministic with fixed salt + info).
    """
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=HKDF_SALT,
        info=HKDF_INFO,
    )
    derived = hkdf.derive(hmac_secret.encode("utf-8"))
    return base64.urlsafe_b64encode(derived)

def get_fernet(hmac_secret: str) -> Fernet:
    """Get a Fernet instance using the derived key."""
    return Fernet(get_fernet_key(hmac_secret))
```

**Verified:** Full roundtrip (derive -> encrypt -> decrypt) tested on the actual system with `cryptography==3.4.8`. Works correctly.

### Pattern 4: Encrypted Export File Generation at Creation Time

**Recommendation: Generate the encrypted export during card generation (when PINs are in memory).**

Rationale:
- Plaintext PINs exist in memory only during generation
- After generation, only HMAC hashes are stored in DB
- Generating on-demand would require decrypting nothing (PINs are not stored)
- Therefore, the ONLY time to create the export is during generation

```python
import csv
import io
from cryptography.fernet import Fernet

def create_encrypted_export(
    pins_data: list[dict],
    fernet: Fernet,
    batch_grants: list[dict],
    face_value: str,
) -> bytes:
    """Build CSV with card data and encrypt with Fernet.

    Args:
        pins_data: List of {"serial_no": "VCH-000001", "pin": "ABC123..."}
        fernet: Fernet instance (from get_fernet())
        batch_grants: Product grants on the batch
        face_value: Batch face value

    Returns:
        Encrypted bytes to store as file
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)

    # Header: serial_no, pin, product_names, face_value
    product_names = ", ".join(g["product_grant"] for g in batch_grants)
    writer.writerow(["serial_no", "pin", "product_names", "face_value"])

    for pd in pins_data:
        writer.writerow([pd["serial_no"], pd["pin"], product_names, face_value])

    csv_bytes = buffer.getvalue().encode("utf-8")
    return fernet.encrypt(csv_bytes)
```

### Pattern 5: Export File Storage and Retrieval

**What:** Store encrypted export as a private Frappe File attached to the batch.

```python
from frappe.utils.file_manager import save_file

def save_encrypted_export(batch_name: str, encrypted_data: bytes) -> str:
    """Save encrypted export file as private Frappe File.

    Returns the file URL for later retrieval.
    """
    file_doc = save_file(
        fname=f"{batch_name}_pins.enc",
        content=encrypted_data,
        dt="Memora Voucher Batch",
        dn=batch_name,
        is_private=1,  # Store in /private/files/
    )
    return file_doc.file_url

def decrypt_and_serve_export(batch_name: str) -> bytes:
    """Decrypt the export file and return CSV bytes for download."""
    batch = frappe.get_doc("Memora Voucher Batch", batch_name)
    if not batch.encrypted_file_url:
        frappe.throw("No export file available for this batch")

    # Read encrypted file from disk
    file_path = frappe.get_site_path(batch.encrypted_file_url.lstrip("/"))
    with open(file_path, "rb") as f:
        encrypted_data = f.read()

    # Decrypt
    hmac_secret = frappe.conf.get("voucher_hmac_secret")
    fernet = get_fernet(hmac_secret)
    return fernet.decrypt(encrypted_data)
```

### Pattern 6: Batch Void with Card Status Update

**What:** Void all non-terminal cards in a batch, delete export file, set batch to Closed.

```python
def void_batch(batch_name: str, void_reason: str):
    """Void all non-final cards in batch. Batch becomes Closed."""
    if not void_reason or not void_reason.strip():
        frappe.throw("Void reason is required")

    batch = frappe.get_doc("Memora Voucher Batch", batch_name)
    if batch.status == "Closed":
        frappe.throw("Batch is already Closed")

    # Void all Available and Allocated cards in one SQL UPDATE
    voided_count = frappe.db.sql(
        """
        UPDATE `tabMemora Voucher Card`
        SET status = 'Void', void_reason = %s, modified = NOW()
        WHERE batch = %s AND status IN ('Available', 'Allocated')
        """,
        (void_reason, batch_name),
    )

    # Delete encrypted export file
    if batch.encrypted_file_url:
        _delete_export_file(batch)

    # Update batch counters and status
    batch.reload()
    batch.voided_count = frappe.db.count(
        "Memora Voucher Card", {"batch": batch_name, "status": "Void"}
    )
    batch.status = "Closed"
    batch.void_reason = void_reason
    batch.save(ignore_permissions=True)
    frappe.db.commit()
```

### Anti-Patterns to Avoid

- **Never store plaintext PINs in the database.** PINs exist in memory only during generation. The DB stores only HMAC hashes. The encrypted export file is the only record of plaintext PINs.
- **Never generate cards synchronously in a web request.** Even for small batches, use `frappe.enqueue` for consistency and to avoid blocking the Frappe web worker.
- **Never use `random.choice()` for PIN generation.** Always use `secrets.choice()` for cryptographic randomness.
- **Never compare HMAC values with `==`.** Always use `hmac.compare_digest()` for constant-time comparison.
- **Never call `frappe.db.commit()` inside doc_events.** It is silently ignored. Use `frappe.db.commit()` only in whitelisted methods or background jobs.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Progress reporting | Custom polling/status field | `frappe.publish_progress()` | Built-in Frappe dialog, socket.io realtime, zero client code |
| Background jobs | Custom task queue | `frappe.enqueue()` | Frappe's RQ infrastructure, queue management, retry handling |
| Key derivation | Manual key construction | `HKDF` from cryptography | Cryptographically sound, handles domain separation |
| Encrypted file storage | Custom file handling | `save_file(is_private=1)` | Frappe's file system with permission checks |
| Serial number uniqueness | Application-level locks | `tabSeries` FOR UPDATE | Frappe's naming infrastructure, atomic increment |
| Bulk card creation | Individual `doc.insert()` loop | `frappe.db.bulk_insert()` | 10x faster, avoids ORM overhead per card |

**Key insight:** Frappe provides infrastructure for all of the complex operations in this phase. The only custom cryptographic code needed is PIN generation (secrets.choice + HMAC) and the HKDF key derivation wrapper.

## Common Pitfalls

### Pitfall 1: Naming Series vs Serial Number Confusion
**What goes wrong:** The Voucher Card `name` (Frappe PK) uses autoname `VCH-.#####.` which generates `VCH-00001` (5-digit). The `serial_no` field requires `VCH-000001` (6-digit). Using `bulk_insert` bypasses autoname, so both `name` and `serial_no` must be set manually.
**Why it happens:** `frappe.db.bulk_insert()` bypasses the autoname machinery. The developer must manually generate both the Frappe document `name` and the `serial_no` field.
**How to avoid:** When using `bulk_insert`, generate both `name` and `serial_no` explicitly. Use the same value for both (the `VCH-000001` format serial number as the document name). This is simpler and ensures 1:1 mapping. Reserve serial numbers as a block before starting bulk insert.
**Warning signs:** Cards with mismatched `name` vs `serial_no`, or 5-digit names with 6-digit serial numbers.

### Pitfall 2: Partial Generation Without Rollback
**What goes wrong:** Generation fails at card 500 of 1,000. Without explicit rollback, 500 cards exist in the database with the batch still in Draft status, creating orphaned cards.
**Why it happens:** `frappe.db.bulk_insert()` auto-commits chunks. If the process crashes mid-way, partial data persists.
**How to avoid:** For 1,000 cards (user-specified max), do NOT chunk. Insert all cards in a single `bulk_insert` call (default `chunk_size=1000` handles this). If any exception occurs, `frappe.db.rollback()` reverts the entire operation. The batch stays Draft.
**Warning signs:** Voucher Cards exist without corresponding batch status of Generated or later.

### Pitfall 3: Export File Persists After Void
**What goes wrong:** Batch is voided but the encrypted export file containing plaintext PINs is not deleted. The PINs are worthless (cards are void) but the file is a data leak risk.
**Why it happens:** The void operation updates card statuses but forgets to clean up the Frappe File.
**How to avoid:** The void operation MUST: (1) delete the Frappe File document, (2) delete the physical file from disk, (3) clear the `encrypted_file_url` field on the batch. Do all three in the void function.
**Warning signs:** `ls sites/*/private/files/*_pins.enc` shows files for voided batches.

### Pitfall 4: HKDF Salt/Info Not Fixed
**What goes wrong:** If HKDF salt or info changes between encryption and decryption, the derived key changes and all existing export files become undecryptable.
**Why it happens:** Developer uses random salt "for security" instead of a fixed, versioned salt.
**How to avoid:** Use fixed, hardcoded salt and info strings. Version them (e.g., `memora-voucher-export-v1`). If rotation is ever needed, use a version field on the batch to select the correct salt/info pair.
**Warning signs:** "InvalidToken" error when trying to decrypt an export file that was previously working.

### Pitfall 5: Export Log Child Table Not Created as DocType
**What goes wrong:** The export_log child table is referenced in the batch JSON but the corresponding DocType directory doesn't exist. `bench migrate` fails.
**Why it happens:** Phase 33 created most DocTypes but the Export Log child table was not included.
**How to avoid:** Create `Memora Voucher Batch Export Log` as a child table DocType (istable: 1) with fields: exported_by (Link to User), exported_at (Datetime), card_count (Int). Add it as a Table field on Voucher Batch.
**Warning signs:** `bench migrate` throws "DocType not found" for Memora Voucher Batch Export Log.

### Pitfall 6: bulk_insert Field List Mismatch
**What goes wrong:** `frappe.db.bulk_insert()` requires exact field-value alignment. Missing a required field (like `owner`, `creation`, `modified`, `modified_by`, `docstatus`) causes MySQL INSERT errors.
**Why it happens:** `bulk_insert` bypasses Document class validation and field defaults. All Frappe system fields must be explicitly provided.
**How to avoid:** Include ALL required fields in the fields list: `name`, all DocType fields, plus Frappe system fields (`owner`, `creation`, `modified`, `modified_by`, `docstatus`). Test with a single card before running bulk.
**Warning signs:** MySQL error "Column count doesn't match value count" or "Field 'owner' doesn't have a default value".

## Code Examples

### Complete PIN Generation Utility
```python
# Source: Python secrets docs + existing STACK_voucher.md research
import secrets
import hmac
import hashlib

# Alphabet excludes ambiguous characters (0/O, 1/I/L)
PIN_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # 30 chars

def generate_pin(length: int = 12) -> str:
    """Generate a cryptographically secure random PIN.

    Entropy: log2(30^length) bits per PIN.
    For length=12: ~58.6 bits. For length=16: ~78.1 bits.
    """
    return "".join(secrets.choice(PIN_ALPHABET) for _ in range(length))

def compute_hmac(pin: str, secret: str) -> str:
    """Compute HMAC-SHA256 of a PIN using server-side secret."""
    return hmac.new(
        secret.encode("utf-8"),
        pin.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
```

### Complete HKDF Key Derivation
```python
# Source: cryptography.io HKDF docs, verified on system (cryptography 3.4.8)
import base64
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

HKDF_SALT = b"memora-voucher-export-v1"
HKDF_INFO = b"fernet-encryption-key"

def get_fernet_key(hmac_secret: str) -> bytes:
    """Derive Fernet key from HMAC secret via HKDF-SHA256."""
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=HKDF_SALT,
        info=HKDF_INFO,
    )
    derived = hkdf.derive(hmac_secret.encode("utf-8"))
    return base64.urlsafe_b64encode(derived)

def get_fernet(hmac_secret: str) -> Fernet:
    """Get a Fernet instance with the HKDF-derived key."""
    return Fernet(get_fernet_key(hmac_secret))
```

### frappe.enqueue Pattern
```python
# Source: Frappe v15 background_jobs docs
frappe.enqueue(
    method="memora_admin.api.voucher.generate_cards_job",  # dotted path string
    batch_name=batch_name,
    queue="default",            # 300s default timeout
    timeout=600,                # explicit 10 min (safety margin for 1K cards)
    job_name=f"voucher_gen_{batch_name}",  # prevents duplicate enqueue
    enqueue_after_commit=True,  # ensures batch status is committed before job starts
)
```

### frappe.publish_progress Pattern
```python
# Source: Frappe v15 realtime API docs
frappe.publish_progress(
    percent=50,                                    # 0-100
    title=f"Generating Cards for {batch_name}",    # dialog title
    description="500 of 1000 cards created",       # dialog body
)
```

### frappe.db.bulk_insert Pattern
```python
# Source: Frappe database.py source + v15 docs
fields = [
    "name", "serial_no", "pin_hmac", "batch", "status",
    "owner", "creation", "modified", "modified_by", "docstatus",
]

now = frappe.utils.now()
values = [
    (
        serial,         # name (= serial_no for this doctype)
        serial,         # serial_no
        pin_hmac,       # HMAC-SHA256 hash
        batch_name,     # Link to batch
        "Available",    # initial status
        "Administrator",  # owner
        now,            # creation
        now,            # modified
        "Administrator",  # modified_by
        0,              # docstatus
    )
    for serial, pin_hmac in cards_data
]

frappe.db.bulk_insert(
    "Memora Voucher Card",
    fields,
    values,
    chunk_size=1000,  # default, handles up to 1K cards in one chunk
)
```

### Client-Side Generate Button
```javascript
// Source: Frappe v15 form API, existing pattern in memora_voucher_batch.js
frappe.ui.form.on("Memora Voucher Batch", {
    refresh(frm) {
        if (frm.doc.status === "Draft" && !frm.is_new()) {
            frm.add_custom_button(__("Generate Cards"), () => {
                frappe.confirm(
                    __("Generate {0} cards for this batch? This cannot be undone.", [frm.doc.quantity]),
                    () => {
                        frappe.call({
                            method: "memora_admin.api.voucher.generate_batch",
                            args: { batch_name: frm.doc.name },
                            callback: (r) => {
                                if (r.message) {
                                    frappe.show_alert({
                                        message: __("Generation started"),
                                        indicator: "blue",
                                    });
                                }
                            },
                        });
                    }
                );
            }, __("Actions"));
        }
    },
});
```

## DocType Schema Changes Required

### Voucher Batch -- New Fields Needed
The existing Voucher Batch DocType (Phase 33) needs these additions:

| Field | Type | Purpose |
|-------|------|---------|
| `encrypted_file_url` | Data (read_only) | URL to encrypted export file in private/files/ |
| `export_log` | Table (child) | Append-only log of every export download |

### New Child Table: Memora Voucher Batch Export Log
| Field | Type | Purpose |
|-------|------|---------|
| `exported_by` | Link to User | Who downloaded |
| `exported_at` | Datetime | When downloaded |
| `card_count` | Int | How many cards were in the export |

This is `istable: 1` (child table), linked from Voucher Batch via `export_log` Table field.

## Discretion Recommendations

### Progress Reporting: Use `frappe.publish_progress`
**Recommendation:** `frappe.publish_progress()` -- the native Frappe progress dialog.

Reasons:
1. Zero client-side code needed -- Frappe shows the dialog automatically
2. Socket.io realtime -- no polling
3. Existing pattern in Frappe ecosystem (file imports, data migration tools use it)
4. For 1,000 cards, update every 100 cards (10 updates) -- minimal socket overhead

### Export File Timing: Generate During Card Creation
**Recommendation:** Generate the encrypted CSV during the background generation job, when plaintext PINs are in memory.

Reasons:
1. After generation, PINs exist ONLY as HMAC hashes in the DB -- cannot reconstruct
2. The export file IS the only plaintext record
3. No on-demand generation is possible (would require storing PINs temporarily)
4. Simpler architecture: one background job does everything

### Auto-Delete Period: 30 Days
**Recommendation:** 30 days for encrypted export file auto-deletion.

Reasons:
1. Print vendors typically need 1-2 weeks to produce physical cards
2. 30 days provides a comfortable buffer for delays, reprints, verification
3. Aligns with the user's suggestion in CONTEXT.md ("e.g., 30 days")
4. Implement as a scheduled task that checks `encrypted_file_url` creation date

### Background Job Queue: `default` with 600s Timeout
**Recommendation:** Use `queue="default"` with explicit `timeout=600`.

Reasons:
1. Default queue has 300s timeout, but we set explicit 600s for safety margin
2. 1,000 cards: PIN generation (~10ms) + HMAC (~10ms) + bulk_insert (~2s) + Fernet encrypt (~10ms) + file save (~50ms) = well under 60 seconds total
3. `queue="long"` (1500s) is overkill for max 1,000 cards
4. `job_name` parameter prevents duplicate generation requests

## State of the Art

| Old Approach (from prior research) | Current Approach (Phase 34 specific) | Why Changed |
|-------------------------------------|--------------------------------------|-------------|
| Chunk into batches of 500, commit per chunk | Single bulk_insert for up to 1,000 cards | Max batch size capped at 1,000 -- no chunking needed |
| Resume/partial generation support | Full rollback on failure, retry from scratch | User decision: simpler, no orphaned cards |
| Separate Fernet key in site_config | Derive Fernet key from HMAC secret via HKDF | User decision: one secret to manage |
| `cryptography>=44.0.0` (new dependency) | `cryptography==3.4.8` (already installed) | Verified: Fernet + HKDF work on existing installation |
| Per-batch encryption keys | Single derived key from HMAC secret | User decision: simplified key management |

## Open Questions

1. **Export Log Child Table vs Existing DocType**
   - What we know: The CONTEXT.md mentions "export_log child table" and Phase 33's plan 33-03 mentions it, but the actual `Memora Voucher Batch Export Log` child table DocType was NOT created in Phase 33.
   - What's unclear: Should we create it in Phase 34 or was it supposed to exist already?
   - Recommendation: Create it in Phase 34 as part of the schema additions (add `encrypted_file_url` and `export_log` fields to Voucher Batch at the same time).

2. **Voucher Card Document Name vs Serial Number**
   - What we know: Autoname is `VCH-.#####.` (5-digit), serial_no field needs `VCH-000001` (6-digit). With `bulk_insert`, we bypass autoname entirely.
   - What's unclear: Should the Frappe document `name` be the same as the serial_no?
   - Recommendation: Yes -- set `name = serial_no` during bulk_insert. This makes the card identifiable by serial number everywhere in Frappe (URLs, links, search). The autoname pattern in the JSON becomes irrelevant when using bulk_insert.

3. **Scheduled Task for Export File Cleanup**
   - What we know: Encrypted files should auto-delete after 30 days.
   - What's unclear: Should this be a scheduler_events cron task or a manual admin action?
   - Recommendation: Add a daily cron task in hooks.py scheduler_events. It checks all Voucher Batches with `encrypted_file_url` set, compares the File document's creation date against the 30-day threshold, and deletes expired files. Low priority -- can be added at the end of the phase.

## Sources

### Primary (HIGH confidence)
- Existing codebase: `memora_voucher_batch.json`, `memora_voucher_batch.py`, `memora_voucher_card.json`, `memora_voucher_card.py` -- Phase 33 DocTypes with field definitions and state transitions
- Existing codebase: `build_worker.py:234` -- `frappe.publish_realtime` pattern for background job notifications
- Existing codebase: `purchase_sync.py` -- doc_events pattern for Frappe hooks
- Existing codebase: `hooks.py` -- scheduler_events and doc_events structure
- System verification: `cryptography==3.4.8` installed, HKDF + Fernet roundtrip test passed
- [Frappe v15 Background Jobs](https://docs.frappe.io/framework/v15/user/en/api/background_jobs) -- `frappe.enqueue()` API, queue types, timeouts
- [Frappe v15 Realtime API](https://docs.frappe.io/framework/v15/user/en/api/realtime) -- `frappe.publish_progress()` API
- [Frappe Database API](https://docs.frappe.io/framework/user/en/api/database) -- `frappe.db.bulk_insert()` signature
- [Fernet Documentation](https://cryptography.io/en/latest/fernet/) -- Encryption API, key format, PBKDF2 example
- [HKDF Documentation](https://cryptography.io/en/latest/hazmat/primitives/key-derivation-functions/) -- Key derivation from existing key material
- [Python secrets module](https://docs.python.org/3/library/secrets.html) -- CSPRNG for PIN generation
- [Python hmac module](https://docs.python.org/3/library/hmac.html) -- HMAC-SHA256, `compare_digest()`

### Secondary (MEDIUM confidence)
- Prior research: `.planning/research/STACK_voucher.md` -- comprehensive stack analysis (2026-02-13)
- Prior research: `.planning/research/PITFALLS_voucher.md` -- domain pitfalls catalog (2026-02-13)
- Prior research: `.planning/research/SUMMARY.md` -- architecture summary (2026-02-13)
- [Frappe Forum: Background Jobs](https://discuss.frappe.io/t/how-to-create-the-background-jobs-using-frappe-enqueue-function/96226)
- [Frappe Forum: Publish Progress Duplicating](https://discuss.frappe.io/t/frappe-publish-realtime-progress-bar-duplicating/119667)
- [Deferred Bulk Inserts In Frappe](https://tej.sh/blog/frappe-deferred-bulk/)

### Tertiary (LOW confidence)
- None -- all findings verified against primary sources

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all libraries verified installed, roundtrip tested
- Architecture: HIGH -- patterns verified in existing codebase, Frappe APIs documented
- Pitfalls: HIGH -- cataloged from prior research, verified against codebase state
- Discretion recommendations: MEDIUM -- based on judgment calls with supporting evidence

**Research date:** 2026-02-14
**Valid until:** 2026-03-14 (30 days -- stable domain, no fast-moving dependencies)
