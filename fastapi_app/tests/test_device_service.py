# Copyright (c) 2026, corex and contributors
"""Tests for DeviceService — device registration with fingerprint matching."""

import uuid
import pytest
import redis.asyncio as redis

from fastapi_app.models.device import DeviceInfo, DeviceRegistrationResult
from fastapi_app.services.device import DeviceService

# Test constants
TEST_USER = "USER-001"
TEST_DEVICE_ID = str(uuid.uuid4())

# Realistic user agents for testing
TEST_USER_AGENT_IPHONE = (
	"Mozilla/5.0 (iPhone; CPU iPhone OS 17_1_1 like Mac OS X) "
	"AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1.1 Mobile/15E148 Safari/604.1"
)

TEST_USER_AGENT_CHROME = (
	"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
	"(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

TEST_USER_AGENT_ANDROID = (
	"Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 "
	"(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
)

# Second iPhone UA (will have same fingerprint as first)
TEST_USER_AGENT_IPHONE_2 = (
	"Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) "
	"AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1"
)

MAX_DEVICES = 3


@pytest.fixture
async def device_service(redis_client: redis.Redis, test_prefix: str) -> DeviceService:
	"""Create DeviceService with test prefix for isolation."""
	return DeviceService(redis_client, key_prefix=test_prefix)


class TestRegisterDevice:
	"""Test device registration with all Lua script paths."""

	async def test_tc_ds_01_new_device_returns_new_status(
		self, device_service: DeviceService, redis_client: redis.Redis, test_prefix: str
	):
		"""TC-DS-01: New device returns status='new' with 6 hash fields."""
		device_id = str(uuid.uuid4())

		result = await device_service.register_device(
			user_id=TEST_USER,
			device_id=device_id,
			user_agent=TEST_USER_AGENT_IPHONE,
			max_devices=MAX_DEVICES,
		)

		assert result.success is True, "Registration should succeed"
		assert result.device_id == device_id, "Returned device_id should match"
		assert result.status == "new", "Status should be 'new'"
		assert result.device_name, "Device name should be populated"

		# Verify 6 hash fields are stored
		key = f"{test_prefix}devices:{TEST_USER}"
		fields = await redis_client.hgetall(key)

		# Count device fields for this device
		device_fields = [f for f in fields.keys() if device_id in str(f)]
		assert len(device_fields) >= 5, f"Should have at least 5 device fields, got {device_fields}"

	async def test_tc_ds_02_existing_device_updates_last_login(
		self, device_service: DeviceService, redis_client: redis.Redis
	):
		"""TC-DS-02: Existing device updates last_login returns status='existing'."""
		device_id = str(uuid.uuid4())

		# First registration
		result1 = await device_service.register_device(
			user_id=TEST_USER,
			device_id=device_id,
			user_agent=TEST_USER_AGENT_IPHONE,
			max_devices=MAX_DEVICES,
		)
		assert result1.status == "new", "First registration should be new"

		# Second registration with same device_id
		result2 = await device_service.register_device(
			user_id=TEST_USER,
			device_id=device_id,
			user_agent=TEST_USER_AGENT_IPHONE,
			max_devices=MAX_DEVICES,
		)

		assert result2.success is True, "Second registration should succeed"
		assert result2.device_id == device_id, "Returned device_id should match"
		assert result2.status == "existing", "Status should be 'existing'"

	async def test_tc_ds_03_fingerprint_match_replaces_device(
		self, device_service: DeviceService, redis_client: redis.Redis, test_prefix: str
	):
		"""TC-DS-03: Different device_id with matching fingerprint replaces old device."""
		device_id_1 = str(uuid.uuid4())
		device_id_2 = str(uuid.uuid4())

		# Register first device with iPhone UA
		result1 = await device_service.register_device(
			user_id=TEST_USER,
			device_id=device_id_1,
			user_agent=TEST_USER_AGENT_IPHONE,
			max_devices=MAX_DEVICES,
		)
		assert result1.status == "new", "First registration should be new"

		# Register second device with different UUID but same fingerprint (iPhone UA)
		result2 = await device_service.register_device(
			user_id=TEST_USER,
			device_id=device_id_2,
			user_agent=TEST_USER_AGENT_IPHONE_2,  # Different UA but same fingerprint family
			max_devices=MAX_DEVICES,
		)

		assert result2.success is True, "Second registration should succeed"
		assert result2.status == "fingerprint_match", "Status should be 'fingerprint_match'"

		# Verify old device_id is no longer in Redis
		key = f"{test_prefix}devices:{TEST_USER}"
		device_fields_old = [f for f in (await redis_client.hgetall(key)).keys() if device_id_1 in str(f)]
		assert len(device_fields_old) == 0, "Old device should be removed"

		# Verify new device_id is in Redis
		device_fields_new = [
			f for f in (await redis_client.hgetall(key)).keys()
			if device_id_2 in str(f)
		]
		assert len(device_fields_new) >= 5, "New device should be registered"

	async def test_tc_ds_04_max_devices_exceeded_returns_limit_exceeded(
		self, device_service: DeviceService
	):
		"""TC-DS-04: Max devices exceeded returns success=False status='limit_exceeded'."""
		# Register MAX_DEVICES (3) different devices with distinct UAs
		distinct_uas = [TEST_USER_AGENT_IPHONE, TEST_USER_AGENT_CHROME, TEST_USER_AGENT_ANDROID]

		for i in range(MAX_DEVICES):
			device_id = str(uuid.uuid4())
			result = await device_service.register_device(
				user_id=TEST_USER,
				device_id=device_id,
				user_agent=distinct_uas[i],
				max_devices=MAX_DEVICES,
			)
			assert result.success is True, f"Registration {i+1} should succeed"

		# Try to register 4th device (should fail)
		device_id_4 = str(uuid.uuid4())
		firefox_ua = (
			"Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0"
		)
		result = await device_service.register_device(
			user_id=TEST_USER,
			device_id=device_id_4,
			user_agent=firefox_ua,
			max_devices=MAX_DEVICES,
		)

		assert result.success is False, "4th registration should fail"
		assert result.status == "limit_exceeded", "Status should be 'limit_exceeded'"
		assert result.current_count == MAX_DEVICES, f"Current count should be {MAX_DEVICES}"
		assert result.max_count == MAX_DEVICES, f"Max count should be {MAX_DEVICES}"


class TestDeviceManagement:
	"""Test device management operations."""

	async def test_tc_ds_05_get_devices_returns_list(self, device_service: DeviceService):
		"""TC-DS-05: Get devices returns list of DeviceInfo objects."""
		# Register 2 devices with distinct UAs
		device_ids = []
		distinct_uas = [TEST_USER_AGENT_IPHONE, TEST_USER_AGENT_CHROME]

		for i in range(2):
			device_id = str(uuid.uuid4())
			device_ids.append(device_id)
			await device_service.register_device(
				user_id=TEST_USER,
				device_id=device_id,
				user_agent=distinct_uas[i],
				max_devices=MAX_DEVICES,
			)

		# Get devices
		devices = await device_service.get_devices(TEST_USER)

		assert len(devices) == 2, "Should return 2 devices"
		assert all(isinstance(d, DeviceInfo) for d in devices), "All should be DeviceInfo"

		returned_ids = {d.device_id for d in devices}
		assert device_ids[0] in returned_ids, "First device should be in list"
		assert device_ids[1] in returned_ids, "Second device should be in list"

	async def test_tc_ds_06_remove_device_deletes_all_fields(
		self, device_service: DeviceService, redis_client: redis.Redis, test_prefix: str
	):
		"""TC-DS-06: Remove device deletes all 6 hash fields."""
		device_id = str(uuid.uuid4())

		# Register device
		await device_service.register_device(
			user_id=TEST_USER,
			device_id=device_id,
			user_agent=TEST_USER_AGENT_IPHONE,
			max_devices=MAX_DEVICES,
		)

		# Verify device exists
		key = f"{test_prefix}devices:{TEST_USER}"
		fields_before = [f for f in (await redis_client.hgetall(key)).keys() if device_id in str(f)]
		assert len(fields_before) >= 5, "Device should have fields before removal"

		# Remove device
		removed = await device_service.remove_device(TEST_USER, device_id)

		assert removed is True, "Should return True when removing existing device"

		# Verify all fields are deleted
		fields_after = [f for f in (await redis_client.hgetall(key)).keys() if device_id in str(f)]
		assert len(fields_after) == 0, "All device fields should be deleted"

	async def test_tc_ds_07_validate_device_returns_true_for_registered(
		self, device_service: DeviceService
	):
		"""TC-DS-07: Validate device returns True for registered device."""
		device_id = str(uuid.uuid4())

		# Register device
		await device_service.register_device(
			user_id=TEST_USER,
			device_id=device_id,
			user_agent=TEST_USER_AGENT_IPHONE,
			max_devices=MAX_DEVICES,
		)

		# Validate device
		is_valid = await device_service.validate_device(TEST_USER, device_id)

		assert is_valid is True, "Registered device should be valid"

	async def test_tc_ds_08_validate_device_returns_false_for_unknown(
		self, device_service: DeviceService
	):
		"""TC-DS-08: Validate device returns False for unknown device."""
		unknown_device_id = str(uuid.uuid4())

		# Validate non-existent device
		is_valid = await device_service.validate_device(TEST_USER, unknown_device_id)

		assert is_valid is False, "Unknown device should not be valid"
