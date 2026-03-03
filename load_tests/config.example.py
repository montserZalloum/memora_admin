"""
Locust load test configuration — EXAMPLE FILE.

Copy this file to config.py and fill in real test data:
    cp config.example.py config.py

config.py is gitignored and will NOT be committed.
"""

# Target FastAPI sidecar
HOST = "http://127.0.0.1:8002"

# Pre-created player accounts (minimum 3 for basic testing, 100-500 for 100k simulation)
TEST_PLAYERS = [
	{"mobile": "+201000000001", "password": "CHANGE_ME", "player_id": "PLAYER-00001"},
	{"mobile": "+201000000002", "password": "CHANGE_ME", "player_id": "PLAYER-00002"},
	{"mobile": "+201000000003", "password": "CHANGE_ME", "player_id": "PLAYER-00003"},
]

# Subjects accessible to test players (need tracks/units/topics for BrowserUser)
TEST_SUBJECTS = ["SUBJ-00001", "SUBJ-00002"]

# Lessons for LessonPlayer session simulation
# Each lesson must be accessible to ALL test players (proper access grants required)
TEST_LESSONS = [
	{
		"lesson_id": "LESSON-00001",
		"subject_id": "SUBJ-00001",
		"topic_id": "TOPIC-00001",
		"stages": [
			{"stage_id": "STAGE-001", "min_time_ms": 3000, "max_time_ms": 8000, "max_fail_count": 2},
			{"stage_id": "STAGE-002", "min_time_ms": 2000, "max_time_ms": 6000, "max_fail_count": 2},
		],
	},
]

# Optional subjects specifically used by review/practice flows.
# Falls back to TEST_SUBJECTS when omitted.
TEST_REVIEW_SUBJECTS = ["SUBJ-00001"]

# Optional low-risk mutable fixtures. Leave empty to skip those tasks.
TEST_AVATARS = ["avatar_01", "avatar_02"]
TEST_PLAN_MANIFEST_IDS = ["PLAN-00001"]

# Optional state-changing flows. Disabled by default.
ENABLE_MUTATION_ENDPOINTS = False
TEST_PLAN_CHANGE_IDS = ["PLAN-00002"]
TEST_PRODUCT_GRANTS = ["GRNT-00001"]
TEST_VOUCHERS = [
	{"pin": "VALID123", "grant_id": "GRNT-00001"},
]

# Optional admin-only flows. Disabled by default.
ENABLE_ADMIN_ENDPOINTS = False
ADMIN_CREDENTIALS = {
	"email": "admin@example.com",
	"password": "CHANGE_ME",
}
TEST_ACCESS_CONTENT_KEYS = ["SUB-MATH"]

# Optional provider-side webhook simulation. Disabled by default.
ENABLE_WEBHOOK_ENDPOINTS = False
TEST_WEBHOOK_EVENTS = [
	{
		"player_id": "PLAYER-00001",
		"product_grant_id": "GRNT-00001",
		"amount": 50.0,
		"currency": "EGP",
		"event_type": "payment.completed",
	},
]

# 5-stage scaling ladder (reference for CLI commands)
SCALING_LADDER = [
	{"stage": 1, "users": 100, "spawn_rate": 10, "duration": "2m"},
	{"stage": 2, "users": 1000, "spawn_rate": 50, "duration": "5m"},
	{"stage": 3, "users": 10000, "spawn_rate": 200, "duration": "10m"},
	{"stage": 4, "users": 50000, "spawn_rate": 500, "duration": "15m"},
	{"stage": 5, "users": 100000, "spawn_rate": 1000, "duration": "15m"},
]
