# Admin Onboarding Checklist - Memora Voucher System

**Goal:** Get a new admin comfortable with phases 33-38 in ~3 hours

---

## Prerequisites (Before Starting)

- [ ] You have access to Frappe Desk (test environment)
- [ ] You have "System Manager" role (needed for vouchers)
- [ ] You've read the system email welcome guide
- [ ] You have admin contact info (in case you get stuck)

**Time estimate:** 15 minutes

---

## Module 1: Understanding the Big Picture (20 minutes)

### 1.1 Watch/Read the Overview

- [ ] Read: `SYSTEM_ARCHITECTURE_OVERVIEW.md` (sections 1-3)
- [ ] Take notes on: "What are the 6 phases?"
- [ ] Understand: How does a card go from creation to redemption?

**Your turn:**
- [ ] Write down in your own words: "Why do we need a voucher system?"
- [ ] List the 3 financial models (Prepaid, Consignment, Free)

### 1.2 Understand Your Role

**Admin responsibilities:**
- [ ] Create batches (decide: how many cards, for which products?)
- [ ] Generate cards (secure PINs)
- [ ] Allocate to libraries (who gets how many cards?)
- [ ] Monitor for fraud (check Security Audit report weekly)
- [ ] Track money (invoice libraries, collect commission)
- [ ] Help students (resolve PIN errors)

**What you DON'T do:**
- [ ] Students enter PINs themselves (you don't do this)
- [ ] Print physical cards (you send CSV to print vendor)
- [ ] Process payments (library sends payment to company billing team)

---

## Module 2: Hands-On with Test Data (60 minutes)

### 2.1 Create Your First Batch

**Time: 10 minutes**

1. Go to **Frappe Desk** → Search for **"Voucher Batch"**
2. Click **+ New**
3. Fill in test data:
   ```
   Batch Name:     Test Batch March 2026
   Quantity:       100  (small batch for testing)
   PIN Length:     14
   Face Value:     $10.00 JOD
   ```
4. Click **Add Row** in "Batch Grants"
5. Select any **Product Grant** (e.g., "Premium Plan 3 Months")
6. Click **Save**

**Status should be:** Draft

**What you just did:** Created a batch definition (no cards yet)

### 2.2 Generate Cards

**Time: 5 minutes**

1. Click **Generate Cards** button (blue)
2. Click **Yes** to confirm
3. Watch the progress bar (takes ~20 seconds for 100 cards)
4. You'll see notification when done

**What happens behind scenes:**
- 100 card records created in database
- Each gets unique PIN hashed with HMAC-SHA256
- Encrypted CSV file created

**Check it worked:**
- [ ] Status changed to "Generated"
- [ ] See notification "Cards generated successfully"

### 2.3 Download & Review Export

**Time: 5 minutes**

1. Click **Export for Print** button (green)
2. Click **Download**
3. Open CSV in Excel or text editor
4. Look at first 5 rows:
   ```
   serial_no,pin,product_names,face_value
   VCH-000001,4a7f2k9x8b3q,Premium Plan 3 Months,$10.00
   VCH-000002,9m2x5l8k3j1w,Premium Plan 3 Months,$10.00
   ...
   ```

**What you just saw:** Every card's serial number and PIN (this gets printed on physical cards)

**Security note:** This file is encrypted and auto-deletes after 30 days

### 2.4 Create a Test Allocation

**Time: 10 minutes**

1. Go to **Voucher Allocation** → **+ New**
2. Fill in:
   ```
   Allocation Type:  Allocate
   Batch:            Test Batch March 2026  (your batch from 2.1)
   Customer:         [Pick any library, e.g., "Cairo Library"]
   Sale Model:       Prepaid
   ```
3. Click **Save** (status = Draft)
4. Click **Fill Cards** button
5. Enter Quantity: 50
6. Click **Fill**

**What happened:** 50 cards added to the Allocation Cards child table

**Next step:** Submit the allocation
1. Click **Submit Allocation** button
2. Click **Confirm**

**Status changes:** Draft → Completed

**Cards updated:**
- [ ] Go to **Voucher Card** list
- [ ] Filter: Batch = "Test Batch March 2026"
- [ ] See first 50 cards: Status = "Allocated"
- [ ] Library field = "Cairo Library"

### 2.5 Check Invoice Was Created

**Time: 5 minutes**

1. Go to **Sales Invoice** list
2. Filter by Customer = "Cairo Library"
3. Filter by date = today
4. Find the invoice created by your allocation

**Verify:**
- [ ] Customer: Cairo Library
- [ ] 50 line items (one per card)
- [ ] Total: 50 × $10 × (100% - commission%) = expected amount
- [ ] Status: Submitted

**This is what library needs to pay**

### 2.6 Handle a Return

**Time: 10 minutes**

1. Go to **Voucher Allocation** → **+ New**
2. Fill in:
   ```
   Allocation Type:  Return  (NOT Allocate this time)
   Customer:         Cairo Library  (same library)
   Batch:            Test Batch March 2026
   Sale Model:       Prepaid
   ```
3. Click **Save**
4. Click **Fill Cards** → Quantity: 10 → Fill
5. Click **Submit Allocation**

**Result:**
- [ ] 10 cards change back: Allocated → Available
- [ ] Credit Note created (refund for 10 cards)
- [ ] Library balance: Originally $500, now $400 owed

**What this teaches:** How returns work

### 2.7 Void a Card

**Time: 5 minutes**

1. Go to **Voucher Card** list
2. Search for "VCH-000075" (any card in your batch)
3. Click to open the card
4. Click **Void Card** button (red)
5. Enter reason: "Test - damaged card"
6. Click **Confirm**

**Result:**
- [ ] Card status: Allocated → Void
- [ ] Card cannot be redeemed
- [ ] Void_reason field: "Test - damaged card"

**Lesson:** Broken cards can be voided immediately

---

## Module 3: Understand the Reports (45 minutes)

### 3.1 Sales by Library Report

**Time: 10 minutes**

1. Go to **Sales by Library** report
2. Leave filters empty (see all libraries)
3. Review table:
   - [ ] See libraries listed
   - [ ] See "Redeemed" count (test: should be 0 since we haven't actually run redemptions)
   - [ ] See "Commission" column (money you pay to library)
   - [ ] See "Net Revenue" column (money you keep)

**Try different filters:**
- [ ] Filter by date: January 2026 (see historical data)
- [ ] Filter by Sale Model: Prepaid only
- [ ] Filter by Library: Cairo Library

**What this tells you:** Revenue per library this month

### 3.2 Batch Performance Report

**Time: 10 minutes**

1. Go to **Batch Performance** report
2. Review your test batch:
   - [ ] Total cards: 100
   - [ ] Available: 40 (100 - 50 allocated - 10 returned)
   - [ ] Allocated: 40 (50 allocated - 10 returned)
   - [ ] Redeemed: 0 (no students have redeemed yet in test env)
   - [ ] Void: 1 (the card you voided)
   - [ ] Expired: 0 (season not ended)
   - [ ] Redemption Rate: 0%
   - [ ] Days until season expires: (depends on your season)

**What this tells you:** Health of each batch

**Red flags to look for:**
- [ ] Redemption rate <10% after 2 weeks = Not selling well
- [ ] Days until expiration <7 = Start planning next batch!

### 3.3 Consignment Reconciliation Report

**Time: 10 minutes**

1. Go to **Consignment Reconciliation** report
2. Filter by date: This month
3. Observe: Mostly empty (no consignment allocations in test)

**Create a consignment allocation to test:**
1. Go to **Voucher Allocation** → **+ New**
2. Set: Allocation Type = Allocate, Batch = your test batch, Sale Model = **Consignment** (key difference)
3. Customer = Alexandria Library
4. Fill Cards: 30
5. Click Submit

**Back to report:**
1. Refresh **Consignment Reconciliation** report
2. See Alexandria Library listed:
   - [ ] Allocated: 30
   - [ ] Redeemed: 0 (no students redeemed yet)
   - [ ] Uninvoiced: 0 (nothing to invoice since nothing redeemed)
   - [ ] Amount Due: $0 (no invoices yet)

**What this tells you:** Which libraries owe you money for redeemed consignment cards

**Note:** Invoices created monthly (1st of month), not immediately

### 3.4 Security Audit Report

**Time: 10 minutes**

1. Go to **Security Audit** report
2. Filter by date: This month
3. Observe: Mostly empty (no failed redemptions in test env yet)

**What would show here:**
- Failed PIN attempts by student
- Failed attempts by IP address
- Pattern detection (brute force, etc.)

**Learn the red flags:**
- Same IP, 10+ attempts in 1 hour → Brute force attack
- Multiple IPs, same player, high count → Account compromised
- Specific card, multiple attempts → Card code leaked

---

## Module 4: Understand Commission (20 minutes)

### 4.1 Commission Priority Chain

**Read:** `ADMIN_GUIDE_PHASES_33-38.md` → Section "Phase 37: Financial Integration" → Subsection "How Commission Works"

**Understand:**

```
Level 1: Batch Grant Override (product-specific)
Level 2: Library Default (customer-specific)
Level 3: Zero (no commission)
```

### 4.2 Set Commission for a Test Library

1. Go to **Customer** → Search "Cairo Library"
2. Scroll down to find:
   - [ ] **Voucher Commission Type** (Percentage or Fixed Amount)
   - [ ] **Voucher Commission Value** (e.g., 10%)
3. If fields don't exist, contact admin (Phase 33 setup issue)
4. Set commission:
   - Type: Percentage
   - Value: 10%
5. Click **Save**

### 4.3 Calculate Expected Invoice Amount

**For your test allocation:**
- 50 cards × $10 face value = $500
- Commission: 10% = $50
- Library receives: $50
- Admin keeps: $450

**Verify:**
1. Go to **Sales Invoice** for Cairo Library
2. See total amount
3. Confirm it matches calculation

**What this teaches:** How commission is calculated and applied

---

## Module 5: Understand Errors & Troubleshooting (30 minutes)

### 5.1 Read Error Codes

- [ ] Read: `ADMIN_GUIDE_PHASES_33-38.md` → Section "Phase 36" → Subsection "What Can Go Wrong (Error Codes)"
- [ ] Create a cheat sheet with error codes you'll need to know

**Key ones:**
- INVALID_PIN = "PIN doesn't exist"
- ALREADY_REDEEMED = "Card used already"
- NOT_ALLOCATED = "Admin didn't allocate yet"
- RATE_LIMITED = "Too many attempts, wait 1 hour"

### 5.2 Walk Through a Real Scenario

**Scenario:** Student says "My card doesn't work!"

**Your detective process:**
1. [ ] Go to **Voucher Redemption Log**
2. [ ] Filter: Player = student name, Date = today
3. [ ] Find their last attempt
4. [ ] Check Status column
5. [ ] Match against error code table
6. [ ] Provide appropriate help

**Practice with test data:**
(In real environment, you'd see actual redemption attempts)

### 5.3 Troubleshooting Guide

- [ ] Read: `ADMIN_GUIDE_PHASES_33-38.md` → Section "Troubleshooting Guide"
- [ ] Bookmark this section (you'll reference it)

**Most common issues:**
- [ ] "Cards not generating" → Check HMAC secret configured
- [ ] "Invoice never created" → Check allocation status is "Completed"
- [ ] "Student PIN keeps failing" → Check Redemption Log for error
- [ ] "Commission is wrong" → Check library default vs. batch override

---

## Module 6: Key Concepts & Mental Models (20 minutes)

### 6.1 The Three Financial Models

- [ ] **Prepaid:** Invoice library immediately for all 100 cards (library pays upfront, risk is on them)
- [ ] **Consignment:** Invoice library monthly for ONLY redeemed cards (library risk-free)
- [ ] **Free:** (Rarely used) No invoicing, promotional

**When to use each:**
- Prepaid: Trusted partners who can afford upfront cost
- Consignment: New partners, risk-averse
- Free: Promotions, fundraising, student scholarships

### 6.2 Card Lifecycle (State Machine)

- [ ] Memorize the 5 states: Available, Allocated, Redeemed, Void, Expired
- [ ] Know which are terminal (Redeemed, Void, Expired = can't change)
- [ ] Know transitions (Available → Allocated OR Void, etc.)

### 6.3 The PIN Security Model

- [ ] PINs use `secrets` module (cryptographically secure)
- [ ] PINs are hashed with HMAC-SHA256 (one-way encryption)
- [ ] HMAC is compared using `hmac.compare_digest()` (timing-safe)
- [ ] Even if database hacked, PINs cannot be extracted

**This is why:** Admins cannot see plaintext PINs after batch generated

### 6.4 Rate Limiting (Fraud Prevention)

- [ ] Students max 5 failed attempts per hour
- [ ] IP max 20 failed attempts per hour
- [ ] Prevents brute force (guessing PINs)
- [ ] Auto-expires (no cleanup job needed)

---

## Module 7: Hands-On Practice Scenarios (60 minutes)

**Do these exercises to solidify your understanding**

### Scenario A: Launch a New Voucher Program (20 minutes)

**Setup:**
1. Create batch: "English Program Spring 2026"
   - Quantity: 200
   - PIN Length: 14
   - Face Value: $15
   - Products: [Select 2-3 products]

2. Generate cards

3. Export CSV (pretend sending to printer)

4. Allocate to 2 libraries:
   - Damascus Library: 100 cards (Prepaid)
   - Aleppo Library: 80 cards (Consignment)

5. Check that:
   - [ ] Invoice created for Damascus (Prepaid)
   - [ ] No invoice yet for Aleppo (Consignment)
   - [ ] Cards updated to Allocated status
   - [ ] Batch automatically activated

**Time check:** 20 minutes

### Scenario B: Handle Fraud Pattern (15 minutes)

**Setup (from logs, pretend scenario):**
- IP 203.0.113.50 has 12 failed INVALID_PIN attempts in 30 minutes
- All attempts from Cairo Library location
- Probably someone trying to guess PINs

**Your action:**
1. [ ] Go to **Security Audit** report
2. [ ] Filter: Failure Type = INVALID_PIN
3. [ ] Observe pattern
4. [ ] Document findings: "Brute force attack from IP 203.0.113.50"
5. [ ] Contact library manager: "Someone at your location is trying to guess card codes"
6. [ ] Recommend: Block IP or tighten library security

### Scenario C: Process Month-End Financials (15 minutes)

**Setup:**
- Multiple allocations with different models (Prepaid, Consignment)
- Some cards redeemed
- Some cards returned

**Your actions:**
1. [ ] Run **Sales by Library** report (date = last month)
2. [ ] See revenue per library
3. [ ] Run **Consignment Reconciliation** report
4. [ ] See which libraries have pending invoices
5. [ ] Run **Batch Performance** report
6. [ ] Identify best/worst performing batches
7. [ ] Document findings in spreadsheet (for monthly business review)

### Scenario D: Resolve Customer Complaint (10 minutes)

**Scenario:** Library says "I allocated 100 cards but only received invoice for 50"

**Your investigation:**
1. [ ] Go to **Voucher Allocation** list
2. [ ] Search for allocations to that library
3. [ ] Check: Did they create 2 allocations? (50 + 50 = 100)
4. [ ] Verify: Each allocation has separate invoice
5. [ ] Total invoices = correct amount

**Resolution:** "You created 2 allocations of 50 each. You have 2 invoices. Total is correct."

---

## Module 8: Your Ongoing Tasks (5 minutes)

### Daily Checklist (5 minutes)
- [ ] Check **Security Audit** report for fraud patterns
- [ ] Respond to student support tickets

### Weekly Checklist (15 minutes)
- [ ] Run **Sales by Library** report (see weekly progress)
- [ ] Check **Batch Performance** (redemption rates healthy?)
- [ ] Follow up on overdue **Sales Invoices**

### Monthly Checklist (30 minutes)
- [ ] Run all 4 reports for business review
- [ ] Verify consignment invoices created on 1st of month
- [ ] Plan next batch (if current batch expiring soon)
- [ ] Reconcile: Cards allocated = Cards in libraries
- [ ] Follow up on library commission payments

### Quarterly Checklist (1 hour)
- [ ] Review all batches launched this quarter
- [ ] Analyze: Best/worst performing batches
- [ ] Decide: Adjust target libraries, marketing, or pricing?
- [ ] Plan next quarter's voucher strategy

---

## Knowledge Check (10 minutes)

**Answer these to confirm you understand:**

1. **What's the difference between Prepaid and Consignment?**
   - [ ] You can explain it in your own words

2. **What happens when you click "Generate Cards"?**
   - [ ] You understand the background job creates 1000 card records with HMAC hashes

3. **Why are PINs masked in the Redemption Log?**
   - [ ] You know it's for security (even if database is hacked, attackers see ****2k9x not full PIN)

4. **What is "rate limiting" and why do we need it?**
   - [ ] You understand it prevents brute force attacks (5 attempts/hour per player)

5. **Which fields must be set on Customer before allocation?**
   - [ ] You know: voucher_requires_approval and voucher_commission_type/value

6. **What is a "terminal state" for a card?**
   - [ ] You understand: Redeemed, Void, Expired are final (can't change)

7. **When are consignment invoices created?**
   - [ ] You know: Monthly on 1st of month, 2:00 AM (for previous month's redeemed cards)

8. **How do you detect fraud?**
   - [ ] You know: Check Security Audit report for suspicious patterns

---

## Certification Completion

**Congratulations!** You've completed the onboarding.

**Before you handle production allocations:**

- [ ] You've successfully created test batch and allocation
- [ ] You've reviewed all 4 reports
- [ ] You understand the 3 financial models
- [ ] You can explain card lifecycle (Available → Allocated → Redeemed)
- [ ] You know the error codes and how to help students
- [ ] You know your weekly/monthly checklist
- [ ] An existing admin has reviewed your test work
- [ ] An existing admin has approved you for production access

**Now you're ready to:**
- [ ] Create production batches
- [ ] Manage real library allocations
- [ ] Handle customer support tickets
- [ ] Monitor fraud and financial health
- [ ] Prepare monthly business reports

---

## Quick Reference Guide (Keep Handy)

**Bookmark these:**
1. `ADMIN_QUICK_REFERENCE.md` — 20 most common tasks
2. `ADMIN_GUIDE_PHASES_33-38.md` → Troubleshooting section
3. This checklist (revisit when confused)

**Your admin contact:**
- [ ] Name: _______________________
- [ ] Email: _______________________
- [ ] Phone: _______________________

**Your test environment:**
- [ ] Frappe URL: _______________________
- [ ] Your username: _______________________
- [ ] Test password: _______________________

---

## Time Summary

| Module | Est. Time | Status |
|--------|-----------|--------|
| 1. Big Picture | 20 min | ☐ Complete |
| 2. Hands-On Testing | 60 min | ☐ Complete |
| 3. Reports | 45 min | ☐ Complete |
| 4. Commission | 20 min | ☐ Complete |
| 5. Errors & Troubleshooting | 30 min | ☐ Complete |
| 6. Key Concepts | 20 min | ☐ Complete |
| 7. Practice Scenarios | 60 min | ☐ Complete |
| 8. Knowledge Check | 10 min | ☐ Complete |
| **TOTAL** | **~3.5 hours** | ☐ ALL DONE |

---

**Onboarding Completed:** _____________ (Date)

**Verified By:** ________________________ (Admin Name)

**Notes/Questions:** ___________________________________________________________

____________________________________________________________________________

---

**Welcome to the team! You're now a Memora Voucher System Admin.** 🎉

If you have questions at any point, refer to the documentation or contact your admin lead.

Happy voucher managing!
