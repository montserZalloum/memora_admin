 Sessions (2)

  ┌────────┬──────────────────────────┬─────────────┐
  │ Method │           Path           │ Description │
  ├────────┼──────────────────────────┼─────────────┤
  │        │                          │ Current     │
  │ GET    │ /api/v1/sessions/current │ active      │
  │        │                          │ session     │
  ├────────┼──────────────────────────┼─────────────┤
  │        │                          │ Start       │
  │ POST   │ /api/v1/sessions/start   │ lesson      │
  │        │                          │ session     │
  └────────┴──────────────────────────┴─────────────┘

  Wallet (2)

  ┌───────┬──────────────────────────┬────────────┐
  │ Metho │           Path           │ Descriptio │
  │   d   │                          │     n      │
  ├───────┼──────────────────────────┼────────────┤
  │       │                          │ Player's   │
  │ GET   │ /api/v1/wallet           │ wallet (XP │
  │       │                          │  + streak) │
  ├───────┼──────────────────────────┼────────────┤
  │       │                          │ Player     │
  │ GET   │ /api/v1/wallet/{player_i │ wallet     │
  │       │ d}                       │ (admin     │
  │       │                          │ only)      │
  └───────┴──────────────────────────┴────────────┘

  Leaderboard (2)

  ┌──────┬───────────────────────────┬─────────────┐
  │ Meth │           Path            │ Description │
  │  od  │                           │             │
  ├──────┼───────────────────────────┼─────────────┤
  │      │                           │ Top         │
  │ GET  │ /api/v1/leaderboard/{lb_t │ students    │
  │      │ ype}                      │ (daily/week │
  │      │                           │ ly)         │
  ├──────┼───────────────────────────┼─────────────┤
  │      │ /api/v1/leaderboard/{lb_t │ Player rank │
  │ GET  │ ype}/me                   │  with       │
  │      │                           │ neighbors   │
  └──────┴───────────────────────────┴─────────────┘

  Profile (6)

  ┌────────┬──────────────────────────┬─────────────┐
  │ Method │           Path           │ Description │
  ├────────┼──────────────────────────┼─────────────┤
  │        │                          │ Hero        │
  │ GET    │ /api/v1/profile          │ section     │
  │        │                          │ (avatar,    │
  │        │                          │ level, XP)  │
  ├────────┼──────────────────────────┼─────────────┤
  │        │                          │ Stats grid  │
  │ GET    │ /api/v1/profile/stats    │ (streak,    │
  │        │                          │ items, XP)  │
  ├────────┼──────────────────────────┼─────────────┤
  │        │                          │ Memory      │
  │ GET    │ /api/v1/profile/mastery  │ mastery     │
  │        │                          │ breakdown   │
  ├────────┼──────────────────────────┼─────────────┤
  │ GET    │ /api/v1/profile/activity │ Weekly XP   │
  │        │                          │ activity    │
  ├────────┼──────────────────────────┼─────────────┤
  │ PUT    │ /api/v1/profile/avatar   │ Update      │
  │        │                          │ avatar      │
  ├────────┼──────────────────────────┼─────────────┤
  │        │                          │ Logout +    │
  │ POST   │ /api/v1/profile/logout   │ invalidate  │
  │        │                          │ session     │
  └────────┴──────────────────────────┴─────────────┘

  Reviews (3)

  Method: GET
  Path: /api/v1/reviews
  Description: Due reviews overview per subject
  ────────────────────────────────────────
  Method: GET
  Path: /api/v1/reviews/{subject}
  Description: Up to 10 due items for subject
  ────────────────────────────────────────
  Method: POST
  Path: /api/v1/reviews/{subject}/submit
  Description: Submit reviewed items batch

  Practice Arena (4)

  ┌───────┬──────────────────────────┬────────────┐
  │ Metho │           Path           │ Descriptio │
  │   d   │                          │     n      │
  ├───────┼──────────────────────────┼────────────┤
  │       │                          │ Browse     │
  │ GET   │ /api/v1/practice/hierarc │ content    │
  │       │ hy                       │ with item  │
  │       │                          │ counts     │
  ├───────┼──────────────────────────┼────────────┤
  │       │                          │ Start      │
  │ POST  │ /api/v1/practice/start   │ practice   │
  │       │                          │ session    │
  ├───────┼──────────────────────────┼────────────┤
  │       │                          │ Submit     │
  │ POST  │ /api/v1/practice/submit  │ practice   │
  │       │                          │ batch      │
  │       │                          │ results    │
  ├───────┼──────────────────────────┼────────────┤
  │       │ /api/v1/practice/continu │ Next batch │
  │ POST  │ e                        │  of        │
  │       │                          │ questions  │
  └───────┴──────────────────────────┴────────────┘

  Plans (3)

  Method: GET
  Path: /api/v1/plans/{plan_id}/manifest
  Description: Plan manifest with subjects
  ────────────────────────────────────────
  Method: POST
  Path: /api/v1/plans/change
  Description: Execute plan change (data reset)
  ────────────────────────────────────────
  Method: GET
  Path: /api/v1/plans/available
  Description: Plans available for switching

  Subscriptions / Catalog / Access (5)

  ┌──────┬────────────────────────────┬────────────┐
  │ Meth │            Path            │ Descriptio │
  │  od  │                            │     n      │
  ├──────┼────────────────────────────┼────────────┤
  │      │                            │ Player's   │
  │ GET  │ /api/v1/subscriptions      │ subscripti │
  │      │                            │ ons        │
  ├──────┼────────────────────────────┼────────────┤
  │      │                            │ Product    │
  │ GET  │ /api/v1/catalog/           │ catalog    │
  │      │                            │ for plan   │
  ├──────┼────────────────────────────┼────────────┤
  │      │                            │ Grant      │
  │ POST │ /api/v1/access/grants      │ access     │
  │      │                            │ (admin)    │
  ├──────┼────────────────────────────┼────────────┤
  │ DELE │                            │ Revoke     │
  │ TE   │ /api/v1/access/grants      │ access     │
  │      │                            │ (admin)    │
  ├──────┼────────────────────────────┼────────────┤
  │ GET  │ /api/v1/access/grants/{pla │ Get grants │
  │      │ yer_id}                    │  (admin)   │
  └──────┴────────────────────────────┴────────────┘

  Voucher (2)

  ┌────────┬─────────────────────────┬──────────────┐
  │ Method │          Path           │ Description  │
  ├────────┼─────────────────────────┼──────────────┤
  │ POST   │ /api/v1/voucher/preview │ Preview      │
  │        │                         │ voucher card │
  ├────────┼─────────────────────────┼──────────────┤
  │ POST   │ /api/v1/voucher/redeem  │ Redeem       │
  │        │                         │ voucher card │
  └────────┴─────────────────────────┴──────────────┘

  Other (4)

  ┌───────┬──────────────────────────┬─────────────┐
  │ Metho │           Path           │ Description │
  │   d   │                          │             │
  ├───────┼──────────────────────────┼─────────────┤
  │       │                          │ Submit      │
  │ POST  │ /api/v1/purchase/        │ purchase    │
  │       │                          │ request     │
  ├───────┼──────────────────────────┼─────────────┤
  │       │                          │ Submit      │
  │ POST  │ /api/v1/reports          │ content     │
  │       │                          │ report      │
  ├───────┼──────────────────────────┼─────────────┤
  │ GET   │ /api/v1/settings/gamific │ Gamificatio │
  │       │ ation                    │ n config    │
  ├───────┼──────────────────────────┼─────────────┤
  │ GET   │ /api/v1/announcements/   │ Active anno │
  │       │                          │ uncements   │
  ├───────┼──────────────────────────┼─────────────┤
  │       │                          │ Real-time   │
  │ WS    │ /api/v1/notifications/ws │ notificatio │
  │       │                          │ ns          │
  ├───────┼──────────────────────────┼─────────────┤
  │ POST  │ /api/v1/webhooks/payment │ Payment     │
  │       │                          │ webhook     │