# Phase 37: Financial Integration - Research

**Researched:** 2026-02-14
**Domain:** ERPNext Sales Invoice integration, commission calculation, scheduled billing
**Confidence:** HIGH

## Summary

Phase 37 integrates the voucher allocation and redemption flows with ERPNext's accounting system. The critical blocker from STATE.md -- "ERPNext Sales Invoice availability needs verification" -- is now **resolved**: ERPNext 15.93.0 IS installed on the site (`x.conanacademy.com`), with an active company ("Montaser Company"), default currency JOD (Jordanian Dinar), and 46 existing Sales Invoices using the `ACC-SINV-.YYYY.-` naming series. The JoFotara e-invoicing integration is also present (Jordanian tax compliance), which means submitted Sales Invoices may be forwarded to tax authorities.

The phase requires four capabilities: (1) prepaid allocation creates a Sales Invoice, (2) prepaid returns create a Credit Note, (3) commission calculation with Decimal precision, and (4) a monthly cron job for consignment billing. All financial logic is Frappe-side (no FastAPI changes). The commission priority chain (product-level override -> library default -> zero) is already modeled in the schema: `Memora Voucher Batch Grant` child table has `commission_type`/`commission_value` fields per-grant row, and `Customer` has custom fields `voucher_commission_type`/`voucher_commission_value`.

**Primary recommendation:** Use ERPNext's native Sales Invoice DocType directly via `frappe.get_doc()`. Create a service Item ("Memora Voucher Card") for invoice line items. Implement commission calculation as a pure Python service module using `decimal.Decimal`. Add `sales_invoice` Link field to Voucher Card and Voucher Allocation DocTypes. Hook financial logic into the existing allocation `on_update` flow (triggered when status transitions to "Completed").

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| ERPNext | 15.93.0 | Sales Invoice, Credit Note, accounting | Already installed, native Frappe integration |
| Frappe | 15.93.0 | ORM, hooks, scheduler, whitelisted API | Platform foundation |
| Python `decimal` | stdlib | Commission arithmetic | Avoids float precision issues (prior decision from 33-01) |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `frappe.utils` | built-in | `nowdate()`, `add_months()`, `get_first_day()`, `get_last_day()` | Date arithmetic for monthly billing windows |
| `frappe.custom.doctype.custom_field` | built-in | `create_custom_fields()` | Adding `sales_invoice` Link to Voucher Card/Allocation |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| ERPNext Sales Invoice | Custom "Memora Invoice" DocType | ERPNext is installed; custom DocType loses GL entries, tax calc, JoFotara integration, reporting. Not recommended. |
| Python `decimal` | `frappe.utils.flt()` | flt() uses Python float internally. Unacceptable for financial calculations per prior decision [33-01]. |
| Direct SQL for invoice creation | `frappe.get_doc().submit()` | ORM ensures GL entries, hooks, JoFotara sync all fire correctly. Direct SQL would skip accounting. |

## Architecture Patterns

### Recommended Project Structure
```
memora_admin/
  memora_admin/
    services/
      voucher/
        commission.py       # Pure commission calculation (Decimal)
        invoice.py          # Sales Invoice creation + Credit Note
    tasks/
      consignment_billing.py  # Monthly cron job
    custom/
      customer_fields.py      # (existing) voucher commission fields
      sales_invoice_fields.py # Custom fields on Sales Invoice (optional)
    events/
      ... (existing)
  hooks.py                    # Add cron entry for consignment billing
```

### Pattern 1: Commission Calculation Service (Pure Function)
**What:** Isolated module that calculates commission using `decimal.Decimal`, returns structured result.
**When to use:** Every time an invoice or credit note amount needs computing.
**Example:**
```python
# Source: Prior research from .planning/research/PITFALLS_voucher.md + 33-01 decision
from decimal import Decimal, ROUND_HALF_UP

TWO_PLACES = Decimal("0.01")


def calculate_commission(
    face_value: str,
    quantity: int,
    commission_type: str | None,
    commission_value: str | None,
) -> dict:
    """Calculate commission with exact Decimal arithmetic.

    Args:
        face_value: Card face value as string (from Voucher Batch.face_value).
        quantity: Number of cards.
        commission_type: "Percentage" or "Fixed Amount" or None/empty.
        commission_value: Rate/amount as string or None/empty.

    Returns:
        Dict with per_card_commission, total_commission,
        net_per_card, net_total -- all as Decimal objects.
    """
    fv = Decimal(str(face_value))
    qty = Decimal(str(quantity))

    if not commission_type or not commission_value:
        # No commission -- full face value invoiced
        return {
            "per_card_commission": Decimal("0.00"),
            "total_commission": Decimal("0.00"),
            "net_per_card": fv.quantize(TWO_PLACES),
            "net_total": (fv * qty).quantize(TWO_PLACES),
        }

    cv = Decimal(str(commission_value))

    if commission_type == "Percentage":
        per_card_commission = (fv * cv / Decimal("100")).quantize(
            TWO_PLACES, rounding=ROUND_HALF_UP
        )
    elif commission_type == "Fixed Amount":
        per_card_commission = cv.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    else:
        per_card_commission = Decimal("0.00")

    net_per_card = (fv - per_card_commission).quantize(TWO_PLACES)
    total_commission = (per_card_commission * qty).quantize(TWO_PLACES)
    net_total = (net_per_card * qty).quantize(TWO_PLACES)

    return {
        "per_card_commission": per_card_commission,
        "total_commission": total_commission,
        "net_per_card": net_per_card,
        "net_total": net_total,
    }
```

### Pattern 2: Commission Priority Chain Resolution
**What:** Resolves which commission to use following the priority chain: batch grant override -> library default -> zero.
**When to use:** Before calculating invoice amounts.
**Example:**
```python
def resolve_commission(batch_name: str, library: str) -> tuple[str | None, str | None]:
    """Resolve commission type and value using priority chain.

    Priority: Batch Grant override > Library (Customer) default > Zero.

    Returns:
        Tuple of (commission_type, commission_value) or (None, None).
    """
    import frappe

    # 1. Check batch grant-level override (product-level)
    # Memora Voucher Batch Grant is a child table on Memora Voucher Batch
    grants = frappe.get_all(
        "Memora Voucher Batch Grant",
        filters={"parent": batch_name, "commission_type": ["is", "set"]},
        fields=["commission_type", "commission_value"],
        limit=1,
    )
    if grants and grants[0].commission_type:
        return grants[0].commission_type, grants[0].commission_value

    # 2. Check library (Customer) default
    customer = frappe.db.get_value(
        "Customer", library,
        ["voucher_commission_type", "voucher_commission_value"],
        as_dict=True,
    )
    if customer and customer.voucher_commission_type:
        return customer.voucher_commission_type, customer.voucher_commission_value

    # 3. No commission
    return None, None
```

### Pattern 3: Sales Invoice Creation via ORM
**What:** Create and submit a Sales Invoice using `frappe.get_doc()`.
**When to use:** On prepaid allocation completion, and during monthly consignment billing.
**Example:**
```python
# Source: ERPNext test_sales_invoice.py patterns + live site data
import frappe
from frappe.utils import nowdate

def create_voucher_invoice(
    customer: str,
    items: list[dict],  # [{description, qty, rate}]
    remarks: str = "",
    is_return: bool = False,
    return_against: str | None = None,
    posting_date: str | None = None,
) -> str:
    """Create and submit a Sales Invoice for voucher cards.

    Args:
        customer: Customer (library) name.
        items: List of line item dicts with description, qty, rate.
        remarks: Explanatory text linking to allocation.
        is_return: True for Credit Notes.
        return_against: Original invoice name (for Credit Notes).
        posting_date: Override posting date (for consignment backdating).

    Returns:
        Submitted Sales Invoice name.
    """
    si = frappe.new_doc("Sales Invoice")
    si.customer = customer
    si.posting_date = posting_date or nowdate()
    si.is_return = 1 if is_return else 0
    si.return_against = return_against
    si.remarks = remarks

    for item in items:
        si.append("items", {
            "item_code": "MEMORA-VOUCHER-CARD",  # Service item
            "description": item["description"],
            "qty": item["qty"],
            "rate": float(item["rate"]),  # ERPNext expects float for rate
            "income_account": "Sales - MC",  # From company defaults
        })

    si.insert(ignore_permissions=True)
    si.submit()

    return si.name
```

### Pattern 4: Hooking Into Allocation Completion
**What:** Trigger invoice creation when a prepaid allocation transitions to Completed.
**When to use:** In `MemoraVoucherAllocation.on_update()`.
**Example:**
```python
# In memora_voucher_allocation.py, extend on_update:
def on_update(self):
    if not self.has_value_changed("status"):
        return
    if self.status == "Completed":
        if self.allocation_type == "Allocate":
            self._apply_allocation()
            if self.sale_model == "Prepaid":
                self._create_prepaid_invoice()
        elif self.allocation_type == "Return":
            self._apply_return()
            if self.sale_model == "Prepaid":
                self._create_credit_note()
        self._update_batch_counters()
```

### Pattern 5: Monthly Consignment Billing Cron
**What:** Scheduler task that runs on the 1st of each month, finds redeemed consignment cards from the previous month, groups them by library, and creates invoices.
**When to use:** Registered in `hooks.py` `scheduler_events["cron"]`.
**Example:**
```python
# hooks.py addition:
"0 2 1 * *": ["memora_admin.tasks.consignment_billing.generate_monthly_invoices"],
# Runs at 02:00 on the 1st of every month
```

### Anti-Patterns to Avoid
- **Using `frappe.utils.flt()` for commission math:** flt() uses Python float. Always use `decimal.Decimal` with string initialization.
- **Creating Sales Invoice with direct SQL:** This skips GL entries, tax calculation, JoFotara sync, and all accounting hooks. Always use `frappe.get_doc().insert().submit()`.
- **Calculating commission at batch level then dividing:** Calculate per-card first, then multiply by quantity. This ensures each card's commission can be independently verified.
- **Hardcoding company/account names:** Fetch from `frappe.db.get_single_value('Global Defaults', 'default_company')` and company doc. Accounts may change.
- **Not handling the Decimal-to-float conversion for ERPNext:** ERPNext's `rate` field is Currency (stored as DECIMAL(21,9) in MariaDB but Python float in ORM). Convert with `float(decimal_value)` only at the point of setting on the Sales Invoice Item, never earlier.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Invoice numbering | Custom sequential naming | ERPNext `naming_series` (`ACC-SINV-.YYYY.-`) | Already configured, handles concurrency, auditable |
| GL entries / double-entry accounting | Custom debit/credit records | ERPNext Sales Invoice `.submit()` | Creates proper journal entries, handles tax, rounding |
| Credit Notes | Custom "return" DocType | ERPNext `is_return=1` + `return_against` on Sales Invoice | Native accounting treatment, links to original invoice |
| Tax calculation | Manual tax computation | ERPNext Taxes and Charges template | JoFotara integration depends on proper tax records |
| Monthly scheduler | Custom cron daemon | Frappe `scheduler_events["cron"]` | Already running, handles failover, logging, retry |
| Decimal arithmetic | Custom rounding functions | Python `decimal.Decimal` with `ROUND_HALF_UP` | Proven stdlib, exact representation, no float drift |

**Key insight:** ERPNext is already installed and actively used (46 invoices, JoFotara integration). Using its native Sales Invoice gives us GL entries, tax compliance, reporting, and e-invoicing for free. A custom invoice DocType would require rebuilding all of this.

## Common Pitfalls

### Pitfall 1: Float Precision in Commission Calculation
**What goes wrong:** Using Python float for `face_value * commission_rate` produces results like 7.4999999999999991 instead of 7.50. Over thousands of cards, errors accumulate to visible discrepancies.
**Why it happens:** IEEE 754 floating-point cannot represent most decimal fractions exactly.
**How to avoid:** Use `decimal.Decimal` initialized from strings. Quantize at each step. Prior decision [33-01] already mandated Data fieldtype for commission_value specifically for this reason.
**Warning signs:** Invoice totals differ by 0.01 from manual calculation; batch summary doesn't reconcile with individual card records.

### Pitfall 2: ERPNext Sales Invoice Requires Submitted (docstatus=1) for GL Entries
**What goes wrong:** Creating a Sales Invoice with `.insert()` only puts it in Draft (docstatus=0). No accounting entries are created until `.submit()` is called.
**Why it happens:** ERPNext accounting follows the Submit model -- GL entries only happen on submission.
**How to avoid:** Always call `.submit()` after `.insert()`. For the allocation flow, the invoice should be created AND submitted atomically when allocation completes.
**Warning signs:** Invoices appear in desk but no accounting entries; P&L report doesn't show voucher revenue.

### Pitfall 3: Credit Note Must Reference Original Invoice
**What goes wrong:** Creating a Sales Invoice with `is_return=1` but no `return_against` creates a standalone credit note that ERPNext doesn't link to the original invoice. Outstanding amounts don't offset correctly.
**Why it happens:** ERPNext tracks return relationships explicitly.
**How to avoid:** Always set `return_against` to the original Sales Invoice name. This means the original invoice name must be stored on the Voucher Card or Allocation.
**Warning signs:** Customer outstanding amount doubles (credit note doesn't reduce it); "Credit Note Issued" status not set on original invoice.

### Pitfall 4: Consignment Cards Must NOT Be Invoiced at Allocation
**What goes wrong:** The allocation completion hook creates an invoice for ALL allocations, including consignment ones.
**Why it happens:** Missing `sale_model` check in the invoice creation logic.
**How to avoid:** Explicitly check `self.sale_model == "Prepaid"` before creating invoice on allocation. Consignment cards are only invoiced by the monthly cron job after redemption.
**Warning signs:** Libraries receive invoices for consignment cards they haven't sold yet.

### Pitfall 5: Consignment Return Requires No Financial Action
**What goes wrong:** The return handler creates a credit note for consignment returns.
**Why it happens:** Same logic applied to both sale models without distinguishing.
**How to avoid:** For returns, check `sale_model`: Prepaid returns create credit notes, Consignment returns do NOT (FIN-06). Consignment cards were never invoiced, so no credit note is needed.
**Warning signs:** Credit notes created for cards that were never invoiced; negative balance on library account.

### Pitfall 6: Monthly Billing Double-Invoices Cards
**What goes wrong:** The consignment billing job runs, invoices cards, but a failure occurs before the cards are marked as invoiced. On retry, the same cards are invoiced again.
**Why it happens:** Non-atomic invoice-creation + card-marking.
**How to avoid:** Mark each card's `sales_invoice` field in the same transaction as invoice submission. The billing job should filter on `sales_invoice IS NULL AND status = 'Redeemed' AND sale_model = 'Consignment'`.
**Warning signs:** Duplicate invoice line items; library billed twice for same card.

### Pitfall 7: JoFotara E-Invoicing Side Effects
**What goes wrong:** Programmatically submitted Sales Invoices get queued for JoFotara (Jordanian electronic invoicing). If the invoice data is malformed or missing required tax fields, JoFotara submission fails.
**Why it happens:** The JoFotara integration (corex_fotara app) hooks into Sales Invoice submission.
**How to avoid:** Ensure the service Item has proper tax template assignments. Test with a manual invoice first to verify JoFotara flow. Consider whether voucher invoices should use JoFotara or be excluded.
**Warning signs:** JoFotara status stuck on "Pending" or "Error" for voucher invoices.

### Pitfall 8: Batch Grant Has Multiple Product Grants with Different Commission
**What goes wrong:** A Voucher Batch can have multiple grants (e.g., "Arabic Full" + "Science Half"), each with different commission overrides. Using only the first grant's commission for the entire invoice is incorrect.
**Why it happens:** The `Memora Voucher Batch Grant` child table allows per-grant commission settings.
**How to avoid:** For the invoice, commission should be resolved at the BATCH level (not per-grant), since each card has ONE face value regardless of which grant is redeemed. The commission priority chain operates on the batch's grants as a unit. If multiple grants have different commission values, this is a data inconsistency -- the batch should have a single commission. Recommendation: use the FIRST grant's commission if set, else library default, else zero. Document this clearly.
**Warning signs:** Different cards from the same batch with same face value show different commission amounts.

## Code Examples

### Creating a Service Item for Voucher Invoices (Setup)
```python
# In setup.py or a one-time migration
import frappe

def ensure_voucher_service_item():
    """Create the Memora Voucher Card service item if it doesn't exist."""
    if frappe.db.exists("Item", "MEMORA-VOUCHER-CARD"):
        return

    item = frappe.get_doc({
        "doctype": "Item",
        "item_code": "MEMORA-VOUCHER-CARD",
        "item_name": "Memora Voucher Card",
        "item_group": "Services",
        "stock_uom": "Nos",
        "is_stock_item": 0,
        "is_sales_item": 1,
        "include_item_in_manufacturing": 0,
        "description": "Memora educational voucher card",
    })
    item.insert(ignore_permissions=True)
    frappe.db.commit()
```

### Adding Invoice Link Fields to Voucher Card and Allocation
```python
# In customer_fields.py or a new invoice_fields.py
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

def add_voucher_invoice_fields():
    """Add sales_invoice Link field to Voucher Card and Allocation. Idempotent."""
    custom_fields = {
        "Memora Voucher Card": [
            {
                "fieldname": "sales_invoice",
                "fieldtype": "Link",
                "label": "Sales Invoice",
                "options": "Sales Invoice",
                "insert_after": "section_void",  # After void section
                "read_only": 1,
            },
        ],
        "Memora Voucher Allocation": [
            {
                "fieldname": "sales_invoice",
                "fieldtype": "Link",
                "label": "Sales Invoice",
                "options": "Sales Invoice",
                "insert_after": "return_reason",
                "read_only": 1,
            },
        ],
    }
    create_custom_fields(custom_fields)
```

### Prepaid Invoice Creation (Full Example)
```python
import frappe
from frappe.utils import nowdate
from decimal import Decimal

def create_prepaid_allocation_invoice(allocation):
    """Create a Sales Invoice for a completed prepaid allocation.

    Args:
        allocation: MemoraVoucherAllocation document (status=Completed, sale_model=Prepaid).
    """
    batch = frappe.get_doc("Memora Voucher Batch", allocation.batch)
    card_count = len(allocation.allocation_cards)

    # Resolve commission
    commission_type, commission_value = resolve_commission(
        allocation.batch, allocation.customer
    )

    # Calculate amounts using Decimal
    result = calculate_commission(
        face_value=str(batch.face_value),
        quantity=card_count,
        commission_type=commission_type,
        commission_value=commission_value,
    )

    # Create Sales Invoice
    si = frappe.new_doc("Sales Invoice")
    si.customer = allocation.customer
    si.posting_date = nowdate()
    si.remarks = (
        f"Voucher allocation {allocation.name} | "
        f"Batch {batch.name} ({batch.batch_name}) | "
        f"{card_count} cards x {batch.face_value} JOD"
    )

    si.append("items", {
        "item_code": "MEMORA-VOUCHER-CARD",
        "description": f"Memora Voucher Cards - Batch {batch.batch_name}",
        "qty": card_count,
        "rate": float(result["net_per_card"]),  # face_value minus commission
    })

    si.insert(ignore_permissions=True)
    si.submit()

    # Link invoice to allocation
    frappe.db.set_value(
        "Memora Voucher Allocation", allocation.name,
        "sales_invoice", si.name,
    )

    # Link invoice to each card
    card_names = [row.voucher_card for row in allocation.allocation_cards]
    if card_names:
        placeholders = ", ".join(["%s"] * len(card_names))
        frappe.db.sql(
            f"""UPDATE `tabMemora Voucher Card`
            SET sales_invoice = %s, modified = NOW(), modified_by = %s
            WHERE name IN ({placeholders})""",
            [si.name, frappe.session.user] + card_names,
        )

    return si.name
```

### Credit Note for Prepaid Return
```python
def create_prepaid_return_credit_note(allocation):
    """Create a Credit Note for a completed prepaid return.

    Finds the original invoice from the allocation's cards and creates
    a return Sales Invoice with negative quantities.
    """
    batch = frappe.get_doc("Memora Voucher Batch", allocation.batch)
    card_names = [row.voucher_card for row in allocation.allocation_cards]
    card_count = len(card_names)

    # Find original invoice -- cards should have sales_invoice from allocation
    original_invoices = frappe.get_all(
        "Memora Voucher Card",
        filters={"name": ["in", card_names], "sales_invoice": ["is", "set"]},
        pluck="sales_invoice",
    )
    original_invoice = list(set(original_invoices))[0] if original_invoices else None

    # Resolve commission (same as original)
    commission_type, commission_value = resolve_commission(
        allocation.batch, allocation.customer
    )
    result = calculate_commission(
        face_value=str(batch.face_value),
        quantity=card_count,
        commission_type=commission_type,
        commission_value=commission_value,
    )

    # Create Credit Note (is_return=1, negative qty)
    si = frappe.new_doc("Sales Invoice")
    si.customer = allocation.customer
    si.posting_date = nowdate()
    si.is_return = 1
    si.return_against = original_invoice
    si.remarks = (
        f"Return allocation {allocation.name} | "
        f"Batch {batch.name} ({batch.batch_name}) | "
        f"{card_count} cards returned"
    )

    si.append("items", {
        "item_code": "MEMORA-VOUCHER-CARD",
        "description": f"Memora Voucher Cards Return - Batch {batch.batch_name}",
        "qty": -card_count,  # Negative for returns
        "rate": float(result["net_per_card"]),
    })

    si.insert(ignore_permissions=True)
    si.submit()

    # Link credit note to allocation
    frappe.db.set_value(
        "Memora Voucher Allocation", allocation.name,
        "sales_invoice", si.name,
    )

    return si.name
```

### Monthly Consignment Billing Task
```python
# tasks/consignment_billing.py
import frappe
from frappe.utils import add_months, get_first_day, get_last_day, nowdate

def generate_monthly_invoices():
    """Generate invoices for redeemed consignment cards from previous month.

    Runs on the 1st of each month. Groups redeemed consignment cards
    by library and creates one invoice per library.
    """
    today = nowdate()
    # Previous month window
    prev_month_start = get_first_day(add_months(today, -1))
    prev_month_end = get_last_day(add_months(today, -1))

    # Find redeemed consignment cards not yet invoiced
    cards = frappe.db.sql("""
        SELECT c.name, c.batch, c.library, c.redeemed_at,
               b.face_value, b.batch_name
        FROM `tabMemora Voucher Card` c
        JOIN `tabMemora Voucher Batch` b ON c.batch = b.name
        WHERE c.status = 'Redeemed'
          AND c.sale_model = 'Consignment'
          AND c.sales_invoice IS NULL
          AND c.redeemed_at >= %s
          AND c.redeemed_at <= %s
        ORDER BY c.library, c.batch
    """, (prev_month_start, prev_month_end), as_dict=True)

    if not cards:
        return

    # Group by library
    from itertools import groupby
    from operator import itemgetter

    for library, lib_cards in groupby(cards, key=itemgetter("library")):
        lib_cards = list(lib_cards)
        try:
            _create_consignment_invoice(library, lib_cards, prev_month_start, prev_month_end)
            frappe.db.commit()
        except Exception:
            frappe.db.rollback()
            frappe.log_error(
                title=f"Consignment billing failed for library {library}"
            )
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Custom invoice DocType | ERPNext Sales Invoice | Confirmed available 2026-02-14 | Full accounting, GL entries, JoFotara integration |
| Float-based commission | Decimal arithmetic with Data fieldtype | Decision 33-01 | Exact financial calculations, no accumulation errors |
| Manual invoice tracking | `sales_invoice` Link field on Voucher Card | Phase 37 (new) | Each card traces to its invoice per FIN-07 |

**Key site configuration discovered:**
- Company: "Montaser Company"
- Currency: JOD (Jordanian Dinar)
- Default income account: "Sales - MC"
- Cost center: "Main - MC"
- Naming series: `ACC-SINV-.YYYY.-`
- JoFotara e-invoicing: Active (custom fields on Sales Invoice)
- Item Groups available: "Services" (suitable for service-type item)

## ERPNext Sales Invoice Key Facts

### Creating a Sales Invoice Programmatically
1. `frappe.new_doc("Sales Invoice")` -- sets defaults from company
2. Set `customer`, `posting_date`, add items with `item_code`, `qty`, `rate`
3. `.insert(ignore_permissions=True)` -- validates, saves as Draft (docstatus=0)
4. `.submit()` -- creates GL entries (docstatus=1)
5. For Credit Notes: set `is_return=1`, `return_against="SINV-NAME"`, negative `qty`

### Required Fields for Minimal Invoice
- `customer` (Link to Customer) -- the library
- `items[].item_code` (Link to Item) -- service item
- `items[].qty` (Int/Float) -- card count
- `items[].rate` (Currency) -- net amount per card (face_value minus commission)
- Company auto-defaults from Global Defaults

### Credit Note Pattern (from ERPNext test suite)
```python
# Credit Note = Sales Invoice with is_return=1 and negative qty
cr_note = frappe.new_doc("Sales Invoice")
cr_note.customer = "Library Name"
cr_note.is_return = 1
cr_note.return_against = "ACC-SINV-2026-00047"  # original invoice
cr_note.append("items", {
    "item_code": "MEMORA-VOUCHER-CARD",
    "qty": -10,   # Negative quantity
    "rate": 45.00,  # Same rate as original
})
cr_note.insert(ignore_permissions=True)
cr_note.submit()
# Original invoice status becomes "Credit Note Issued"
```

## Voucher Card Field Addition (FIN-07)

The `Memora Voucher Card` DocType JSON currently does NOT have a `sales_invoice` field. It must be added as a custom field (via `create_custom_fields()`) rather than modifying the DocType JSON directly, since the field links to ERPNext's Sales Invoice which is from another app.

Similarly, `Memora Voucher Allocation` needs a `sales_invoice` field to track which invoice was created for the allocation.

## Commission Resolution Details

**Priority chain (FIN-03):**
1. **Product-level override:** `Memora Voucher Batch Grant` child table rows have `commission_type` and `commission_value` fields. If ANY grant row in the batch has commission set, use it.
2. **Library default:** `Customer` custom fields `voucher_commission_type` and `voucher_commission_value`.
3. **Zero:** No commission applied (full face value invoiced).

**Commission types (FIN-04):**
- `Percentage`: `commission = face_value * commission_value / 100`
- `Fixed Amount`: `commission = commission_value` (per card, regardless of face value)

**Invoice amount:** `rate = face_value - per_card_commission` (the library pays face value minus their commission)

**Important clarification:** The commission is the library's cut. The invoice charges the library for the NET amount (face value minus commission). So if face_value=50 JOD and commission=15%, the library gets 7.50 JOD commission and owes Memora 42.50 JOD per card.

## Cron Job Configuration

**Existing pattern** (from hooks.py):
```python
scheduler_events = {
    "cron": {
        "* * * * *": [...],  # Every minute tasks
        "5 0 * * *": [...],  # Daily at 00:05
        "0 2 1 * *": ["memora_admin.tasks.consignment_billing.generate_monthly_invoices"],
        # New: 1st of month at 02:00 AM
    }
}
```

The `0 2 1 * *` cron expression means: minute 0, hour 2, day 1 of month, every month, every day of week. Runs at 02:00 AM on the 1st, which matches SCHED-02 requirement.

## Open Questions

1. **JoFotara integration for voucher invoices**
   - What we know: JoFotara custom fields exist on Sales Invoice; corex_fotara app is installed
   - What's unclear: Whether voucher invoices should go through JoFotara e-invoicing or be excluded
   - Recommendation: Include by default (legal compliance). If excluded, would need a custom field flag to skip. Let it go through normally unless the admin configures tax exemptions.

2. **Multi-grant batch commission resolution**
   - What we know: A Voucher Batch can have multiple grants. Each grant row has its own commission fields.
   - What's unclear: Should commission differ per-grant or be uniform per-batch?
   - Recommendation: Use batch-level commission (first grant's override, or library default). A single card has one face value; splitting commission by grant adds complexity with no business value. The commission is about the library's discount, not about what the card unlocks.

3. **Income account for voucher revenue**
   - What we know: Default income account is "Sales - MC"
   - What's unclear: Whether voucher revenue should use a separate income account for reporting
   - Recommendation: Use "Sales - MC" default for now. If needed later, create a dedicated "Voucher Revenue - MC" account and update the Item defaults.

4. **Handling returns when cards came from multiple original invoices**
   - What we know: A return allocation can include cards from different original allocations (and thus different invoices)
   - What's unclear: Should one credit note per original invoice be created, or a standalone credit note?
   - Recommendation: Group return cards by their original `sales_invoice`. Create one credit note per original invoice, each with `return_against` set correctly. Cards without a `sales_invoice` (shouldn't happen for prepaid but defensive) are skipped.

## Sources

### Primary (HIGH confidence)
- **Live site inspection** -- `bench list-apps` confirms ERPNext 15.93.0 installed, company "Montaser Company" with JOD currency, 46 existing Sales Invoices
- **ERPNext source code** -- `/home/corex/aurevia-bench/apps/erpnext/erpnext/accounts/doctype/sales_invoice/test_sales_invoice.py` lines 4769-4847 (create_sales_invoice pattern), lines 1557-1631 (Credit Note with is_return/return_against)
- **Existing codebase** -- `memora_voucher_allocation.py` (allocation completion flow), `customer_fields.py` (commission custom fields), `hooks.py` (scheduler_events pattern), `setup.py` (custom field creation pattern)
- **Prior research** -- `.planning/research/PITFALLS_voucher.md` Pitfall #5 (float precision) and Pitfall #14 (Credit Note edge cases)
- **Prior decisions** -- STATE.md: [33-01] Data fieldtype for commission_value, [33-03] commission as Decimal

### Secondary (MEDIUM confidence)
- **ERPNext Sales Invoice JSON schema** -- verified `is_return`, `return_against`, `return_against` fields exist
- **Custom Field query** -- verified Customer has `voucher_commission_type` and `voucher_commission_value` fields
- **JoFotara integration** -- custom fields `custom_jofotara_*` on Sales Invoice confirmed

### Tertiary (LOW confidence)
- **JoFotara behavior on programmatic submission** -- not verified whether programmatic `.submit()` triggers JoFotara sync automatically or requires manual queue. Needs testing during implementation.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- ERPNext verified installed with matching company/currency/accounts
- Architecture: HIGH -- patterns follow existing codebase conventions, ERPNext test suite verified
- Commission logic: HIGH -- Data fieldtype and Decimal approach pre-decided in 33-01; PITFALLS research covers exact code patterns
- Credit Note mechanics: HIGH -- ERPNext test suite demonstrates exact `is_return`/`return_against` pattern
- Consignment billing: MEDIUM -- cron pattern well-understood, but edge cases (partial months, timezone, duplicate prevention) need careful implementation
- JoFotara interaction: LOW -- verified custom fields exist but unclear if programmatic submission triggers auto-sync

**Research date:** 2026-02-14
**Valid until:** 2026-03-14 (stable domain -- ERPNext 15.x API unlikely to change)
