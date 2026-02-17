# Test Contracts: Remaining Service Tests

**Feature**: 012-remaining-service-tests | **Date**: 2026-02-17

Each service test file has a contract: the exact test cases, their inputs, and expected assertions.

---

## 1. `test_voucher_service.py` — 6 tests

### Fixture: `voucher_svc`
```python
VoucherService(redis_client, frappe_client=mock_frappe, hmac_secret="test-hmac-secret")
```

### Cleanup: `autouse` fixture
```python
# After each test, delete memora:voucher_fail:player:* and memora:voucher_fail:ip:* keys
```

| TC | Method | Setup | Action | Assert |
|----|--------|-------|--------|--------|
| TC-VCH-01 | `_compute_hmac` | Service with known secret | Call `_compute_hmac("PIN123")` twice | Same deterministic hex digest both times; matches `hmac.new(b"test-hmac-secret", b"PIN123", sha256).hexdigest()` |
| TC-VCH-02 | `check_rate_limit` | No prior failures | Call `check_rate_limit(player, ip)` | Returns `None` (not limited) |
| TC-VCH-03 | `check_rate_limit` | 5 player failures recorded (via `record_failure` ×5) | Call `check_rate_limit(player, ip)` | Returns positive `retry_after` int (TTL) |
| TC-VCH-04 | `check_rate_limit` | 20 IP failures recorded (from 20 different players) | Call `check_rate_limit(player, ip)` | Returns positive `retry_after` int |
| TC-VCH-05 | `preview` | `mock_frappe.call.return_value = {"face_value": 100}` | Call `preview("PIN123", "PLAYER-001")` | Returns dict with `face_value`; `mock_frappe.call` called with `preview_voucher` method and `pin_hmac` (not plaintext PIN) |
| TC-VCH-06 | `redeem` | `mock_frappe.call.side_effect = FrappeAPIError(417, "EXPIRED")` | Call `redeem("PIN123", "PLAYER-001", "GRNT-001", "1.2.3.4")` | Returns `{"error": "SERVICE_ERROR"}` |
| TC-VCH-07 (edge) | `__init__` | Empty hmac_secret | Call `VoucherService(redis, frappe, hmac_secret="")` | Raises `ValueError` |

---

## 2. `test_leaderboard_service.py` — 5 tests

### Fixture: `lb_svc`
```python
LeaderboardService(redis_client)
```

### Cleanup: `autouse` fixture
```python
# SCAN and DELETE memora:lb:* keys
```

| TC | Method | Setup | Action | Assert |
|----|--------|-------|--------|--------|
| TC-LB-01 | `update_leaderboards` | Empty Redis | Call `update_leaderboards("P1", 50, 50)` | `memora:lb:alltime` has P1 with score ≥ 50. Daily and weekly keys have P1 with score 50.0. |
| TC-LB-02 | `get_top` | ZADD 3 players with different XP | Call `get_top("alltime", limit=10)` | Returns list sorted desc by XP with dense ranking: rank 1, 2, 3 |
| TC-LB-03 | `get_top` + dense rank | ZADD 3 players: P1=100, P2=100, P3=50 (composite scores) | Call `get_top("alltime", limit=10)` | P1 and P2 share rank 1, P3 gets rank 3 |
| TC-LB-04 | `get_top` | Empty Redis | Call `get_top("alltime")` | Returns empty list `[]` |
| TC-LB-05 | `compute_composite_score` | Known XP and timestamp | Call `compute_composite_score(100, 1000000000.5)` | Integer part is 100. Earlier timestamp produces higher fractional part. |

---

## 3. `test_stats_service.py` — 5 tests

### Fixture: `stats_svc`
```python
StatsService(redis_client, key_prefix=test_prefix)
```

| TC | Method | Setup | Action | Assert |
|----|--------|-------|--------|--------|
| TC-STS-01 | `get_stats` (hit) | `set_stats(user, subj, 1, {"completed": "5", "total": "10"})` | Call `get_stats(user, subj, 1)` | Returns `{"completed": "5", "total": "10"}` |
| TC-STS-02 | `get_stats` (miss) | Empty Redis | Call `get_stats(user, subj, 1)` | Returns `None` |
| TC-STS-03 | `set_stats` | Empty Redis | Call `set_stats(user, subj, 1, stats)` | Redis HGETALL returns the stats; TTL ≈ 3600s |
| TC-STS-04 | `increment_completion_stats` | Pre-seed stats hash `{"completed": "5"}` | Call `increment_completion_stats(user, subj, 1, trk, unit, topic)` | `completed` is now `"6"`; `{trk}:completed` is `"1"` |
| TC-STS-05 | `compute_stats_from_hierarchy` | Build `SubjectHierarchy` with 1 track → 1 unit → 1 topic → 2 lessons (bits 0,1) | Call with `completed_bits={0}` | Returns `{"completed": "1", "total": "2", "{trk}:completed": "1", "{trk}:total": "2", ...}` |
| TC-STS-06 (edge) | `compute_stats_from_hierarchy` | Empty hierarchy (no tracks) | Call with `completed_bits=set()` | Returns `{"completed": "0", "total": "0"}` |

---

## 4. `test_hierarchy_service.py` — 4 tests

### Fixture: `hierarchy_svc`
```python
HierarchyService(redis_client, frappe_client=mock_frappe, key_prefix=test_prefix)
```

| TC | Method | Setup | Action | Assert |
|----|--------|-------|--------|--------|
| TC-HIR-01 | `get_hierarchy` (hit) | Pre-seed `{prefix}hierarchy:{subj}` with valid JSON | Call `get_hierarchy(subj)` | Returns `SubjectHierarchy`; `mock_frappe.call` NOT called |
| TC-HIR-02 | `get_hierarchy` (miss) | Empty Redis; `mock_frappe.call.return_value = hierarchy_dict` | Call `get_hierarchy(subj)` | Returns `SubjectHierarchy`; `mock_frappe.call` called once; Redis key now exists with TTL ≈ 3600 |
| TC-HIR-03 | `invalidate` | Pre-seed hierarchy key | Call `invalidate(subj)` | Redis key deleted |
| TC-HIR-04 (miss + free) | `get_hierarchy` (miss, subject has free content) | `mock_frappe.call` returns hierarchy with `free_units=["U1"]` | Call `get_hierarchy(subj)` | `{prefix}subjects_with_free_content` set contains `subj` |

---

## 5. `test_catalog_service.py` — 4 tests

### Fixture: `catalog_svc`
```python
CatalogService(redis_client, frappe_client=mock_frappe, key_prefix=test_prefix)
```

| TC | Method | Setup | Action | Assert |
|----|--------|-------|--------|--------|
| TC-CAT-01 | `get_catalog` (hit) | Pre-seed `{prefix}catalog:{plan}` with JSON array | Call `get_catalog(plan)` | Returns `list[CatalogProduct]`; `mock_frappe.call` NOT called |
| TC-CAT-02 | `get_catalog` (miss) | `mock_frappe.call.return_value = [product_dict]` | Call `get_catalog(plan)` | Returns products; Frappe called once; Redis key exists with NO TTL |
| TC-CAT-03 | `get_player_catalog` (exclude pending) | Cache catalog with 2 products; SADD `{prefix}pending:{player}` with product A's grant ID | Call `get_player_catalog(plan, player)` | Returns only product B |
| TC-CAT-04 | `get_player_catalog` (exclude purchased) | Cache catalog; SADD `{prefix}access:{player}` with ALL subject access keys for product A | Call `get_player_catalog(plan, player)` | Returns only product B |

---

## 6. `test_profile_service.py` — 4 tests

### Fixture: `profile_svc`
```python
ProfileService(redis_client, frappe_client=mock_frappe, key_prefix=test_prefix)
```

| TC | Method | Setup | Action | Assert |
|----|--------|-------|--------|--------|
| TC-PRF-01 | `get_profiles_batch` (all cached) | Pre-seed 2 profile keys | Call `get_profiles_batch(["P1", "P2"])` | Returns dict with both profiles; Frappe NOT called |
| TC-PRF-02 | `get_profiles_batch` (miss → Frappe) | 1 cached, 1 missing; `mock_frappe.call.return_value = [profile_dict]` | Call `get_profiles_batch(["P1", "P2"])` | P1 from cache, P2 from Frappe; Frappe called once |
| TC-PRF-03 | `get_profiles_batch` (fallback) | All missing; `mock_frappe.call.return_value = []` | Call `get_profiles_batch(["PLAYER-TEST-9999"])` | Returns `PlayerProfile(display_name="Anonymous 9999")` |
| TC-PRF-04 (edge) | `get_profiles_batch` (empty input) | n/a | Call `get_profiles_batch([])` | Returns `{}` immediately; no Redis calls |

---

## 7. `test_plan_service.py` — 3 tests

### Fixture: `plan_svc`
```python
PlanService(redis_client, frappe_client=mock_frappe, key_prefix=test_prefix)
```

| TC | Method | Setup | Action | Assert |
|----|--------|-------|--------|--------|
| TC-PLN-01 | `get_manifest` (hit) | Pre-seed `{prefix}plan:{plan}:manifest` with JSON | Call `get_manifest(plan)` | Returns `PlanManifest`; Frappe NOT called |
| TC-PLN-02 | `get_manifest` (miss) | `mock_frappe.call.return_value = manifest_dict` | Call `get_manifest(plan)` | Returns manifest; Frappe called; key cached with TTL ≈ 3600 |
| TC-PLN-03 | `invalidate` | Pre-seed manifest key | Call `invalidate(plan)` | Redis key deleted |

---

## 8. `test_settings_service.py` — 3 tests

### Fixture: `settings_svc`
```python
SettingsService(redis_client, frappe_client=mock_frappe)
```

### Cleanup: `autouse` fixture
```python
# DELETE memora:settings:gamification
```

| TC | Method | Setup | Action | Assert |
|----|--------|-------|--------|--------|
| TC-SET-01 | `get_gamification_settings` (hit) | Pre-seed `memora:settings:gamification` with JSON | Call `get_gamification_settings()` | Returns `GamificationSettings`; Frappe NOT called |
| TC-SET-02 | `get_gamification_settings` (miss) | `mock_frappe.call.return_value = settings_dict` | Call `get_gamification_settings()` | Returns settings; Frappe called; key cached with TTL ≈ 300 |
| TC-SET-03 (edge) | `get_gamification_settings` (Frappe unavailable) | `mock_frappe.call.return_value = None` | Call `get_gamification_settings()` | Returns default `GamificationSettings()` with `base_lesson_xp=100` |

---

## 9. `test_purchase_service.py` — 4 tests

### Fixture: `purchase_svc`
```python
PurchaseService(redis_client, frappe_client=mock_frappe)
```

### Cleanup: `autouse` fixture
```python
# DELETE memora:pending:* for test user
```

| TC | Method | Setup | Action | Assert |
|----|--------|-------|--------|--------|
| TC-PUR-01 | `submit_purchase` | `mock_frappe.call.return_value = {"name": "TXN-001"}` | Call `submit_purchase(user, plan, PurchaseRequest(...))` | Returns `PurchaseResponse`; `memora:pending:{user}` set contains grant ID |
| TC-PUR-02 | `submit_purchase` (duplicate) | Pre-SADD grant ID to `memora:pending:{user}` | Call `submit_purchase` with same grant | Raises `HTTPException(409)` |
| TC-PUR-03 | `submit_purchase` (Frappe 417 duplicate) | `mock_frappe.call.side_effect = FrappeAPIError(417, "DuplicateEntryError: ...")` | Call `submit_purchase` | Raises `HTTPException(409)` |
| TC-PUR-04 | `submit_purchase` (Frappe 404) | `mock_frappe.call.side_effect = FrappeAPIError(404, "Not Found")` | Call `submit_purchase` | Raises `HTTPException(404)` |

---

## 10. `test_review_service.py` — 4 tests

### Fixture: `review_svc`
```python
ReviewService(redis_client, frappe_client=mock_frappe)
```

### Cleanup: `autouse` fixture
```python
# DELETE memora:reviews_overview:* for test player
```

| TC | Method | Setup | Action | Assert |
|----|--------|-------|--------|--------|
| TC-REV-01 | `get_overview` (hit) | Pre-seed `memora:reviews_overview:{player}` with JSON | Call `get_overview(player)` | Returns cached list; Frappe NOT called |
| TC-REV-02 | `get_overview` (miss) | `mock_frappe.call.return_value = [{"subject_id": "S1", "due_count": 5}]` | Call `get_overview(player)` | Returns subjects; Frappe called; key cached with TTL ≈ 300 |
| TC-REV-03 | `get_due_items` | `mock_frappe.call.return_value = {"items": [...], "has_more": False}` | Call `get_due_items(player, subject)` | Returns dict from Frappe; always fresh (no cache check) |
| TC-REV-04 | `submit_reviews` | `mock_frappe.call.return_value = {"processed": 2}` | Pre-seed overview cache; call `submit_reviews(player, subject, items)` | Returns result; overview cache key DELETED (invalidated) |

---

## Total Test Count

| File | Tests |
|------|-------|
| test_voucher_service.py | 7 |
| test_leaderboard_service.py | 5 |
| test_stats_service.py | 6 |
| test_hierarchy_service.py | 4 |
| test_catalog_service.py | 4 |
| test_profile_service.py | 4 |
| test_plan_service.py | 3 |
| test_settings_service.py | 3 |
| test_purchase_service.py | 4 |
| test_review_service.py | 4 |
| **Total** | **44** |

Exceeds the 30-test minimum (SC-001) and 2-per-file minimum (SC-002).
