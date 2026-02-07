# Phase 21: Product Catalog API - Context

**Gathered:** 2026-02-07
**Status:** Ready for planning

<domain>
## Phase Boundary

Players can discover available products for their plan and submit purchase actions. This phase delivers a **browsable product catalog endpoint** that returns structured data about purchasable content bundles with fast cached responses (sub-100ms on cache hit). Purchase submission and approval flows are separate phases (22-23).

</domain>

<decisions>
## Implementation Decisions

### Response Structure and Product Metadata

**Structure:**
- Nested structure: each product has a 'bundle' object containing name/metadata, with 'subjects' as nested collection
- Include product_grant_id (DocType name like 'PGRANT-00123') for tracking/debugging

**Subject Metadata:**
- Subject fields: subject_id, alias_title, and descriptions/notes
- Provides rich product preview without content counts

**Pricing:**
- Raw price_list_rate number only (e.g., 99.99)
- Client handles currency formatting and localization

### Filtering and Exclusion Logic

**Bundle Exclusion:**
- Show all bundles UNLESS player has purchased that exact bundle (100% match on bundle identity)
- Different bundles with overlapping subjects are shown
- Same bundle purchased = hide from catalog

**Pending Transactions:**
- Hide from catalog entirely if product has pending purchase transaction
- Prevents duplicate purchase requests by removing actionable products

**Eligibility Filtering:**
- No additional filters beyond plan membership
- If Product Grant exists for player's plan, show it
- Purchase flow handles any additional restrictions

**Edge Cases:**
- Filter at query time: check Product Grant.enabled and Product Bundle.disabled in Frappe query
- Never return inactive grants or disabled bundles

### Cache Strategy and Invalidation

**Cache Granularity:**
- Per-plan cache: `memora:catalog:{plan_id}`
- All players on same plan share catalog (most efficient)

**Invalidation Triggers:**
- Product Grant created/updated/deleted for the plan
- Product Bundle modified (name, price, items)
- Subject metadata changes (alias_title or notes)

**TTL Strategy:**
- No TTL (infinite cache lifetime)
- Cache lives until explicitly invalidated by events
- Maximum performance, relies on comprehensive event hooks

**Pending Transaction Handling:**
- Claude's discretion: choose approach that balances cache efficiency with accurate pending status
- Likely post-cache filter or separate pending endpoint

### Empty States and Error Responses

**Empty Catalog:**
- Return `{products: []}` with 200 OK status
- Client handles empty state UI

**No Plan Assignment:**
- Return empty catalog (200 OK)
- Treat no-plan as empty catalog, not an error

**Cache Failure:**
- Return 503 Service Unavailable
- Do not fallback to Frappe or serve stale data
- Treat cache as critical dependency

**Error Message Format:**
- Minimal error messages only (user-facing)
- No internal details, error codes, or request IDs exposed
- Keep responses clean and simple

### Claude's Discretion

- Exact Pydantic model structure and field naming conventions
- Whether to use post-cache filtering or separate endpoint for pending transactions
- Redis key naming beyond the plan_id pattern
- HTTP response model structure and JSON formatting
- Logging and monitoring approach

</decisions>

<specifics>
## Specific Ideas

- Success criteria calls for "sub-100ms cached responses" — performance is critical
- Phase 22 will feed back into this catalog (pending status display logic)
- Existing infrastructure: Redis access sync patterns from Phase 3, plan caching from Phase 12/17
- Requirements reference: CTLG-01 through CTLG-06

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 21-plan-product-discovery-api*
*Context gathered: 2026-02-07*
