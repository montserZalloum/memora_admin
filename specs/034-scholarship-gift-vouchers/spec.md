# Feature Specification: Scholarship & Gift Voucher System

**Feature Branch**: `034-scholarship-gift-vouchers`
**Created**: 2026-03-02
**Status**: Draft
**Input**: User description: "Enable admins to create free voucher batches for scholarships, gifts, and promotions — with a dedicated Direct Activate flow that bypasses library allocation, clear separation from paid sales in reports, and a dedicated Script Report for tracking all non-sale grants."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Create and Distribute Scholarship Vouchers (Priority: P1)

An admin creates a voucher batch specifically for scholarships. The batch is marked as "Scholarship" purpose, which enforces a face value of zero. After generating the cards, the admin uses "Direct Activate" to make all cards immediately available for distribution — bypassing the normal library allocation workflow. The admin then exports the PINs and distributes them to scholarship recipients, who redeem them like any other voucher to gain access.

**Why this priority**: Core feature — without this, there is no way to issue free vouchers for scholarships. This is the primary use case driving the entire feature.

**Independent Test**: Can be fully tested by creating a Scholarship batch, generating cards, activating them directly, exporting PINs, and redeeming one as a student. Delivers the core scholarship distribution capability.

**Acceptance Scenarios**:

1. **Given** an admin is creating a new voucher batch, **When** they select batch purpose "Scholarship", **Then** face value is automatically set to 0 and becomes read-only.
2. **Given** a Scholarship batch with status "Generated", **When** the admin clicks "Direct Activate", **Then** all cards transition to "Allocated" status with library set to "Admin-Direct".
3. **Given** a directly activated Scholarship card, **When** a student redeems the PIN, **Then** a subscription transaction is created (payment method "Voucher") and the student gains access to the granted content.
4. **Given** a Scholarship batch, **When** the admin attempts to set face value above 0, **Then** the system rejects the change with a validation error.

---

### User Story 2 - Prevent Cross-Purpose Misuse (Priority: P1)

The system enforces strict separation between sale batches and non-sale batches (Scholarship/Gift/Promotion). Non-sale batches cannot be allocated to libraries, and sale batches cannot be directly activated. This prevents accidental mixing of paid and free distribution channels.

**Why this priority**: Without these guards, admins could accidentally allocate free cards through the paid sales channel or vice versa, causing accounting and audit issues.

**Independent Test**: Can be tested by attempting to allocate a Scholarship batch to a library (should fail) and attempting to Direct Activate a Sale batch (button should not appear; server should reject).

**Acceptance Scenarios**:

1. **Given** a voucher batch with purpose "Scholarship", **When** the admin attempts to allocate it to a library, **Then** the system rejects the operation with the message "Cannot allocate cards from a non-sale batch. Use Direct Activate instead."
2. **Given** a voucher batch with purpose "Sale", **When** the admin views the batch form, **Then** the "Direct Activate" button is not visible.
3. **Given** a voucher batch with purpose "Sale", **When** a direct activate request is made via the server, **Then** the system rejects it with a validation error.
4. **Given** a batch with purpose "Scholarship" that has been generated, **When** the admin attempts to change the batch purpose, **Then** the field is read-only and cannot be modified.

---

### User Story 3 - Grant Access Without Voucher (Priority: P2)

An admin manually creates a subscription transaction for a specific student with payment method "Scholarship" or "Gift" — granting access without issuing a voucher PIN. This is useful for individual grants where creating a whole batch would be excessive.

**Why this priority**: Provides flexibility for one-off grants. Less critical than batch-based distribution but important for operational convenience.

**Independent Test**: Can be tested by creating a manual subscription transaction with "Scholarship" payment method, verifying the student gains access, and confirming no invoice is generated.

**Acceptance Scenarios**:

1. **Given** an admin creating a subscription transaction, **When** they select payment method "Scholarship" and set amount to 0, **Then** the transaction completes and the student gains access.
2. **Given** an admin creating a subscription transaction, **When** they select payment method "Gift" and set amount to 0, **Then** the transaction completes and the student gains access.
3. **Given** a completed Scholarship/Gift transaction, **When** viewing reports, **Then** no sales invoice is associated with the transaction.

---

### User Story 4 - Track Non-Sale Grants via Report (Priority: P2)

Admins can view a dedicated report showing all non-sale voucher batches (Scholarship, Gift, Promotion) with card distribution statistics: total cards, activated, redeemed, voided, and remaining. The report supports filtering by batch purpose, date range, and product grant.

**Why this priority**: Admins need visibility into scholarship/gift distribution from day one. Without reporting, the feature lacks accountability and audit capability.

**Independent Test**: Can be tested by creating batches with different non-sale purposes, activating and redeeming some cards, then verifying the report shows correct counts and responds to filters.

**Acceptance Scenarios**:

1. **Given** multiple non-sale batches exist, **When** an admin opens the Scholarship & Gift Grants Report, **Then** all non-sale batches are listed with correct card counts.
2. **Given** the report is open, **When** the admin filters by "Scholarship" purpose, **Then** only Scholarship batches are shown.
3. **Given** the report is open, **When** the admin filters by date range, **Then** only batches created within that range are shown.
4. **Given** a batch with some redeemed and voided cards, **When** viewing the report, **Then** the remaining count accurately reflects (total - redeemed - voided - expired).

---

### User Story 5 - Add Recipient Notes to Non-Sale Cards (Priority: P3)

For non-sale batches, each card can have an optional "recipient note" field where admins can record who the card is intended for or why it was issued. This field is hidden on regular sale cards to reduce noise.

**Why this priority**: Nice-to-have for tracking and auditing individual card distribution. Not essential for core functionality.

**Independent Test**: Can be tested by creating a Scholarship batch, adding recipient notes to cards, and verifying notes appear in the report drill-down and on the card form.

**Acceptance Scenarios**:

1. **Given** a voucher card from a Scholarship batch, **When** the admin views or edits the card, **Then** the "recipient note" field is visible and editable.
2. **Given** a voucher card from a Sale batch, **When** the admin views the card, **Then** the "recipient note" field is hidden.
3. **Given** a redeemed Scholarship card with a recipient note, **When** viewing the report's redeemed students detail, **Then** the recipient note is included in the output.

---

### Edge Cases

- **Direct Activate called twice**: Second call finds zero available cards and returns activated count of 0 (idempotent, no error).
- **Batch has mixed card statuses at activation time**: Only cards with "Available" status are activated. Cards already voided or in other states remain unchanged.
- **Void a directly activated card**: Uses existing void mechanism — works because the card is in "Allocated" status.
- **Return/recall on directly activated cards**: Standard library-based return queries filter by library name. "Admin-Direct" cards won't match library-specific returns. Admin should use void instead.
- **Batch purpose changed after cards generated**: Field becomes read-only after batch leaves Draft status, preventing mid-lifecycle changes.
- **Student redeems a directly activated card**: Works identically to any other allocated card. The redemption log shows library as "Admin-Direct" for audit trail.
- **Report drill-down on redeemed students**: Shows card ID, student name, grant, redemption time, and recipient note for each redeemed card in a batch.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST support a "batch purpose" classification on voucher batches with values: Sale, Scholarship, Gift, and Promotion. Default is Sale.
- **FR-002**: System MUST enforce face value of zero for all non-Sale batches (Scholarship, Gift, Promotion). Attempts to set face value above zero on non-Sale batches MUST be rejected.
- **FR-003**: System MUST provide a "Direct Activate" action on non-Sale batches that transitions all available cards to allocated status, bypassing library allocation.
- **FR-004**: System MUST set the library field to the sentinel value "Admin-Direct" on all directly activated cards for audit trail purposes.
- **FR-005**: System MUST block library allocation for non-Sale batches. Attempts to allocate non-Sale batch cards to a library MUST be rejected.
- **FR-006**: System MUST block Direct Activate for Sale batches. The action MUST not be available and MUST be rejected if attempted via server.
- **FR-007**: System MUST make batch purpose read-only after the batch leaves Draft status to prevent mid-lifecycle changes.
- **FR-008**: System MUST support "Scholarship" and "Gift" as additional payment method options on subscription transactions for manual (non-voucher) grants.
- **FR-009**: Redemption of directly activated cards MUST work identically to regular allocated cards — creating subscription transactions with payment method "Voucher" and granting access.
- **FR-010**: System MUST provide a dedicated Script Report for non-sale batches showing: batch name, purpose, product grant, total cards, activated, redeemed, voided, and remaining counts.
- **FR-011**: The report MUST support filtering by batch purpose, date range, and product grant.
- **FR-012**: The report MUST support a drill-down view showing redeemed students per batch with student name, redemption time, and recipient note.
- **FR-013**: System MUST support an optional "recipient note" field on voucher cards, visible only for non-Sale batches.
- **FR-014**: The batch purpose MUST be propagated from the batch to each card (read-only) to enable filtering and conditional field visibility on cards.
- **FR-015**: No sales invoice MUST be created for non-Sale batches (since there is no library allocation, no invoice trigger occurs).
- **FR-016**: All existing sale batch flows (Generate, Allocate to Library, Redeem) MUST remain completely unchanged.

### Key Entities

- **Voucher Batch**: Extended with a "batch purpose" classification (Sale/Scholarship/Gift/Promotion) that governs which distribution flow is available and enforces face value constraints.
- **Voucher Card**: Extended with a read-only "batch purpose" derived from its parent batch (for filtering/visibility) and an optional "recipient note" for non-sale cards.
- **Subscription Transaction**: Extended with "Scholarship" and "Gift" payment method options for manual admin grants without vouchers.
- **Script Report (Scholarship & Gift Grants)**: New report entity showing non-sale batch distribution statistics with drill-down to redeemed students.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Admins can create and distribute a scholarship voucher batch (create, generate, activate, export, redeem) in under 5 minutes end-to-end.
- **SC-002**: 100% of non-Sale batches have face value enforced to zero — no phantom revenue in financial reports.
- **SC-003**: Cross-purpose misuse is completely prevented: zero instances of non-Sale batch cards allocated to libraries or Sale batch cards directly activated.
- **SC-004**: Directly activated cards redeem with the same success rate and speed as library-allocated cards — no degradation in the student redemption experience.
- **SC-005**: The Scholarship & Gift Grants Report accurately reflects real-time card distribution counts (activated, redeemed, voided, remaining) with less than 1% discrepancy.
- **SC-006**: All existing sale batch workflows continue to operate without any behavior changes or regressions.
- **SC-007**: Admin can filter and find any non-sale batch within the report by purpose, date, or product grant in under 10 seconds.

## Assumptions

- A sentinel record named "Admin-Direct" will be created in the system (either manually or auto-created on first use) to serve as the library value for directly activated cards.
- The existing card export ("Export for Print") functionality works for directly activated cards without modification.
- The existing void mechanism handles directly activated cards without modification (since they are in "Allocated" status).
- Non-sale batches do not need commission calculations, sale model assignments, or allocation documents.
- The "Promotion" batch purpose follows the same rules as "Scholarship" and "Gift" (face value zero, direct activate only).
- Voucher-based scholarships use "Voucher" as payment method on the subscription transaction — the batch purpose provides the Scholarship/Gift classification for reporting. Only manual (non-voucher) grants use the new "Scholarship"/"Gift" payment method options.

## Decisions Log

| # | Question | Decision | Rationale |
|---|----------|----------|-----------|
| 1 | How to grant a student free access? | Three paths: Manual Transaction, Voucher, or Admin API | Flexibility for different admin workflows |
| 2 | Track grant type in transactions? | Add options to payment_method on Subscription Transaction | Pragmatic — reuses existing filterable field |
| 3 | Track batch purpose? | New batch_purpose field on Voucher Batch | Clean separation for reports and accounting |
| 4 | Recipient notes on cards? | recipient_note on Voucher Card, visible only for non-Sale batches | Reduces noise on regular sale cards |
| 5 | How to activate cards without library? | "Direct Activate" button on batch — activates ALL cards | Simplest admin flow |
| 6 | Library field on Direct Activate cards? | Set library to sentinel value "Admin-Direct" | Clear audit trail in Redemption Log |
| 7 | Prevent cross-purpose misuse? | Block Allocation on non-Sale batches; block Direct Activate on Sale batches | Prevents accidental mixing |
| 8 | How to know batch purpose from card? | Propagate read-only batch_purpose from batch to card | Enables conditional field visibility |
| 9 | Payment method on voucher redeem? | Keep "Voucher" as-is — trace to batch purpose via card-to-batch chain | Minimal change in sensitive redemption flow |
| 10 | Force face_value = 0 for non-Sale? | Yes — enforced via validation | Prevents phantom revenue in reports |
| 11 | Cancel Direct Activate cards? | Use existing void mechanism | No new mechanism needed |
| 12 | Direct Activate — partial or all? | Activate ALL cards in the batch | Simpler UX and logic |
| 13 | Reports? | Dedicated Script Report included in this feature | Admin needs visibility from day one |

## Out of Scope

- Partial Direct Activate (activate X of Y cards) — all cards are activated at once
- Automatic student notification on scholarship grant
- Scholarship application/approval workflow
- Budget tracking for scholarships
- Integration with external scholarship management systems
