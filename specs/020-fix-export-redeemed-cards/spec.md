# Feature Specification: Fix Export For Print Includes Redeemed Cards

**Feature Branch**: `020-fix-export-redeemed-cards`
**Created**: 2026-02-18
**Status**: Draft
**Input**: User description: "When I generate a Memora Voucher Batch, then a Player redeems a card from that batch, then we go back to that batch and click on 'Export for Print' — it prints all the cards, including the redeemed ones. It returns all cards even if some are no longer available."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Export excludes non-available cards (Priority: P1)

As an admin exporting a voucher batch for printing, I want the CSV to only include cards that are still available, so that I don't print cards that have already been redeemed, voided, or expired — which would waste printing costs and create confusion.

**Why this priority**: This is the core bug. Printing redeemed/voided cards wastes resources and could lead to customer complaints if invalid cards are distributed.

**Independent Test**: Can be tested by generating a batch, changing some card statuses to Redeemed/Void/Expired, then clicking "Export for Print" and verifying the CSV only contains Available cards.

**Acceptance Scenarios**:

1. **Given** a batch with 10 cards where 3 have been redeemed, **When** the admin clicks "Export for Print", **Then** the downloaded CSV contains only the 7 Available cards.
2. **Given** a batch with 10 cards where 2 are Void and 1 is Expired, **When** the admin clicks "Export for Print", **Then** the downloaded CSV contains only the 7 Available cards.
3. **Given** a batch with 10 cards where all 10 are still Available, **When** the admin clicks "Export for Print", **Then** the downloaded CSV contains all 10 cards (no regression for new batches).

---

### User Story 2 - Export log reflects actual exported count (Priority: P2)

As an admin, when I export a batch, the export log should record how many cards were actually included in the export, not the original generated count.

**Why this priority**: Accurate audit trail. The current code logs `batch.generated_count` which would be wrong if some cards are excluded.

**Independent Test**: Export a partially-redeemed batch and check the export_log child table entry for the correct card_count.

**Acceptance Scenarios**:

1. **Given** a batch of 10 cards with 3 redeemed, **When** the admin exports for print, **Then** the export_log entry shows card_count = 7.

---

### User Story 3 - Export blocked when no available cards remain (Priority: P2)

As an admin, if all cards in a batch have been redeemed, voided, or expired, I should not be able to export an empty file.

**Why this priority**: Prevents confusion from downloading an empty or header-only CSV.

**Independent Test**: Set all cards in a batch to non-Available statuses, then attempt export.

**Acceptance Scenarios**:

1. **Given** a batch where all cards are Redeemed or Void, **When** the admin clicks "Export for Print", **Then** the system shows an error message indicating no available cards to export.

---

### Edge Cases

- What happens when a batch has a mix of all statuses (Available, Allocated, Redeemed, Void, Expired)? Only Available cards should be exported.
- What happens if cards are redeemed between clicking "Export for Print" and the server processing the request? The server-side query at export time determines inclusion, so race conditions are effectively handled.
- What about Allocated cards? Allocated cards are assigned to a distributor but not yet redeemed — they should also be excluded from the print export since they are no longer "available" for general distribution.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST exclude cards with status Redeemed, Void, or Expired from the "Export for Print" CSV output.
- **FR-002**: System MUST exclude cards with status Allocated from the "Export for Print" CSV output (allocated cards belong to a specific distributor).
- **FR-003**: System MUST regenerate the CSV at export time by querying current card statuses, rather than serving the original pre-generated file as-is.
- **FR-004**: System MUST record the actual number of exported cards (not the original generated count) in the export_log child table entry.
- **FR-005**: System MUST return an error message when no Available cards exist in the batch at export time.
- **FR-006**: System MUST preserve the existing CSV format (columns: serial_no, pin, product_names, face_value) — no changes to column structure.
- **FR-007**: System MUST maintain the existing security controls (System Manager role check, audit logging, encrypted file decryption for PIN retrieval).

### Key Entities

- **Memora Voucher Card**: Individual card with status (Available, Allocated, Redeemed, Void, Expired), serial_no, and pin_hmac (hashed PIN — plaintext PIN is only in the encrypted export file).
- **Memora Voucher Batch**: Parent container for cards, holds the encrypted_file_url reference and export_log child table.
- **Export Log**: Child table entry recording each export event (exported_by, exported_at, card_count).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Exported CSV contains only cards with "Available" status — zero non-available cards appear in any export.
- **SC-002**: Export log card_count matches the actual number of rows in the exported CSV (excluding header).
- **SC-003**: Export of a batch with zero available cards returns a clear error instead of an empty file.
- **SC-004**: Export continues to complete within acceptable time (under 5 seconds for a 1000-card batch).

## Assumptions

- **A-001**: Allocated cards should NOT be included in the export. Rationale: Allocated cards are assigned to a specific distributor and are no longer in the general "available" pool for printing and distribution.
- **A-002**: The plaintext PINs needed for the CSV can be recovered by decrypting the original encrypted export file and filtering its rows by serial_no against the current Available cards in the database. This avoids needing to store plaintext PINs anywhere else.
- **A-003**: The existing encrypted export file (generated at batch creation time) will continue to be stored — it serves as the source of plaintext PINs. The filtering happens at export/download time.
