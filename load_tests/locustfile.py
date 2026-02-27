"""Locust load test suite for Memora FastAPI sidecar.

4 user behavior profiles simulating realistic player traffic:
- DashboardUser (40%): Checks profile, stats, activity, wallet, progress
- LessonPlayer (35%): Full lesson session lifecycle
- BrowserUser (15%): Hierarchy drill-down (subjects → tracks)
- LeaderboardChecker (10%): Daily/weekly leaderboard checks
"""

import random
import time
from datetime import datetime, timezone

from locust import HttpUser, between, task

from load_tests import config
from load_tests.helpers import AuthMixin, api_get, api_post

# --- Startup config validation (fail fast before Locust spawns users) ---
assert getattr(config, "TEST_PLAYERS", None), (
	"No test players configured — copy config.example.py to config.py and add player credentials"
)
assert getattr(config, "TEST_SUBJECTS", None), (
	"No test subjects configured — add at least one subject ID to config.TEST_SUBJECTS"
)
assert getattr(config, "TEST_LESSONS", None), (
	"No test lessons configured — add at least one lesson entry to config.TEST_LESSONS"
)


class DashboardUser(AuthMixin, HttpUser):
	"""Simulates a student checking their dashboard stats.

	Weight: 40% of spawned users.
	Tasks: profile(3), stats(2), activity(2), mastery(1), wallet(1), progress(1).
	"""

	weight = 40
	wait_time = between(3, 8)

	@task(3)
	def check_profile(self):
		api_get(self, "/api/v1/profile")

	@task(2)
	def check_stats(self):
		api_get(self, "/api/v1/profile/stats")

	@task(2)
	def check_activity(self):
		api_get(self, "/api/v1/profile/activity")

	@task(1)
	def check_mastery(self):
		api_get(self, "/api/v1/profile/mastery")

	@task(1)
	def check_wallet(self):
		api_get(self, "/api/v1/wallet")

	@task(1)
	def check_progress(self):
		api_get(self, "/api/v1/progress")


class LessonPlayer(AuthMixin, HttpUser):
	"""Simulates a student completing a lesson.

	Weight: 35% of spawned users.
	Flow: Browse topic lessons → start session → think time → end session → check wallet.
	"""

	weight = 35
	wait_time = between(5, 15)

	@task
	def play_lesson(self):
		lesson = random.choice(config.TEST_LESSONS)
		subject_id = lesson["subject_id"]
		topic_id = lesson.get("topic_id")

		# Simulate browsing to the lesson
		if topic_id:
			api_get(
				self,
				f"/api/v1/progress/{subject_id}/topics/{topic_id}/lessons",
				name="/api/v1/progress/[subject]/topics/[topic]/lessons",
			)

		# Start session
		resp = api_post(
			self,
			"/api/v1/sessions/start",
			json={"lesson_id": lesson["lesson_id"], "subject_id": subject_id},
		)
		if not resp:
			return  # Login failed, rate-limited, or 409 conflict

		# Simulate student thinking/completing the lesson (3-10s)
		time.sleep(random.uniform(3, 10))

		# Build stage results (1-3 stages with randomized data)
		templates = lesson.get("stages") or []
		num_stages = random.randint(1, max(1, min(3, len(templates)))) if templates else 1
		stages = []

		for i in range(num_stages):
			if i < len(templates):
				tpl = templates[i]
				stages.append(
					{
						"stage_id": tpl["stage_id"],
						"time_spent": random.randint(
							tpl.get("min_time_ms", 3000),
							tpl.get("max_time_ms", 10000),
						),
						"fail_count": random.randint(0, tpl.get("max_fail_count", 2)),
						"completed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
						"items": [],
					}
				)
			else:
				stages.append(
					{
						"stage_id": f"STAGE-{i + 1:03d}",
						"time_spent": random.randint(3000, 10000),
						"fail_count": random.randint(0, 2),
						"completed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
						"items": [],
					}
				)

		# End session
		api_post(self, "/api/v1/sessions/end", json={"stages": stages})

		# Check wallet for XP update
		api_get(self, "/api/v1/wallet")


class BrowserUser(AuthMixin, HttpUser):
	"""Simulates a student browsing the content hierarchy.

	Weight: 15% of spawned users.
	Flow: subjects → tracks → units (full drill-down, stops on empty responses).
	"""

	weight = 15
	wait_time = between(2, 6)

	@task
	def browse_hierarchy(self):
		# Step 1: Get subject list
		resp = api_get(self, "/api/v1/progress")
		if not resp:
			return

		# Pick a random subject from response (fall back to config if parse fails)
		try:
			subjects = resp.json()
			subject_id = random.choice(subjects)["subject_id"] if subjects else None
		except Exception:
			subject_id = None
		if not subject_id:
			subject_id = random.choice(config.TEST_SUBJECTS)

		# Step 2: Get tracks for subject
		resp = api_get(
			self,
			f"/api/v1/progress/{subject_id}/tracks",
			name="/api/v1/progress/[subject]/tracks",
		)
		if not resp:
			return

		# Extract a track_id from response
		try:
			tracks = resp.json()
			track_id = random.choice(tracks)["track_id"] if tracks else None
		except Exception:
			track_id = None
		if not track_id:
			return  # No tracks — stop drilling

		# Step 3: Get units for track
		resp = api_get(
			self,
			f"/api/v1/progress/{subject_id}/tracks/{track_id}",
			name="/api/v1/progress/[subject]/tracks/[track]",
		)
		if not resp:
			return

		# Extract a unit_id from response
		try:
			data = resp.json()
			units = data.get("units", [])
			unit_id = random.choice(units)["unit_id"] if units else None
		except Exception:
			unit_id = None
		if not unit_id:
			return  # No units — stop drilling

		# Step 4: Get topics for unit
		api_get(
			self,
			f"/api/v1/progress/{subject_id}/tracks/{track_id}/units/{unit_id}",
			name="/api/v1/progress/[subject]/tracks/[track]/units/[unit]",
		)


class LeaderboardChecker(AuthMixin, HttpUser):
	"""Simulates a student checking leaderboard rankings.

	Weight: 10% of spawned users.
	Tasks: daily(2), weekly(1), my_rank(2).
	"""

	weight = 10
	wait_time = between(5, 10)

	@task(2)
	def check_daily(self):
		api_get(
			self,
			"/api/v1/leaderboard/daily",
			name="/api/v1/leaderboard/[type]",
		)

	@task(1)
	def check_weekly(self):
		api_get(
			self,
			"/api/v1/leaderboard/weekly",
			name="/api/v1/leaderboard/[type]",
		)

	@task(2)
	def check_my_rank(self):
		lb_type = random.choice(["daily", "weekly"])
		api_get(
			self,
			f"/api/v1/leaderboard/{lb_type}/me",
			name="/api/v1/leaderboard/[type]/me",
		)
