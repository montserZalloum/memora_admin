# Quickstart: Challenge Hub (038)

## Prerequisites

- Frappe v15 bench with `memora_admin` installed
- FastAPI sidecar running on port 8002
- Redis on port 13001 (dedicated Memora instance)
- Review Item table populated (via `review_item_sync.py`)
- At least one subject with published lessons containing MCQ stages

## New Files to Create

### FastAPI (Game API)

```
fastapi_app/
├── api/v1/endpoints/
│   └── challenge.py              # 5 endpoints (2 hierarchy, 1 attempt, 2 leaderboard)
├── services/
│   └── challenge.py              # ChallengeService (progress, grading, XP, FSRS push)
└── models/
    └── challenge.py              # Pydantic request/response models
```

### Frappe (Admin + Storage)

```
memora_admin/memora_admin/doctype/
├── memora_challenge_progress/    # 1 record per student per topic per season
│   ├── memora_challenge_progress.json
│   ├── memora_challenge_progress.py
│   └── test_memora_challenge_progress.py
├── memora_challenge_attempt/     # 1 record per attempt
│   ├── memora_challenge_attempt.json
│   ├── memora_challenge_attempt.py
│   └── test_memora_challenge_attempt.py
└── memora_challenge_attempt_detail/  # Child table (per-question)
    ├── memora_challenge_attempt_detail.json
    └── memora_challenge_attempt_detail.py
```

### Build Pipeline Extension

```
memora_admin/services/build/
└── challenge_questions.py        # Generate topic question JSON files
```

### Sync Task Extension

```
memora_admin/tasks/
└── sync.py                       # Add sync_dirty_challenge_progress() function
```

## Files to Modify

| File | Change |
|------|--------|
| `fastapi_app/api/v1/router.py` | Add `challenge.router` |
| `fastapi_app/api/deps.py` | Add `ChallengeServiceDep` |
| `fastapi_app/core/redis_keys.py` | Add `ch_progress_key`, `ch_leaderboard_key`, `ch_leaderboard_subject_key`, `ch_idem_key`, `dirty_ch_progress_key`, `ch_attempt_buffer_key` |
| `fastapi_app/main.py` | Initialize `ChallengeService` in lifespan (if singleton needed) |
| `memora_admin/memora_admin/doctype/memora_settings/memora_settings.json` | Add 4 challenge settings fields |
| `memora_admin/hooks.py` | Add `sync_dirty_challenge_progress` scheduled job |
| `memora_admin/events/build_trigger.py` | Trigger question file rebuild on Review Item changes |
| `memora_admin/services/build/plan_generator.py` | Include `mcq_count` per topic in hierarchy JSON |

## Key Integration Points

### 1. Hierarchy — Reuse existing
```python
# In ChallengeService
hierarchy = await hierarchy_service.get_hierarchy(subject_id)
# Walk hierarchy.tracks → units → topics
# For each topic: check 3 unlock conditions
```

### 2. Progress — Reuse existing bitmap for condition 2
```python
# Check normal path completion
stats = await stats_service.get_or_recompute(user_id, subject_id, version)
topic_complete = int(stats.get(f"{topic_id}:completed", 0)) >= int(stats.get(f"{topic_id}:total", 0))
```

### 3. Access — Reuse existing double-gate
```python
# Check condition 1 (access)
has_access = await access_service.check_access_with_plan(player_id, f"SUB-{subject_id}", plan_id)
```

### 4. FSRS — Push to existing interaction buffer
```python
# After attempt submission, push each question result
# lesson + stage_id come from the cached topic question JSON file (not MariaDB lookup)
topic_questions = load_topic_question_file(subject_id, topic_id)
q_lookup = {q["item_id"]: q for q in topic_questions["questions"]}

pipe = redis.pipeline()
for q in submitted_questions:
    cached_q = q_lookup[q.item_id]
    interaction = json.dumps({
        "player": player_id,
        "lesson": cached_q["lesson"],
        "stage_id": cached_q["stage_id"],
        "item_id": q.item_id,
        "event_type": "Completed",
        "errors_count": 0 if q.correct else 1,
        "time_spent": q.time_spent,
        "timestamp": now_iso,
        "metadata": {"source": "challenge_hub"}
    })
    pipe.rpush(interaction_buffer_key(), interaction)
await pipe.execute()
```

### 5. Leaderboard — Reuse existing infrastructure
```python
# After XP delta earned, update challenge leaderboard ZSETs
if xp_delta > 0:
    pipe = redis.pipeline()
    # Plan-level
    pipe.zincrby(ch_leaderboard_key(season_id, plan_id), xp_delta, player_id)
    # Subject-level
    pipe.zincrby(ch_leaderboard_key(season_id, plan_id, subject_id), xp_delta, player_id)
    await pipe.execute()
```

## Testing

```bash
# Run FastAPI tests
cd /home/corex/aurevia-bench/apps/memora_admin
python -m pytest fastapi_app/tests/test_challenge_*.py -v

# Run Frappe tests
cd /home/corex/aurevia-bench
bench --site x.conanacademy.com run-tests --app memora_admin --module memora_admin.memora_admin.doctype.memora_challenge_progress

# Restart FastAPI after changes
pkill -f "uvicorn fastapi_app.main:app"
curl http://127.0.0.1:8002/api/v1/health/live
```

## Verification Checklist

- [ ] Challenge hierarchy shows correct topic states (locked/open/stamped)
- [ ] Empty topics are hidden and auto-stamped correctly
- [ ] Attempt submission stamps topic on pass (≥ threshold)
- [ ] XP delta calculated correctly (only improvement earns XP)
- [ ] FSRS receives all question results from completed attempts
- [ ] Abandoned attempts leave zero trace
- [ ] Challenge XP isolated from main XP/wallet/leaderboard
- [ ] Leaderboard shows plan-scoped rankings
- [ ] Settings (XP per question, threshold) are configurable
- [ ] Idempotent attempt submission handles network retries
