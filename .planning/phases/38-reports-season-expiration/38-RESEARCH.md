# Phase 38: Reports & Season Expiration - Research

**Researched:** 2026-02-14
**Domain:** Frappe Script Reports + Scheduled Job (MariaDB SQL aggregation, card lifecycle management)
**Confidence:** HIGH

## Summary

Phase 38 closes out the v3.0 Voucher Management System with four admin-facing Script Reports and one daily scheduled job for season-based card expiration. All data already exists in MariaDB (Voucher Card, Voucher Batch, Voucher Allocation, Voucher Redemption Log, Sales Invoice) -- this phase is purely about querying and presenting that data, plus a simple bulk UPDATE for card expiration.

The reports are Frappe Script Reports (Python + JS), not Query Reports or Report Builder reports, because they require SQL JOINs across multiple DocTypes, computed columns (commission, net revenue, redemption rate, days until season end), and report_summary indicators. All four reports follow the same file structure pattern established by ERPNext's built-in Script Reports. The season expiration job follows the existing `tasks/*.py` pattern used by 10+ existing scheduled tasks in the codebase.

**Primary recommendation:** Create four Script Reports under `memora_admin/memora_admin/report/` following the ERPNext pattern (`.py` + `.js` + `.json` + `__init__.py` per report). Use direct `frappe.db.sql()` for all report queries since they require multi-table JOINs. Add the season expiration scheduled task to `hooks.py` under an early-morning cron slot (e.g., `"5 1 * * *"`). The batch-to-season link requires a JOIN chain: `Voucher Card -> Batch -> Batch Grant -> Product Grant -> Academic Plan -> Season`.

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Frappe Script Report | v15 | Admin-facing report framework | Built-in Frappe Desk feature with filters, columns, charts, report_summary |
| `frappe.db.sql()` | v15 | Direct SQL queries | Required for multi-table JOINs that `frappe.get_all()` cannot express |
| `decimal.Decimal` | stdlib | Commission/revenue calculation in reports | Already used in `services/voucher/commission.py` -- maintain consistency |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `frappe.utils` | v15 | Date helpers (today, add_days, getdate) | Season end date calculations |
| `resolve_commission()` | existing | Commission resolution for report calculations | Sales by Library and Consignment Reconciliation reports |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Script Report | Query Report | Query Reports are simpler but cannot do computed columns, report_summary, or complex JOINs |
| Script Report | Report Builder | Report Builder cannot join across DocTypes or compute derived values |
| Direct SQL | frappe.get_all() | `get_all()` does not support JOINs across multiple DocTypes |

## Architecture Patterns

### Recommended Project Structure
```
memora_admin/memora_admin/report/
├── __init__.py
├── sales_by_library/
│   ├── __init__.py
│   ├── sales_by_library.py
│   ├── sales_by_library.js
│   └── sales_by_library.json
├── batch_performance/
│   ├── __init__.py
│   ├── batch_performance.py
│   ├── batch_performance.js
│   └── batch_performance.json
├── consignment_reconciliation/
│   ├── __init__.py
│   ├── consignment_reconciliation.py
│   ├── consignment_reconciliation.js
│   └── consignment_reconciliation.json
└── security_audit/
    ├── __init__.py
    ├── security_audit.py
    ├── security_audit.js
    └── security_audit.json
```

Scheduled task file:
```
memora_admin/tasks/
└── season_expiration.py    # SCHED-01: expire_season_cards()
```

### Pattern 1: Frappe Script Report File Structure

**What:** Each Script Report has 4 files: JSON (metadata), Python (execute logic), JavaScript (filters), `__init__.py`.

**JSON file format:**
```json
{
  "add_total_row": 1,
  "columns": [],
  "creation": "2026-02-14 00:00:00.000000",
  "disable_prepared_report": 0,
  "disabled": 0,
  "docstatus": 0,
  "doctype": "Report",
  "filters": [],
  "idx": 0,
  "is_standard": "Yes",
  "letter_head": "",
  "modified": "2026-02-14 00:00:00.000000",
  "modified_by": "Administrator",
  "module": "Memora Admin",
  "name": "Sales by Library",
  "owner": "Administrator",
  "prepared_report": 0,
  "ref_doctype": "Memora Voucher Card",
  "report_name": "Sales by Library",
  "report_type": "Script Report",
  "roles": [
    {"role": "System Manager"}
  ]
}
```

Key fields:
- `"module": "Memora Admin"` -- must match `modules.txt`
- `"is_standard": "Yes"` -- marks it as part of the app (survives bench updates)
- `"ref_doctype"` -- the primary DocType the report is "about" (determines permissions)
- `"report_type": "Script Report"` -- not "Query Report" or "Report Builder"
- `"roles"` -- who can see the report (System Manager for admin reports)
- `"add_total_row": 1` -- auto-adds a total row at bottom for numeric columns

**Python execute() signature:**
```python
def execute(filters=None):
    columns = get_columns(filters)
    data = get_data(filters)
    report_summary = get_report_summary(data)
    chart = get_chart_data(data)
    return columns, data, None, chart, report_summary
```

Return order: `columns, data, message, chart, report_summary, skip_total_rows`

**JavaScript filters:**
```javascript
frappe.query_reports["Sales by Library"] = {
    filters: [
        {
            fieldname: "from_date",
            label: __("From Date"),
            fieldtype: "Date",
            default: frappe.datetime.add_months(frappe.datetime.get_today(), -1),
            reqd: 1,
        },
        {
            fieldname: "to_date",
            label: __("To Date"),
            fieldtype: "Date",
            default: frappe.datetime.get_today(),
            reqd: 1,
        },
        {
            fieldname: "library",
            label: __("Library"),
            fieldtype: "Link",
            options: "Customer",
        },
    ],
};
```

### Pattern 2: Existing Scheduled Task Pattern

**What:** Tasks are plain Python modules in `memora_admin/tasks/` with a single function, registered in `hooks.py` under `scheduler_events["cron"]`.

**Example from codebase** (Source: `memora_admin/tasks/voucher_cleanup.py`):
```python
def cleanup_expired_exports():
    """Docstring describing what the task does."""
    # Query items to process
    items = frappe.get_all(...)

    count = 0
    for item in items:
        try:
            # Process each item independently
            ...
            count += 1
        except Exception:
            frappe.log_error(title=f"Task failed for {item.name}")

    if count:
        frappe.db.commit()
        frappe.logger().info(f"Task complete: {count} items processed")
```

**hooks.py registration:**
```python
scheduler_events = {
    "cron": {
        "5 1 * * *": ["memora_admin.tasks.season_expiration.expire_season_cards"],
    }
}
```

### Pattern 3: Column Definition Standard

**What:** Report columns use Frappe's standard fieldtype system for proper formatting.

```python
columns = [
    {
        "fieldname": "library",
        "label": _("Library"),
        "fieldtype": "Link",
        "options": "Customer",
        "width": 200,
    },
    {
        "fieldname": "redeemed_count",
        "label": _("Redeemed Cards"),
        "fieldtype": "Int",
        "width": 120,
    },
    {
        "fieldname": "face_value",
        "label": _("Face Value"),
        "fieldtype": "Currency",
        "width": 120,
    },
]
```

Key fieldtypes: `Link` (clickable DocType link), `Currency` (formatted with currency symbol), `Int` (integer), `Float` (decimal), `Percent` (percentage with %), `Data` (plain text), `Date`/`Datetime`.

### Anti-Patterns to Avoid
- **Using frappe.get_all() for report data:** It cannot express JOINs across DocTypes. Use `frappe.db.sql()` with parameterized queries instead.
- **Computing commission in Python loops:** Use the existing `resolve_commission()` and `calculate_commission()` functions. Do NOT re-implement commission logic.
- **Hardcoding date formats:** Use `frappe.utils.today()`, `frappe.utils.getdate()`, `frappe.utils.add_days()` instead of raw string manipulation.
- **Committing inside report execute():** Reports are read-only queries. Never call `frappe.db.commit()` inside a report.
- **Forgetting `__init__.py` files:** Every report directory and the parent `report/` directory needs an `__init__.py` or Frappe will not discover the report.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Commission calculation | Custom SQL arithmetic | `services/voucher/commission.py` `resolve_commission()` + `calculate_commission()` | Already handles Decimal precision, priority chain, edge cases |
| Date arithmetic | `datetime.timedelta` or string math | `frappe.utils` (today, add_days, getdate, date_diff) | Handles Frappe's naive datetime convention |
| Report framework | Custom API endpoint returning JSON | Frappe Script Report | Built-in Desk integration: export to CSV/PDF, print, email, share, pagination |
| Status enum handling | Hardcoded strings scattered in code | Reference `MemoraVoucherCard.VALID_TRANSITIONS` / DocType JSON options | Single source of truth for status values |

**Key insight:** These reports are SQL queries with Frappe presentation. The data model already has all the fields needed (status, library, batch, face_value, sale_model, redeemed_at, sales_invoice, ip_address, failure_reason). No new DocTypes or schema changes are required for reports.

## Common Pitfalls

### Pitfall 1: Batch-to-Season Join Chain is Indirect
**What goes wrong:** The Voucher Batch has no direct `season` field. Finding which season a batch belongs to requires joining through: `Batch Grant -> Product Grant -> Academic Plan -> Season`. A batch can have multiple grants pointing to different plans (potentially different seasons).
**Why it happens:** The batch is linked to Product Grants, not directly to a season, because the grant defines the content package.
**How to avoid:** For the expiration job, join through the chain and use `GROUP BY` to get distinct seasons per batch. For "days until season end" in the Batch Performance report, use the earliest ending season across all grants in the batch (worst case).
**Warning signs:** Cards with batches that have grants pointing to multiple different seasons -- handle by taking the earliest (most conservative) season end date.

### Pitfall 2: sales_invoice is a Custom Field, Not in DocType JSON
**What goes wrong:** Querying `sales_invoice` on `tabMemora Voucher Card` or `tabMemora Voucher Allocation` fails because it is not in the DocType JSON -- it was added as a Custom Field via `create_custom_fields()` in Phase 37.
**Why it happens:** The field links to ERPNext's `Sales Invoice` DocType which is from another app, so it was added as a custom field.
**How to avoid:** The custom field still creates a real column in the MariaDB table. Direct SQL queries with `frappe.db.sql()` work fine. Just verify the field exists: `SELECT sales_invoice FROM tabMemora Voucher Card LIMIT 1`.
**Warning signs:** No `sales_invoice` entry in `memora_voucher_card.json` -- it is in `Custom Field` instead.

### Pitfall 3: Consignment Cards Without Invoices Are Expected
**What goes wrong:** Report logic might flag consignment cards without a `sales_invoice` as errors, when in fact consignment cards are only invoiced monthly (1st of each month).
**Why it happens:** The consignment billing job runs monthly. Cards redeemed mid-month will not yet have an invoice.
**How to avoid:** The Consignment Reconciliation report should explicitly show "uninvoiced" cards as a normal category, not an error. Filter: `sale_model='Consignment' AND status='Redeemed' AND (sales_invoice IS NULL OR sales_invoice = '')`.
**Warning signs:** Consignment reconciliation report showing 0 uninvoiced cards when there are recent redemptions.

### Pitfall 4: Season Expiration Must Not Touch Terminal Cards
**What goes wrong:** The expiration job updates ALL cards in a batch linked to an ended season, including already-Redeemed or already-Void cards.
**Why it happens:** Missing WHERE clause on status.
**How to avoid:** The WHERE clause MUST include `status IN ('Available', 'Allocated')`. Redeemed and Void are terminal states that should never change. The `VALID_TRANSITIONS` dict in `memora_voucher_card.py` confirms: Redeemed and Void have empty allowed transition sets.
**Warning signs:** Redeemed cards showing as "Expired" after the job runs.

### Pitfall 5: Report Expects Non-Null Filters
**What goes wrong:** Report crashes when optional filters are None/empty.
**Why it happens:** Python code does `filters.from_date` which returns None if not set.
**How to avoid:** Always use `filters.get("fieldname")` and build WHERE clauses conditionally. Required filters should have `reqd: 1` in the JS file.

## Code Examples

### RPT-01: Sales by Library Report SQL
```python
# Source: Codebase analysis of tabMemora Voucher Card + tabMemora Voucher Batch schemas
def get_data(filters):
    conditions = "WHERE c.status = 'Redeemed'"
    values = []

    if filters.get("from_date"):
        conditions += " AND c.redeemed_at >= %s"
        values.append(filters.get("from_date"))
    if filters.get("to_date"):
        conditions += " AND c.redeemed_at <= %s"
        values.append(filters.get("to_date") + " 23:59:59")
    if filters.get("library"):
        conditions += " AND c.library = %s"
        values.append(filters.get("library"))

    return frappe.db.sql(f"""
        SELECT
            c.library,
            COUNT(*) as redeemed_count,
            b.face_value,
            SUM(b.face_value) as total_face_value,
            c.sale_model,
            CASE
                WHEN c.sales_invoice IS NOT NULL AND c.sales_invoice != '' THEN 'Invoiced'
                ELSE 'Not Invoiced'
            END as invoice_status
        FROM `tabMemora Voucher Card` c
        JOIN `tabMemora Voucher Batch` b ON c.batch = b.name
        {conditions}
        GROUP BY c.library, b.face_value, c.sale_model, invoice_status
        ORDER BY c.library, b.face_value
    """, values, as_dict=True)
```

### RPT-02: Batch Performance Aggregation SQL
```python
# Source: Codebase analysis -- batch has generated_count, allocated_count, redeemed_count, voided_count
def get_data(filters):
    # Card status distribution per batch
    return frappe.db.sql("""
        SELECT
            b.name as batch,
            b.batch_name,
            b.quantity as total_cards,
            b.face_value,
            SUM(CASE WHEN c.status = 'Available' THEN 1 ELSE 0 END) as available,
            SUM(CASE WHEN c.status = 'Allocated' THEN 1 ELSE 0 END) as allocated,
            SUM(CASE WHEN c.status = 'Redeemed' THEN 1 ELSE 0 END) as redeemed,
            SUM(CASE WHEN c.status = 'Void' THEN 1 ELSE 0 END) as voided,
            SUM(CASE WHEN c.status = 'Expired' THEN 1 ELSE 0 END) as expired,
            ROUND(SUM(CASE WHEN c.status = 'Redeemed' THEN 1 ELSE 0 END) * 100.0
                / NULLIF(b.quantity, 0), 1) as redemption_rate,
            s.end_date as season_end,
            DATEDIFF(s.end_date, CURDATE()) as days_until_season_end
        FROM `tabMemora Voucher Batch` b
        LEFT JOIN `tabMemora Voucher Card` c ON c.batch = b.name
        LEFT JOIN `tabMemora Voucher Batch Grant` bg ON bg.parent = b.name
        LEFT JOIN `tabMemora Product Grant` pg ON bg.product_grant = pg.name
        LEFT JOIN `tabMemora Academic Plan` ap ON pg.plan = ap.name
        LEFT JOIN `tabMemora Season` s ON ap.season = s.name
        WHERE b.status != 'Draft'
        GROUP BY b.name, b.batch_name, b.quantity, b.face_value, s.end_date
        ORDER BY b.creation DESC
    """, as_dict=True)
```

Note: The LEFT JOIN chain to Season may produce NULL for `season_end` and `days_until_season_end` if a batch grant's product grant has no plan or season. The report should display "N/A" for these.

Also note: A batch with multiple grants pointing to different plans/seasons will produce multiple rows. The GROUP BY should pick the earliest (MIN) season end date using a subquery:

```python
# Corrected: subquery for earliest season end per batch
LEFT JOIN (
    SELECT bg2.parent as batch_name, MIN(s2.end_date) as end_date
    FROM `tabMemora Voucher Batch Grant` bg2
    JOIN `tabMemora Product Grant` pg2 ON bg2.product_grant = pg2.name
    JOIN `tabMemora Academic Plan` ap2 ON pg2.plan = ap2.name
    JOIN `tabMemora Season` s2 ON ap2.season = s2.name
    GROUP BY bg2.parent
) season_info ON season_info.batch_name = b.name
```

### RPT-04: Security Audit SQL
```python
# Source: memora_voucher_redemption_log.json -- has player, ip_address, status, failure_reason, timestamp
def get_data(filters):
    conditions = "WHERE rl.status != 'Success'"
    values = []

    if filters.get("from_date"):
        conditions += " AND rl.timestamp >= %s"
        values.append(filters.get("from_date"))
    if filters.get("to_date"):
        conditions += " AND rl.timestamp <= %s"
        values.append(filters.get("to_date") + " 23:59:59")

    return frappe.db.sql(f"""
        SELECT
            rl.player,
            rl.ip_address,
            rl.status as failure_type,
            COUNT(*) as attempt_count,
            MIN(rl.timestamp) as first_attempt,
            MAX(rl.timestamp) as last_attempt
        FROM `tabMemora Voucher Redemption Log` rl
        {conditions}
        GROUP BY rl.player, rl.ip_address, rl.status
        ORDER BY attempt_count DESC
    """, values, as_dict=True)
```

### SCHED-01: Season Expiration Job
```python
# Source: Codebase analysis -- batch_grant -> product_grant -> plan -> season chain
def expire_season_cards():
    """Expire Available/Allocated cards linked to ended or unpublished seasons.

    A card's season is determined by: Card -> Batch -> Batch Grant -> Product Grant -> Plan -> Season.
    Cards are expired if ANY of their batch's grants link to a season that has ended (end_date < today)
    or is unpublished (is_published = 0).
    """
    today = frappe.utils.today()

    # Find batches with at least one grant linked to an ended/unpublished season
    expired_batches = frappe.db.sql("""
        SELECT DISTINCT b.name as batch_name
        FROM `tabMemora Voucher Batch` b
        JOIN `tabMemora Voucher Batch Grant` bg ON bg.parent = b.name
        JOIN `tabMemora Product Grant` pg ON bg.product_grant = pg.name
        JOIN `tabMemora Academic Plan` ap ON pg.plan = ap.name
        JOIN `tabMemora Season` s ON ap.season = s.name
        WHERE b.status IN ('Generated', 'Active')
          AND (s.end_date < %s OR s.is_published = 0)
    """, (today,), as_dict=True)

    if not expired_batches:
        frappe.logger().info("Season expiration: No batches with ended/unpublished seasons")
        return

    batch_names = [b.batch_name for b in expired_batches]
    total_expired = 0

    for batch_name in batch_names:
        try:
            result = frappe.db.sql("""
                UPDATE `tabMemora Voucher Card`
                SET status = 'Expired', void_reason = 'Season Ended',
                    modified = NOW(), modified_by = 'Administrator'
                WHERE batch = %s AND status IN ('Available', 'Allocated')
            """, (batch_name,))

            affected = frappe.db.sql("SELECT ROW_COUNT()")[0][0]
            if affected:
                total_expired += affected
                frappe.logger().info(f"Season expiration: {affected} cards expired in batch {batch_name}")
        except Exception:
            frappe.log_error(title=f"Season expiration failed for batch {batch_name}")

    if total_expired:
        frappe.db.commit()

    frappe.logger().info(f"Season expiration complete: {total_expired} card(s) expired across {len(batch_names)} batch(es)")
```

### Report Summary Example
```python
# Source: Frappe v15 Script Report docs
def get_report_summary(data):
    total_redeemed = sum(d.get("redeemed_count", 0) for d in data)
    total_revenue = sum(d.get("net_revenue", 0) for d in data)
    return [
        {
            "value": total_redeemed,
            "indicator": "Green" if total_redeemed > 0 else "Grey",
            "label": _("Total Redeemed"),
            "datatype": "Int",
        },
        {
            "value": total_revenue,
            "indicator": "Blue",
            "label": _("Total Net Revenue"),
            "datatype": "Currency",
            "currency": "JOD",
        },
    ]
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Query Report (SQL-only) | Script Report (Python + JS) | Frappe v13+ | Script Reports support computed columns, report_summary, charts |
| Report Builder | Script Report for complex queries | Always | Report Builder cannot JOIN across DocTypes |
| Manual commission calculation in SQL | `services/voucher/commission.py` | Phase 37 (2026-02-14) | Centralized Decimal-precision commission with priority chain |

**Deprecated/outdated:**
- `frappe.query_report.set_filter_value()` syntax has been stable since v13 -- no breaking changes in v15

## Data Model Summary for Reports

### Key Tables and Relationships

```
tabMemora Voucher Card (c)
├── batch -> tabMemora Voucher Batch (b)
├── library -> tabCustomer (cust)
├── allocation -> tabMemora Voucher Allocation (a)
├── sales_invoice -> tabSales Invoice (si)  [Custom Field]
├── status: Available | Allocated | Redeemed | Void | Expired
├── sale_model: Prepaid | Consignment | (empty)
├── redeemed_by -> tabMemora Player Profile
├── redeemed_at: Datetime
└── void_reason: Small Text

tabMemora Voucher Batch (b)
├── batch_grants -> tabMemora Voucher Batch Grant (bg)
│   ├── product_grant -> tabMemora Product Grant (pg)
│   │   └── plan -> tabMemora Academic Plan (ap)
│   │       └── season -> tabMemora Season (s)
│   ├── commission_type
│   └── commission_value
├── face_value: Currency
├── quantity: Int
├── status: Draft | Generated | Active | Closed
├── generated_count, allocated_count, redeemed_count, voided_count
└── batch_name: Data

tabMemora Voucher Allocation (a)
├── allocation_type: Allocate | Return
├── customer -> tabCustomer (library)
├── sale_model: Prepaid | Consignment
├── sales_invoice -> tabSales Invoice  [Custom Field]
└── status: Draft | Pending Approval | Approved | Rejected | Completed | Cancelled

tabMemora Voucher Redemption Log (rl)
├── player -> tabMemora Player Profile
├── ip_address: Data
├── status: Success | Invalid PIN | Already Redeemed | Expired | Void | ...
├── failure_reason: Data
├── timestamp: Datetime
├── card -> tabMemora Voucher Card
├── batch -> tabMemora Voucher Batch
└── library -> tabCustomer

tabMemora Season (s)
├── end_date: Date
├── is_published: Check
└── season_title: Data
```

### Commission Resolution for Reports

For RPT-01 (Sales by Library) and RPT-03 (Consignment Reconciliation), commission must be calculated per card/batch/library combination. The existing `resolve_commission(batch_name, library)` function uses the priority chain:

1. Batch grant-level: `Memora Voucher Batch Grant.commission_type/value` (first grant with commission set)
2. Library (Customer) default: `Customer.voucher_commission_type/value` (custom fields)
3. Zero: No commission

For report efficiency, commission calculation should be done in Python after the SQL query returns rows (not inside SQL). Load all commission data upfront, then calculate per row.

## Open Questions

1. **Multiple grants per batch pointing to different seasons**
   - What we know: A batch can have multiple `batch_grants`, each linking to a different `Product Grant -> Plan -> Season`
   - What's unclear: Should the expiration job expire cards if ANY grant's season has ended, or only if ALL have ended?
   - Recommendation: Expire if ANY grant's season has ended (conservative approach -- the card cannot fully deliver its value if any season is over). This matches the requirement wording "cards linked to ended seasons."

2. **Batch counters after expiration**
   - What we know: Batch has `voided_count` but no `expired_count` field
   - What's unclear: Should the expiration job update any batch-level counter?
   - Recommendation: The Batch Performance report computes counts from live card data (not cached counters), so no batch field update is strictly needed. However, adding an `expired_count` field to the batch for quick visibility would be a nice addition. For now, skip it -- the report handles it.

3. **Invoice status granularity in Sales by Library**
   - What we know: `sales_invoice` is either NULL/empty or set to a Sales Invoice name
   - What's unclear: Should the report show the Sales Invoice's payment status (Paid/Unpaid/Overdue)?
   - Recommendation: Start with simple Invoiced/Not Invoiced. Querying Sales Invoice payment status adds complexity (joins to GL entries). Can be enhanced later.

## Sources

### Primary (HIGH confidence)
- Codebase analysis: `memora_voucher_card.json` -- Card DocType schema with all status values and fields
- Codebase analysis: `memora_voucher_batch.json` -- Batch DocType schema with counter fields
- Codebase analysis: `memora_voucher_allocation.json` -- Allocation schema with sale_model, customer
- Codebase analysis: `memora_voucher_redemption_log.json` -- Redemption Log schema with all audit fields
- Codebase analysis: `memora_voucher_batch_grant.json` -- Batch Grant child table with commission fields
- Codebase analysis: `memora_product_grant.json` -- Product Grant with plan link
- Codebase analysis: `memora_academic_plan.json` -- Academic Plan with season link
- Codebase analysis: `memora_season.json` -- Season with end_date, is_published
- Codebase analysis: `services/voucher/commission.py` -- Commission resolution and calculation
- Codebase analysis: `services/voucher/invoice.py` -- Invoice creation and sales_invoice field usage
- Codebase analysis: `tasks/consignment_billing.py` -- Existing scheduled task pattern
- Codebase analysis: `tasks/voucher_cleanup.py` -- Existing scheduled task pattern
- Codebase analysis: `custom/invoice_fields.py` -- Custom Field definition for sales_invoice
- Codebase analysis: `hooks.py` -- Existing scheduler_events cron registration
- ERPNext source: `erpnext/manufacturing/report/job_card_summary/` -- Script Report reference implementation

### Secondary (MEDIUM confidence)
- [Frappe v15 Script Report Documentation](https://docs.frappe.io/framework/v15/user/en/desk/reports/script-report) -- File structure, execute() return values, report_summary format
- [Frappe Forum - Script Reports Tutorial](https://discuss.frappe.io/t/tutorial-script-reports-in-erpnext-a-step-by-step-guide/110969) -- Community guide with examples

### Tertiary (LOW confidence)
- None -- all findings verified against codebase and official docs

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- Frappe Script Report is the established pattern, directly verified against ERPNext examples in the same bench
- Architecture: HIGH -- File structure pattern copied from working ERPNext reports in the local codebase
- SQL queries: HIGH -- All table schemas verified, all JOIN paths confirmed by reading DocType JSONs
- Season expiration: HIGH -- Batch-to-season chain verified through DocType schema analysis (5-table JOIN)
- Pitfalls: HIGH -- Based on direct codebase analysis of custom fields, data model relationships

**Research date:** 2026-02-14
**Valid until:** 2026-03-14 (stable -- Frappe v15 report framework is mature)
