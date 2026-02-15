# Test Contract: test_commission.py

**Feature**: 004-commission-invoice-tests
**Date**: 2026-02-15

## Module: TestCalculateCommission (unittest.TestCase)

Pure unit tests — no database access.

### Test 1: test_no_commission_none_type

**Requirement**: FR-001
**Input**: `face_value="5.00", quantity=10, commission_type=None, commission_value=None`
**Expected Output**:
- `per_card_commission == Decimal("0.00")`
- `total_commission == Decimal("0.00")`
- `net_per_card == Decimal("5.00")`
- `net_total == Decimal("50.00")`

### Test 2: test_no_commission_empty_string

**Requirement**: FR-001
**Input**: `face_value="5.00", quantity=10, commission_type="", commission_value=""`
**Expected Output**: Same as Test 1

### Test 3: test_percentage_commission

**Requirement**: FR-002
**Input**: `face_value="5.00", quantity=10, commission_type="Percentage", commission_value="10"`
**Expected Output**:
- `per_card_commission == Decimal("0.50")`
- `total_commission == Decimal("5.00")`
- `net_per_card == Decimal("4.50")`
- `net_total == Decimal("45.00")`

### Test 4: test_fixed_amount_commission

**Requirement**: FR-003
**Input**: `face_value="5.00", quantity=10, commission_type="Fixed Amount", commission_value="1.00"`
**Expected Output**:
- `per_card_commission == Decimal("1.00")`
- `total_commission == Decimal("10.00")`
- `net_per_card == Decimal("4.00")`
- `net_total == Decimal("40.00")`

### Test 5: test_repeating_decimal_precision

**Requirement**: FR-004
**Input**: `face_value="10.00", quantity=1, commission_type="Percentage", commission_value="33.33"`
**Expected Output**:
- `per_card_commission == Decimal("3.33")` (10.00 * 33.33 / 100 = 3.333 → ROUND_HALF_UP → 3.33)
- `net_per_card == Decimal("6.67")`

### Test 6: test_quantity_multiplication

**Requirement**: FR-005
**Input**: `face_value="5.00", quantity=10, commission_type="Percentage", commission_value="10"`
**Expected Output**:
- `net_per_card == Decimal("4.50")`
- `net_total == Decimal("45.00")` (4.50 * 10)

### Test 7: test_zero_face_value

**Requirement**: FR-006
**Input**: `face_value="0", quantity=10, commission_type="Percentage", commission_value="10"`
**Expected Output**: All four values == `Decimal("0.00")`

### Test 8: test_unknown_commission_type_defaults_to_zero

**Requirement**: FR-007
**Input**: `face_value="5.00", quantity=10, commission_type="UnknownType", commission_value="10"`
**Expected Output**:
- `per_card_commission == Decimal("0.00")`
- `net_per_card == Decimal("5.00")`

---

## Module: TestResolveCommission (VoucherTestCase)

Database-dependent tests using Frappe ORM.

### Test 9: test_grant_level_takes_precedence

**Requirement**: FR-008
**Setup**:
1. `make_product_grant(season="SEAS-00027")` → grant
2. `make_batch(grants=[grant.name])` → batch
3. Set `commission_type="Percentage"`, `commission_value="15"` on batch grant child row
4. `make_customer(commission_type="Fixed Amount", commission_value="2.00")` → customer

**Execute**: `resolve_commission(batch.name, customer.name)`
**Expected Output**: `("Percentage", "15")`

### Test 10: test_customer_default_when_no_grant_override

**Requirement**: FR-008
**Setup**:
1. `make_product_grant(season="SEAS-00027")` → grant
2. `make_batch(grants=[grant.name])` → batch (no commission on grant row)
3. `make_customer(commission_type="Fixed Amount", commission_value="2.00")` → customer

**Execute**: `resolve_commission(batch.name, customer.name)`
**Expected Output**: `("Fixed Amount", "2.00")`

### Test 11: test_no_commission_returns_none_none

**Requirement**: FR-008
**Setup**:
1. `make_product_grant(season="SEAS-00027")` → grant
2. `make_batch(grants=[grant.name])` → batch (no commission on grant row)
3. `make_customer()` → customer (no commission fields)

**Execute**: `resolve_commission(batch.name, customer.name)`
**Expected Output**: `(None, None)`
