# Memora Voucher Management System (v3.0)
## Comprehensive Admin Guide: Phases 33-38

**Date:** February 14, 2026
**Target Audience:** Memora Admins
**Version:** 1.0

---

## Table of Contents

1. [Overview: What is the Voucher System?](#overview)
2. [Phase 33: DocType Foundation - System Setup](#phase-33)
3. [Phase 34: Batch Generation & Void - Creating Cards](#phase-34)
4. [Phase 35: Allocation & Distribution - Sending Cards to Libraries](#phase-35)
5. [Phase 36: Redemption API - Student Experience](#phase-36)
6. [Phase 37: Financial Integration - Money Tracking](#phase-37)
7. [Phase 38: Reports & Season Expiration - Monitoring & Cleanup](#phase-38)
8. [Complete Admin Workflows](#complete-workflows)
9. [Troubleshooting Guide](#troubleshooting)

---

## Overview: What is the Voucher System? {#overview}

### The Big Picture

The Memora Voucher System allows libraries to sell **physical cards** containing secret PIN codes. Students buy the card from a library, enter the PIN in the mobile app, and instantly unlock educational content.

**Why this matters:**
- Reaches students in areas without internet payment systems
- Libraries get commission revenue from card sales
- No PayPal, no Stripe needed — just a printed card

### How It Works (High Level)

```
Admin Creates Batch (1000 cards)
    ↓
Admin Generates PINs & Prints Cards
    ↓
Admin Allocates Cards to Libraries (100 to Library A, 200 to Library B, etc.)
    ↓
Student Buys Card from Library, Enters PIN in App
    ↓
Content Unlocks Instantly, Student Can Learn
    ↓
Admin Reviews Reports, Processes Invoices, Pays Libraries Commission
    ↓
Season Ends, Old Cards Automatically Expire
```

### Key Business Concepts

**Three Financial Models for Cards:**

1. **Prepaid** — Admin sells a batch to a library, library pays upfront, gets commission
   - Library risk: If they don't sell the cards, they lose money
   - Admin benefit: Immediate cash
   - Example: "I'm selling 100 cards @ $10 each to Library A for $800 (after commission)"

2. **Consignment** — Admin sends cards to library, library only pays for cards that sell
   - Library risk: None (they only pay for sold cards)
   - Admin benefit: Slower payment, but higher conversion
   - Example: "I'm sending 100 cards to Library A, they'll invoice me only for redeemed ones"

3. **Free Cards** — Not commonly used, but supported (e.g., promotional cards)

### Key Entities You'll Interact With

| Entity | What It Is | Example |
|--------|-----------|---------|
| **Voucher Batch** | A group of 1000+ cards with same properties | "Batch 001: 1000 Arabic cards, $10 each, Premium Plan" |
| **Voucher Card** | Individual card with unique PIN | Serial VCH-000001, PIN: 4a7f2k9x8b3q |
| **Voucher Allocation** | Assigning cards to a library | "Allocate 100 cards to Cairo Library, Prepaid, $10 commission" |
| **Voucher Redemption Log** | Record of every attempt to use a card | "VCH-000001: Successful redeem by student Ahmed, Feb 10 2:30 PM" |
| **Sales Invoice** | Bill sent to library for prepaid cards | "Invoice #INV-001: Cairo Library, 100 cards, $800 total" |
| **Credit Note** | Refund for returned prepaid cards | "Credit Note #CN-001: Cairo Library returned 10 cards, -$80" |

---

## Phase 33: DocType Foundation - System Setup {#phase-33}

### What This Phase Does

Phase 33 sets up all the **database structure and forms** you'll use in Frappe Desk. Think of it as building the filing cabinets before you file anything.

**Status:** ✅ Complete (no admin action needed — it's already built)

### What Gets Created

#### 1. Voucher Batch (Main Form)

**Location:** Frappe Desk → Voucher Batch
**Purpose:** Container for a group of cards

**Fields You'll Fill:**

| Field | Type | Example | Notes |
|-------|------|---------|-------|
| **Batch Name** | Text | "Arabic 2026 Q1 - Premium" | Auto-generated with prefix VBATCH- |
| **Quantity** | Number | 1000 | Total cards to generate in this batch |
| **PIN Length** | Select | 14 | Choose 12, 14, or 16 characters (longer = more secure) |
| **Face Value** | Currency | $10.00 JOD | Price of each card |
| **Batch Grants** | Child Table | [rows] | Which Products does this batch unlock? |
| **Status** | Select | Draft | Starts as Draft, becomes Generated → Active → Closed |

**Status Transitions:**
```
Draft → Generate Cards → Generated → Submit to Activate → Active → Close When Done → Closed
```

#### 2. Voucher Card (Auto-Generated)

**Location:** Frappe Desk → Voucher Card
**Purpose:** Individual card with PIN

**Key Fields:**

| Field | Visibility | Example | Notes |
|-------|-----------|---------|-------|
| **Serial Number** | Public | VCH-000001 | Printed on card, for customers to identify |
| **PIN** | 🔒 HIDDEN | 4a7f2k9x8b3q | Only admins can see in backend (masked in logs) |
| **PIN HMAC** | 🔒 HIDDEN | hash_value | Cryptographic hash — never store plain PIN |
| **Status** | Public | Available | Available → Allocated → Redeemed (or Void/Expired) |
| **Batch** | Public | VBATCH-00001 | Which batch does this card belong to? |
| **Library** | Public | Cairo Library | (empty until allocated) |
| **Redeemed By** | Public | student_ahmed | (empty until redeemed) |
| **Redeemed At** | Public | Feb 10, 2:30 PM | (empty until redeemed) |

**Card Lifecycle:**

```
Available (sitting in warehouse)
    ↓
Allocated (sent to library)
    ├→ Redeemed (student used it) ✓ FINAL STATE
    ├→ Void (admin canceled it) ✓ FINAL STATE
    └→ Expired (season ended) ✓ FINAL STATE
```

#### 3. Voucher Allocation (Batch Assignment Form)

**Location:** Frappe Desk → Voucher Allocation
**Purpose:** Assign cards from a batch to a specific library

**Fields:**

| Field | Example | Notes |
|-------|---------|-------|
| **Allocation Type** | Allocate or Return | "Allocate" = sending new cards; "Return" = accepting returns |
| **Batch** | VBATCH-00001 | Which batch are these cards from? |
| **Customer (Library)** | Cairo Library | Which library gets these? |
| **Sale Model** | Prepaid or Consignment | Financial model (see Overview) |
| **Allocation Cards** | [list of VCH-000001...100] | Actual cards in this allocation |
| **Status** | Completed | Tracks approval workflow |

**Approval Workflow:**

```
Draft (editing mode)
    ↓ Click "Submit"
Pending Approval (if library requires_approval=Yes) OR Approved (auto-approve)
    ↓ Click "Approve" OR "Reject"
Completed (cards move to library) OR Rejected (stays in batch)
```

#### 4. Voucher Redemption Log (Audit Log — Read-Only)

**Location:** Frappe Desk → Voucher Redemption Log
**Purpose:** Audit trail of every PIN entry attempt

**Auto-Created Fields** (admin cannot edit):

| Field | Example | Purpose |
|-------|---------|---------|
| **Player** | student_ahmed | Who tried to redeem? |
| **PIN Masked** | ****2k9x | Last 4 digits only (security) |
| **Card** | VCH-000001 | Which card (if found)? |
| **Status** | Success or INVALID_PIN | Did it work? |
| **IP Address** | 192.168.1.100 | For fraud detection |
| **Timestamp** | Feb 10, 2:30 PM | Exact moment of attempt |
| **Failure Reason** | "Card already redeemed" | Why did it fail? |

**Example Entries:**
```
Success ✓        VCH-000001  student_ahmed   ****2k9x  Feb 10 2:30 PM  Cairo Library IP
INVALID_PIN ✗    (unknown)   student_farah   ****xxxx  Feb 10 2:35 PM  Cairo Library IP
ALREADY_REDEEMED ✗ VCH-000002 student_omar   ****8b3q  Feb 10 2:40 PM  Cairo Library IP
```

**Note:** This is a **read-only log** — you cannot edit or delete entries. It's tamper-proof for accounting/compliance.

#### 5. Customer Custom Fields (Library Settings)

**Location:** Frappe Desk → Customer → [Any Library] → Scroll Down

**New Fields Added:**

| Field | Type | Example | Purpose |
|-------|------|---------|---------|
| **Voucher Requires Approval** | Checkbox | ✓ Yes | Does this library need admin approval for allocations? |
| **Voucher Commission Type** | Select | Percentage | Is commission a % of price, or flat amount? |
| **Voucher Commission Value** | Number | 10% or $2 | How much commission do they get? |

**When to Use:**
- Set `requires_approval=Yes` if you want to review each allocation before cards go to the library
- Set `requires_approval=No` for trusted partners (cards auto-approve when you submit)
- Commission lets each library have different rates (e.g., Cairo gets 10%, Alexandria gets 8%)

---

## Phase 34: Batch Generation & Void - Creating Cards {#phase-34}

### What This Phase Does

Phase 34 lets you **generate secure PINs**, **print cards**, and **void unwanted cards**.

### Step 1: Create a Batch (Draft)

**In Frappe Desk:**

1. Go to **Voucher Batch** → **New**
2. Fill in:
   - **Batch Name:** "Arabic 2026 Q1 - Premium" (auto-gets prefix VBATCH-00001)
   - **Quantity:** 1000 (total cards to create)
   - **PIN Length:** 14 (most common; 12 = less secure, 16 = very secure)
   - **Face Value:** $10.00 JOD
3. Add **Batch Grants** (which products unlock?)
   - Click "Add Row" in the Batch Grants table
   - Select a Product Grant (e.g., "Premium Plan 3 Months")
   - Optionally override commission just for this product
4. Click **Save** (status stays **Draft**)

**At this point:** You've created a batch definition, but NO cards exist yet.

### Step 2: Generate Cards (Background Job)

**In Frappe Desk:**

1. Open your batch (status = Draft)
2. Click **Generate Cards** button (blue)
3. Click **Yes** to confirm

**What happens behind the scenes:**
- Frappe enqueues a **background job** that runs immediately
- Job creates 1000 card records in database
- Each card gets a unique **sequential serial number** (VCH-000001, VCH-000002, ... VCH-001000)
- Each card gets a **cryptographically random PIN** using Python `secrets` module
- PIN is hashed with HMAC-SHA256 (never stored in plaintext)
- An encrypted file is created with all plain PINs (for printing)
- You get real-time progress notifications

**Security Notes:**
- PINs use `secrets.choice()` — cryptographically secure random
- HMAC uses site_config.json `voucher_hmac_secret` key (set during setup)
- Encrypted file uses Fernet (AES) — cannot decrypt without secret key

**Duration:** For 1000 cards = ~10-30 seconds (depends on server)

### Step 3: Download & Print (Export for Print)

**In Frappe Desk:**

1. Batch status is now **Generated**
2. Click **Export for Print** button (green)
3. Click **Download** to get CSV file

**CSV Contains:**
```
serial_no,pin,product_names,face_value
VCH-000001,4a7f2k9x8b3q,"Premium Plan 3 Months",$10.00
VCH-000002,9m2x5l8k3j1w,"Premium Plan 3 Months",$10.00
VCH-000003,7b4k1f9x2m8e,"Premium Plan 3 Months",$10.00
...
```

**What You Do:**
1. Open CSV in Excel
2. Print using your card template (physical cards with serial + PIN)
3. CSV is automatically deleted after 30 days (secure cleanup)

**Every export is logged** — you can see in Frappe Desk → Batch → Export Log who downloaded what and when.

### Step 4: Activate Batch (Optional But Recommended)

**In Frappe Desk:**

1. Batch status = **Generated**
2. (No button needed — batch auto-activates when first allocation is completed)
3. If you want to manually activate: use console or API (rare)

**Status becomes:** **Active** (cards can be allocated)

### Step 5: Void Cards (If Needed)

**Scenario:** You printed 1000 cards, but 50 got damaged. You want to mark them as void so they can't be redeemed.

#### Option A: Void Entire Batch

**In Frappe Desk:**

1. Open batch (status = Generated or Active)
2. Click **Void Batch** button (red)
3. Enter **Void Reason** (e.g., "Damaged shipment")
4. Click **Confirm**

**What happens:**
- All non-final cards in batch become **Void**
- Batch status becomes **Closed**
- Cards cannot be allocated anymore
- This is **permanent** — cannot undo

#### Option B: Void Individual Card

**In Frappe Desk:**

1. Go to **Voucher Card** (search VCH-000001)
2. Click **Void Card** button (red)
3. Enter **Void Reason** (e.g., "Damaged during printing")
4. Click **Confirm**

**What happens:**
- Only this card becomes **Void**
- Batch stays active, other cards can still be allocated
- This is **permanent** — cannot undo

**Which statuses can be voided?**
- ✓ Available
- ✓ Allocated
- ✗ Redeemed (already used — cannot void)
- ✗ Void (already void — cannot void again)
- ✗ Expired (season ended — cannot void)

### Important Notes for Phase 34

**PIN Security:**
- PINs are **never shown** in Frappe Desk once batch is generated
- Only the HMAC hash is stored (one-way encryption)
- Encrypted export file is the ONLY place with plain PINs
- File auto-deletes after 30 days

**Batch Statuses:**

| Status | Can Allocate? | Can Void? | Notes |
|--------|---------------|----------|-------|
| **Draft** | No | No | Still editing batch definition |
| **Generated** | No (auto-activates on first allocation) | Yes | PINs created, not yet allocated |
| **Active** | Yes | Yes | Cards being allocated to libraries |
| **Closed** | No | No | Batch is done, no more changes |

---

## Phase 35: Allocation & Distribution - Sending Cards to Libraries {#phase-35}

### What This Phase Does

Phase 35 lets you **assign cards to libraries** and manage **returns/re-allocations**.

### Scenario 1: Simple Allocation (Auto-Approve)

**Setup:** Library A has `requires_approval=No`

**Step 1: Create Allocation**

In Frappe Desk:

1. Go to **Voucher Allocation** → **New**
2. Fill in:
   - **Allocation Type:** `Allocate` (sending new cards)
   - **Batch:** Select the batch (e.g., VBATCH-00001)
   - **Customer:** Select library (e.g., Cairo Library)
   - **Sale Model:** `Prepaid` or `Consignment`
3. Click **Save** (status = Draft)

**Step 2: Auto-Fill Cards**

1. Click **Fill Cards** button (blue)
2. Enter **Quantity:** 100 (how many cards to give them?)
3. Click **Fill**

**What happens:**
- System queries **Available** cards from the batch
- Adds first 100 Available cards to the Allocation Cards table
- You can manually edit if needed (remove/add cards)

**Step 3: Submit Allocation**

1. Click **Submit Allocation** button (blue)
2. Click **Confirm**

**What happens (Auto-Approve Flow):**
- Status changes from Draft → Approved → Completed
- All 100 cards in the allocation get updated to status=**Allocated**
- Library field set to "Cairo Library"
- Allocation field set to this allocation ID
- Cards are now in Cairo Library's possession

**Duration:** Instant

**Financial Impact (Prepaid):**
- Sales Invoice automatically created
- Cairo Library owes you: 100 cards × $10 face value × (100% - commission%)
- Example: 100 × $10 × (100% - 10% commission) = $900

---

### Scenario 2: Allocation with Approval (Requires Approval)

**Setup:** Library B has `requires_approval=Yes`

**Steps 1-2:** Same as Scenario 1 (Create + Auto-Fill)

**Step 3: Submit Allocation**

1. Click **Submit Allocation** button
2. System checks: "Does this library require approval?"
3. Status changes: Draft → **Pending Approval** (cards NOT allocated yet)
4. Card status stays **Available** (still in your warehouse)

**Step 4: Approve or Reject**

**If You Approve:**

1. Click **Approve Allocation** button (green)
2. Click **Confirm**
3. Status: Pending Approval → **Completed**
4. Cards allocated (same as auto-approve scenario)
5. Invoice created

**If You Reject:**

1. Click **Reject Allocation** button (red)
2. Enter **Rejection Reason** (optional note)
3. Status: Pending Approval → **Rejected**
4. Cards stay **Available** (go back to your warehouse)
5. No invoice created

---

### Scenario 3: Re-Allocation (Move Cards Between Libraries)

**Use Case:** You allocated 100 cards to Cairo Library, but they only sold 30. They want to return 50, and you want to send them to Alexandria Library instead.

**Step 1: Create Return Allocation**

1. Go to **Voucher Allocation** → **New**
2. Fill in:
   - **Allocation Type:** `Return` (accepting returns)
   - **Batch:** VBATCH-00001
   - **Customer:** Cairo Library
   - **Sale Model:** Prepaid (same as original)
3. Click **Save**

**Step 2: Auto-Fill Returning Cards**

1. Click **Fill Cards** button
2. Enter **Quantity:** 50
3. Click **Fill**

**What happens:**
- System queries **Allocated** cards currently in Cairo Library
- Adds first 50 Allocated cards to the table
- You can manually edit

**Step 3: Submit Return**

1. Click **Submit Allocation** button
2. Status: Draft → Approved/Pending → Completed
3. 50 cards revert back to status=**Available**
4. Library/Allocation/Sale Model fields are cleared
5. If Prepaid: Credit Note created automatically (Library gets refund)

**Step 4: Create New Allocation for Alexandria**

1. Go to **Voucher Allocation** → **New**
2. **Allocation Type:** `Allocate`
3. **Customer:** Alexandria Library
4. Click **Fill Cards** → Quantity: 50
5. Click **Submit**
6. System allocates the 50 Available cards you just got back from Cairo
7. New invoice created for Alexandria

---

### Important Notes for Phase 35

**Card Statuses After Each Action:**

| Action | Before | After | Notes |
|--------|--------|-------|-------|
| Auto-fill (Allocate) | Available | Available | Not yet allocated |
| Submit (requires_approval=No) | Available | Allocated | Immediate |
| Submit (requires_approval=Yes) | Available | Available | Waiting for approval |
| Approve | Available | Allocated | Now finalized |
| Reject | Available | Available | Returns to inventory |
| Return & Submit | Allocated | Available | Card goes back |
| Student Redeems | Allocated | Redeemed | Terminal state |

**Financial Status by Sale Model:**

| Model | Timing | Refund? | Example |
|-------|--------|--------|---------|
| **Prepaid** | Invoice created when allocation completes | Yes (Credit Note) | Admin gets $900 upfront, can refund $450 on return |
| **Consignment** | Invoice created monthly for redeemed cards | No | Admin only invoices Cairo for 30 redeemed cards, $300 paid monthly |

---

## Phase 36: Redemption API - Student Experience {#phase-36}

### What This Phase Does

Phase 36 is **student-facing** — this is what happens when a student enters a PIN in the mobile app.

**As an admin, you need to understand:** How redemptions work, what can go wrong, and how to monitor them in logs.

### The Student Flow (What They See)

**Step 1: Student Opens App**
- Student launches Memora app on phone
- Navigates to "Unlock with Code" section
- Sees: "Enter your 14-digit PIN from the card"

**Step 2: Student Previews PIN**
- Student enters PIN (e.g., 4a7f2k9x8b3q)
- App sends to API: `POST /api/v1/voucher/preview` with PIN
- App shows: "This card unlocks: Premium Plan 3 Months"
- Student can see what they're getting before confirming

**Step 3: Student Confirms Redemption**
- Student clicks "Redeem"
- App sends: `POST /api/v1/voucher/redeem` with PIN + chosen product
- System:
  - Validates PIN
  - Locks card in database (SELECT FOR UPDATE)
  - Marks card as Redeemed
  - Creates Subscription Transaction
  - Adds content to student's account
  - Returns success

**Step 4: Student Sees Content**
- App refreshes
- Premium content now visible
- Student can start learning

---

### What Can Go Wrong (Error Codes)

When a student enters a PIN, the system checks multiple things. If any fail, the student gets an error:

| Error Code | Meaning | Why It Happened | Action for Admin |
|------------|---------|-----------------|------------------|
| **INVALID_PIN** | PIN doesn't match our database | Typo, fake PIN, damaged card | Tell student: "Check the PIN again" |
| **ALREADY_REDEEMED** | This card was already used | Another student used it first | Tell student: "This card was already used. Buy a new one." |
| **NOT_ALLOCATED** | Card hasn't been sent to any library yet | Admin mistake (didn't allocate) | Check: Was the card allocated? |
| **ALL_GRANTS_OWNED** | Student already owns all products on this card | They bought the same thing before | Student needs a different card |
| **ALREADY_OWNED** | Student already owns THIS specific product | They have the same course/plan | Does NOT consume the card (they can try another product) |
| **EXPIRED** | Card expired (season ended) | Admin didn't extend season, or new season not published | Check season dates in Frappe |
| **VOID** | Admin marked this card as broken | Admin clicked "Void Card" | Card is permanently unusable |
| **BATCH_INACTIVE** | Batch wasn't activated properly | Rare bug during generation | Contact dev |
| **SEASON_INACTIVE** | Season hasn't started or ended | Wrong season selected in app config | Check season status |
| **RATE_LIMITED** | Too many failed attempts | Student tried 5+ times in 1 hour from same IP | Wait 1 hour and retry |

---

### How Admins Use This Information

**1. Audit Logs (Read-Only)**

In Frappe Desk:
1. Go to **Voucher Redemption Log**
2. Filter by date range, player, library, etc.
3. See every PIN entry attempt (success and failure)

**Example Log Entry:**
```
✓ Success     | Card VCH-000001 | Student ahmed_2024    | IP 192.168.1.50   | Cairo Library | 2026-02-10 14:30
✗ INVALID_PIN | (unknown)       | Student farah_2024    | IP 192.168.1.51   | Cairo Library | 2026-02-10 14:32
✗ ALREADY_OWNED | Card VCH-000002 | Student omar_2024    | IP 192.168.1.50   | Cairo Library | 2026-02-10 14:35
```

**2. Fraud Detection**

Look for patterns:
- Same IP trying 10+ failed PINs in 1 hour → Possible brute force attack
- Multiple students from same library failing within seconds → Fake card in circulation
- Student trying to redeem same card from 3 different IPs → Possible code sharing

**3. Customer Support**

When a student contacts support saying "My card doesn't work":
1. Search Voucher Redemption Log for their username
2. Find their latest attempts
3. See the error code
4. Help them based on the error (see table above)

---

### Rate Limiting (Safety Feature)

**What it does:**
- If student fails 5+ PIN attempts in 1 hour from the same IP, they get RATE_LIMITED error
- They must wait 1 hour before trying again
- Prevents brute force attacks (guessing PINs)

**Per IP:** Max 20 failed attempts per hour (to prevent distributed attacks)

**As Admin:**
- You can't disable rate limiting (it's hardcoded security)
- If a legitimate student gets rate limited, tell them to wait 1 hour
- If it's an IP range issue, contact dev to adjust limits

---

### Important Notes for Phase 36

**PIN Security (Reinforced):**
- Student PINs are NEVER stored in plaintext
- Only HMAC hash is stored
- HMAC comparison uses `hmac.compare_digest()` (timing-attack safe)
- Even if someone hacks the database, PINs cannot be extracted
- PINs shown in Redemption Log are masked: `****2k9x` (last 4 chars only)

**One-Way Guarantee (ALREADY_OWNED):**
- If student already owns a product, the card is NOT consumed
- They can try another product on the same card
- If all products are owned, card still stays **Allocated** (not Redeemed)
- Card can be reallocated to another library or student

**No Admin Notification Spam:**
- When student redeems a voucher, the admin email system sees it's payment_method="Voucher"
- Does NOT send "New subscription purchased" email
- (This is to avoid spamming admins with thousands of redemptions)

---

## Phase 37: Financial Integration - Money Tracking {#phase-37}

### What This Phase Does

Phase 37 handles **invoices, credit notes, and commission tracking**. Think of it as the accounting backbone.

### How Commission Works

**Three-Level Priority Chain:**

```
Level 1: Product-Level Override (Voucher Batch Grant)
  ├─ Does this batch have a custom commission for this product?
  │  YES: Use it. STOP.
  │  NO: Go to Level 2

Level 2: Library Default (Customer Custom Fields)
  ├─ Does this library have a default commission rate?
  │  YES: Use it. STOP.
  │  NO: Go to Level 3

Level 3: Zero Commission
  └─ No commission specified anywhere
     Use: 0% (admin gets 100% of face value, library gets 0)
```

**Example Calculations:**

| Scenario | Face Value | Commission Type | Commission Value | Library Gets | Admin Gets |
|----------|-----------|-----------------|------------------|--------------|------------|
| Cairo Library, 10% | $10 | Percentage | 10% | $1 | $9 |
| Alexandria Library, $1.50 flat | $10 | Fixed Amount | $1.50 | $1.50 | $8.50 |
| Damascus Library, no commission | $10 | (none) | (none) | $0 | $10 |
| Premium Product override | $10 | Percentage | 15% | $1.50 | $8.50 |

**All calculations use precise Decimal arithmetic** (no rounding errors like float math).

---

### Prepaid Invoicing (Immediate)

**Timeline:**

```
Day 1: Admin submits allocation
         ↓ (instant)
Day 1: Sales Invoice created automatically
         ↓ (same day)
Day 1: Library can pay invoice
         ↓ (whenever)
Day 30: If returned, Credit Note issued
```

**Step 1: Allocate Cards (Prepaid Model)**

In Frappe Desk:

1. Create allocation with **Sale Model = Prepaid**
2. Fill Cards: 100 cards
3. Click Submit

**Step 2: Invoice Auto-Created**

In Frappe Desk (you'll see notification):

1. Go to **Sales Invoice** → find new invoice
2. Verify details:
   - Customer: Cairo Library
   - 100 line items (1 per card)
   - Each line: Serial Number, Face Value, Commission
   - Total: 100 × $10 × (100% - 10%) = $900
3. Invoice automatically submitted (ready to pay)

**Example Invoice:**
```
TO: Cairo Library                              DATE: 2026-02-10
                                              INVOICE #: INV-1001

DESCRIPTION                          QTY    RATE      AMOUNT
Voucher Card VCH-000001              1      $10.00    $10.00 (-$1.00 comm) = $9.00
Voucher Card VCH-000002              1      $10.00    $10.00 (-$1.00 comm) = $9.00
Voucher Card VCH-000003              1      $10.00    $10.00 (-$1.00 comm) = $9.00
... [100 lines total]
                                     ___________________________
TOTAL DUE:                                                $900.00
```

**Step 3: Handle Returns (If Library Returns Cards)**

1. Create new Allocation with **Allocation Type = Return**
2. Fill Cards: 10 cards (they're returning these)
3. Click Submit

**What happens:**
- Credit Note automatically created
- Amount: 10 × $10 × (100% - 10%) = $90 refund
- Cairo Library's balance: $900 - $90 = $810 owed

---

### Consignment Invoicing (Monthly)

**Timeline:**

```
Feb 10: Admin sends 100 cards to Library (consignment, no invoice yet)
Feb 15: Student redeems card #5
Feb 20: Student redeems card #23
...
Mar 1: 2:00 AM: Scheduled job runs
         ↓
Mar 1: Invoice created for February redeemed cards only
       "30 cards redeemed in Feb" = Admin invoices library for 30 × $10 × (100% - commission%)
```

**Key Difference from Prepaid:**
- **Prepaid:** Invoice for ALL 100 cards immediately
- **Consignment:** Invoice for ONLY redeemed cards, monthly

**Step 1: Allocate Cards (Consignment Model)**

In Frappe Desk:

1. Create allocation with **Sale Model = Consignment**
2. Fill Cards: 100 cards
3. Click Submit
4. No invoice created yet

**Step 2: Students Redeem Cards**

When students use the voucher PIN:
- Cards change status: Allocated → Redeemed
- Redemption Log records the event
- Admin is NOT invoiced yet

**Step 3: Monthly Job Creates Invoices (Automatic)**

**When:** 1st of month at 2:00 AM server time

**What it does:**
1. Finds all cards with:
   - Status = Redeemed
   - Sale Model = Consignment
   - Redeemed in previous month
   - Not yet invoiced
2. Groups by library (e.g., "all redeemed cards for Cairo Library")
3. For each batch within the library:
   - Resolves commission
   - Calculates: cards × face value × (100% - commission%)
   - Creates one line item in invoice
4. Creates one invoice per library (may have multiple batches)
5. Submits invoice
6. Updates cards with sales_invoice link (prevents double-invoicing)

**Example Consignment Invoice (March 1):**
```
TO: Cairo Library                              DATE: 2026-03-01
                                              INVOICE #: INV-1002

DESCRIPTION                                 QTY    AMOUNT
Consignment - Batch VBATCH-00001 (Feb)      30     $270.00 (30 × $10 × 90% commission)
Consignment - Batch VBATCH-00002 (Feb)      15     $135.00 (15 × $10 × 90% commission)
                                             _______________
TOTAL DUE:                                        $405.00
```

**Automatic Recalculation:**

If commission changes mid-month, consignment invoices still use the commission that was in place when the card was REDEEMED (historical accuracy).

---

### How Admins Use This

**1. Check Invoice Status**

In Frappe Desk:

1. Go to **Sales Invoice**
2. Filter by Customer (library), date range
3. See all invoices and payment status

**2. Track Commission Revenue**

In Frappe Desk:

1. Go to **Sales Invoice** (summary view)
2. See total library commissions paid out
3. Calculate your net: Total face value - Total commissions = Admin revenue

**3. Handle Disputes**

If library says: "You invoiced us for 100 cards but we only sold 50":

1. Go to **Voucher Redemption Log**
2. Filter: Customer = Library, Status = Success, Date Range = Feb 2026
3. Count redeemed cards
4. Verify against invoice

---

### Important Notes for Phase 37

**Invoice Creation is Automatic:**
- Prepaid: Created immediately when allocation completes
- Consignment: Created monthly by scheduled job
- Cannot be manually created (system maintains consistency)

**Credit Notes (Prepaid Only):**
- Consignment returns create NO financial action (library only pays for what sold)
- Prepaid returns create Credit Notes (refund)
- Credit Note automatically linked to original invoice

**Commission is Smart:**
- If Library A has 10% but this batch overrides to 15%, invoice uses 15%
- If library changes commission mid-year, NEW invoices use new rate
- OLD invoices are never recalculated (locked for accounting)

---

## Phase 38: Reports & Season Expiration - Monitoring & Cleanup {#phase-38}

### What This Phase Does

Phase 38 provides **visibility into performance** and **automatic card expiration** when seasons end.

---

### Report 1: Sales by Library (RPT-01)

**Purpose:** How much revenue did each library generate?

**Location:** Frappe Desk → Sales by Library Report

**How to Use:**

1. Set date range: Feb 1 - Feb 28
2. Filter by Library (optional)
3. Filter by Sale Model: Prepaid, Consignment, or Both
4. View table:

| Library | Redeemed | Face Value | Total Rev. | Commission | Net Revenue | Model |
|---------|----------|-----------|-----------|------------|-------------|-------|
| Cairo | 50 | $10 | $500 | $50 | $450 | Consignment |
| Alexandria | 75 | $10 | $750 | $75 | $675 | Prepaid |
| Damascus | 20 | $10 | $200 | $20 | $180 | Prepaid |

**Insights:**
- Alexandria is your best performer
- Cairo's consignment is slower but steady
- Damascus is smallest market

**Use for:**
- Monthly business review
- Identify high-performing vs. under-performing libraries
- Decide where to allocate more cards next month

---

### Report 2: Batch Performance (RPT-02)

**Purpose:** How healthy is each batch? How many cards were redeemed?

**Location:** Frappe Desk → Batch Performance Report

**How to Use:**

1. View all batches in table:

| Batch | Face Value | Total Cards | Available | Allocated | Redeemed | Void | Expired | Redemption % | Days Till Season End |
|-------|-----------|------------|-----------|-----------|----------|------|---------|-------------|-----|
| VBATCH-00001 | $10 | 1000 | 100 | 300 | 550 | 40 | 10 | 55% | 45 days |
| VBATCH-00002 | $15 | 500 | 50 | 150 | 280 | 20 | 0 | 56% | 45 days |
| VBATCH-00003 | $10 | 2000 | 800 | 900 | 200 | 100 | 0 | 10% | 45 days |

**Insights:**
- Batch 1 & 2 performing well (~55% redemption rate)
- Batch 3 performing poorly (only 10% redemption) — maybe wrong target market?
- Most batches have 45 days before season expires — start planning next batch

**Use for:**
- Quality control (which batches are profitable?)
- Demand forecasting (how many cards to print next time?)
- Risk assessment (are unsold cards piling up?)

---

### Report 3: Consignment Reconciliation (RPT-03)

**Purpose:** Which libraries owe you money for consignment sales?

**Location:** Frappe Desk → Consignment Reconciliation Report

**How to Use:**

1. Set date range (or leave blank for current month)
2. View table:

| Library | Allocated | Redeemed | Uninvoiced | Amount Due | Invoice Status |
|---------|-----------|----------|------------|-----------|-----------------|
| Cairo | 100 | 35 | 0 | $315 | Invoiced Feb 1 |
| Alexandria | 80 | 45 | 10 | $90 | Pending (next invoice Mar 1) |
| Damascus | 60 | 20 | 5 | $45 | Pending (next invoice Mar 1) |

**Insights:**
- Cairo's February invoice is ready to send
- Alexandria & Damascus have redeemed 15 cards total since last invoice — will be invoiced Mar 1
- Can predict when each library's payment is coming

**Use for:**
- Cash flow forecasting
- Late payment follow-up
- Monthly reconciliation with libraries

**Note:** "Uninvoiced" cards are redeemed but not yet on a monthly invoice. They WILL be invoiced when the monthly job runs on the 1st.

---

### Report 4: Security Audit (RPT-04)

**Purpose:** Detect fraud and suspicious patterns

**Location:** Frappe Desk → Security Audit Report

**How to Use:**

1. Set date range (default: last 30 days)
2. Filter by failure type (optional)
3. View table:

| Player | IP Address | Failure Type | Attempts | First Try | Last Try |
|--------|-----------|--------------|----------|-----------|----------|
| student_hacker | 192.168.1.100 | INVALID_PIN | 23 | Feb 10 2:00 PM | Feb 10 2:10 PM |
| student_farah | 192.168.1.50 | ALREADY_REDEEMED | 3 | Feb 10 2:15 PM | Feb 10 2:20 PM |
| (unknown IP) | 203.0.113.42 | INVALID_PIN | 8 | Feb 10 3:00 PM | Feb 10 3:05 PM |

**Insights:**
- `student_hacker` tried 23 invalid PINs in 10 minutes → Brute force attack!
- External IP (203.0.113.42) trying to guess PINs → Possible automated attack
- `student_farah` legitimately tried same card 3 times (might need help)

**Use for:**
- Security monitoring
- Fraud detection
- Customer support (help legitimate users)

**Fraud Red Flags:**
- Same IP, 10+ INVALID_PIN attempts in 1 hour → Brute force
- Multiple IPs, same player, high attempt count → Account compromise
- Specific PIN targeted (same card, multiple attempts) → Leaked PIN in wild

**Actions to Take:**
- Block IP range (contact IT)
- Reset student password if account compromised
- Invalidate leaked PIN batch (void remaining cards)
- Contact library (if abuse from their location)

---

### Automatic Feature: Season Expiration (SCHED-01)

**What It Does:**

Voucher cards are tied to seasons. When a season ends or is unpublished, all cards linked to that season should expire.

**Timeline:**

```
Dec 1: Admin creates "Winter 2026" season, publishes it
Dec 1: Admin creates batch linked to Winter season
Dec 1: Students buy & redeem cards

Mar 1: Winter season ends (season.end_date = Feb 28)
Mar 1 at 1:05 AM: Scheduled job runs
       ↓
       Finds all cards linked to ended seasons
       Status: Available or Allocated → Status: Expired
       void_reason: "Season Ended"

Mar 1 1:10 AM: Admin checks Batch Performance report
        Redeemed cards: still show (students used them)
        Available/Allocated cards: now show as Expired
        → "We had 50 cards left unsold, now they're expired"
```

**How It Works:**

1. **Daily at 1:05 AM** (server time), a scheduled job runs
2. Job queries: "Which batches are linked to ended/unpublished seasons?"
3. For each batch found:
   - Finds all cards with status=Available OR status=Allocated
   - Sets status=Expired, void_reason="Season Ended"
   - Leaves Redeemed cards alone (already finalized)
4. Logs results

**As Admin, What You Need to Know:**

- You DON'T manually mark cards as expired (automatic)
- Once season ends, unsold cards WILL expire automatically
- No way to "unblock" expired cards (by design — season is over)
- Plan next season BEFORE current one ends

**Example Timeline:**

```
Jan 1: Create Spring 2026 season (end_date = May 31)
Jan 1: Create batch for Spring
Jan-May: Students buy cards, some are left unsold
May 31: Season ends
Jun 1: 1:05 AM: Automated job expires remaining cards
Jun 1: You cannot sell leftover cards (they're expired)
Jun 2: Create Summer 2026 batch for new season
```

---

### Important Notes for Phase 38

**Report Filtering:**

All reports support different filter combinations:
- **Date Range:** See data for specific months
- **Customer/Library:** Focus on one library
- **Status:** Filter by card status (Available, Allocated, etc.)
- **Sale Model:** See Prepaid vs. Consignment separately

**Scheduled Job Timing:**

- **Consignment Invoicing:** 1st of month, 2:00 AM
- **Season Expiration:** Daily at 1:05 AM
- **Export Cleanup:** Daily (deletes 30-day-old encrypted files)

All times are **server time** (usually GMT). Check your server config if needed.

**Report Performance:**

- For large datasets (10,000+ cards), reports may take a few seconds
- All reports have database indexes for fast querying
- Monthly filtering recommended for historical analysis

---

## Complete Admin Workflows {#complete-workflows}

### Workflow A: Launch a New Voucher Program (Prepaid Model)

**Timeline:** 2-3 weeks

**Week 1: Planning**

1. Decide: How many cards? ($10 each? $15 each?)
2. Which libraries get them?
3. Who gets commission and how much?
4. Which products should cards unlock?

**Week 2: Setup**

1. In Customer form for each library:
   - Set `voucher_requires_approval` = Yes/No
   - Set commission type (Percentage or Fixed Amount) and value

2. Create Voucher Batch:
   - Name: "Arabic 2026 Spring - Premium"
   - Quantity: 1000
   - PIN Length: 14
   - Face Value: $10
   - Add batch grants (which products)

3. Click "Generate Cards" → Wait 30 seconds

4. Click "Export for Print" → Download CSV

5. Send CSV to printing vendor

**Week 3: Distribution**

1. Once cards arrive, create allocations:
   - Allocation Type: Allocate
   - Batch: Your batch
   - Customer: Cairo Library
   - Sale Model: Prepaid
   - Quantity: 100 cards
   - Click Submit

2. If library requires approval:
   - Click "Approve Allocation" (or "Reject" if needed)

3. Invoice automatically created → Send to library to pay

4. Repeat for other libraries (Alexandria 200, Damascus 100, etc.)

**Ongoing:**

- Monitor Sales by Library report weekly
- Check Security Audit report for fraud
- Handle customer support questions

**Month End:**

- Review Batch Performance → Decide if next batch needed
- Check library payments → Follow up on overdue invoices

---

### Workflow B: Consignment Program (Monthly Invoicing)

**Timeline:** Setup once, then monthly automation

**Month 1: Setup**

1. Configure libraries:
   - Set commission (e.g., 10%)
   - Set `voucher_requires_approval` = No (auto-approve for faster distribution)

2. Create multiple allocations:
   - Cairo: 100 cards (Consignment)
   - Alexandria: 80 cards (Consignment)
   - Damascus: 60 cards (Consignment)

3. Submit allocations (auto-approved, no invoices yet)

**Month 1-2: Sales Period**

- Students enter PINs and redeem cards
- Each success recorded in Redemption Log
- Inventory: Available → Redeemed

**Month 2, 1st of Month (at 2:00 AM):**

- Scheduled job automatically creates invoices for last month's redeemed cards
- Cairo: "30 cards redeemed in January" → Invoice for $270 (30 × $10 × 90%)
- Alexandria: "22 cards redeemed" → Invoice for $198
- Damascus: "15 cards redeemed" → Invoice for $135

**Month 2, Admin Actions:**

1. Check Consignment Reconciliation report
2. Verify invoices created correctly
3. Send invoices to libraries to pay
4. Libraries pay within 30 days
5. You deposit payment

**Ongoing:**

- Repeat monthly
- Monitor which libraries are selling well
- Reallocate cards from slow libraries to fast ones (Return + New Allocate)
- Check Batch Performance to forecast next batch size

---

### Workflow C: Handle Returns (Prepaid Model)

**Scenario:** Cairo Library only sold 70 out of 100 cards. They want to return 25.

**Step 1: Process Return**

1. Create Voucher Allocation:
   - Allocation Type: Return
   - Customer: Cairo Library
   - Sale Model: Prepaid (same as original)

2. Click "Fill Cards" → Quantity: 25

3. Click "Submit Allocation"

4. Credit Note automatically created: -$225 (25 × $10 × 90%)

**Step 2: Re-Allocate Cards**

1. Create new Voucher Allocation:
   - Type: Allocate
   - Customer: Alexandria Library (new library)
   - Batch: Same batch
   - Quantity: 25

2. Click Submit

3. New invoice created for Alexandria: $225

**Result:**
- Cairo returns 25 cards: Invoiced $900, refunded $225, owes $675
- Alexandria gets 25 cards: Invoiced $225
- Cards moved from slow library to fast library

---

## Troubleshooting Guide {#troubleshooting}

### "I Clicked Generate Cards But No Cards Were Created"

**Possible Causes:**

1. **Batch is not in Draft status**
   - Go to batch, check status
   - Should be Draft to generate
   - If Generated already, cards exist (check Voucher Card list)

2. **Background job failed**
   - Check Frappe logs: Desk → Tools → Logs
   - Search for "generate_cards_job"
   - Look for error message

**Solutions:**

- **If logs show "HMAC secret not configured":**
  - Admin needs to set `voucher_hmac_secret` in site_config.json
  - Contact dev to run: `bench set-config voucher_hmac_secret $(python3 -c 'import secrets; print(secrets.token_hex(32))')`

- **If logs show "Database error":**
  - Check disk space on server
  - 1000 cards ≈ 1 MB
  - Contact your server admin

---

### "Students Report: Card Keeps Saying INVALID_PIN"

**Possible Causes:**

1. Student mistyped PIN
   - Ask them to type slowly, check for 0/O, 1/l confusion
   - Try different card

2. PIN is actually invalid (card was never printed)
   - Check Voucher Card list: Does VCH-000XXX exist?
   - If not, card was never generated → Try another card

3. Card was voided
   - Search Voucher Card list for serial number
   - Check status: If "Void", it's broken → Try another card

**Solutions:**

- Verify with Voucher Redemption Log:
  1. Go to Redemption Log
  2. Filter by Student username
  3. Check recent attempts
  4. If INVALID_PIN: Card doesn't exist, try another
  5. If ALREADY_REDEEMED: Card used already, new card needed

---

### "Invoice for Library Never Created"

**Possible Causes (Prepaid):**

1. Allocation was rejected
   - Go to Allocation, check status: Rejected?
   - Create new allocation, click Approve this time

2. Allocation didn't complete
   - Check status: Still Pending Approval?
   - Click Approve button

3. Sale Model is Consignment (not Prepaid)
   - Consignment invoices are created monthly, not immediately
   - Check on 1st of next month at 2:00 AM

**Solutions:**

- For Prepaid: Check allocation status must be "Completed" for invoice
- For Consignment: Wait until 1st of month, then check report

**Verify Invoice Created:**
1. Go to Sales Invoice
2. Filter by Customer (library name)
3. Filter by Date (today's date)
4. See if invoice appears
5. If not, check allocation status again

---

### "Consignment Invoice Didn't Create on 1st of Month"

**Possible Causes:**

1. No cards were redeemed last month
   - Go to Voucher Redemption Log
   - Filter: Previous month, Status = Success
   - If empty, no cards to invoice

2. All cards already invoiced
   - Go to Voucher Card
   - Filter: Status = Redeemed, sale_model = Consignment
   - Check if sales_invoice field is filled
   - If filled, already invoiced

3. Scheduled job didn't run
   - Check system logs (rare)
   - Contact dev to manually run: `bench execute memora_admin.tasks.consignment_billing.generate_monthly_invoices`

**Solutions:**

- Consignment invoices only created if:
  - Cards were redeemed in previous month
  - Cards not yet invoiced
  - At least one library has redeemed cards

- Check Consignment Reconciliation report to verify uninvoiced count

---

### "Student Says Card Already Redeemed But Log Shows Null"

**Possible Causes:**

1. Two students tried same PIN simultaneously
   - Database locked first one (winner), second got ALREADY_REDEEMED
   - But first student's Redemption Log entry shows null card
   - Actually card was redeemed by first student

2. PIN guessed from another PIN that was actually redeemed
   - Similar issue

**Solutions:**

- Check Redemption Log for STATUS = ALREADY_REDEEMED and CARD = (null or empty)
- This is normal edge case, not a bug
- Tell second student: "Card was redeemed by someone else seconds before you tried"
- If genuinely confused, check Voucher Card status directly to confirm

---

### "Batch Performance Report Shows 0% Redemption, But Cards Showing Redeemed"

**Possible Causes:**

1. Cards redeemed but not yet visible in report cache (rare)
   - Reports cache for 5 minutes
   - Refresh page

2. Cards have different batch (check serial numbers)

**Solutions:**

- Refresh report page
- Check individual cards in Voucher Card list
- Verify serial numbers match batch

---

### "Commission Is Wrong on Invoice"

**Possible Causes:**

1. Commission changed after invoice created
   - Invoices lock commission at time of creation
   - Cannot change old invoice

2. Batch grant override not applied
   - Check if batch grant has commission_type and commission_value set
   - If blank, uses library default

3. Library has no default commission
   - Go to Customer form
   - Check voucher_commission_type and voucher_commission_value
   - If blank, commission = 0% (library gets nothing)

**Solutions:**

- **To verify correct commission:**
  1. Go to Sales Invoice
  2. Calculate: Total price × commission % = line amount
  3. Match against batch grant OR library defaults

- **To change commission for FUTURE invoices:**
  1. Update Customer voucher_commission_value
  2. Next allocation/consignment invoice will use new rate
  3. Old invoices never change

- **If historical invoice wrong:**
  - Create Credit Note for difference
  - Create new Sales Invoice line for adjustment
  - (Contact dev for complex scenarios)

---

### "Season Ended But Cards Didn't Expire"

**Possible Causes:**

1. Season status not updated
   - Go to Season form
   - Check status: Must be "Inactive" or "Completed" for expiration
   - If "Active", change it first

2. Scheduled job hasn't run yet
   - Expiration job runs daily at 1:05 AM
   - If it's 1:04 AM, wait a minute

3. Cards are already Redeemed (not expired)
   - Redeemed cards are final — don't expire
   - Only Available/Allocated cards expire

**Solutions:**

- Manually expire cards (rare):
  1. Go to Voucher Card
  2. Filter: Status = Available or Allocated, Batch = [your batch]
  3. Bulk update status to Expired (if you have permissions)
  4. Or contact dev to run expiration job

---

### "I Locked Myself Out (Too Many Failed PIN Attempts)"

**Possible Causes:**

1. Too many invalid PIN attempts from your IP (rate limited)

**Solutions:**

- **Wait 1 hour** — Rate limit auto-expires
- **Use different IP** — Hotspot, different device, VPN
- **Test from different IP** — Ask colleague to try from their location

---

## Summary: What You Now Know

You understand all 6 phases of the Voucher System:

1. **Phase 33 (Foundation):** How data is organized (DocTypes, fields, relationships)
2. **Phase 34 (Batch):** How to create and print card batches with secure PINs
3. **Phase 35 (Allocation):** How to distribute cards to libraries with approval workflows
4. **Phase 36 (Redemption):** How students use cards in the app (your monitoring role)
5. **Phase 37 (Financial):** How invoices and commissions are calculated and tracked
6. **Phase 38 (Reports):** How to monitor performance and automate card expiration

**You're ready to:**
- Launch voucher programs
- Manage multiple libraries
- Track revenue and commission
- Debug customer issues
- Monitor for fraud
- Prepare financial reports

**Next Steps:**

1. Familiarize yourself with Frappe Desk navigation
2. Create test batch and allocations
3. Try all 4 reports with different filters
4. Monitor a few live redemptions in the Redemption Log
5. Review this guide again for specific scenarios

**When you need help:**

- Check this guide's Troubleshooting section
- Ask a colleague who's used vouchers before
- Contact dev team if database/technical issue
- Check Frappe logs (Desk → Tools → Logs) for errors

---

**Document Version:** 1.0
**Last Updated:** February 14, 2026
**Questions?** Reach out to your platform admin or dev team.

Happy voucher managing! 🎓
