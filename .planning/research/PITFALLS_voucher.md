# Domain Pitfalls: Voucher Management System

**Domain:** Voucher/recharge card distribution for gamified education platform
**Project:** Memora Admin (Frappe v15 + FastAPI sidecar)
**Researched:** 2026-02-13
**Confidence:** HIGH (verified against existing codebase, Frappe internals, and security literature)

---

## Executive Summary

Adding a voucher/recharge card system to the existing Memora platform introduces **financial integrity**, **cryptographic security**, and **Frappe-at-scale** risks that differ fundamentally from the game session and leaderboard pitfalls in previous milestones. The most dangerous pitfalls are:

1. **Concurrent redemption race condition** -- two requests redeem the same card simultaneously, creating duplicate subscriptions
2. **HMAC comparison using `==` instead of `hmac.compare_digest()`** -- timing attack leaks PIN bytes
3. **`frappe.db.commit()` silently ignored inside doc_events** -- the existing `_handle_approval()` code already calls `frappe.db.commit()` inside `on_update`, which Frappe disables during doc_events; the voucher redemption path inherits this risk
4. **Batch generation of 10K+ documents overwhelming Frappe workers** -- memory exhaustion, naming series contention, HTTP timeout
5. **Float-based commission calculations** -- rounding errors accumulate across thousands of cards, creating irreconcilable financial discrepancies

These are not generic pitfalls. They are specific to adding a financial transaction system to a platform that currently uses Frappe for content management and FastAPI for high-performance reads. The voucher system pushes both sides into unfamiliar territory: Frappe must handle bulk write operations at scale, and FastAPI must handle a write-heavy atomic operation (redemption) that currently only Frappe manages.

**Critical recommendation:** The redemption endpoint must bypass Frappe's ORM entirely for the atomic lock-check-update step, using raw `frappe.db.sql("SELECT ... FOR UPDATE")` within a controlled transaction boundary. Do NOT route redemption through Frappe's document save pipeline.

---

## Critical Pitfalls

### Pitfall 1: Concurrent Redemption Race Condition (Double-Spend)

**Severity:** CRITICAL
**Phase:** Redemption endpoint implementation
**Confidence:** HIGH

**What goes wrong:**
Two HTTP requests arrive within milliseconds, both attempting to redeem the same voucher card PIN. Without database-level locking, both requests read the card status as "Active", both proceed to mark it "Redeemed", and both create Subscription Transactions. The player receives double the content access, or two different players both redeem the same card.

```
T0: Card VC-00001 status = "Active"
T1: Request A reads card -> status = "Active" -> proceeds
T2: Request B reads card -> status = "Active" -> proceeds (A not committed yet)
T3: Request A sets status = "Redeemed", creates Subscription Transaction
T4: Request B sets status = "Redeemed", creates SECOND Subscription Transaction
Result: Same card redeemed twice, two sets of subscriptions created
```

**Why it happens:**
- Frappe's `frappe.get_doc()` uses standard SELECT (no locking)
- The check-then-update pattern (`if card.status == "Active": card.status = "Redeemed"; card.save()`) is NOT atomic
- FastAPI is async and handles concurrent requests naturally
- Even at modest scale (50 libraries), a popular card batch distributed to multiple locations could see concurrent attempts

**Consequences:**
- Financial loss: card value granted twice
- Audit trail corruption: two "Completed" transactions for one card
- Access pipeline confusion: `_handle_approval()` creates duplicate Player Subscriptions (the `existing` check prevents per-key duplicates, but the player gets subscriptions from TWO transactions)

**Prevention:**

Use `SELECT ... FOR UPDATE` with explicit transaction boundaries in a Frappe whitelisted method:

```python
@frappe.whitelist()
def redeem_voucher_card(pin_plaintext: str, player_id: str) -> dict:
    """Atomic voucher redemption with pessimistic locking.

    MUST run as a standalone Frappe API call (not inside doc_events)
    so that frappe.db.commit()/rollback() are NOT disabled.
    """
    pin_hmac = compute_hmac(pin_plaintext)

    # 1. Lock the specific card row (blocks concurrent redemption)
    card_row = frappe.db.sql(
        """
        SELECT name, status, card_batch, denomination
        FROM `tabMemora Voucher Card`
        WHERE pin_hash = %s
        FOR UPDATE
        """,
        (pin_hmac,),
        as_dict=True,
    )

    if not card_row:
        frappe.throw("Invalid PIN", exc=frappe.ValidationError)

    card = card_row[0]

    if card.status != "Active":
        frappe.throw(f"Card already {card.status}", exc=frappe.ValidationError)

    # 2. Update card status (still under row lock)
    frappe.db.sql(
        """
        UPDATE `tabMemora Voucher Card`
        SET status = 'Redeemed', redeemed_by = %s, redeemed_at = NOW()
        WHERE name = %s AND status = 'Active'
        """,
        (player_id, card.name),
    )

    # 3. Verify exactly one row was updated (defense in depth)
    if frappe.db.sql("SELECT ROW_COUNT()")[0][0] != 1:
        frappe.db.rollback()
        frappe.throw("Redemption conflict - please try again")

    # 4. Create Subscription Transaction (triggers existing on_update pipeline)
    trx = frappe.get_doc({
        "doctype": "Memora Subscription Transaction",
        "player": player_id,
        "payment_method": "Voucher",
        "status": "Completed",  # Auto-approve for vouchers
        "amount_paid": card.denomination,
        "related_grant": get_grant_for_batch(card.card_batch),
    })
    trx.insert(ignore_permissions=True)

    # Commit happens at end of whitelisted method (Frappe auto-commit)
    return {"status": "success", "transaction_id": trx.name}
```

**Why SELECT FOR UPDATE works here:**
- MariaDB InnoDB uses a record lock (not gap lock) when the WHERE clause matches a unique index. The `pin_hash` column MUST have a unique index.
- The lock is held until the transaction commits (at end of whitelisted method call).
- Concurrent requests block on the SELECT FOR UPDATE until the first transaction commits.
- If the first request changes status to "Redeemed", the second request's subsequent status check fails cleanly.

**Deadlock prevention:**
- Lock exactly ONE table, ONE row. Never lock multiple cards in the same transaction.
- Keep the locked section short (no external API calls while lock is held).
- Set `innodb_lock_wait_timeout` appropriately (default 50s is fine; requests timeout at 30s via FastAPI).

**Detection (warning signs):**
- Two Subscription Transactions with `payment_method = "Voucher"` referencing the same card
- Redemption log shows two entries with timestamps < 1 second apart for the same PIN hash
- MariaDB deadlock monitor shows contention on `tabMemora Voucher Card`

**Sources:**
- [Database Locking to Solve Race Condition](https://www.coderbased.com/p/database-locking)
- [Transaction Locking to Prevent Race Conditions](https://sqlfordevs.com/transaction-locking-prevent-race-condition)
- [InnoDB Lock Modes - MariaDB](https://mariadb.com/docs/server/server-usage/storage-engines/innodb/innodb-lock-modes)

---

### Pitfall 2: HMAC Timing Attack on PIN Verification

**Severity:** CRITICAL
**Phase:** Card model and PIN storage implementation
**Confidence:** HIGH

**What goes wrong:**
The PIN verification code uses Python's `==` operator to compare HMAC digests instead of `hmac.compare_digest()`. The `==` operator short-circuits on the first differing byte, leaking information about how many leading bytes match. An attacker sends thousands of requests, measures response times, and iteratively discovers the correct HMAC byte by byte.

```python
# WRONG -- timing attack vulnerable
def verify_pin(submitted_pin: str, stored_hash: str) -> bool:
    computed = hmac.new(SECRET_KEY, submitted_pin.encode(), hashlib.sha256).hexdigest()
    return computed == stored_hash  # Short-circuits on first difference!

# An attacker can distinguish:
# - "wrong first byte" (fast: ~0.1ms comparison)
# - "wrong last byte" (slow: ~0.3ms comparison, all 63 prior bytes matched)
```

**Practical threat level:**
With a 64-character hex HMAC digest, an attacker needs approximately 64 x 16 = 1,024 measurements to determine each byte, totaling roughly 65,000 requests to reconstruct the full HMAC. At 10 requests/second (under the rate limit), this takes ~1.8 hours. The attacker does not need the original PIN -- knowing the HMAC lets them forge a valid redemption request if the comparison is the only gate.

However, the PRD specifies HMAC-SHA256 where the attacker submits a plaintext PIN, the server computes HMAC(PIN), and compares against the stored hash. The attacker controls the input, not the hash comparison target. This means a timing attack reveals information about the stored hash, which COULD allow the attacker to test PINs faster by confirming partial HMAC matches offline. The risk is moderate-to-high depending on implementation.

**Prevention:**

```python
import hashlib
import hmac

def compute_pin_hmac(pin: str, secret: bytes) -> str:
    """Compute HMAC-SHA256 of PIN. Uses site_config secret."""
    return hmac.new(secret, pin.encode("utf-8"), hashlib.sha256).hexdigest()

def verify_pin(submitted_pin: str, stored_hmac: str, secret: bytes) -> bool:
    """Constant-time PIN verification. NEVER use == for this."""
    computed = compute_pin_hmac(submitted_pin, secret)
    # hmac.compare_digest() runs in constant time regardless of where strings differ
    return hmac.compare_digest(computed, stored_hmac)
```

**Additional HMAC implementation rules:**
1. **Secret storage:** Use `frappe.conf.voucher_hmac_secret` from `site_config.json`, NOT the JWT secret, NOT an environment variable, NOT hardcoded in source code.
2. **Secret length:** Minimum 32 bytes (256 bits). Generate with `secrets.token_hex(32)`.
3. **Never log PINs:** Not in plaintext, not the HMAC. Log only card name (e.g., "VC-00001") and result ("redeemed" / "invalid" / "already_used").
4. **PIN entropy:** For 12-character alphanumeric PINs (A-Z, 0-9), entropy is 36^12 = ~62 bits. Sufficient against brute force with rate limiting.
5. **Python version:** Ensure Python >= 3.10 (the `hmac.compare_digest` timing fix from CVE-2022-48566 is included).

**Detection (warning signs):**
- Code review finds `==` comparing any hash/HMAC values
- Security scan flags `hmac` import without corresponding `compare_digest` usage
- Unusual pattern of PIN verification failures with incrementally similar PINs from same IP

**Sources:**
- [Timing Attacks against String Comparison in Python](https://sqreen.github.io/DevelopersSecurityBestPractices/timing-attack/python)
- [hmac timing attack - Precli Documentation](https://precli.readthedocs.io/0.6.3/rules/python/stdlib/hmac-timing-attack/)
- [CVE-2022-48566 - Python hmac.compare_digest timing flaw](https://www.cve.news/cve-2022-48566/)
- [Constant time compare in Python](https://securitypitfalls.wordpress.com/2018/08/03/constant-time-compare-in-python/)

---

### Pitfall 3: `frappe.db.commit()` Silently Ignored Inside Doc Events

**Severity:** CRITICAL
**Phase:** Integration with existing Subscription Transaction pipeline
**Confidence:** HIGH (verified in existing codebase)

**What goes wrong:**
The existing `MemoraSubscriptionTransaction.on_update()` method (line 59 of `memora_subscription_transaction.py`) calls `frappe.db.commit()` explicitly after creating Player Subscriptions. During doc_events, Frappe DISABLES manual commit/rollback to preserve atomicity. The commit call is **silently ignored** -- it does not raise an error, it simply does nothing.

This means the "all-or-nothing" subscription creation in `_handle_approval()` is NOT actually atomic in the way the code suggests. The real commit happens at the end of the HTTP request that triggered the `on_update` event.

```python
# In memora_subscription_transaction.py lines 36-65:
def _handle_approval(self):
    created_subs = []
    try:
        for access_key in grant_keys:
            sub = frappe.get_doc({...})
            sub.insert(ignore_permissions=True)
            created_subs.append(sub.name)

        frappe.db.commit()  # <-- SILENTLY IGNORED during doc_events!
    except Exception:
        for sub_name in created_subs:
            frappe.delete_doc(...)
        frappe.db.commit()  # <-- ALSO SILENTLY IGNORED
        frappe.throw("Failed to create subscriptions...")
```

**For voucher redemption, this means:**
When the voucher redemption creates a Subscription Transaction with `status="Completed"`, the `on_update` hook fires and creates Player Subscriptions. But these subscriptions are NOT committed until the outer whitelisted method returns. If the outer method fails AFTER `_handle_approval()` succeeds (e.g., during Redis pending set cleanup on line 69), the entire transaction rolls back -- including the subscriptions that the code thought were committed.

This is actually CORRECT behavior for atomicity, but the code's manual rollback logic (lines 62-64) is unnecessary and misleading. It manually deletes subscriptions that Frappe's auto-rollback would have already handled.

**The real danger for vouchers:**
If the redemption whitelisted method creates the Subscription Transaction with `status="Completed"` and the `on_update` hook fires inline, ALL of the following must succeed or NONE are committed:
- Card status update to "Redeemed"
- Subscription Transaction creation
- Player Subscription creation (triggered by on_update)
- Redis access sync (triggered by on_subscription_change)

If Redis is down when `on_subscription_change` tries to SADD (line 97 of access_sync.py), the Redis call raises an exception, the entire transaction rolls back, and the card is NOT marked as redeemed. This is actually SAFE (card stays "Active", player can retry). But if you add a `try/except` around the Redis call to "gracefully handle Redis failure", you break the atomicity -- the card is marked "Redeemed" but access is never granted.

**Prevention:**

1. **Do NOT call `frappe.db.commit()` inside doc_events.** It will be silently ignored. Remove the existing calls in `_handle_approval()` (they are dead code).

2. **Create Subscription Transaction with `status="Completed"` directly** (not "Pending Approval" then update to "Completed"). This avoids the two-step create-then-update pattern and ensures the `on_update` hook fires exactly once during the initial insert's `after_insert` or use a different approach:

```python
# RECOMMENDED: Set status to Completed on insert, NOT via subsequent update
# This way, _handle_approval fires during on_update of the INSERT operation
trx = frappe.get_doc({
    "doctype": "Memora Subscription Transaction",
    "player": player_id,
    "payment_method": "Voucher",
    "status": "Completed",  # Set on creation, not changed after
    ...
})
trx.insert(ignore_permissions=True)
# on_update fires, _handle_approval runs, all within same transaction
```

3. **Handle the Redis failure case explicitly:**
```python
# In _handle_approval or a voucher-specific handler:
# The Redis SADD in on_subscription_change may fail.
# This is OK -- the ensure_hydrated() pattern will self-heal on next API call.
# But do NOT let Redis failure prevent the DB transaction from committing.
# Solution: wrap Redis operations in try/except in the event handler.
```

Wait -- this contradicts point 2 above. The key insight is: `on_subscription_change` in access_sync.py already does NOT raise exceptions that bubble up (it uses direct Redis calls that could fail silently on the sync Redis connection). But verify this -- if `get_fastapi_redis()` raises a `ConnectionError`, it will bubble up and roll back the entire transaction.

4. **Best approach for vouchers:** Create the Subscription Transaction with `status="Completed"` directly. Let the existing `on_update` pipeline handle subscription creation and Redis sync. Accept that if Redis is temporarily down, the `ensure_hydrated()` pattern will self-heal when the player next calls the FastAPI access check.

**Detection (warning signs):**
- `frappe.db.commit()` calls inside any `on_update`, `after_insert`, `validate`, or `before_save` handler
- Subscription Transactions with `status="Completed"` but no corresponding Player Subscriptions
- Error logs showing "Failed to create subscriptions" but the Subscription Transaction still shows "Completed"

**Sources:**
- [Frappe Database API](https://docs.frappe.io/framework/user/en/api/database) -- "Commit/rollback are disabled during certain events"
- [Frappe database.py source](https://github.com/frappe/frappe/blob/develop/frappe/database/database.py) -- TRANSACTION_DISABLED_MSG
- [frappe.db.commit() has no effect - Forum](https://discuss.frappe.io/t/frappe-db-commit-has-no-effect/129106)

---

### Pitfall 4: Batch Generation Overwhelming Frappe Workers

**Severity:** CRITICAL
**Phase:** Batch creation and card generation
**Confidence:** HIGH

**What goes wrong:**
An admin clicks "Generate Cards" for a batch of 10,000 cards. The generation runs synchronously in the web request context. Frappe's default HTTP timeout is 2 minutes (120 seconds). Each card requires: PIN generation, HMAC computation, document creation with naming series, and database INSERT. At ~5ms per card (optimistic), 10,000 cards take 50 seconds. But Frappe's ORM overhead (validation, field checking, event hooks, naming series lookup) makes each `insert()` take 20-50ms, pushing total time to 200-500 seconds -- well beyond the timeout.

**Specific bottlenecks:**

1. **Naming Series Contention:** Frappe uses `SELECT ... FOR UPDATE` on the `tabSeries` table to get the next number. With 10,000 sequential inserts, each one locks the series row, gets the number, increments, and releases. Under concurrent batch generation (two admins generating simultaneously), this becomes a serial bottleneck.

2. **Memory Accumulation:** Each `frappe.get_doc({...}).insert()` call loads the full DocType metadata, creates a Document object, runs validation, triggers hooks, and keeps references in memory. At 10,000 documents, memory usage can exceed the worker's limit (typically 150MB per gunicorn worker).

3. **Doc Events per Card:** If doc_events hooks are registered on the voucher card DocType, each of the 10,000 inserts triggers the hook chain. Even simple logging adds up.

4. **Global Search Sync:** Frappe's `sync_global_search` runs for every document insert by default, adding significant overhead at scale.

**Prevention:**

**Strategy 1: Background Job with Chunked Processing (RECOMMENDED)**
```python
@frappe.whitelist()
def generate_batch_cards(batch_name: str):
    """Enqueue card generation as background job."""
    batch = frappe.get_doc("Memora Voucher Batch", batch_name)

    if batch.status != "Draft":
        frappe.throw("Can only generate cards for Draft batches")

    batch.status = "Generating"
    batch.save(ignore_permissions=True)
    frappe.db.commit()

    frappe.enqueue(
        "memora_admin.api.vouchers.generate_cards_background",
        batch_name=batch_name,
        queue="long",
        timeout=1800,  # 30 minutes for large batches
        job_name=f"generate_cards_{batch_name}",
    )

    return {"status": "queued", "message": f"Generating {batch.quantity} cards in background"}


def generate_cards_background(batch_name: str):
    """Generate cards in chunks with progress tracking."""
    batch = frappe.get_doc("Memora Voucher Batch", batch_name)
    secret = frappe.conf.voucher_hmac_secret.encode()
    chunk_size = 500  # Process 500 cards per chunk
    total = batch.quantity
    generated = 0

    try:
        for chunk_start in range(0, total, chunk_size):
            chunk_end = min(chunk_start + chunk_size, total)
            cards_data = []

            for i in range(chunk_start, chunk_end):
                pin = generate_secure_pin()
                pin_hmac = compute_pin_hmac(pin, secret)
                cards_data.append({
                    "doctype": "Memora Voucher Card",
                    "card_batch": batch_name,
                    "pin_hash": pin_hmac,
                    "status": "Inactive",
                    "denomination": batch.denomination,
                    # Store PIN temporarily for export (encrypted, cleared after export)
                })

            # Bulk insert to bypass ORM overhead
            for card_data in cards_data:
                doc = frappe.get_doc(card_data)
                doc.flags.ignore_validate = True
                doc.flags.no_value_check = True
                doc.insert(ignore_permissions=True)

            frappe.db.commit()  # Commit each chunk (NOT inside doc_events, safe here)
            generated += len(cards_data)

            # Update progress
            frappe.publish_realtime(
                "voucher_generation_progress",
                {"batch": batch_name, "generated": generated, "total": total},
                user=frappe.session.user,
            )

        batch.reload()
        batch.status = "Generated"
        batch.cards_generated = generated
        batch.save(ignore_permissions=True)
        frappe.db.commit()

    except Exception as e:
        batch.reload()
        batch.status = "Generation Failed"
        batch.generation_error = str(e)[:500]
        batch.save(ignore_permissions=True)
        frappe.db.commit()
        raise
```

**Strategy 2: Raw SQL Bulk Insert for Maximum Performance**
```python
def bulk_insert_cards(batch_name: str, cards: list[dict]):
    """Raw SQL bulk insert -- bypasses ORM entirely.

    Use when generating 10K+ cards and ORM overhead is unacceptable.
    WARNING: No doc_events fire, no naming series, must handle naming manually.
    """
    if not cards:
        return

    values = []
    now = frappe.utils.now()

    for card in cards:
        # Manual naming: BATCH-SEQUENCE format
        card_name = f"{batch_name}-{card['sequence']:05d}"
        values.append((
            card_name,
            batch_name,
            card["pin_hash"],
            "Inactive",
            card["denomination"],
            now,
            now,
            "Administrator",
            "Administrator",
        ))

    # Bulk insert in single statement
    frappe.db.sql(
        """
        INSERT INTO `tabMemora Voucher Card`
        (name, card_batch, pin_hash, status, denomination,
         creation, modified, owner, modified_by)
        VALUES {}
        """.format(", ".join(["%s"] * len(values))),
        values,
    )
```

**Key rules for batch generation:**
1. **Always use `frappe.enqueue()` with `queue="long"` and explicit `timeout`**
2. **Chunk into batches of 500** -- commit after each chunk to avoid mega-transactions
3. **Disable global search** for the voucher card DocType (set `index_web_pages_for_search = 0` in DocType JSON)
4. **Track progress** via `frappe.publish_realtime()` so the admin sees progress
5. **Implement idempotent retry** -- if generation fails at card 5,001, restart should resume from 5,001 not start over

**Detection (warning signs):**
- Background job timeout errors in `frappe.log`
- Worker memory exceeds 300MB during generation
- `tabSeries` table shows high lock wait time
- Admin reports "Generate Cards button does nothing" (HTTP request timed out, no response)

**Sources:**
- [Frappe Background Jobs](https://docs.frappe.io/framework/user/en/api/background_jobs)
- [Deferred Bulk Inserts In Frappe](https://tej.sh/blog/frappe-deferred-bulk/)
- [Frappe Performance Tuning](https://github.com/frappe/erpnext/wiki/ERPNext-Performance-Tuning)
- [Frappe Bulk Insert Discussion](https://discuss.frappe.io/t/bulk-insert-in-frappe/99300)

---

### Pitfall 5: Float-Based Financial Calculations

**Severity:** CRITICAL
**Phase:** Commission calculation, invoice generation, financial reporting
**Confidence:** HIGH

**What goes wrong:**
Commission percentages and card denominations are multiplied using Python float arithmetic. Over thousands of cards, rounding errors accumulate into visible financial discrepancies.

```python
# WRONG -- float arithmetic
denomination = 50.00
commission_rate = 0.15  # 15%
commission = denomination * commission_rate  # = 7.4999999999999991 (not 7.50!)

# Over 10,000 cards:
total_commission = 10000 * denomination * commission_rate
# Expected: 75,000.00
# Actual:   74,999.99999999999 (rounds to 74,999.99 in some contexts)
```

Frappe's Currency fieldtype stores values as DECIMAL(21,9) in MariaDB, which is exact. But Python-side calculations before saving to the database use float, creating a mismatch.

**Real-world impact:**
- Invoice total does not match sum of line items (auditor flag)
- Commission report shows 74,999.99 but accountant expects 75,000.00
- Credit note calculations compound the error (refund amount != original commission)
- Batch financial summary does not reconcile with individual card records

**Prevention:**

```python
from decimal import Decimal, ROUND_HALF_UP

# CORRECT -- use Decimal for all financial math
denomination = Decimal("50.00")  # String initialization, NOT Decimal(50.00)!
commission_rate = Decimal("0.15")
commission = (denomination * commission_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
# = Decimal("7.50") exactly

# For per-card calculation in batch:
def calculate_commission(denomination: str, rate: str, quantity: int) -> dict:
    """Calculate commission using exact decimal arithmetic.

    Args:
        denomination: Card face value as string (e.g., "50.00")
        rate: Commission rate as string (e.g., "0.15" for 15%)
        quantity: Number of cards

    Returns:
        Dict with per_card and total commission as strings
    """
    d = Decimal(denomination)
    r = Decimal(rate)
    q = Decimal(str(quantity))
    TWO_PLACES = Decimal("0.01")

    per_card = (d * r).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    total = (per_card * q).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

    return {
        "per_card_commission": str(per_card),
        "total_commission": str(total),
        "net_per_card": str((d - per_card).quantize(TWO_PLACES)),
        "net_total": str(((d - per_card) * q).quantize(TWO_PLACES)),
    }
```

**Additional rules:**
1. **Always initialize Decimal from strings:** `Decimal("50.00")` NOT `Decimal(50.00)` (the latter inherits float imprecision)
2. **Quantize at each calculation step,** not just the final result
3. **Store commission rate as string in DocType** (Data fieldtype, not Float/Percent)
4. **Verify reconciliation:** total_revenue = sum(card.denomination for card in batch) must equal net_to_platform + total_commission. Add an assertion in the batch generation code.
5. **Use Frappe's flt() function cautiously:** `frappe.utils.flt()` rounds to a specified precision but uses Python float internally. For financial calculations, bypass `flt()` and use Decimal directly.

**Detection (warning signs):**
- Financial report totals differ by 0.01 from manual calculation
- Batch summary commission does not match sum of individual card commissions
- Invoice grand_total shows trailing 9s (e.g., 74,999.99 instead of 75,000.00)
- Auditor flags: "Sum of parts does not equal whole"

**Sources:**
- [Python Decimal vs Float: The $10,000 Mistake](https://pranaysuyash.medium.com/how-i-lost-10-000-because-of-a-python-float-and-how-you-can-avoid-my-mistake-3bd2e5b4094d)
- [Python decimal module documentation](https://docs.python.org/3/library/decimal.html)
- [How to Handle Monetary Values in Python](https://shakuro.com/blog/how-to-handle-monetary-values)

---

## Moderate Pitfalls

### Pitfall 6: Subscription Transaction on_update Side Effects During Voucher Redemption

**Severity:** MODERATE
**Phase:** Redemption integration with existing access pipeline
**Confidence:** HIGH (verified in codebase)

**What goes wrong:**
Voucher redemption creates a Subscription Transaction with `status="Completed"` and `payment_method="Voucher"`. The existing `on_update` hook (`_handle_approval`) assumes certain fields exist and have specific values. If the voucher redemption pathway does not populate all required fields correctly, the hook fails silently or creates invalid subscriptions.

**Specific integration risks identified in the existing code:**

1. **`related_grant` is required by `_handle_approval()`** (line 25-26):
   ```python
   if not self.related_grant:
       frappe.throw("Cannot approve: no Related Grant linked to this transaction.")
   ```
   Voucher cards are tied to a card batch, which links to a plan or product grant. The mapping from batch to product grant MUST be established before the transaction is created.

2. **`_get_expires_at()` derives expiration from player's plan season** (lines 134-159). If a player does not have a plan assigned (new registration, plan expired), the sentinel `2099-12-31` is used. This is probably acceptable for vouchers but should be explicitly decided.

3. **`purchase_sync.on_purchase_request_created`** fires on `after_insert` (hooks.py line 172). This sends email notifications to admins for "Pending Approval" transactions. Voucher transactions are created with `status="Completed"` so this hook WILL fire but the status check on line 19 (`if doc.status != "Pending Approval": return`) will skip notification. This is correct behavior but should be verified.

4. **`_publish_notification`** sends a WebSocket notification to the player (line 98-131). For voucher redemptions, this is desirable (player sees "Subscription activated") but the notification payload includes `product_name` from the grant's item. Verify that voucher batch grants have proper item names.

**Prevention:**
- Create an integration test that exercises the FULL redemption path: PIN verify -> card update -> Subscription Transaction create -> on_update fires -> Player Subscription created -> access_sync fires -> Redis SADD
- Document the required field mapping: `card_batch.product_grant -> transaction.related_grant`
- Add a `Voucher`-specific code path in `_handle_approval()` if voucher transactions need different handling (e.g., immediate expiry based on card batch validity, not player's season)

**Detection (warning signs):**
- Player redeems card but does not see content unlocked
- Subscription Transaction shows "Completed" but no Player Subscriptions exist
- Error in Frappe error log: "Cannot approve: no Related Grant linked"

---

### Pitfall 7: State Machine Enforcement Gaps

**Severity:** MODERATE
**Phase:** Card lifecycle management (all phases)
**Confidence:** HIGH

**What goes wrong:**
The card lifecycle has defined states (Inactive -> Active -> Redeemed/Expired/Voided), but without enforcement, admin operations or bugs can create invalid transitions:

- Card goes from "Redeemed" back to "Active" (admin "fixes" a complaint)
- Card goes from "Voided" to "Redeemed" (race between void and redemption)
- Card goes from "Inactive" to "Redeemed" (skipping activation, meaning card was never distributed)

**Consequences:**
- Redeemed card "un-redeemed" but subscriptions already created (orphaned subscriptions)
- Financial reports show impossible transitions
- Audit trail becomes unreliable

**Prevention:**

```python
# In Memora Voucher Card DocType class:
VALID_TRANSITIONS = {
    "Inactive": {"Active", "Voided"},      # Can activate or void before distribution
    "Active": {"Redeemed", "Expired", "Voided"},  # Normal lifecycle
    "Redeemed": set(),                      # Terminal state -- IMMUTABLE
    "Expired": {"Voided"},                  # Can void expired cards for accounting
    "Voided": set(),                        # Terminal state -- IMMUTABLE
}

class MemoraVoucherCard(Document):
    def validate(self):
        if self.has_value_changed("status"):
            old_status = self.get_doc_before_save().status if self.get_doc_before_save() else None
            if old_status and self.status not in self.VALID_TRANSITIONS.get(old_status, set()):
                frappe.throw(
                    f"Invalid status transition: {old_status} -> {self.status}. "
                    f"Allowed: {self.VALID_TRANSITIONS.get(old_status, 'none')}",
                    exc=frappe.ValidationError,
                )

    def before_save(self):
        # Prevent modification of redeemed cards (except by system)
        if (self.get_doc_before_save()
            and self.get_doc_before_save().status == "Redeemed"
            and not frappe.flags.in_patch):
            frappe.throw("Redeemed cards cannot be modified", exc=frappe.PermissionError)
```

**Additional enforcement:**
- Log every state transition to an immutable child table or separate log DocType
- Make the status field read-only in the Frappe form for non-System Manager roles
- The redemption endpoint must use raw SQL `UPDATE ... WHERE status = 'Active'` (not `save()`), so the state check is atomic with the update

**Detection (warning signs):**
- Card status changed without corresponding log entry
- Cards in "Redeemed" state with no `redeemed_by` or `redeemed_at` values
- Cards that were "Active" but have no allocation record

---

### Pitfall 8: Whitelisted Method Security for Redemption Endpoint

**Severity:** MODERATE
**Phase:** FastAPI redemption endpoint and Frappe API
**Confidence:** HIGH

**What goes wrong:**
The redemption flow involves FastAPI calling a Frappe whitelisted method. Several security pitfalls arise:

1. **Missing `allow_guest=False`:** If the Frappe whitelisted method accidentally has `allow_guest=True` or omits permission checks, anyone can call it directly via `/api/method/...` without authentication.

2. **No role-based restriction:** The whitelisted method must verify that the caller is authorized. The FastAPI sidecar authenticates via API key (`frappe_api_key:frappe_api_secret` in config.py), but direct Frappe Desk users should NOT be able to call the redemption method manually.

3. **Parameter injection:** The Frappe whitelisted method receives `player_id` from FastAPI. If a malicious admin modifies the request, they could pass a different `player_id` to grant subscriptions to arbitrary players.

**Prevention:**

```python
@frappe.whitelist(methods=["POST"])  # POST only, not GET
def redeem_voucher_card(pin_plaintext: str, player_id: str) -> dict:
    """Atomic voucher redemption.

    NOTE: This is called by FastAPI sidecar using API key auth.
    Direct Desk access is restricted by frappe.only_for().
    """
    # Restrict to API key users (FastAPI sidecar) and System Managers
    if not frappe.local.api_authentication:
        frappe.only_for("System Manager")

    # Validate player_id exists
    if not frappe.db.exists("Memora Player Profile", player_id):
        frappe.throw(f"Player {player_id} not found", exc=frappe.DoesNotExistError)

    # ... redemption logic
```

**FastAPI side validation:**
```python
# The FastAPI endpoint must validate that the JWT user matches the player_id
@router.post("/vouchers/redeem")
async def redeem_voucher(
    body: RedeemRequest,
    user: CurrentUser,  # JWT-authenticated player
    frappe_client: FrappeClient = Depends(get_frappe_client),
):
    # CRITICAL: Player can only redeem for themselves
    result = await frappe_client.call(
        "memora_admin.api.vouchers.redeem_voucher_card",
        {"pin_plaintext": body.pin, "player_id": user.sub},  # user.sub, NOT body.player_id
    )
    return result
```

**Detection (warning signs):**
- Redemption log shows `redeemed_by` player that does not match the authenticated user
- Direct API calls to `/api/method/memora_admin.api.vouchers.redeem_voucher_card` from browser (not FastAPI)
- Audit trail shows redemptions without corresponding FastAPI request logs

**Sources:**
- [Frappe Code Security Guidelines](https://github.com/frappe/erpnext/wiki/Code-Security-Guidelines)
- [Frappe Security Vulnerabilities](https://www.cvedetails.com/vulnerability-list/vendor_id-17053/product_id-40772/Frappe-Frappe.html)

---

### Pitfall 9: Encrypted Export File Key Management

**Severity:** MODERATE
**Phase:** Card export functionality
**Confidence:** MEDIUM

**What goes wrong:**
Generated PINs must be exported (for printing on physical cards) and the export file must be encrypted. Several key management pitfalls:

1. **Key stored alongside data:** If the encryption key is in the same `site_config.json` as the database credentials, a server compromise exposes both the encrypted file and its key.

2. **No key rotation plan:** When the HMAC secret or export encryption key needs rotation (employee departure, suspected compromise), there is no mechanism to re-encrypt existing exports or re-HMAC existing cards.

3. **Export files not cleaned up:** Encrypted PIN files accumulate on disk. If an old export is decrypted (key found in backup), all PINs from that batch are compromised.

4. **IV reuse in AES encryption:** If using AES-CBC or AES-GCM, reusing the same Initialization Vector across exports breaks the encryption's security properties entirely.

**Prevention:**

```python
import os
from cryptography.fernet import Fernet  # Fernet handles IV internally

def encrypt_export(plaintext_csv: bytes, export_key: bytes) -> bytes:
    """Encrypt export file using Fernet (AES-128-CBC with HMAC-SHA256).

    Fernet handles IV generation, authentication, and versioning internally.
    Each call generates a unique IV, preventing IV reuse.
    """
    f = Fernet(export_key)
    return f.encrypt(plaintext_csv)

def generate_export_key() -> str:
    """Generate a per-batch export key. Store in batch document, NOT site_config."""
    return Fernet.generate_key().decode()
```

**Key management rules:**
1. **HMAC secret** (for PIN hashing): One per site, in `site_config.json`, rotated annually or on compromise
2. **Export encryption key**: One per batch, stored in the Voucher Batch document (Password fieldtype), transmitted out-of-band to the person who needs to print cards
3. **Export file TTL**: Delete encrypted export files after 7 days (configurable). Log deletions for audit.
4. **Never store plaintext PINs** in the database after the export file is generated. The only permanent record is the HMAC hash.

**HMAC key rotation procedure:**
```python
def rotate_hmac_key(old_secret: str, new_secret: str):
    """Re-hash all card PINs with new secret.

    WARNING: This requires the plaintext PINs, which are only available
    in the encrypted export files. Cards whose export files have been
    deleted CANNOT have their HMAC rotated -- they must be voided.
    """
    # This is inherently complex. Document the limitation upfront.
    pass
```

The practical implication: **HMAC key rotation for cards whose PINs are already printed and distributed is effectively impossible** without re-issuing cards. This means the HMAC secret must be treated as a long-lived secret with exceptional protection.

**Detection (warning signs):**
- Export files older than 30 days still present on disk
- Multiple export files using the same encryption key
- `site_config.json` contains both HMAC secret and export encryption key
- Encrypted export file and its key stored in same backup archive

**Sources:**
- [AES-256 Encryption Types and Pitfalls](https://terrazone.io/aes-256-encryption-types/)
- [Encryption Key Rotation Best Practices](https://www.kiteworks.com/regulatory-compliance/encryption-key-rotation-strategies/)
- [Key Management Best Practices](https://dev.ubiqsecurity.com/docs/key-mgmt-best-practices)

---

### Pitfall 10: Rate Limiting Bypass via Distributed Attack

**Severity:** MODERATE
**Phase:** Redemption endpoint security
**Confidence:** MEDIUM

**What goes wrong:**
The PRD specifies rate limits of 5 redemption attempts per player per hour and 20 per IP per hour. But:

1. **IP-based limiting is ineffective against distributed botnets.** An attacker with 100 IPs gets 2,000 attempts/hour.
2. **Player-based limiting requires authentication.** An attacker who does NOT have an account cannot be rate-limited by player ID -- only by IP.
3. **The existing `RateLimiter` class** (rate_limit.py) uses Redis INCR with TTL, which is correct for single-key limiting but does not prevent distributed attacks.

For a 12-character alphanumeric PIN (36^12 = 4.7 x 10^18 combinations), brute force is infeasible even at 2,000 attempts/hour. But if PINs have lower entropy (e.g., 8 numeric digits = 10^8 = 100 million combinations), an attacker with 1,000 IPs at 20/hour could try 20,000/hour and exhaust the space in 5,000 hours (~208 days). With reduced entropy (predictable batch prefixes, sequential generation), the attack becomes feasible.

**Prevention:**

1. **PIN entropy must be sufficient:** Minimum 12 alphanumeric characters using `secrets.choice()` from a 36-character alphabet (A-Z, 0-9). This gives ~62 bits of entropy.

```python
import secrets
import string

PIN_ALPHABET = string.ascii_uppercase + string.digits  # 36 chars
PIN_LENGTH = 12  # 36^12 = ~4.7 x 10^18

def generate_secure_pin() -> str:
    """Generate cryptographically secure random PIN."""
    return "".join(secrets.choice(PIN_ALPHABET) for _ in range(PIN_LENGTH))
```

2. **Global rate limit** (in addition to per-player and per-IP):
```python
# Global redemption attempt limit: 200/minute across all users
GLOBAL_REDEEM_KEY = "memora:ratelimit:redeem:global"
global_count = await redis.incr(GLOBAL_REDEEM_KEY)
if global_count == 1:
    await redis.expire(GLOBAL_REDEEM_KEY, 60)
if global_count > 200:
    raise HTTPException(429, "System busy, try again later")
```

3. **Exponential backoff on failures per PIN prefix:** If the same first 4 characters are tried repeatedly, increase delay. This detects targeted brute-force against a specific batch.

4. **Account lockout on excessive failures:** After 10 failed redemption attempts, require a cooldown period or CAPTCHA challenge (server-side, not client-side).

**Detection (warning signs):**
- High rate of "Invalid PIN" responses from diverse IPs
- Redemption attempts with sequential or patterned PIN inputs
- Spike in failed redemptions from a geographic region where no cards were distributed

---

### Pitfall 11: Naming Series Contention During Concurrent Batch Operations

**Severity:** MODERATE
**Phase:** Batch generation and card creation
**Confidence:** MEDIUM

**What goes wrong:**
Frappe's naming series uses `SELECT ... FOR UPDATE` on the `tabSeries` table to ensure unique sequential names. If two batch generation jobs run concurrently (admin generates batch A while batch B is still generating), they both contend for the same series counter. This creates:

1. **Serial bottleneck:** Each card insert waits for the previous insert's series lock to release
2. **Potential deadlock:** If the card DocType and batch DocType share series counter timing
3. **Gaps in sequence:** If one batch fails mid-generation and rolls back, the series counter has already advanced but the names were rolled back, leaving gaps

**Prevention:**

1. **Use batch-scoped naming** instead of global naming series:
```json
{
    "autoname": "format:VC-{card_batch}-.#####"
}
```
This ties the naming to the batch, reducing cross-batch contention. But Frappe may still use the global series counter.

2. **Better: Use hash-based naming** to eliminate series contention entirely:
```python
# In DocType class:
def autoname(self):
    """Use batch + sequence for deterministic, contention-free naming."""
    self.name = f"{self.card_batch}-{self.sequence_in_batch:05d}"
```

3. **Prevent concurrent generation:** Only allow one batch to be in "Generating" state at a time:
```python
def validate_no_concurrent_generation(batch_name: str):
    generating = frappe.db.count(
        "Memora Voucher Batch",
        filters={"status": "Generating", "name": ["!=", batch_name]},
    )
    if generating > 0:
        frappe.throw("Another batch is currently being generated. Please wait.")
```

**Detection (warning signs):**
- MariaDB slow query log shows `tabSeries` in lock waits
- Card names have unexpected gaps (VC-00100, VC-00101, VC-00150 -- gap from 101 to 150)
- Batch generation takes 3x longer when two batches run simultaneously

---

## Minor Pitfalls

### Pitfall 12: Export Log Not Created Atomically with File

**Severity:** LOW
**Phase:** Export functionality
**Confidence:** MEDIUM

**What goes wrong:**
The export creates an encrypted file on disk, then creates an Export Log document in Frappe. If the process crashes between file creation and log creation, the file exists on disk with no audit trail record.

**Prevention:**
Create the Export Log document FIRST (with status "In Progress"), then create the file, then update the log to "Completed". If the process crashes, the "In Progress" log serves as evidence that a file MAY exist on disk.

---

### Pitfall 13: Library Allocation Without Inventory Reconciliation

**Severity:** LOW
**Phase:** Library/distributor management
**Confidence:** MEDIUM

**What goes wrong:**
Cards are allocated to libraries (marked as "belonging to Library X") but there is no mechanism to verify that the physical cards were actually received. If a shipment is lost, the system shows cards as "Allocated to Library X" and Active, but the cards do not physically exist at the library. If someone finds the lost shipment and redeems the cards, the library is financially responsible for cards they never received.

**Prevention:**
- Add a "Library Confirmed Receipt" step after allocation
- Cards remain in "Allocated" (not "Active") state until the library confirms receipt
- Include a bulk "Activate" action that the library admin triggers after confirming physical receipt

---

### Pitfall 14: Credit Note / Return Handling Edge Cases

**Severity:** LOW
**Phase:** Financial operations (returns, adjustments)
**Confidence:** MEDIUM

**What goes wrong:**
When a library returns unsold/expired cards, a credit note must be generated. Edge cases:

1. **Partially used batch:** Library returns 80 of 100 cards. 15 were redeemed, 5 are lost. Credit note must account for only the 80 returned cards.
2. **Commission clawback:** If the platform already collected commission on the full batch, the credit note must reverse commission for only the returned cards.
3. **Already-expired cards:** Cards past their expiration date cannot be returned for credit (they have zero value), but the library may dispute this.
4. **Redeemed cards in return pile:** A library accidentally includes a redeemed card in the return. The system must verify each card's status before crediting.

**Prevention:**
- Return processing must scan/verify each card (by PIN or card ID)
- System is the source of truth for card status (as PRD specifies)
- Credit note line items must list each card with its status at time of return
- Commission reversal calculated per-card, not per-batch, using the same Decimal arithmetic as the original calculation

---

### Pitfall 15: Frappe Desk UI Performance with Large Card Lists

**Severity:** LOW
**Phase:** Admin interface
**Confidence:** MEDIUM

**What goes wrong:**
A Frappe list view loading 10,000 voucher cards per batch is extremely slow. The default list view fetches 20 records at a time, but filtering by batch and sorting by status creates a slow SQL query without proper indexes.

**Prevention:**
- Add database indexes on `(card_batch, status)` composite
- Set `grid_page_length = 50` in DocType JSON
- Consider a custom report page instead of list view for batch analysis
- Add `card_batch` and `status` to `in_standard_filter` for fast filtering

---

## Phase-Specific Warnings

| Phase Topic | Likely Pitfall | Severity | Mitigation |
|-------------|---------------|----------|------------|
| DocType creation (schema) | Float for commission rate | CRITICAL | Use Data fieldtype with Decimal in Python |
| DocType creation (schema) | No unique index on pin_hash | CRITICAL | Add unique index in DocType JSON or via SQL |
| PIN generation | Using `random` instead of `secrets` | CRITICAL | Always use `secrets.choice()` for PIN chars |
| HMAC implementation | Using `==` for comparison | CRITICAL | Use `hmac.compare_digest()` exclusively |
| Batch generation | Synchronous in web request | CRITICAL | Always use `frappe.enqueue()` with long queue |
| Batch generation | No progress tracking | MODERATE | Use `frappe.publish_realtime()` for updates |
| Card activation | No batch state validation | MODERATE | Verify batch status before activating cards |
| Redemption endpoint | No SELECT FOR UPDATE | CRITICAL | Use pessimistic locking for atomic redemption |
| Redemption endpoint | Player ID from request body | MODERATE | Always use JWT `sub` claim, never trust body |
| Subscription Transaction creation | Missing `related_grant` | MODERATE | Map batch -> product grant before creating TRX |
| Commission calculation | Python float math | CRITICAL | Use `decimal.Decimal` with string initialization |
| Export generation | Plaintext PINs stored permanently | CRITICAL | Clear PIN storage after export, keep only HMAC |
| Export encryption | IV reuse across exports | MODERATE | Use Fernet (handles IV internally) |
| Library allocation | No receipt confirmation | LOW | Add confirmation step between allocate and activate |
| Returns processing | Float-based credit calculation | MODERATE | Same Decimal treatment as original commission |
| Financial reporting | Aggregate mismatch (sum of parts != total) | MODERATE | Add reconciliation assertions in batch code |

---

## Memora-Specific Integration Risks

### Risk: on_subscription_change Hook + Redis Connection Failure

The existing `on_subscription_change` handler in `access_sync.py` creates a new Redis connection per call via `get_fastapi_redis()`. If Redis is temporarily unreachable during voucher redemption, this handler raises `redis.ConnectionError`, which propagates up through the Player Subscription insert, through `_handle_approval()`, through the Subscription Transaction `on_update`, and rolls back the ENTIRE transaction -- including the card status update from "Active" to "Redeemed".

This is actually SAFE (card stays "Active", player retries), but it means a Redis outage blocks ALL voucher redemptions even though Redis is just a cache. The existing `ensure_hydrated()` pattern could handle the delayed sync.

**Mitigation:** Wrap the Redis SADD in `on_subscription_change` with a try/except that logs a warning and adds the player to a "needs Redis sync" queue, rather than letting it fail the transaction. This is a CHANGE to existing code and should be carefully considered -- it trades atomicity for availability.

### Risk: FrappeClient Singleton in FastAPI Sidecar

The FastAPI sidecar uses a singleton `FrappeClient` (deps.py line 231-239) for calling Frappe whitelisted methods. The voucher redemption endpoint will make a synchronous HTTP call from FastAPI to Frappe. Under high redemption load, the singleton `httpx.AsyncClient` with default connection pool settings could become a bottleneck. The default `httpx` connection pool allows 100 connections, but all redemption requests must wait for Frappe to process them sequentially (Frappe is not async).

**Mitigation:** Set explicit connection pool limits on the FrappeClient and implement a timeout shorter than the FastAPI request timeout (e.g., 15s for the Frappe call within a 30s FastAPI timeout). Add circuit breaker logic if Frappe becomes unresponsive.

---

## Pre-Implementation Checklist

Before writing any voucher code, verify:

- [ ] `site_config.json` has `voucher_hmac_secret` with >= 32 bytes
- [ ] MariaDB has `innodb_lock_wait_timeout >= 30`
- [ ] The `tabMemora Voucher Card` table has a UNIQUE index on `pin_hash`
- [ ] Background job workers are configured with sufficient timeout (long queue)
- [ ] The existing `_handle_approval()` code is understood (commit is silently ignored)
- [ ] The `on_subscription_change` Redis call behavior is documented (failure = rollback)
- [ ] Financial calculations use `decimal.Decimal` not `float`
- [ ] PIN generation uses `secrets.choice()` not `random.choice()`

---

## Sources

### Concurrency & Locking
- [Database Locking to Solve Race Condition](https://www.coderbased.com/p/database-locking)
- [Transaction Locking to Prevent Race Conditions](https://sqlfordevs.com/transaction-locking-prevent-race-condition)
- [InnoDB Lock Modes - MariaDB](https://mariadb.com/docs/server/server-usage/storage-engines/innodb/innodb-lock-modes)
- [InnoDB Deadlocks - MySQL](https://dev.mysql.com/doc/refman/8.0/en/innodb-deadlocks.html)
- [Avoid deadlock from gap lock in InnoDB](https://medium.com/@tanishiking/avoid-deadlock-caused-by-a-conflict-of-transactions-that-accidentally-acquire-gap-lock-in-innodb-a114e975fd72)

### Cryptographic Security
- [Timing Attacks against String Comparison in Python](https://sqreen.github.io/DevelopersSecurityBestPractices/timing-attack/python)
- [hmac timing attack - Precli Documentation](https://precli.readthedocs.io/0.6.3/rules/python/stdlib/hmac-timing-attack/)
- [CVE-2022-48566 - Python hmac.compare_digest](https://www.cve.news/cve-2022-48566/)
- [Constant time compare in Python](https://securitypitfalls.wordpress.com/2018/08/03/constant-time-compare-in-python/)
- [Python secrets module](https://docs.python.org/3/library/secrets.html)

### Frappe Framework Internals
- [Frappe Database API](https://docs.frappe.io/framework/user/en/api/database)
- [Frappe database.py source](https://github.com/frappe/frappe/blob/develop/frappe/database/database.py)
- [frappe.db.commit() has no effect](https://discuss.frappe.io/t/frappe-db-commit-has-no-effect/129106)
- [Frappe Background Jobs](https://docs.frappe.io/framework/user/en/api/background_jobs)
- [Deferred Bulk Inserts In Frappe](https://tej.sh/blog/frappe-deferred-bulk/)
- [Frappe Bulk Insert Discussion](https://discuss.frappe.io/t/bulk-insert-in-frappe/99300)
- [Frappe Code Security Guidelines](https://github.com/frappe/erpnext/wiki/Code-Security-Guidelines)
- [Frappe Naming Series Performance](https://discuss.frappe.io/t/frappe-performance-naming-series/38108)

### Financial Calculations
- [Python Decimal vs Float](https://pranaysuyash.medium.com/how-i-lost-10-000-because-of-a-python-float-and-how-you-can-avoid-my-mistake-3bd2e5b4094d)
- [Python decimal module](https://docs.python.org/3/library/decimal.html)
- [How to Handle Monetary Values in Python](https://shakuro.com/blog/how-to-handle-monetary-values)

### Encryption & Key Management
- [AES-256 Encryption Types and Pitfalls](https://terrazone.io/aes-256-encryption-types/)
- [Encryption Key Rotation Best Practices](https://www.kiteworks.com/regulatory-compliance/encryption-key-rotation-strategies/)
- [Key Management Best Practices](https://dev.ubiqsecurity.com/docs/key-mgmt-best-practices)

### Brute Force & PIN Security
- [Prevent Gift Card Cracking](https://www.f5.com/go/solution/gift-card-cracking)
- [Prevent Brute Force Coupon/Gift Card Fraud](https://medium.com/perimeterx/prevent-brute-force-attacks-coupon-fraud-gift-card-fraud-199a02c5d43)
- [Gift Card Hacking](https://medium.com/@claudio_moranb/gift-card-hacking-821d63a0b248)

---

**Research completed:** 2026-02-13
**Confidence level:** HIGH (verified against existing Memora codebase, Frappe framework internals, and security literature)
**Downstream:** Use in roadmap creation for voucher management milestone (phase ordering, research flags, implementation constraints)
