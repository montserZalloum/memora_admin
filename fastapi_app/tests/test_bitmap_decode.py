"""Tests for single-fetch bitmap decode in ProgressService.get_completed_bits().

Tests the BITFIELD-based bitmap decode that replaced the N-GETBIT pipeline,
verifying correct bit extraction for edge cases: empty keys, sparse bitmaps,
full byte coverage (0x00-0xFF), and partial bitmaps.
"""

import pytest
import redis.asyncio as redis

from fastapi_app.core.redis_keys import progress_key
from fastapi_app.services.progress import BITFIELD_CHUNK_SIZE, ProgressService


# Use a unique user/subject per test to avoid cross-test pollution
TEST_USER = "USER-BITMAP-TEST"
TEST_SUBJECT = "SUBJ-BITMAP-TEST"
TEST_VERSION = 1


def _key() -> str:
	return progress_key(TEST_USER, TEST_SUBJECT, TEST_VERSION)


@pytest.fixture
async def svc(redis_client: redis.Redis) -> ProgressService:
	"""ProgressService with real Redis, no Frappe (hydration skipped)."""
	return ProgressService(redis_client, frappe_client=None)


@pytest.fixture(autouse=True)
async def _cleanup_bitmap(redis_client: redis.Redis):
	"""Clean up test bitmap key after each test."""
	yield
	await redis_client.delete(_key())


class TestBitmapDecodeEmptyKey:
	"""T013.1: Empty/missing key returns empty set."""

	@pytest.mark.asyncio
	async def test_missing_key_returns_empty_set(self, svc: ProgressService):
		"""GET on non-existent key returns empty set (no error)."""
		result = await svc.get_completed_bits(TEST_USER, TEST_SUBJECT, bit_range=500)
		assert result == set()

	@pytest.mark.asyncio
	async def test_deleted_key_returns_empty_set(self, svc: ProgressService, redis_client: redis.Redis):
		"""After deleting an existing bitmap, returns empty set."""
		# Set a bit, then delete the key
		await redis_client.setbit(_key(), 0, 1)
		await redis_client.delete(_key())

		result = await svc.get_completed_bits(TEST_USER, TEST_SUBJECT, bit_range=100)
		assert result == set()


class TestBitmapDecodeBitRangeZero:
	"""T013.2: bit_range=0 returns empty set (no iteration)."""

	@pytest.mark.asyncio
	async def test_bit_range_zero_returns_empty(self, svc: ProgressService, redis_client: redis.Redis):
		"""bit_range=0 short-circuits to empty set even if bitmap has data."""
		# Set some bits in Redis
		await redis_client.setbit(_key(), 0, 1)
		await redis_client.setbit(_key(), 5, 1)

		result = await svc.get_completed_bits(TEST_USER, TEST_SUBJECT, bit_range=0)
		assert result == set()

	@pytest.mark.asyncio
	async def test_negative_bit_range_returns_empty(self, svc: ProgressService):
		"""Negative bit_range returns empty set."""
		result = await svc.get_completed_bits(TEST_USER, TEST_SUBJECT, bit_range=-1)
		assert result == set()


class TestBitmapDecodeSparse:
	"""T013.3: Sparse bitmap (5 of 500 bits set) returns correct 5-element set."""

	@pytest.mark.asyncio
	async def test_sparse_bitmap_correct_bits(self, svc: ProgressService, redis_client: redis.Redis):
		"""Set 5 specific bits across a 500-bit range, verify exact set returned."""
		expected_bits = {0, 42, 127, 300, 499}

		for bit in expected_bits:
			await redis_client.setbit(_key(), bit, 1)

		result = await svc.get_completed_bits(TEST_USER, TEST_SUBJECT, bit_range=500)
		assert result == expected_bits

	@pytest.mark.asyncio
	async def test_sparse_bitmap_no_extra_bits(self, svc: ProgressService, redis_client: redis.Redis):
		"""Only the set bits are returned — no false positives."""
		set_bits = {10, 20, 30}
		for bit in set_bits:
			await redis_client.setbit(_key(), bit, 1)

		result = await svc.get_completed_bits(TEST_USER, TEST_SUBJECT, bit_range=100)
		assert len(result) == 3
		assert result == set_bits


class TestBitmapDecodeFullByteCoverage:
	"""T013.4: All byte values 0x00-0xFF round-trip losslessly through BITFIELD decode."""

	@pytest.mark.asyncio
	async def test_all_byte_values_lossless(self, svc: ProgressService, redis_client: redis.Redis):
		"""Set specific bits to produce all 256 possible byte values, verify lossless decode.

		We write 256 bytes (2048 bits) where byte[i] = i, using SETBIT to set
		the appropriate bits per MSB-first ordering. Then verify get_completed_bits
		returns exactly the right set.
		"""
		# Build expected bits: for each byte value 0-255, set the appropriate bits
		expected = set()
		for byte_idx in range(256):
			byte_val = byte_idx  # byte[i] = i to cover all 0x00-0xFF
			for bit_pos in range(8):
				if byte_val & (0x80 >> bit_pos):
					global_bit = byte_idx * 8 + bit_pos
					expected.add(global_bit)
					await redis_client.setbit(_key(), global_bit, 1)

		bit_range = 256 * 8  # 2048 bits
		result = await svc.get_completed_bits(TEST_USER, TEST_SUBJECT, bit_range=bit_range)
		assert result == expected, (
			f"Lossless round-trip failed: "
			f"missing={expected - result}, extra={result - expected}"
		)


class TestBitmapDecodePartial:
	"""T013.5: Partial bitmap (bit_range exceeds stored bytes) — out-of-range bits treated as 0."""

	@pytest.mark.asyncio
	async def test_bit_range_exceeds_bitmap(self, svc: ProgressService, redis_client: redis.Redis):
		"""When bit_range is larger than the stored bitmap, extra bits are 0 (unset)."""
		# Set bits only in the first 2 bytes (bits 0-15)
		set_bits = {0, 7, 8, 15}
		for bit in set_bits:
			await redis_client.setbit(_key(), bit, 1)

		# Request bit_range=1000 — way beyond the 2 bytes stored
		result = await svc.get_completed_bits(TEST_USER, TEST_SUBJECT, bit_range=1000)

		# Should only contain the bits we actually set
		assert result == set_bits

	@pytest.mark.asyncio
	async def test_single_bit_at_start(self, svc: ProgressService, redis_client: redis.Redis):
		"""Single bit at position 0, large bit_range — returns {0}."""
		await redis_client.setbit(_key(), 0, 1)

		result = await svc.get_completed_bits(TEST_USER, TEST_SUBJECT, bit_range=10000)
		assert result == {0}

	@pytest.mark.asyncio
	async def test_bit_range_trims_higher_bits(self, svc: ProgressService, redis_client: redis.Redis):
		"""Bits set beyond bit_range are NOT included in result."""
		# Set bit 50 (within range) and bit 200 (beyond range)
		await redis_client.setbit(_key(), 50, 1)
		await redis_client.setbit(_key(), 200, 1)

		result = await svc.get_completed_bits(TEST_USER, TEST_SUBJECT, bit_range=100)
		assert result == {50}  # bit 200 is beyond bit_range=100


class TestBitmapDecodeChunked:
	"""Chunked BITFIELD path: bitmaps larger than BITFIELD_CHUNK_SIZE bytes."""

	@pytest.mark.asyncio
	async def test_chunked_path_sparse(self, svc: ProgressService, redis_client: redis.Redis):
		"""Bits spread across multiple chunks are all returned correctly."""
		# bit_range that exceeds BITFIELD_CHUNK_SIZE bytes → forces chunked path
		bits_per_chunk = BITFIELD_CHUNK_SIZE * 8
		bit_range = bits_per_chunk * 2 + 100  # 2 full chunks + partial 3rd

		# Place one bit in each chunk
		expected = {
			0,                         # first bit of chunk 0
			bits_per_chunk - 1,        # last bit of chunk 0
			bits_per_chunk,            # first bit of chunk 1
			bits_per_chunk * 2 + 50,   # middle of chunk 2
		}
		for bit in expected:
			await redis_client.setbit(_key(), bit, 1)

		result = await svc.get_completed_bits(TEST_USER, TEST_SUBJECT, bit_range=bit_range)
		assert result == expected

	@pytest.mark.asyncio
	async def test_chunked_path_ordering(self, svc: ProgressService, redis_client: redis.Redis):
		"""Chunk flattening preserves correct byte ordering across boundaries."""
		bits_per_chunk = BITFIELD_CHUNK_SIZE * 8
		bit_range = bits_per_chunk + 16  # just over 1 chunk

		# Set the last bit of chunk 0 and first bit of chunk 1
		boundary_bits = {bits_per_chunk - 1, bits_per_chunk}
		for bit in boundary_bits:
			await redis_client.setbit(_key(), bit, 1)

		result = await svc.get_completed_bits(TEST_USER, TEST_SUBJECT, bit_range=bit_range)
		assert result == boundary_bits

	@pytest.mark.asyncio
	async def test_chunked_empty_key(self, svc: ProgressService):
		"""Chunked path on missing key returns empty set."""
		bits_per_chunk = BITFIELD_CHUNK_SIZE * 8
		bit_range = bits_per_chunk * 3  # forces 3 chunks

		result = await svc.get_completed_bits(TEST_USER, TEST_SUBJECT, bit_range=bit_range)
		assert result == set()
