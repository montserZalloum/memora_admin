"""Player authentication APIs for FastAPI bridge.

Five whitelisted Frappe APIs that let the FastAPI sidecar verify passwords,
register players, manage passwords, check phone existence, and fetch
registration options -- all without creating Frappe sessions.

Called by FastAPI via FrappeClient with API key auth (allow_guest=False).
"""

import re

import frappe

from fastapi_app.core.redis_keys import session_key, wallet_key

MOBILE_PATTERN = re.compile(r"^\d{9,15}$")


@frappe.whitelist(allow_guest=False)
def verify_player_password(mobile: str, password: str) -> dict:
	"""Verify player credentials and return profile data including XP.

	Resolves mobile -> docname FIRST (critical: __Auth keys by docname, not phone).
	Returns generic 'Invalid credentials' for both wrong phone and wrong password
	to prevent phone enumeration.

	Does NOT create a Frappe session -- just verifies and returns data.
	"""
	# Normalize phone
	cleaned = re.sub(r"[^\d]", "", mobile)
	if not MOBILE_PATTERN.match(cleaned):
		frappe.throw("Invalid credentials", frappe.AuthenticationError)

	# Resolve mobile to docname
	player_name = frappe.db.get_value("Memora Player Profile", {"mobile": cleaned}, "name")
	if not player_name:
		frappe.throw("Invalid credentials", frappe.AuthenticationError)

	# Verify password against __Auth table (keyed by docname)
	try:
		from frappe.utils.password import check_password

		check_password(player_name, password, doctype="Memora Player Profile", fieldname="password")
	except frappe.AuthenticationError:
		frappe.throw("Invalid credentials", frappe.AuthenticationError)

	# Fetch profile and XP
	profile = frappe.get_doc("Memora Player Profile", player_name)
	xp = _get_player_xp(player_name)

	return {
		"player_id": profile.name,
		"display_name": profile.display_name,
		"plan": profile.plan,
		"avatar": profile.avatar,
		"gender": profile.gender,
		"mobile": profile.mobile,
		"xp": xp,
	}


@frappe.whitelist(allow_guest=False)
def register_player(
	mobile: str,
	password: str,
	plan: str,
	grade: str,
	major: str,
	season: str,
	display_name: str | None = None,
	avatar: str | None = None,
	gender: str | None = None,
) -> dict:
	"""Register a new player with PBKDF2-SHA256 hashed password.

	The DocType's __setup__/validate/after_insert hooks handle password hashing
	and wallet creation automatically. This function additionally seeds the Redis
	wallet so the player is fully ready after registration.

	Returns profile data including xp: 0.
	"""
	# Normalize phone
	cleaned = re.sub(r"[^\d]", "", mobile)
	if not MOBILE_PATTERN.match(cleaned):
		frappe.throw("Mobile number must be 9-15 digits", frappe.ValidationError)

	# Check uniqueness (specific error is safe -- OTP verified before register)
	if frappe.db.exists("Memora Player Profile", {"mobile": cleaned}):
		frappe.throw("Phone already registered", frappe.DuplicateEntryError)

	# Default display_name
	if not display_name:
		count = frappe.db.count("Memora Player Profile") + 1
		display_name = f"\u0644\u0627\u0639\u0628 {count}"

	# Default avatar
	if not avatar:
		avatar = "pre"

	# Create player doc -- DocType hooks handle password hashing + wallet DocType creation
	doc = frappe.get_doc(
		{
			"doctype": "Memora Player Profile",
			"mobile": cleaned,
			"password": password,
			"plan": plan,
			"grade": grade,
			"major": major,
			"season": season,
			"display_name": display_name,
			"avatar": avatar,
			"gender": gender,
		}
	)
	doc.insert(ignore_permissions=True)

	# Seed Redis wallet so player is fully ready immediately
	_initialize_redis_wallet(doc.name)

	return {
		"player_id": doc.name,
		"display_name": doc.display_name,
		"plan": doc.plan,
		"avatar": doc.avatar,
		"gender": doc.gender,
		"mobile": doc.mobile,
		"xp": 0,
	}


@frappe.whitelist(allow_guest=False)
def set_player_password(player_name: str, new_password: str) -> dict:
	"""Update a player's password hash and invalidate all sessions.

	Called from admin Desk form (Reset Password button) or via FrappeClient.
	"""
	if not frappe.db.exists("Memora Player Profile", player_name):
		frappe.throw("Player not found", frappe.DoesNotExistError)

	if len(new_password) < 8:
		frappe.throw("Password must be at least 8 characters", frappe.ValidationError)

	from frappe.utils.password import update_password

	update_password(player_name, new_password, doctype="Memora Player Profile", fieldname="password")

	_invalidate_player_sessions(player_name)

	return {"success": True, "player_name": player_name}


@frappe.whitelist(allow_guest=False)
def check_phone_exists(mobile: str) -> dict:
	"""Check whether a phone number is already registered.

	Used by FastAPI registration endpoint for upfront duplicate detection
	and by password reset for mobile-to-docname resolution.

	Returns:
		{"exists": bool, "player_name": str|None}
	"""
	cleaned = re.sub(r"[^\d]", "", mobile)
	player_name = frappe.db.get_value("Memora Player Profile", {"mobile": cleaned}, "name")
	return {"exists": bool(player_name), "player_name": player_name}


@frappe.whitelist(allow_guest=False)
def get_registration_options() -> dict:
	"""Return available grades, plans, and seasons for registration.

	Called by FastAPI via FrappeClient. Provides data for mobile app pickers.
	Results are cached in Redis by the FastAPI endpoint (5-min TTL).
	Avatars and genders are hardcoded client-side and not included here.
	"""
	# Get published seasons (latest by season_seq)
	seasons = frappe.get_all(
		"Memora Season",
		filters={"is_published": 1},
		fields=["name", "season_title"],
		order_by="season_seq DESC",
		limit=5,
	)

	# Get all grades with their majors (sorted by sort_order)
	grades = frappe.get_all(
		"Memora Grade",
		fields=["name", "grade_title", "sort_order"],
		order_by="sort_order ASC",
	)
	for grade in grades:
		# Fetch child table rows linking to Memora Major
		grade_majors = frappe.get_all(
			"Memora Grade Major",
			filters={"parent": grade["name"]},
			fields=["major"],
		)
		# Resolve each major link to get the title
		majors = []
		for gm in grade_majors:
			major_title = frappe.db.get_value("Memora Major", gm["major"], "major_title")
			majors.append({"name": gm["major"], "title": major_title or gm["major"]})
		grade["majors"] = majors

	# Get published plans
	plans = frappe.get_all(
		"Memora Academic Plan",
		filters={"is_published": 1},
		fields=["name", "plan_name", "grade", "major"],
	)

	return {
		"grades": [
			{
				"name": g["name"],
				"title": g["grade_title"],
				"sort_order": g["sort_order"],
				"majors": g["majors"],
			}
			for g in grades
		],
		"plans": [
			{
				"name": p["name"],
				"title": p["plan_name"],
				"grade": p["grade"],
				"major": p.get("major"),
			}
			for p in plans
		],
		"seasons": [{"name": s["name"], "title": s["season_title"]} for s in seasons],
	}


# =============================================================================
# Helper functions
# =============================================================================


def _get_player_xp(player_name: str) -> int:
	"""Fetch XP from Redis wallet. Returns 0 on miss or failure (non-fatal)."""
	try:
		from memora_admin.events.access_sync import get_fastapi_redis

		r = get_fastapi_redis()
		wk = wallet_key(player_name)
		xp_value = r.hget(wk, "xp")
		return int(xp_value) if xp_value else 0
	except Exception:
		return 0


def _initialize_redis_wallet(player_name: str) -> None:
	"""Seed Redis wallet with xp=0, streak=0. Non-fatal on failure."""
	try:
		from memora_admin.events.access_sync import get_fastapi_redis

		r = get_fastapi_redis()
		wk = wallet_key(player_name)
		r.hset(wk, mapping={"xp": 0, "streak": 0})
	except Exception:
		frappe.logger().warning(f"Failed to initialize Redis wallet for {player_name}")


def _invalidate_player_sessions(player_name: str) -> None:
	"""Delete session key from Redis to force logout. Non-fatal on failure."""
	try:
		from memora_admin.events.access_sync import get_fastapi_redis

		r = get_fastapi_redis()
		sk = session_key(player_name)
		r.delete(sk)
		frappe.logger().info(f"Invalidated session for {player_name}")
	except Exception:
		frappe.logger().error(f"Failed to invalidate session for {player_name}")
