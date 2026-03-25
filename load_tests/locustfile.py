"""Locust load test suite for Memora FastAPI sidecar.

User profiles simulate realistic player traffic plus optional admin/destructive
flows that are disabled unless explicitly configured in load_tests/config.py.
"""

import random
import time
from datetime import datetime, timezone
from uuid import uuid4

from locust import HttpUser, between, task

from load_tests import config
from load_tests.helpers import api_delete, api_get, api_post, api_put

# --- Startup config validation (fail fast before Locust spawns users) ---
assert getattr(
	config, "TEST_PLAYERS", None
), "No test players configured — copy config.example.py to config.py and add player credentials"
assert getattr(
	config, "TEST_SUBJECTS", None
), "No test subjects configured — add at least one subject ID to config.TEST_SUBJECTS"
assert getattr(
	config, "TEST_LESSONS", None
), "No test lessons configured — add at least one lesson entry to config.TEST_LESSONS"


def _pick_subject():
	subjects = getattr(config, "TEST_REVIEW_SUBJECTS", None) or config.TEST_SUBJECTS
	return random.choice(subjects)


def _pick_avatar():
	avatars = getattr(config, "TEST_AVATARS", None) or []
	if not avatars:
		return None
	return random.choice(avatars)


def _pick_product_grant():
	product_grants = getattr(config, "TEST_PRODUCT_GRANTS", None) or []
	if not product_grants:
		return None
	return random.choice(product_grants)


def _pick_manifest_plan():
	plan_ids = getattr(config, "TEST_PLAN_MANIFEST_IDS", None) or []
	if not plan_ids:
		return None
	return random.choice(plan_ids)


def _pick_change_plan():
	plan_ids = getattr(config, "TEST_PLAN_CHANGE_IDS", None) or []
	if not plan_ids:
		return None
	return random.choice(plan_ids)


def _pick_voucher():
	vouchers = getattr(config, "TEST_VOUCHERS", None) or []
	if not vouchers:
		return None
	return random.choice(vouchers)


def _mutations_enabled():
	return bool(getattr(config, "ENABLE_MUTATION_ENDPOINTS", False))


def _admin_enabled():
	return bool(
		getattr(config, "ENABLE_ADMIN_ENDPOINTS", False) and getattr(config, "ADMIN_CREDENTIALS", None)
	)


def _webhooks_enabled():
	return bool(getattr(config, "ENABLE_WEBHOOK_ENDPOINTS", False))


class AuthMixin:
	"""Player auth mixin with optional warm-token support."""

	def on_start(self):
		from load_tests.helpers import AuthMixin as _AuthMixin

		_AuthMixin.on_start(self)


class AdminAuthMixin:
	"""Separate admin authentication for admin-only endpoint coverage."""

	def on_start(self):
		self.fake_ip = f"10.{random.randint(1, 254)}.{random.randint(1, 254)}.{random.randint(1, 254)}"
		self.device_id = f"locust-admin-{uuid4().hex[:12]}"
		self.token = None

		if not _admin_enabled():
			return

		admin = config.ADMIN_CREDENTIALS
		with self.client.post(
			"/api/v1/auth/admin/login",
			json={"email": admin["email"], "password": admin["password"]},
			headers={"X-Forwarded-For": self.fake_ip},
			catch_response=True,
		) as resp:
			if resp.status_code == 200:
				data = resp.json()
				self.token = data["access_token"]
				resp.success()
			elif resp.status_code == 429:
				resp.success()
			else:
				resp.failure(f"Admin login failed: {resp.status_code}")


class DashboardUser(AuthMixin, HttpUser):
	"""Simulates a student checking dashboard and low-risk discovery APIs."""

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

	@task(2)
	def check_catalog(self):
		api_get(self, "/api/v1/catalog/")

	@task(1)
	def check_subscriptions(self):
		api_get(self, "/api/v1/subscriptions")

	@task(1)
	def check_gamification_settings(self):
		api_get(self, "/api/v1/settings/gamification")

	@task(1)
	def check_announcements(self):
		lang = random.choice(["ar", "en"])
		api_get(
			self,
			f"/api/v1/announcements/?lang={lang}",
			name="/api/v1/announcements/",
		)

	@task(1)
	def check_available_plans(self):
		api_get(self, "/api/v1/plans/available")

	@task(1)
	def check_plan_manifest(self):
		plan_id = _pick_manifest_plan()
		if not plan_id:
			return
		api_get(
			self,
			f"/api/v1/plans/{plan_id}/manifest",
			name="/api/v1/plans/[plan]/manifest",
		)


class LessonPlayer(AuthMixin, HttpUser):
	"""Simulates a student completing a lesson."""

	weight = 35
	wait_time = between(5, 15)

	@task
	def play_lesson(self):
		lesson = random.choice(config.TEST_LESSONS)
		subject_id = lesson["subject_id"]
		topic_id = lesson.get("topic_id")

		if topic_id:
			api_get(
				self,
				f"/api/v1/progress/{subject_id}/topics/{topic_id}/lessons",
				name="/api/v1/progress/[subject]/topics/[topic]/lessons",
			)

		resp = api_post(
			self,
			"/api/v1/sessions/start",
			json={"lesson_id": lesson["lesson_id"], "subject_id": subject_id},
		)
		if not resp:
			return

		try:
			session_id = resp.json().get("session_id")
		except Exception:
			session_id = None

		api_get(self, "/api/v1/sessions/current")

		time.sleep(random.uniform(3, 10))

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

		if not session_id:
			return  # sessions/start succeeded but returned no session_id — skip end

		api_post(self, "/api/v1/sessions/end", json={"session_id": session_id, "stages": stages})
		api_get(self, "/api/v1/wallet")


class BrowserUser(AuthMixin, HttpUser):
	"""Simulates a student browsing the content hierarchy."""

	weight = 15
	wait_time = between(2, 6)

	@task
	def browse_hierarchy(self):
		resp = api_get(self, "/api/v1/progress")
		if not resp:
			return

		try:
			subjects = resp.json()
			subject_id = random.choice(subjects)["subject_id"] if subjects else None
		except Exception:
			subject_id = None
		if not subject_id:
			subject_id = random.choice(config.TEST_SUBJECTS)

		resp = api_get(
			self,
			f"/api/v1/progress/{subject_id}/tracks",
			name="/api/v1/progress/[subject]/tracks",
		)
		if not resp:
			return

		try:
			tracks = resp.json()
			track_id = random.choice(tracks)["track_id"] if tracks else None
		except Exception:
			track_id = None
		if not track_id:
			return

		resp = api_get(
			self,
			f"/api/v1/progress/{subject_id}/tracks/{track_id}",
			name="/api/v1/progress/[subject]/tracks/[track]",
		)
		if not resp:
			return

		try:
			data = resp.json()
			units = data.get("units", [])
			unit_id = random.choice(units)["unit_id"] if units else None
		except Exception:
			unit_id = None
		if not unit_id:
			return

		api_get(
			self,
			f"/api/v1/progress/{subject_id}/tracks/{track_id}/units/{unit_id}",
			name="/api/v1/progress/[subject]/tracks/[track]/units/[unit]",
		)


class LeaderboardChecker(AuthMixin, HttpUser):
	"""Simulates a student checking leaderboard rankings."""

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


class ReviewUser(AuthMixin, HttpUser):
	"""Simulates player review sessions."""

	weight = 10
	wait_time = between(4, 9)

	@task
	def review_due_items(self):
		api_get(self, "/api/v1/reviews")

		subject_id = _pick_subject()
		resp = api_get(
			self,
			f"/api/v1/reviews/{subject_id}",
			name="/api/v1/reviews/[subject]",
		)
		if not resp:
			return

		try:
			items = resp.json().get("items", [])
		except Exception:
			items = []
		if not items:
			return

		payload = {
			"items": [
				{
					"item_id": item["item_id"],
					"fail_count": random.randint(0, 2),
				}
				for item in items[: min(5, len(items))]
				if item.get("item_id")
			]
		}
		if not payload["items"]:
			return

		api_post(
			self,
			f"/api/v1/reviews/{subject_id}/submit",
			json=payload,
			name="/api/v1/reviews/[subject]/submit",
		)


class PracticeUser(AuthMixin, HttpUser):
	"""Simulates practice arena browsing and batch submission."""

	weight = 12
	wait_time = between(4, 10)

	@task
	def practice(self):
		subject_id = _pick_subject()
		# Pick a random track from the seeded hierarchy (10 tracks per subject)
		track_idx = random.randint(1, 10)
		start_payload = {
			"subject_id": subject_id,
			"track_ids": [f"TRK-{subject_id}-T{track_idx:02d}"],
		}

		start_resp = api_post(
			self,
			"/api/v1/practice/start",
			json=start_payload,
		)
		if not start_resp:
			return

		try:
			batch = start_resp.json()
			question_ids = batch.get("question_ids", [])
			batch_seq = batch.get("batch_seq", 0)
		except Exception:
			return
		if not question_ids:
			return

		results = [
			{
				"item_id": qid,
				"is_correct": random.choice([True, False]),
			}
			for qid in question_ids
		]
		if not results:
			return

		submit_resp = api_post(
			self,
			"/api/v1/practice/submit",
			json={"batch_seq": batch_seq, "results": results},
		)
		if not submit_resp:
			return

		continue_resp = api_post(self, "/api/v1/practice/continue")
		if not continue_resp:
			return

		try:
			next_batch = continue_resp.json()
			next_question_ids = next_batch.get("question_ids", [])
			next_seq = next_batch.get("batch_seq", batch_seq + 1)
		except Exception:
			return
		if not next_question_ids:
			return

		next_results = [
			{
				"item_id": qid,
				"is_correct": random.choice([True, False]),
			}
			for qid in next_question_ids
		]
		if not next_results:
			return

		api_post(
			self,
			"/api/v1/practice/submit",
			json={"batch_seq": next_seq, "results": next_results},
		)


class MutationUser(AuthMixin, HttpUser):
	"""Optional state-changing player flows; disabled unless explicitly enabled."""

	weight = 2
	wait_time = between(10, 20)

	@task(2)
	def update_avatar(self):
		if not _mutations_enabled():
			return
		avatar = _pick_avatar()
		if not avatar:
			return
		api_put(self, "/api/v1/profile/avatar", json={"avatar": avatar})

	@task(1)
	def preview_and_redeem_voucher(self):
		if not _mutations_enabled():
			return
		voucher = _pick_voucher()
		if not voucher:
			return

		preview_resp = api_post(
			self,
			"/api/v1/voucher/preview",
			json={"pin": voucher["pin"]},
		)
		if not preview_resp:
			return

		grant_id = voucher.get("grant_id")
		if not grant_id:
			try:
				grants = preview_resp.json().get("grants", [])
				grant_id = grants[0]["grant_id"] if grants else None
			except Exception:
				grant_id = None
		if not grant_id:
			return

		api_post(
			self,
			"/api/v1/voucher/redeem",
			json={"pin": voucher["pin"], "grant_id": grant_id},
		)

	@task(1)
	def submit_purchase(self):
		if not _mutations_enabled():
			return
		product_grant_id = _pick_product_grant()
		if not product_grant_id:
			return
		api_post(
			self,
			"/api/v1/purchase/",
			json={"product_grant_id": product_grant_id, "payment_method": "Manual-Admin"},
		)

	@task(1)
	def submit_report(self):
		if not _mutations_enabled():
			return
		lesson = random.choice(config.TEST_LESSONS)
		api_post(
			self,
			"/api/v1/reports",
			json={
				"report_type": "Suggestion",
				"description": "Locust load-test report submission",
				"subject": lesson["subject_id"],
				"lesson": lesson["lesson_id"],
			},
		)

	@task(1)
	def change_plan(self):
		if not _mutations_enabled():
			return
		new_plan_id = _pick_change_plan()
		if not new_plan_id:
			return
		api_post(self, "/api/v1/plans/change", json={"new_plan_id": new_plan_id})


class AdminAccessUser(AdminAuthMixin, HttpUser):
	"""Optional admin-only coverage for access grant endpoints."""

	weight = 1
	wait_time = between(10, 20)

	@task
	def manage_access(self):
		if not _admin_enabled():
			return

		player = random.choice(config.TEST_PLAYERS)
		player_id = player.get("player_id")
		content_keys = getattr(config, "TEST_ACCESS_CONTENT_KEYS", None) or []
		if not player_id or not content_keys:
			return

		payload = {"player_id": player_id, "content_keys": [random.choice(content_keys)]}
		api_post(self, "/api/v1/access/grants", json=payload)
		api_get(
			self,
			f"/api/v1/access/grants/{player_id}",
			name="/api/v1/access/grants/[player]",
		)
		api_delete(self, "/api/v1/access/grants", json=payload)


class WebhookUser(HttpUser):
	"""Optional external-provider simulation for payment webhook coverage."""

	weight = 1
	wait_time = between(15, 30)

	@task
	def send_payment_webhook(self):
		if not _webhooks_enabled():
			return

		events = getattr(config, "TEST_WEBHOOK_EVENTS", None) or []
		if not events:
			return

		event = random.choice(events)
		payload = {
			"event_id": f"evt-{uuid4().hex[:16]}",
			"event_type": event.get("event_type", "payment.completed"),
			"transaction_id": f"txn-{uuid4().hex[:12]}",
			"player_id": event["player_id"],
			"product_grant_id": event["product_grant_id"],
			"amount": event.get("amount", 50.0),
			"currency": event.get("currency", "EGP"),
			"timestamp": datetime.now(timezone.utc).isoformat(),
		}

		with self.client.post(
			"/api/v1/webhooks/payment",
			json=payload,
			name="/api/v1/webhooks/payment",
			catch_response=True,
		) as resp:
			if resp.status_code >= 400:
				resp.failure(f"{resp.status_code}: {resp.text[:200]}")
				return
			resp.success()
