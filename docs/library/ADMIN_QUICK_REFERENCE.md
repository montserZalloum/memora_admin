# Memora Voucher System - Quick Reference Card

## 🎯 Most Common Tasks (Copy & Keep Handy)

---

## 1️⃣ Create a New Voucher Batch

**Location:** Frappe Desk → Voucher Batch → New

```
Field                   Example Value
─────────────────────────────────────────
Batch Name             Arabic 2026 Spring
Quantity               1000
PIN Length             14  (or 12, 16)
Face Value             $10.00
Batch Grants           [Click Add Row] → Select Product Grant
Status                 Draft (auto)
```

**Next:** Click "Save" → Card not yet created

---

## 2️⃣ Generate PINs & Create Cards

**Location:** Voucher Batch (Draft) → Click "Generate Cards" button

```
Action:    Click Generate Cards → Yes → Confirm
Time:      ~10-30 seconds for 1000 cards
Result:    Status changes Draft → Generated
           1000 Voucher Card records created with hashed PINs
```

---

## 3️⃣ Download CSV for Printing

**Location:** Voucher Batch (Generated) → Click "Export for Print" button

```
What you get:  CSV file with columns:
               serial_no, pin, product_names, face_value
               Example: VCH-000001,4a7f2k9x8b3q,"Premium Plan",$10.00
What to do:    Open in Excel → Print on physical cards
Auto-delete:   After 30 days (encrypted file removed)
```

---

## 4️⃣ Allocate Cards to a Library (Prepaid)

**Location:** Frappe Desk → Voucher Allocation → New

```
Field                       Value
───────────────────────────────────────
Allocation Type            Allocate
Batch                      [Your batch]
Customer                   Cairo Library
Sale Model                 Prepaid
Status                     Draft (auto)
```

**Next:** Click "Fill Cards" → Enter 100 → Click Fill

**Then:** Click "Submit Allocation" → Confirm

```
Result:
  ✓ 100 cards change status: Available → Allocated
  ✓ Invoice created: 100 × $10 × 90% commission = $900
  ✓ Library owes you $900
  ✓ Status becomes: Completed
```

---

## 5️⃣ Allocate Cards (Consignment - Monthly Invoicing)

**Same as #4, but:**

```
Sale Model                 Consignment  (not Prepaid)
```

**Result:**
- No invoice yet
- Cards allocated to library
- Invoiced monthly (1st of month) for redeemed cards only

---

## 6️⃣ Handle Library Returns (Prepaid)

**Location:** Voucher Allocation → New

```
Allocation Type            Return
Customer                   Cairo Library
Batch                      [original batch]
Sale Model                 Prepaid
```

**Fill Cards:** 25 (how many they're returning)

**Submit**

```
Result:
  ✓ 25 cards: Allocated → Available
  ✓ Credit Note created: -$225 refund
  ✓ Cairo Library balance: $900 - $225 = $675 owed
```

---

## 7️⃣ Void a Broken Card

**Location:** Voucher Card → [Search VCH-000001]

**Click** "Void Card" button → Enter reason → Confirm

```
Result:  Status: Available/Allocated → Void (permanent)
         Card cannot be used or allocated
```

---

## 8️⃣ Void Entire Batch

**Location:** Voucher Batch → [Generated or Active] → "Void Batch" button

**Enter reason:** "Damaged shipment"

```
Result:  All non-final cards → Void
         Batch status → Closed
         No more allocations allowed (batch is done)
```

---

## 9️⃣ Check Sales by Library Report

**Location:** Frappe Desk → Sales by Library Report

**Use to:** See revenue per library this month

**Columns:**
- Library name
- Redeemed count
- Face Value total
- Commission (what you paid them)
- Net Revenue (what you kept)

**Example:**
```
Cairo       30 cards   $300   -$30 (10%)   $270 ✓
Alexandria  50 cards   $500   -$50 (10%)   $450 ✓
```

---

## 🔟 Check Batch Performance Report

**Location:** Frappe Desk → Batch Performance Report

**Use to:** See health of each batch

**Columns:**
- Batch name
- Total cards / Available / Allocated / Redeemed / Void / Expired
- Redemption Rate %
- Days until season expires

**Example:**
```
VBATCH-001  1000/100/300/550/40/10   55% redeemed   45 days left
VBATCH-002  500/50/150/280/20/0      56% redeemed   45 days left
```

**What it means:**
- High % = Good batch
- Low % = Check if it's targeting wrong market
- Days left = Plan next batch before season ends

---

## 1️⃣1️⃣ Check Consignment Reconciliation Report

**Location:** Frappe Desk → Consignment Reconciliation Report

**Use to:** See what consignment libraries owe you

**Columns:**
- Library
- Allocated count
- Redeemed count
- Uninvoiced count (cards sold since last invoice)
- Amount Due (calculated with commission)

**Example:**
```
Cairo       100  35  0   $315  (Invoiced Feb 1)
Alexandria  80   50  5   $225  (10 cards pending invoice)
Damascus    60   25  3   $135  (3 cards pending invoice)
```

---

## 1️⃣2️⃣ Check Security Audit Report

**Location:** Frappe Desk → Security Audit Report

**Use to:** Detect fraud patterns

**Columns:**
- Player name
- IP address
- Failure type (INVALID_PIN, ALREADY_REDEEMED, etc.)
- Attempt count
- First/Last attempt times

**Red Flags:**
```
student_hacker    192.168.1.100   INVALID_PIN   23 attempts in 10 min   🚨 BRUTE FORCE
(unknown)         203.0.113.42    INVALID_PIN   8 attempts in 5 min     ⚠️ SUSPICIOUS
```

**Action:** Block IP or investigate account

---

## 1️⃣3️⃣ Check Voucher Redemption Log (Audit Trail)

**Location:** Frappe Desk → Voucher Redemption Log

**Use to:** See every PIN entry attempt

**Columns:**
- Status (Success ✓ or error code)
- Card serial (or empty if PIN invalid)
- Player name
- PIN masked (****last4)
- Library
- Timestamp

**Example:**
```
Success        VCH-000001  ahmed_2024   ****2k9x   Cairo   Feb 10 2:30 PM
INVALID_PIN    (empty)     farah_2024   ****xxxx   Cairo   Feb 10 2:35 PM
ALREADY_REDEEMED VCH-000002 omar_2024   ****8b3q   Cairo   Feb 10 2:40 PM
```

**Filters:**
- Date range
- Player name
- Library
- Status (Success, INVALID_PIN, etc.)

---

## 1️⃣4️⃣ Check Why Student PIN Failed

**Scenario:** Student says "My card doesn't work!"

**Find in Redemption Log:**
1. Filter: Player = student name, Date = today
2. Look at Status column
3. Match against this table:

| Status | Meaning | Action |
|--------|---------|--------|
| INVALID_PIN | PIN doesn't exist | Try different card |
| ALREADY_REDEEMED | Card used already | Need new card |
| NOT_ALLOCATED | Admin didn't allocate | Wait, admin is allocating |
| EXPIRED | Card timed out | Season ended, need new card |
| VOID | Admin broke it | Need new card |
| ALL_GRANTS_OWNED | Has all products already | Don't need this card |
| ALREADY_OWNED | Has THIS product | Try different product on same card |
| RATE_LIMITED | Too many tries | Wait 1 hour |

---

## 1️⃣5️⃣ Verify Invoice Was Created

**After creating Prepaid allocation:**

1. Go to **Sales Invoice** list
2. Filter: Customer = [library name]
3. Check today's date
4. See invoice with correct amount

**Expected:**
```
Customer: Cairo Library
Items: 100 lines (one per card)
Total: 100 × $10 × (100% - 10% comm) = $900
Status: Submitted (ready to pay)
```

---

## 1️⃣6️⃣ Track Library Payments

**Location:** Sales Invoice list

**Filter:**
- Customer = [Library name]
- Date range

**Columns:**
- Invoice #
- Amount due
- Status (Unpaid, Paid, Partially Paid)
- Due date

**Follow up if:**
- Status = Unpaid AND date > 30 days old

---

## 1️⃣7️⃣ Set Up Commission for a Library

**Location:** Frappe Desk → Customer → [Library name]

**Scroll down, find:**

```
Voucher Requires Approval       Yes/No
Voucher Commission Type         Percentage or Fixed Amount
Voucher Commission Value        10% or $2.00
```

**Examples:**
- Cairo: 10% (library gets $1 per $10 card)
- Alexandria: $1.50 flat (library gets $1.50 per $10 card)
- Damascus: 0% (library gets $0, admin keeps all)

**When to change:**
- Every library can have different rate
- Change applies to FUTURE allocations
- Old invoices never change

---

## 1️⃣8️⃣ Override Commission for One Batch/Product

**Location:** Voucher Batch → Batch Grants (child table)

**For each product grant, optionally:**

```
Commission Type        Percentage
Commission Value       15%  (overrides library default 10%)
```

**Result:**
- This batch gives 15% commission (not 10%)
- Only for this batch+product combination
- Other batches use library default

---

## 1️⃣9️⃣ Plan for Season Expiration

**When season ends, allocated cards AUTOMATICALLY expire at 1:05 AM daily**

```
Season end date:  May 31
Expiration date:  June 1 at 1:05 AM
→ All Available/Allocated cards → Expired
→ Redeemed cards stay Redeemed (finalized)
```

**Before season ends:**
1. Check Batch Performance report
2. See "Days until season expires"
3. Plan new batch for next season
4. Don't let cards pile up unsold

---

## 2️⃣0️⃣ Manually Run Consignment Billing (Debug Only)

**If monthly invoice didn't create:**

Contact dev to run:
```bash
bench execute memora_admin.tasks.consignment_billing.generate_monthly_invoices
```

**What it does:**
- Creates invoices for all redeemed consignment cards from last month
- One invoice per library
- Marks cards as invoiced (prevents duplicates)

---

## 🔑 Key Numbers to Remember

| Task | Limit/Time |
|------|-----------|
| PIN Entry Attempts | 5 per player/hour, 20 per IP/hour |
| Export File Lifetime | 30 days (auto-deleted) |
| Consignment Invoice | 1st of month, 2:00 AM |
| Season Expiration Check | Daily 1:05 AM |
| Report Cache | 5 minutes (refresh to see latest) |
| Card Status Transitions | Terminal = Redeemed, Void, Expired (no undo) |

---

## 🚨 Emergency Contacts

| Issue | Action |
|-------|--------|
| PIN secret not configured | Contact dev: setup voucher_hmac_secret |
| Database error on generation | Check disk space, contact server admin |
| Scheduled job not running | Check server logs, contact dev |
| Need to bulk expire cards | Contact dev with batch name and date |
| Wrong invoice amount | Create credit note + adjustment invoice |

---

## 📋 Daily Checklist

- [ ] Check **Security Audit report** for fraud (5 min)
- [ ] Check **Consignment Reconciliation** for money owed (5 min)
- [ ] Follow up on overdue **Sales Invoices** (5 min)
- [ ] Review **Voucher Redemption Log** for errors/issues (10 min)

---

## 📱 Mobile Student Experience (For Admin Awareness)

**What students see:**

```
App: "Unlock Content with Code"
     ↓
Student: Enter 14-digit PIN from card
     ↓
App: "Previewing... Premium Plan 3 Months"
     ↓
Student: Click "Redeem" to unlock
     ↓
App: "Success! Content unlocked. Enjoy!"
     OR
     "Error: Card already used" (red banner)
```

**You monitor this via Redemption Log**

---

## 🎓 Training Checklist (First Time Setup)

- [ ] Read full guide (1 hour)
- [ ] Create test batch (10 min)
- [ ] Generate test cards (5 min)
- [ ] Export CSV (2 min)
- [ ] Create test allocations (15 min)
- [ ] Review test reports (15 min)
- [ ] Check test Redemption Log (10 min)
- [ ] Create commission rules (10 min)
- [ ] Test different approval workflows (20 min)

**Total training time:** ~90 minutes

---

## 💡 Tips & Best Practices

1. **Always use "Fill Cards" button** (auto-fills Available/Allocated cards correctly)
2. **Set requires_approval=Yes for new libraries** (you review first allocation)
3. **Run reports at month-end** (planning for next month)
4. **Check Security Audit weekly** (catch fraud early)
5. **Keep backup of exported CSVs** (in case reprinting needed)
6. **Plan next batch 2 weeks before season ends** (no gaps in supply)
7. **Void individual cards immediately if damaged** (don't wait to batch void)
8. **Set all commissions upfront** (don't change mid-season if possible)

---

**Bookmark this page. Reference it whenever you need a quick lookup!**

*Last updated: February 14, 2026*

