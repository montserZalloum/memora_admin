"""Device management service with atomic registration via Lua script."""

from datetime import datetime, timezone
from typing import Any, Optional

import redis.asyncio as redis
import structlog
from user_agents import parse

from fastapi_app.models.device import DeviceInfo, DeviceRegistrationResult

logger = structlog.get_logger()


# Lua script for atomic device registration
# KEYS[1] = memora:devices:{user_id}
# ARGV[1-7] = device_id, device_name, user_agent, platform, timestamp, max_devices, fingerprint
#
# Returns:
#   {1, device_id, "existing"} - device already registered, updated last_login
#   {1, device_id, "fingerprint_match"} - same device with new UUID, replaced old
#   {1, device_id, "new"} - new device registered
#   {0, "", "limit_exceeded", count, max} - limit reached, registration blocked
REGISTER_DEVICE_SCRIPT = """
local key = KEYS[1]
local device_id = ARGV[1]
local device_name = ARGV[2]
local user_agent = ARGV[3]
local platform = ARGV[4]
local timestamp = ARGV[5]
local max_devices = tonumber(ARGV[6])
local fingerprint = ARGV[7]

-- Check if device already exists by ID (existing login)
local existing = redis.call('HGET', key, 'device:' .. device_id .. ':fingerprint')
if existing then
    -- Update last_login and return success
    redis.call('HSET', key, 'device:' .. device_id .. ':last_login', timestamp)
    return {1, device_id, 'existing'}
end

-- Scan for fingerprint match (same device, new UUID after reinstall)
-- Also count total devices
local all_fields = redis.call('HGETALL', key)
local device_count = 0
local matched_device = nil

for i = 1, #all_fields, 2 do
    local field = all_fields[i]
    -- Count devices by fingerprint fields
    if string.match(field, '^device:.*:fingerprint$') then
        device_count = device_count + 1
        -- Check if fingerprint matches
        if all_fields[i+1] == fingerprint then
            matched_device = string.match(field, '^device:(.+):fingerprint$')
        end
    end
end

-- Fingerprint match: update existing slot with new UUID
if matched_device then
    -- Remove old device fields
    redis.call('HDEL', key,
        'device:' .. matched_device .. ':name',
        'device:' .. matched_device .. ':ua',
        'device:' .. matched_device .. ':platform',
        'device:' .. matched_device .. ':last_login',
        'device:' .. matched_device .. ':fingerprint',
        'device:' .. matched_device .. ':push_token')
    -- Add with new UUID (reuses same slot)
    redis.call('HSET', key,
        'device:' .. device_id .. ':name', device_name,
        'device:' .. device_id .. ':ua', user_agent,
        'device:' .. device_id .. ':platform', platform,
        'device:' .. device_id .. ':last_login', timestamp,
        'device:' .. device_id .. ':fingerprint', fingerprint)
    return {1, device_id, 'fingerprint_match'}
end

-- New device: check limit before registering
if device_count >= max_devices then
    return {0, '', 'limit_exceeded', device_count, max_devices}
end

-- Register new device
redis.call('HSET', key,
    'device:' .. device_id .. ':name', device_name,
    'device:' .. device_id .. ':ua', user_agent,
    'device:' .. device_id .. ':platform', platform,
    'device:' .. device_id .. ':last_login', timestamp,
    'device:' .. device_id .. ':fingerprint', fingerprint)

return {1, device_id, 'new'}
"""


class DeviceService:
	"""
	Manages device registration with atomic limit enforcement.

	Per CONTEXT.md:
	- Device limit from Memora Settings (max_devices_per_player)
	- Fingerprint fallback for device recognition after app reinstall
	- Atomic registration via Lua script to prevent race conditions
	- Admin removal handled separately via Frappe hooks
	"""

	def __init__(self, redis_client: redis.Redis, key_prefix: str = "memora:"):
		"""
		Initialize DeviceService.

		Args:
			redis_client: Async Redis client
			key_prefix: Prefix for Redis keys (default: "memora:")
		"""
		self.redis = redis_client
		self.prefix = key_prefix
		self._register_script: Optional[Any] = None  # redis Script object

	def _device_key(self, user_id: str) -> str:
		"""Get Redis key for user's device registry."""
		return f"{self.prefix}devices:{user_id}"

	async def _get_register_script(self) -> Any:
		"""Get or create the registration Lua script (lazy-loaded and cached)."""
		if self._register_script is None:
			self._register_script = self.redis.register_script(REGISTER_DEVICE_SCRIPT)
		return self._register_script

	def _generate_fingerprint(self, user_agent: str) -> str:
		"""
		Generate stable fingerprint from user agent.

		Uses device family + brand + OS family + browser family (without versions)
		to handle browser updates gracefully. Version numbers are excluded because
		they change frequently with updates.

		Args:
			user_agent: Raw User-Agent header value

		Returns:
			Fingerprint string in format "device:brand:os:browser"
		"""
		ua = parse(user_agent)
		components = [
			ua.device.family or "Unknown",
			ua.device.brand or "",
			ua.os.family or "Unknown",
			ua.browser.family or "Unknown",
		]
		# Join non-empty components with colon separator
		return ":".join(c for c in components if c)

	def _extract_device_info(
		self,
		user_agent: str,
		platform_hint: str | None = None,
	) -> dict:
		"""
		Extract device metadata from user agent string.

		Args:
			user_agent: Raw User-Agent header value
			platform_hint: Client-provided platform (iOS, Android, Web) for override

		Returns:
			dict with device_name, platform, user_agent, fingerprint
		"""
		ua = parse(user_agent)

		# Determine platform (client hint overrides if valid)
		if platform_hint and platform_hint in ("iOS", "Android", "Web"):
			platform = platform_hint
		elif ua.is_mobile or ua.is_tablet:
			# Check for Apple devices
			if "iPhone" in str(ua.device.family) or "iPad" in str(ua.device.family):
				platform = "iOS"
			elif "iOS" in str(ua.os.family):
				platform = "iOS"
			else:
				platform = "Android"
		else:
			platform = "Web"

		# Generate device name: "{Device} / {OS}"
		# Examples: "iPhone / iOS 17.0", "Chrome / Windows 10", "Samsung Galaxy S23 / Android 14"
		if ua.device.family and ua.device.family != "Other":
			# Use brand + model if available (e.g., "Samsung Galaxy S23")
			if ua.device.brand and ua.device.model and ua.device.model != ua.device.family:
				device_part = f"{ua.device.brand} {ua.device.model}"
			else:
				device_part = ua.device.family
		else:
			# Fall back to browser family for web
			device_part = ua.browser.family or "Unknown Browser"

		os_part = ua.os.family or "Unknown OS"
		if ua.os.version_string:
			os_part += f" {ua.os.version_string}"

		return {
			"device_name": f"{device_part} / {os_part}",
			"platform": platform,
			"user_agent": user_agent,
			"fingerprint": self._generate_fingerprint(user_agent),
		}

	async def register_device(
		self,
		user_id: str,
		device_id: str,
		user_agent: str,
		max_devices: int,
		platform_hint: str | None = None,
	) -> DeviceRegistrationResult:
		"""
		Register device atomically with limit enforcement.

		This method uses a Lua script to atomically:
		1. Check if device already exists (by ID) -> update last_login
		2. Check for fingerprint match (same device, new UUID) -> replace old slot
		3. Check device count against limit -> block if exceeded
		4. Register new device

		Args:
			user_id: Player's user ID
			device_id: Client-generated UUID
			user_agent: User-Agent header value
			max_devices: Maximum allowed devices from settings
			platform_hint: Optional platform override from client

		Returns:
			DeviceRegistrationResult with success status and device info
		"""
		info = self._extract_device_info(user_agent, platform_hint)
		timestamp = datetime.now(tz=timezone.utc).isoformat()

		script = await self._get_register_script()
		key = self._device_key(user_id)

		result = await script(
			keys=[key],
			args=[
				device_id,
				info["device_name"],
				info["user_agent"],
				info["platform"],
				timestamp,
				str(max_devices),
				info["fingerprint"],
			],
		)

		success = bool(result[0])
		registered_id = result[1] if len(result) > 1 else ""
		status = result[2] if len(result) > 2 else "unknown"

		# Handle bytes from Redis if decode_responses=False
		if isinstance(registered_id, bytes):
			registered_id = registered_id.decode("utf-8")
		if isinstance(status, bytes):
			status = status.decode("utf-8")

		if success:
			logger.info(
				"device_registered",
				user_id=user_id,
				device_id=registered_id,
				status=status,
				platform=info["platform"],
				device_name=info["device_name"],
			)
			return DeviceRegistrationResult(
				success=True,
				device_id=registered_id,
				device_name=info["device_name"],
				status=status,
			)
		else:
			current = int(result[3]) if len(result) > 3 else 0
			limit = int(result[4]) if len(result) > 4 else max_devices
			logger.warning(
				"device_limit_exceeded",
				user_id=user_id,
				current_count=current,
				max_count=limit,
			)
			return DeviceRegistrationResult(
				success=False,
				device_id="",
				device_name="",
				status="limit_exceeded",
				current_count=current,
				max_count=limit,
			)

	async def get_devices(self, user_id: str) -> list[DeviceInfo]:
		"""
		Get all registered devices for user.

		Parses Redis hash fields into DeviceInfo objects.

		Args:
			user_id: Player's user ID

		Returns:
			List of DeviceInfo for all registered devices
		"""
		key = self._device_key(user_id)
		data = await self.redis.hgetall(key)

		# Parse hash fields into device objects
		# Fields: device:{id}:name, device:{id}:ua, device:{id}:platform, etc.
		devices: dict[str, dict] = {}
		for field, value in data.items():
			# Handle bytes from Redis if decode_responses=False
			if isinstance(field, bytes):
				field = field.decode("utf-8")
			if isinstance(value, bytes):
				value = value.decode("utf-8")

			parts = field.split(":")
			if len(parts) == 3 and parts[0] == "device":
				device_id, attr = parts[1], parts[2]
				if device_id not in devices:
					devices[device_id] = {"device_id": device_id}
				# Map Redis field names to model attributes
				if attr == "name":
					devices[device_id]["device_name"] = value
				elif attr == "ua":
					devices[device_id]["user_agent"] = value
				elif attr == "last_login":
					devices[device_id]["last_login"] = value
				else:
					devices[device_id][attr] = value

		return [DeviceInfo(**d) for d in devices.values()]

	async def remove_device(self, user_id: str, device_id: str) -> bool:
		"""
		Remove a specific device from user's registry.

		Note: This does NOT invalidate session. For complete device removal
		(e.g., admin-initiated), use with SessionService.invalidate_session().

		Args:
			user_id: Player's user ID
			device_id: Device UUID to remove

		Returns:
			True if device existed and was removed, False otherwise
		"""
		key = self._device_key(user_id)
		fields = [
			f"device:{device_id}:name",
			f"device:{device_id}:ua",
			f"device:{device_id}:platform",
			f"device:{device_id}:last_login",
			f"device:{device_id}:fingerprint",
			f"device:{device_id}:push_token",
		]
		deleted = await self.redis.hdel(key, *fields)

		if deleted > 0:
			logger.info(
				"device_removed",
				user_id=user_id,
				device_id=device_id,
			)

		return deleted > 0

	async def validate_device(self, user_id: str, device_id: str) -> bool:
		"""
		Check if device is registered for user.

		Uses HEXISTS for O(1) lookup.

		Args:
			user_id: Player's user ID
			device_id: Device UUID to check

		Returns:
			True if device is registered, False otherwise
		"""
		key = self._device_key(user_id)
		exists = await self.redis.hexists(key, f"device:{device_id}:fingerprint")
		return exists

	async def update_push_token(
		self,
		user_id: str,
		device_id: str,
		push_token: str,
	) -> bool:
		"""
		Update push notification token for a device.

		Args:
			user_id: Player's user ID
			device_id: Device UUID
			push_token: FCM/APNs push token

		Returns:
			True if device exists and token was updated, False otherwise
		"""
		# First validate device exists
		if not await self.validate_device(user_id, device_id):
			return False

		key = self._device_key(user_id)
		await self.redis.hset(key, f"device:{device_id}:push_token", push_token)

		logger.info(
			"push_token_updated",
			user_id=user_id,
			device_id=device_id,
		)
		return True
