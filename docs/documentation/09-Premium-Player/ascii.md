Monetized Access Feature
├── 1) Core References
│   ├── Player
│   │   └── Memora Player Profile
│   │
│   ├── Plan
│   │   └── Study Plan
│   │       └── belongs to one Season
│   │
│   ├── Season
│   │   ├── start_at
│   │   └── end_at
│   │
│   ├── ERPNext Item
│   │   └── one dedicated item per sellable thing
│   │
│   └── ERPNext Sales Invoice
│       └── accounting record only
│
├── 2) Plan Premium Domain
│   ├── 2.1) Memora Plan Premium
│   │   ├── player                          -> Memora Player Profile
│   │   ├── plan                            -> Study Plan
│   │   ├── status                          = active | revoked
│   │   ├── source_type                     = purchase | voucher | admin
│   │   ├── purchase_ref                    -> Memora Plan Premium Purchase   (optional)
│   │   ├── voucher_ref                     -> Memora Voucher Redemption      (optional)
│   │   ├── granted_by                      -> User                           (optional)
│   │   ├── granted_at
│   │   ├── revoked_at                      (optional)
│   │   └── notes                           (optional)
│   │
│   ├── 2.2) Memora Plan Premium Purchase
│   │   ├── player                          -> Memora Player Profile
│   │   ├── plan                            -> Study Plan
│   │   ├── status                          = pending | paid | failed | cancelled | refunded
│   │   ├── amount
│   │   ├── currency
│   │   ├── erpnext_item_code               -> Item
│   │   ├── erpnext_invoice                 -> Sales Invoice
│   │   ├── payment_gateway                 (optional)
│   │   ├── payment_reference               (optional)
│   │   ├── created_at
│   │   ├── paid_at                         (optional)
│   │   ├── refunded_at                     (optional)
│   │   └── notes                           (optional)
│   │
│   ├── 2.3) Memora Voucher Redemption
│   │   ├── voucher_card
│   │   ├── player                          -> Memora Player Profile
│   │   ├── status                          = success | reversed
│   │   ├── redeemed_at
│   │   ├── redeemed_plan                   -> Study Plan
│   │   ├── premium_ref                     -> Memora Plan Premium           (optional)
│   │   ├── notes                           (optional)
│   │   └── reversed_at                     (optional)
│   │
│   ├── 2.4) Plan Premium Meaning
│   │   ├── premium is scoped to one plan only
│   │   ├── premium unlocks everything inside that plan
│   │   ├── premium overrides any paid gate inside that plan
│   │   ├── premium includes future content added to that plan
│   │   ├── premium does not move to another plan
│   │   └── premium becomes unusable if player changes plan
│   │
│   ├── 2.5) Plan Premium Validity
│   │   ├── no expires_at field stored on premium
│   │   ├── validity is computed
│   │   └── premium is usable if:
│   │       ├── premium.status == active
│   │       ├── premium.plan == player.current_plan
│   │       └── now <= premium.plan.season.end_at
│   │
│   ├── 2.6) Plan Premium Prevention Rules
│   │   ├── if usable premium already exists for (player, plan):
│   │   │   ├── reject new purchase
│   │   │   ├── reject voucher redemption
│   │   │   └── reject admin grant
│   │   ├── no stacking
│   │   ├── no replace
│   │   └── no duplicate usable premium
│   │
│   └── 2.7) Plan Premium State Rules
│       ├── stored status:
│       │   ├── active
│       │   └── revoked
│       ├── revoked means:
│       │   └── manual revoke only
│       └── not stored as status:
│           ├── expired
│           └── unusable due to plan change or season end
│
├── 3) Paid Live Event Domain
│   ├── 3.1) Memora Live Challenge Event
│   │   ├── event_name
│   │   ├── status
│   │   ├── plan                           -> Study Plan
│   │   ├── is_paid
│   │   ├── price
│   │   ├── currency
│   │   ├── erpnext_item_code              -> Item
│   │   ├── eligible_plans[]
│   │   ├── capacity
│   │   ├── exam_start_ts
│   │   └── exam_end_ts
│   │
│   ├── 3.2) Memora Live Event Purchase
│   │   ├── player                         -> Memora Player Profile
│   │   ├── event                          -> Memora Live Challenge Event
│   │   ├── plan_snapshot                  -> Study Plan
│   │   ├── status                         = pending | paid | failed | cancelled | refunded
│   │   ├── amount
│   │   ├── currency
│   │   ├── erpnext_item_code              -> Item
│   │   ├── erpnext_invoice                -> Sales Invoice
│   │   ├── payment_gateway                (optional)
│   │   ├── payment_reference              (optional)
│   │   ├── created_at
│   │   ├── paid_at                        (optional)
│   │   ├── refunded_at                    (optional)
│   │   └── notes                          (optional)
│   │
│   ├── 3.3) Memora Live Event Access
│   │   ├── player                         -> Memora Player Profile
│   │   ├── event                          -> Memora Live Challenge Event
│   │   ├── status                         = active | revoked | refunded
│   │   ├── access_type                    = purchase | voucher | admin
│   │   ├── purchase_ref                   -> Memora Live Event Purchase     (optional)
│   │   ├── voucher_ref                    -> Memora Voucher Redemption      (optional)
│   │   ├── granted_by                     -> User                           (optional)
│   │   ├── granted_at
│   │   ├── revoked_at                     (optional)
│   │   └── notes                          (optional)
│   │
│   ├── 3.4) Memora Live Challenge Participation
│   │   ├── event                          -> Memora Live Challenge Event
│   │   ├── player                         -> Memora Player Profile
│   │   ├── joined_at
│   │   ├── submitted_at
│   │   ├── score
│   │   ├── rank
│   │   └── xp_awarded
│   │
│   ├── 3.5) Paid Event Meaning
│   │   ├── if event.is_paid = 0
│   │   │   └── normal access rules apply
│   │   ├── if event.is_paid = 1
│   │   │   └── require either:
│   │   │       ├── usable plan premium on same plan
│   │   │       └── active event access for that player/event
│   │   └── premium wins over event-level paid gate
│   │
│   └── 3.6) Event Access State Rules
│       ├── active = event entitlement granted
│       ├── revoked = manually removed
│       └── refunded = purchase reversed
│
├── 4) Voucher Domain
│   ├── Voucher Card
│   │   └── original voucher asset/code
│   │
│   ├── Voucher Redemption
│   │   ├── may create Plan Premium
│   │   ├── may create Live Event Access
│   │   └── always keeps audit trail of who redeemed what
│   │
│   └── Voucher Rule
│       ├── voucher card is not the entitlement itself
│       └── redemption event is the source reference
│
├── 5) ERP / Accounting Layer
│   ├── Sales Invoice is accounting only
│   ├── Item is sellable representation in ERPNext
│   ├── one dedicated Item per paid live event
│   ├── one dedicated Item per plan premium product
│   ├── Purchase docs reference Sales Invoice
│   └── Access docs never use Sales Invoice as source of truth
│
├── 6) Runtime Access Resolution
│   ├── 6.1) For any gated resource inside a plan
│   │   ├── first check: is resource inside player.current_plan?
│   │   ├── second check: does player have usable Memora Plan Premium?
│   │   │   └── if yes -> allow immediately
│   │   ├── third check: if resource is specifically paid
│   │   │   └── check direct resource access
│   │   └── else deny
│   │
│   ├── 6.2) For live event join
│   │   ├── check event status
│   │   ├── check eligible_plans
│   │   ├── check event belongs to player.current_plan
│   │   ├── if usable plan premium exists
│   │   │   └── allow
│   │   ├── else if event.is_paid = 1
│   │   │   └── require active Memora Live Event Access
│   │   └── then continue atomic join
│   │
│   └── 6.3) Effective Premium Rule
│       ├── status == active
│       ├── plan == player.current_plan
│       └── now <= plan.season.end_at
│
├── 7) Creation Flows
│   ├── 7.1) Plan Premium via Payment Gateway
│   │   ├── create Memora Plan Premium Purchase (pending)
│   │   ├── create ERPNext Sales Invoice
│   │   ├── wait for payment success
│   │   ├── mark purchase as paid
│   │   └── create Memora Plan Premium
│   │
│   ├── 7.2) Plan Premium via Voucher
│   │   ├── redeem voucher
│   │   ├── create Memora Voucher Redemption
│   │   └── create Memora Plan Premium
│   │
│   ├── 7.3) Plan Premium via Admin
│   │   └── create Memora Plan Premium directly
│   │
│   ├── 7.4) Live Event Access via Payment Gateway
│   │   ├── create Memora Live Event Purchase (pending)
│   │   ├── create ERPNext Sales Invoice
│   │   ├── wait for payment success
│   │   ├── mark event purchase as paid
│   │   └── create Memora Live Event Access
│   │
│   ├── 7.5) Live Event Access via Voucher
│   │   ├── redeem voucher
│   │   ├── create Memora Voucher Redemption
│   │   └── create Memora Live Event Access
│   │
│   └── 7.6) Live Event Access via Admin
│       └── create Memora Live Event Access directly
│
├── 8) Prevention / Validation Rules
│   ├── 8.1) Plan Premium
│   │   ├── one active premium per (player, plan)
│   │   ├── reject grant if usable premium already exists
│   │   ├── source_type=purchase => purchase_ref required
│   │   ├── source_type=voucher  => voucher_ref required
│   │   └── source_type=admin    => granted_by required
│   │
│   ├── 8.2) Plan Premium Purchase
│   │   ├── reject create if usable premium already exists
│   │   ├── no duplicate open purchase per (player, plan)
│   │   ├── paid => paid_at required
│   │   └── refunded => refunded_at required
│   │
│   ├── 8.3) Voucher Redemption
│   │   ├── one successful redemption per voucher card
│   │   ├── reject redeem if usable premium already exists
│   │   └── reversed => reversed_at required
│   │
│   └── 8.4) Live Event Access
│       ├── one active access per (player, event)
│       ├── reject direct purchase if usable plan premium already covers it
│       └── if source is purchase => purchase_ref required
│
├── 9) Plan Change Interaction
│   ├── when player changes plan
│   │   ├── old plan premium record remains historically
│   │   ├── but becomes unusable automatically
│   │   ├── old event access and other old-plan entitlements are removed by existing logic
│   │   └── plan history snapshot is stored elsewhere
│   │
│   └── important:
│       └── Memora Plan Premium status does not become revoked on plan change
│
└── 10) Source of Truth Summary
    ├── Plan Premium doc
    │   └── source of truth for plan-wide unlock
    │
    ├── Live Event Access doc
    │   └── source of truth for direct paid event unlock
    │
    ├── Purchase docs
    │   └── source of truth for payment lifecycle
    │
    ├── Voucher Redemption
    │   └── source of truth for voucher usage event
    │
    ├── Sales Invoice
    │   └── source of truth for accounting only
    │
    └── Computed validity
        └── source of truth for whether premium is currently usable


والنسخة المختصرة جدًا للعلاقات:
Study Plan
├── has one Season
├── has many Plan Premiums
└── has many Live Challenge Events

Memora Plan Premium
├── belongs to one Player
├── belongs to one Plan
├── may come from one Plan Premium Purchase
└── may come from one Voucher Redemption

Memora Plan Premium Purchase
├── belongs to one Player
├── belongs to one Plan
└── references one ERPNext Sales Invoice

Memora Live Challenge Event
├── belongs to one Plan
├── may be free or paid
└── has many Live Event Access records

Memora Live Event Access
├── belongs to one Player
├── belongs to one Event
├── may come from one Live Event Purchase
└── may come from one Voucher Redemption