# Data Model: Remaining Service Tests

**Feature**: 012-remaining-service-tests | **Date**: 2026-02-17

## Entities

This feature produces test files — no new production data models. The entities below describe the test infrastructure contracts.

### Test File Entity

Each test file follows a fixed structure:

| Field | Type | Description |
|-------|------|-------------|
| file_name | string | `test_{service_name}_service.py` |
| service_under_test | class | The FastAPI service class being tested |
| service_fixture | pytest fixture | Instantiates service with `redis_client`, `test_prefix`, `mock_frappe` |
| cleanup_fixture | pytest fixture (autouse) | Cleans up service-specific Redis keys after each test |
| test_classes | list[class] | Grouped by behavior (e.g., `TestCacheHit`, `TestCacheMiss`, `TestInvalidation`) |

### Service Fixture Patterns

**Pattern A — Services with `key_prefix`** (HierarchyService, CatalogService, ProfileService, PlanService, StatsService):

```python
@pytest.fixture
async def svc(redis_client, test_prefix, mock_frappe):
    return ServiceClass(redis_client, frappe_client=mock_frappe, key_prefix=test_prefix)
```

Cleanup: Handled by conftest `cleanup_keys` fixture (test_prefix SCAN+DELETE).

**Pattern B — Services with hardcoded keys** (VoucherService, LeaderboardService, SettingsService, ReviewService, PurchaseService):

```python
@pytest.fixture
async def svc(redis_client, mock_frappe):
    return ServiceClass(redis_client, frappe_client=mock_frappe, ...)

@pytest.fixture(autouse=True)
async def cleanup_hardcoded_keys(redis_client):
    yield
    # SCAN and DELETE specific patterns
```

### Service Constructor Signatures

| Service | Constructor | Pattern |
|---------|------------|---------|
| VoucherService | `(redis_client, frappe_client, hmac_secret)` | B — no key_prefix |
| LeaderboardService | `(redis_client)` | B — no key_prefix, no frappe |
| StatsService | `(redis_client, key_prefix="memora:")` | A — no frappe |
| HierarchyService | `(redis_client, frappe_client, key_prefix="memora:")` | A |
| CatalogService | `(redis_client, frappe_client, key_prefix="memora:")` | A |
| ProfileService | `(redis_client, frappe_client, key_prefix="memora:")` | A |
| PlanService | `(redis_client, frappe_client, key_prefix="memora:")` | A |
| SettingsService | `(redis_client, frappe_client, key_prefix="memora:")` | B — key_prefix exists but CACHE_KEY is hardcoded |
| PurchaseService | `(redis_client, frappe_client)` | B — no key_prefix |
| ReviewService | `(redis_client, frappe_client)` | B — no key_prefix |

### Redis Key Patterns Under Test

| Service | Key Pattern | Type | TTL | Prefix-Isolated? |
|---------|-------------|------|-----|-------------------|
| VoucherService | `memora:voucher_fail:player:{id}` | string (counter) | 3600s | NO |
| VoucherService | `memora:voucher_fail:ip:{ip}` | string (counter) | 3600s | NO |
| LeaderboardService | `memora:lb:alltime` | zset | none | NO |
| LeaderboardService | `memora:lb:daily:{date}` | zset | none | NO |
| LeaderboardService | `memora:lb:weekly:{week}` | zset | none | NO |
| StatsService | `{prefix}stats:{user}:{subj}:v{ver}` | hash | 3600s | YES |
| HierarchyService | `{prefix}hierarchy:{subject}` | string (JSON) | 3600s | YES |
| HierarchyService | `{prefix}subjects_with_free_content` | set | none | YES |
| CatalogService | `{prefix}catalog:{plan}` | string (JSON) | none | YES |
| CatalogService | `{prefix}access:{player}` | set | none | YES (read) |
| CatalogService | `{prefix}pending:{player}` | set | none | YES (read) |
| ProfileService | `{prefix}profile:{player}` | string (JSON) | 3600s | YES |
| PlanService | `{prefix}plan:{plan}:manifest` | string (JSON) | 3600s | YES |
| SettingsService | `memora:settings:gamification` | string (JSON) | 300s | NO |
| PurchaseService | `memora:pending:{user}` | set | none | NO |
| ReviewService | `memora:reviews_overview:{player}` | string (JSON) | 300s | NO |

### Mock FrappeClient Return Values per Service

| Service | Method | Frappe API | Mock Return Value |
|---------|--------|-----------|-------------------|
| VoucherService | `preview` | `memora_admin.memora_admin.api.voucher.preview_voucher` | `{"face_value": 100, "grants": [...]}` |
| VoucherService | `redeem` | `memora_admin.memora_admin.api.voucher.redeem_voucher` | `{"status": "success", "transaction_id": "TXN-001"}` |
| HierarchyService | `get_hierarchy` | `memora_admin.api.hierarchy.get_subject_hierarchy` | `SubjectHierarchy` dict |
| CatalogService | `get_catalog` | `memora_admin.memora_admin.api.catalog.get_plan_catalog` | `[CatalogProduct]` list |
| ProfileService | `get_profiles_batch` | `memora_admin.api.profile.get_profiles_batch` | `[{player_id, display_name, avatar}]` |
| PlanService | `get_manifest` | `memora_admin.api.plan.get_plan_manifest` | `PlanManifest` dict |
| SettingsService | `get_gamification_settings` | `memora_admin.api.settings.get_gamification_settings` | `GamificationSettings` dict |
| PurchaseService | `submit_purchase` | `memora_admin.api.purchase.create_purchase_request` | `{"name": "TXN-001"}` |
| ReviewService | `get_overview` | `memora_admin.api.reviews.get_review_overview` | `[{subject_id, due_count}]` |
| ReviewService | `get_due_items` | `memora_admin.api.reviews.get_due_items` | `{items: [...], has_more: false}` |
| ReviewService | `submit_reviews` | `memora_admin.api.reviews.submit_reviews` | `{processed: 3, remaining_due: 0}` |
