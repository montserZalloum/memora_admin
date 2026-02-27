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
	{"mobile": "+201000000001", "password": "CHANGE_ME"},
	{"mobile": "+201000000002", "password": "CHANGE_ME"},
	{"mobile": "+201000000003", "password": "CHANGE_ME"},
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

# 5-stage scaling ladder (reference for CLI commands)
SCALING_LADDER = [
	{"stage": 1, "users": 100, "spawn_rate": 10, "duration": "2m"},
	{"stage": 2, "users": 1000, "spawn_rate": 50, "duration": "5m"},
	{"stage": 3, "users": 10000, "spawn_rate": 200, "duration": "10m"},
	{"stage": 4, "users": 50000, "spawn_rate": 500, "duration": "15m"},
	{"stage": 5, "users": 100000, "spawn_rate": 1000, "duration": "15m"},
]
