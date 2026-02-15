# API Contracts: Voucher Test Infrastructure

**Feature**: 002-voucher-test-infra | **Date**: 2026-02-15

## Module: `memora_admin.memora_admin.tests.voucher_fixtures`

### `make_season(**kwargs) -> Document`

Creates a `Memora Season` document.

```python
def make_season(
    season_title: str | None = None,   # Auto-generated if None
    season_seq: int = 1,
    start_date: str | None = None,     # Defaults to today()
    end_date: str | None = None,       # Defaults to today() + 365 days
    is_published: bool = True,
) -> Document:
```

**Returns**: Saved `Memora Season` document.

**Example**:
```python
season = make_season()
season = make_season(is_published=False)
season = make_season(end_date="2025-01-01")  # expired season
```

---

### `make_product_grant(**kwargs) -> Document`

Creates a `Memora Product Grant` document with optional plan dependency chain.

```python
def make_product_grant(
    item_code: str = "MEMORA-VOUCHER-CARD",
    plan: str | None = None,           # Auto-created if None
    is_published: bool = True,
    season: str | None = None,         # Used when auto-creating plan
    grade: str | None = None,          # Used when auto-creating plan
) -> Document:
```

**Returns**: Saved `Memora Product Grant` document.

**Side effects**: Creates `Memora Academic Plan`, `Memora Season`, and `Memora Grade` if `plan` is not provided.

**Example**:
```python
grant = make_product_grant()
grant = make_product_grant(plan="PLAN-00001")
grant = make_product_grant(item_code="CUSTOM-ITEM")
```

---

### `make_customer(**kwargs) -> Document`

Creates a `Customer` document with voucher-specific custom fields.

```python
def make_customer(
    customer_name: str | None = None,         # Auto-generated if None
    requires_approval: bool = False,
    commission_type: str | None = None,       # "Percentage" or "Fixed Amount"
    commission_value: str | None = None,      # e.g., "10" for 10%
) -> Document:
```

**Returns**: Saved `Customer` document.

**Example**:
```python
library = make_customer()
library = make_customer(requires_approval=True)
library = make_customer(commission_type="Percentage", commission_value="10")
```

---

### `make_batch(**kwargs) -> Document`

Creates a `Memora Voucher Batch` document.

```python
def make_batch(
    batch_name: str | None = None,     # Auto-generated if None
    quantity: int = 10,
    pin_length: int = 12,
    face_value: float = 5,
    grants: list[str] | None = None,   # List of Product Grant names
    status: str = "Draft",
) -> Document:
```

**Returns**: Saved `Memora Voucher Batch` document.

**Notes**:
- If `grants` is provided, creates `Memora Voucher Batch Grant` child rows.
- `status` is set directly — caller is responsible for logical consistency.

**Example**:
```python
batch = make_batch()
batch = make_batch(quantity=50, face_value=10)
batch = make_batch(grants=[grant.name])
```

---

### `make_player(**kwargs) -> Document`

Creates a `Memora Player Profile` document with all required dependencies.

```python
def make_player(
    display_name: str | None = None,   # Auto-generated if None
    plan: str | None = None,           # Auto-created with dependencies if None
    grade: str | None = None,          # Auto-created if None
    major: str | None = None,          # Auto-created if None
    season: str | None = None,         # Auto-created if None
) -> Document:
```

**Returns**: Saved `Memora Player Profile` document.

**Side effects**: Creates `Memora Academic Plan`, `Memora Grade`, `Memora Major`, and `Memora Season` when not provided.

**Example**:
```python
player = make_player()
player = make_player(plan=existing_plan.name, grade=grade.name, major=major.name, season=season.name)
```

---

### `make_allocation(**kwargs) -> Document`

Creates a `Memora Voucher Allocation` document.

```python
def make_allocation(
    batch: str,                        # Required: Voucher Batch name
    customer: str,                     # Required: Customer name
    allocation_type: str = "Allocate",
    sale_model: str = "Prepaid",
) -> Document:
```

**Returns**: Saved `Memora Voucher Allocation` document in Draft status.

**Example**:
```python
alloc = make_allocation(batch=batch.name, customer=library.name)
alloc = make_allocation(batch=batch.name, customer=library.name, sale_model="Consignment")
```

---

## Module: `memora_admin.memora_admin.tests.voucher_helpers`

### `generate_batch_sync(batch_name: str) -> None`

Generates cards synchronously by calling `generate_cards_job()` directly.

```python
def generate_batch_sync(batch_name: str) -> None:
```

**Preconditions**: Batch must be in Draft status with valid quantity and grants.

**Postconditions**: Batch transitions to Generated status. Cards are created with serial numbers and HMAC-hashed PINs. Encrypted export file is attached.

**Raises**: Any exception from `generate_cards_job()` propagates directly.

**Example**:
```python
batch = make_batch(grants=[grant.name])
generate_batch_sync(batch.name)
batch.reload()
assert batch.status == "Generated"
```

---

### `get_card_statuses(batch_name: str) -> dict[str, int]`

Returns a dictionary of card status counts for a batch.

```python
def get_card_statuses(batch_name: str) -> dict[str, int]:
```

**Returns**: Dict mapping status strings to counts, e.g., `{"Available": 8, "Allocated": 2}`. Only includes statuses with count > 0.

**Example**:
```python
statuses = get_card_statuses(batch.name)
assert statuses.get("Available", 0) == 10
```

---

### `fill_and_complete_allocation(batch_name: str, customer_name: str, quantity: int = 0, sale_model: str = "Prepaid") -> Document`

Creates, fills, and completes an allocation in one call.

```python
def fill_and_complete_allocation(
    batch_name: str,
    customer_name: str,
    quantity: int = 0,           # 0 = all available cards
    sale_model: str = "Prepaid",
) -> Document:
```

**Returns**: Completed `Memora Voucher Allocation` document.

**Postconditions**: Cards are transitioned to Allocated status. Batch may transition to Active. If prepaid, a Sales Invoice is created.

**Example**:
```python
alloc = fill_and_complete_allocation(batch.name, library.name, quantity=5)
assert alloc.status == "Completed"
```

---

### `redeem_card_by_pin(pin: str, player_id: str, grant_id: str, ip_address: str = "") -> dict`

Computes HMAC from plaintext PIN and calls the redemption API.

```python
def redeem_card_by_pin(
    pin: str,                    # Plaintext PIN (from decrypted export)
    player_id: str,              # Player Profile name
    grant_id: str,               # Product Grant name
    ip_address: str = "",
) -> dict:
```

**Returns**: Result dict from `redeem_voucher()` — either `{"status": "success", "transaction_id": "..."}` or `{"error": "ERROR_CODE"}`.

**Example**:
```python
result = redeem_card_by_pin("ABCD1234EFGH", player.name, grant.name)
assert result["status"] == "success"
```

---

### `assert_batch_counters(test_case, batch_name: str, **expected) -> None`

Asserts batch counter fields match expected values.

```python
def assert_batch_counters(
    test_case: FrappeTestCase,   # For assertEqual/fail methods
    batch_name: str,
    generated_count: int | None = None,
    allocated_count: int | None = None,
    redeemed_count: int | None = None,
    voided_count: int | None = None,
    expired_count: int | None = None,
) -> None:
```

**Notes**: Only asserts counters that are explicitly passed. Omitted counters are not checked. Reloads batch from DB before asserting.

**Example**:
```python
assert_batch_counters(self, batch.name, generated_count=10, allocated_count=5)
assert_batch_counters(self, batch.name, redeemed_count=1, voided_count=0)
```

---

## Module: `memora_admin.memora_admin.tests.voucher_test_base`

### `class VoucherTestCase(FrappeTestCase)`

Base test class with prerequisite checks.

```python
class VoucherTestCase(FrappeTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        """Check prerequisites before running voucher tests."""
        super().setUpClass()
        # Skips with descriptive message if:
        # 1. voucher_hmac_secret is not in site config
        # 2. MEMORA-VOUCHER-CARD Item does not exist
```

**Usage**:
```python
from memora_admin.memora_admin.tests.voucher_test_base import VoucherTestCase

class TestBatchGeneration(VoucherTestCase):
    def test_something(self):
        # Prerequisites guaranteed to be met
        pass
```
