# Phase 6: Build Pipeline - Research

**Researched:** 2026-02-02
**Domain:** Content build pipeline with JSON generation, CDN upload abstraction, and cache invalidation
**Confidence:** HIGH

## Summary

This research covers implementing a content build pipeline for the Memora platform. The pipeline triggers on content DocType changes (Subject, Track, Unit, Topic, Lesson), debounces multiple edits within a 2-minute window, generates hierarchical JSON files for mobile app consumption, uploads to a mock CDN (abstraction layer for future Cloudflare R2), and invalidates FastAPI's hierarchy cache via Redis pub/sub.

The standard approach uses Frappe's `doc_events` hooks to queue builds into the existing `Memora Build Queue` DocType, with `scheduler_events` running a worker every 2 minutes. JSON generation traverses the content hierarchy (already implemented in `hierarchy.py`), producing separate files per level. Redis pub/sub channels notify FastAPI to invalidate its hierarchy cache. The CDN abstraction uses a storage interface pattern with boto3 for S3-compatible storage (R2) and local filesystem for development.

**Primary recommendation:** Use Frappe doc_events to trigger debounced builds via Redis SET with NX/EX flags, process with scheduler_events cron job, generate JSON per CONTEXT.md structure, upload atomically via temp-then-rename pattern, and publish `memora:cache:invalidate` message for FastAPI cache refresh.

## Standard Stack

The established libraries/tools for this domain:

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Frappe hooks (doc_events) | v15 | Trigger builds on content changes | Already in project, standard pattern |
| Frappe scheduler_events | v15 | Run build worker on cron | Built-in, reliable, no extra infrastructure |
| redis-py | 5.0+ | Debounce keys, pub/sub | Already in project, async support |
| boto3 | 1.34+ | S3-compatible CDN upload | Standard for R2/S3, well documented |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| structlog | 24.0+ | Build operation logging | All build events, errors, timing |
| hashlib (stdlib) | - | Content hash for change detection | Verify JSON changes before upload |
| tempfile (stdlib) | - | Atomic file operations | Safe JSON generation |
| os.replace (stdlib) | - | Atomic file swap | Atomic local file operations |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Frappe scheduler | Celery/RQ | Scheduler is simpler, already integrated |
| Redis debounce | Frappe enqueue with dedup | Redis key TTL is cleaner for 2-minute debounce |
| boto3 for R2 | Cloudflare Workers | boto3 is S3-compatible, no vendor lock-in |
| Local mock CDN | MinIO | Filesystem is simpler for dev, no extra service |

**Installation:**
```bash
# boto3 for CDN upload (add to requirements.txt)
pip install boto3
```

## Architecture Patterns

### Recommended Project Structure
```
memora_admin/memora_admin/
├── events/
│   ├── access_sync.py        # Existing
│   └── build_trigger.py      # NEW: doc_events for content changes
├── api/
│   ├── hierarchy.py          # Existing
│   └── build.py              # NEW: Manual build trigger endpoint
├── services/
│   └── build/
│       ├── __init__.py
│       ├── generator.py      # JSON generation logic
│       ├── publisher.py      # CDN upload abstraction
│       └── storage/
│           ├── __init__.py
│           ├── base.py       # Abstract storage interface
│           ├── local.py      # Local filesystem (mock CDN)
│           └── r2.py         # Cloudflare R2 (boto3)
└── tasks/
    └── build_worker.py       # Scheduler job

fastapi_app/
├── core/
│   └── pubsub.py             # NEW: Redis pub/sub listener
└── services/
    └── hierarchy.py          # Existing - add invalidation listener
```

### Pattern 1: Debounced Build Trigger via Redis TTL
**What:** Use Redis SET NX EX to debounce multiple content edits into single build job
**When to use:** All content DocType changes (Subject, Track, Unit, Topic, Lesson)

```python
# Source: CONTEXT.md decisions + Redis patterns
# memora_admin/events/build_trigger.py

import frappe
from datetime import datetime

DEBOUNCE_SECONDS = 120  # 2 minutes per CONTEXT.md
DEBOUNCE_KEY_PREFIX = "memora:build:pending:"


def on_content_updated(doc, method):
    """
    Trigger debounced build on content DocType changes.

    Per CONTEXT.md:
    - Build scope is per-subject
    - Multiple edits within 2-minute window merge into one build
    - Triggers on Lesson and above (not Stage)
    """
    # Get subject ID from document hierarchy
    subject_id = _get_subject_id(doc)
    if not subject_id:
        frappe.log_error(f"Cannot determine subject for {doc.doctype} {doc.name}")
        return

    cache = frappe.cache
    debounce_key = f"{DEBOUNCE_KEY_PREFIX}{subject_id}"

    # SET NX EX pattern: only set if key doesn't exist, with 2-minute TTL
    # If key exists, build already pending - no action needed
    already_pending = cache.set(
        debounce_key,
        datetime.utcnow().isoformat(),
        nx=True,  # Only set if not exists
        ex=DEBOUNCE_SECONDS,
    )

    if already_pending is None:
        # Key already existed - build already queued within debounce window
        frappe.logger().debug(f"Build for {subject_id} already pending, skipping")
        return

    # Queue the build job
    _queue_build(subject_id, doc, method)


def _get_subject_id(doc) -> str | None:
    """Extract subject ID from any content DocType."""
    if doc.doctype == "Memora Subject":
        return doc.name
    elif doc.doctype == "Memora Track":
        return doc.subject
    elif doc.doctype == "Memora Unit":
        track = frappe.get_cached_value("Memora Track", doc.track, "subject")
        return track
    elif doc.doctype == "Memora Topic":
        unit = frappe.get_cached_value("Memora Unit", doc.unit, "track")
        if unit:
            return frappe.get_cached_value("Memora Track", unit, "subject")
    elif doc.doctype == "Memora Lesson":
        return doc.subject  # Lesson has direct subject link
    return None


def _queue_build(subject_id: str, doc, method: str):
    """Create Build Queue entry for subject."""
    build_queue = frappe.new_doc("Memora Build Queue")
    build_queue.target_type = "Memora Subject"
    build_queue.target_name = subject_id
    build_queue.trigger_reason = "content_update"
    build_queue.triggered_by = frappe.session.user
    build_queue.status = "Pending"
    build_queue.insert(ignore_permissions=True)

    frappe.logger().info(f"Queued build for {subject_id} triggered by {doc.doctype} {doc.name}")
```

### Pattern 2: Build Worker via Scheduler Events
**What:** Cron job processes pending builds every 2 minutes
**When to use:** Background processing of queued builds

```python
# Source: Frappe scheduler_events documentation
# memora_admin/tasks/build_worker.py

import frappe
from datetime import datetime


def process_pending_builds():
    """
    Process all pending builds in queue.

    Per CONTEXT.md:
    - Run every 2 minutes via scheduler
    - Process builds for all pending subjects
    """
    pending_builds = frappe.get_all(
        "Memora Build Queue",
        filters={"status": "Pending"},
        fields=["name", "target_name", "target_type"],
        order_by="creation asc",
    )

    for build in pending_builds:
        try:
            _process_single_build(build)
        except Exception as e:
            _mark_build_failed(build.name, str(e))


def _process_single_build(build: dict):
    """Process a single build queue entry."""
    build_doc = frappe.get_doc("Memora Build Queue", build.name)
    build_doc.status = "Processing"
    build_doc.started_at = datetime.utcnow()
    build_doc.save(ignore_permissions=True)

    try:
        from memora_admin.services.build.generator import generate_subject_json
        from memora_admin.services.build.publisher import publish_to_cdn

        # Generate JSON files
        files = generate_subject_json(build.target_name)

        # Upload to CDN with retry
        upload_success = publish_to_cdn(files, max_retries=3)

        if upload_success:
            build_doc.status = "Completed"
            build_doc.files_generated = len(files)
            _notify_cache_invalidation(build.target_name)
            _send_notification(build.target_name, success=True)
        else:
            # Re-queue with exponential backoff
            _requeue_build(build_doc)

    except Exception as e:
        build_doc.status = "Failed"
        build_doc.error_message = str(e)
        _send_notification(build.target_name, success=False, error=str(e))
        raise
    finally:
        build_doc.completed_at = datetime.utcnow()
        if build_doc.started_at:
            build_doc.duration_sec = (
                build_doc.completed_at - build_doc.started_at
            ).total_seconds()
        build_doc.save(ignore_permissions=True)


def _notify_cache_invalidation(subject_id: str):
    """Publish cache invalidation via Redis pub/sub."""
    import json
    cache = frappe.cache
    message = json.dumps({
        "type": "hierarchy",
        "subject_id": subject_id,
        "timestamp": datetime.utcnow().isoformat(),
    })
    cache.publish("memora:cache:invalidate", message)
    frappe.logger().info(f"Published cache invalidation for {subject_id}")


def _send_notification(subject_id: str, success: bool, error: str = None):
    """Send Frappe System Notification to user."""
    if success:
        message = f"Build completed for {subject_id}"
    else:
        message = f"Build failed for {subject_id}: {error}"

    # Notify all System Managers
    frappe.publish_realtime(
        event="build_complete",
        message={"subject_id": subject_id, "success": success, "error": error},
        after_commit=True,
    )
```

### Pattern 3: JSON Generation Per CONTEXT.md Structure
**What:** Generate separate JSON files per hierarchy level
**When to use:** Build processing

```python
# Source: CONTEXT.md JSON output structure decisions
# memora_admin/services/build/generator.py

import frappe
import json
import hashlib
from typing import Any


SCHEMA_VERSION = 1


def generate_subject_json(subject_id: str) -> list[dict]:
    """
    Generate JSON files for subject per CONTEXT.md structure.

    Files generated:
    - _subjects.json -> subjects + track IDs
    - track_{id}.json -> units + topic IDs
    - unit_{id}.json -> topics + lesson IDs
    - topic_{id}.json -> lessons (id, title, url)
    - {subject_id}_b.json -> bitmap metadata

    Per CONTEXT.md:
    - snake_case field naming
    - schema_version inside JSON
    - relative media paths
    """
    files = []

    subject = frappe.get_doc("Memora Subject", subject_id)

    # Generate track-level files
    tracks = frappe.get_all(
        "Memora Track",
        filters={"subject": subject_id, "is_published": 1},
        fields=["name", "track_title", "image", "is_linear", "sort_order"],
        order_by="sort_order asc",
    )

    track_ids = []
    for track in tracks:
        track_file = _generate_track_json(track)
        files.append(track_file)
        track_ids.append(track.name)

    # Generate subjects list (includes this subject with track IDs)
    subjects_data = {
        "schema_version": SCHEMA_VERSION,
        "subjects": [{
            "subject_id": subject.name,
            "title": subject.subject_title,
            "image": _relative_path(subject.image),
            "is_linear": bool(subject.in_linear),
            "track_ids": track_ids,
        }],
    }
    files.append({
        "filename": "_subjects.json",
        "content": json.dumps(subjects_data, ensure_ascii=False, indent=2),
        "subject_id": subject_id,
    })

    # Generate bitmap metadata
    bitmap_data = _generate_bitmap_json(subject_id)
    files.append({
        "filename": f"{subject_id}_b.json",
        "content": json.dumps(bitmap_data, ensure_ascii=False, indent=2),
        "subject_id": subject_id,
    })

    return files


def _generate_track_json(track: dict) -> dict:
    """Generate track JSON with unit IDs."""
    units = frappe.get_all(
        "Memora Unit",
        filters={"track": track.name, "is_published": 1},
        fields=["name", "unit_title", "is_linear", "is_free", "sort_order"],
        order_by="sort_order asc",
    )

    unit_ids = []
    unit_files = []

    for unit in units:
        unit_file = _generate_unit_json(unit)
        unit_files.append(unit_file)
        unit_ids.append(unit.name)

    track_data = {
        "schema_version": SCHEMA_VERSION,
        "track_id": track.name,
        "title": track.track_title,
        "image": _relative_path(track.image),
        "is_linear": bool(track.is_linear),
        "unit_ids": unit_ids,
    }

    return {
        "filename": f"track_{track.name}.json",
        "content": json.dumps(track_data, ensure_ascii=False, indent=2),
        "subject_id": None,  # Track-level file
        "children": unit_files,
    }


def _generate_unit_json(unit: dict) -> dict:
    """Generate unit JSON with topic IDs."""
    topics = frappe.get_all(
        "Memora Topic",
        filters={"unit": unit.name, "is_published": 1},
        fields=["name", "topic_title", "is_linear", "is_free", "sort_order"],
        order_by="sort_order asc",
    )

    topic_ids = []
    topic_files = []

    for topic in topics:
        topic_file = _generate_topic_json(topic)
        topic_files.append(topic_file)
        topic_ids.append(topic.name)

    unit_data = {
        "schema_version": SCHEMA_VERSION,
        "unit_id": unit.name,
        "title": unit.unit_title,
        "is_linear": bool(unit.is_linear),
        "is_free": bool(unit.is_free),
        "topic_ids": topic_ids,
    }

    return {
        "filename": f"unit_{unit.name}.json",
        "content": json.dumps(unit_data, ensure_ascii=False, indent=2),
        "subject_id": None,
        "children": topic_files,
    }


def _generate_topic_json(topic: dict) -> dict:
    """Generate topic JSON with full lesson metadata."""
    lessons = frappe.get_all(
        "Memora Lesson",
        filters={"topic": topic.name},
        fields=["name", "lesson_title", "bit_index"],
        order_by="sort_order asc",
    )

    lesson_data = []
    for lesson in lessons:
        lesson_data.append({
            "lesson_id": lesson.name,
            "title": lesson.lesson_title,
            "url": f"/lessons/{lesson.name}",  # Relative path
        })

    topic_data = {
        "schema_version": SCHEMA_VERSION,
        "topic_id": topic.name,
        "title": topic.topic_title,
        "is_linear": bool(topic.is_linear),
        "is_free": bool(topic.is_free),
        "lessons": lesson_data,
    }

    return {
        "filename": f"topic_{topic.name}.json",
        "content": json.dumps(topic_data, ensure_ascii=False, indent=2),
        "subject_id": None,
        "children": [],
    }


def _generate_bitmap_json(subject_id: str) -> dict:
    """Generate bitmap metadata for progress tracking."""
    # Get all lessons with bit_index
    lessons = frappe.get_all(
        "Memora Lesson",
        filters={"subject": subject_id},
        fields=["name", "bit_index"],
        order_by="bit_index asc",
    )

    # Calculate bit_range (max bit_index + 1)
    bit_range = max((l.bit_index or 0) for l in lessons) + 1 if lessons else 0

    # Find excluded bits (gaps in sequence from deleted lessons)
    used_bits = {l.bit_index for l in lessons if l.bit_index is not None}
    excluded_bits = [i for i in range(bit_range) if i not in used_bits]

    return {
        "schema_version": SCHEMA_VERSION,
        "subject_id": subject_id,
        "bit_range": bit_range,
        "excluded_bits": excluded_bits,
        "generated_at": frappe.utils.now(),
    }


def _relative_path(url: str | None) -> str | None:
    """Convert absolute URL to relative path for CDN migration."""
    if not url:
        return None
    # Strip domain prefix if present
    if url.startswith(("http://", "https://")):
        from urllib.parse import urlparse
        parsed = urlparse(url)
        return parsed.path
    return url
```

### Pattern 4: CDN Storage Abstraction
**What:** Abstract interface for local filesystem and R2 storage
**When to use:** All CDN upload operations

```python
# Source: CONTEXT.md mock CDN requirement + boto3 R2 docs
# memora_admin/services/build/storage/base.py

from abc import ABC, abstractmethod
from typing import Protocol


class StorageBackend(ABC):
    """Abstract storage backend for CDN uploads."""

    @abstractmethod
    def upload(self, key: str, content: bytes, content_type: str = "application/json") -> str:
        """
        Upload content to storage.

        Args:
            key: File path/key in storage
            content: File content as bytes
            content_type: MIME type

        Returns:
            Public URL of uploaded file
        """
        pass

    @abstractmethod
    def delete(self, key: str) -> bool:
        """Delete a file from storage."""
        pass

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Check if file exists."""
        pass


# memora_admin/services/build/storage/local.py

import os
import shutil
from pathlib import Path
from .base import StorageBackend


class LocalStorageBackend(StorageBackend):
    """
    Local filesystem storage for development.

    Mock CDN that writes to local directory.
    Files served via Frappe's static file server.
    """

    def __init__(self, base_path: str, base_url: str = "/files/cdn"):
        self.base_path = Path(base_path)
        self.base_url = base_url
        self.base_path.mkdir(parents=True, exist_ok=True)

    def upload(self, key: str, content: bytes, content_type: str = "application/json") -> str:
        """Upload using atomic temp-then-rename pattern."""
        target_path = self.base_path / key
        target_path.parent.mkdir(parents=True, exist_ok=True)

        # Atomic write: temp file then rename
        import tempfile
        fd, temp_path = tempfile.mkstemp(dir=target_path.parent)
        try:
            with os.fdopen(fd, 'wb') as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, target_path)
        except Exception:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            raise

        return f"{self.base_url}/{key}"

    def delete(self, key: str) -> bool:
        target_path = self.base_path / key
        if target_path.exists():
            target_path.unlink()
            return True
        return False

    def exists(self, key: str) -> bool:
        return (self.base_path / key).exists()


# memora_admin/services/build/storage/r2.py

import boto3
from botocore.config import Config
from .base import StorageBackend


class R2StorageBackend(StorageBackend):
    """
    Cloudflare R2 storage backend via S3-compatible API.

    Per Cloudflare R2 docs:
    - Use boto3 with custom endpoint_url
    - Region is always 'auto' or 'us-east-1'
    - Signature version s3v4
    """

    def __init__(
        self,
        account_id: str,
        access_key_id: str,
        secret_access_key: str,
        bucket_name: str,
        public_url: str,
    ):
        self.bucket_name = bucket_name
        self.public_url = public_url

        self.client = boto3.client(
            "s3",
            endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            config=Config(signature_version="s3v4"),
            region_name="auto",
        )

    def upload(self, key: str, content: bytes, content_type: str = "application/json") -> str:
        """Upload to R2 bucket."""
        self.client.put_object(
            Bucket=self.bucket_name,
            Key=key,
            Body=content,
            ContentType=content_type,
        )
        return f"{self.public_url}/{key}"

    def delete(self, key: str) -> bool:
        try:
            self.client.delete_object(Bucket=self.bucket_name, Key=key)
            return True
        except Exception:
            return False

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket_name, Key=key)
            return True
        except self.client.exceptions.ClientError:
            return False
```

### Pattern 5: Atomic Upload with Retry
**What:** Upload all files to temp location, swap atomically on success
**When to use:** CDN publish step

```python
# Source: CONTEXT.md atomic swap requirement
# memora_admin/services/build/publisher.py

import frappe
from typing import Protocol
import time


def publish_to_cdn(files: list[dict], max_retries: int = 3) -> bool:
    """
    Publish generated JSON files to CDN.

    Per CONTEXT.md:
    - Upload to temp location first
    - Swap only after all files succeed
    - Retry upload 3 times on failure
    - Auto-requeue with exponential backoff after 3 failures
    """
    from memora_admin.services.build.storage import get_storage_backend

    storage = get_storage_backend()
    temp_prefix = f"_temp_{int(time.time())}/"

    # Flatten file tree (including children)
    all_files = _flatten_files(files)

    # Phase 1: Upload all to temp location
    uploaded_temps = []
    for file_info in all_files:
        temp_key = temp_prefix + file_info["filename"]
        content = file_info["content"].encode("utf-8")

        for attempt in range(max_retries):
            try:
                storage.upload(temp_key, content)
                uploaded_temps.append((temp_key, file_info["filename"]))
                break
            except Exception as e:
                if attempt == max_retries - 1:
                    # Cleanup temp files
                    for temp_key, _ in uploaded_temps:
                        try:
                            storage.delete(temp_key)
                        except Exception:
                            pass
                    frappe.log_error(f"CDN upload failed after {max_retries} retries: {e}")
                    return False
                time.sleep(2 ** attempt)  # Exponential backoff

    # Phase 2: Atomic swap - rename temps to final locations
    for temp_key, final_key in uploaded_temps:
        try:
            # Read temp content and write to final location
            # Note: R2/S3 doesn't have rename, so copy-then-delete
            # For local storage, use os.replace
            _atomic_move(storage, temp_key, final_key)
        except Exception as e:
            frappe.log_error(f"Atomic swap failed for {final_key}: {e}")
            return False

    # Phase 3: Cleanup temp files
    for temp_key, _ in uploaded_temps:
        try:
            storage.delete(temp_key)
        except Exception:
            pass  # Best effort cleanup

    return True


def _flatten_files(files: list[dict]) -> list[dict]:
    """Flatten nested file structure."""
    result = []
    for f in files:
        result.append({"filename": f["filename"], "content": f["content"]})
        if "children" in f and f["children"]:
            result.extend(_flatten_files(f["children"]))
    return result


def _atomic_move(storage, source_key: str, dest_key: str):
    """Move file atomically (copy-then-delete for S3-compatible)."""
    # For S3/R2: read content, write to dest, delete source
    # Local storage handles this in the upload method with os.replace
    if hasattr(storage, 'move'):
        storage.move(source_key, dest_key)
    else:
        # S3-compatible: no native rename
        # Content already uploaded to temp, just clean up
        pass
```

### Pattern 6: FastAPI Pub/Sub Listener for Cache Invalidation
**What:** Background task subscribing to Redis pub/sub channel
**When to use:** FastAPI startup to listen for build completions

```python
# Source: redis-py asyncio pub/sub docs
# fastapi_app/core/pubsub.py

import asyncio
import json
import structlog
import redis.asyncio as redis

logger = structlog.get_logger()

INVALIDATION_CHANNEL = "memora:cache:invalidate"


async def start_pubsub_listener(redis_pool: redis.ConnectionPool, app_state):
    """
    Start background task for cache invalidation via pub/sub.

    Per BUILD-06:
    - Subscribe to memora:cache:invalidate channel
    - Invalidate HierarchyService cache on message
    """
    client = redis.Redis(connection_pool=redis_pool)
    pubsub = client.pubsub()

    await pubsub.subscribe(INVALIDATION_CHANNEL)
    logger.info("pubsub_subscribed", channel=INVALIDATION_CHANNEL)

    try:
        async for message in pubsub.listen():
            if message["type"] == "message":
                await _handle_invalidation(message["data"], app_state)
    except asyncio.CancelledError:
        logger.info("pubsub_listener_cancelled")
    finally:
        await pubsub.unsubscribe(INVALIDATION_CHANNEL)
        await client.close()


async def _handle_invalidation(data: str | bytes, app_state):
    """Handle cache invalidation message."""
    try:
        if isinstance(data, bytes):
            data = data.decode()

        payload = json.loads(data)

        if payload.get("type") == "hierarchy":
            subject_id = payload.get("subject_id")
            if subject_id:
                # Get HierarchyService from app state
                hierarchy_service = getattr(app_state, "hierarchy_service", None)
                if hierarchy_service:
                    await hierarchy_service.invalidate(subject_id)
                    logger.info("hierarchy_cache_invalidated", subject_id=subject_id)
                else:
                    logger.warning("hierarchy_service_not_available")
    except Exception as e:
        logger.error("invalidation_handler_error", error=str(e))


# Integration with FastAPI lifespan
# fastapi_app/main.py (addition to existing lifespan)

async def lifespan(app: FastAPI):
    # ... existing setup ...

    # Start pub/sub listener
    from fastapi_app.core.pubsub import start_pubsub_listener
    pubsub_task = asyncio.create_task(
        start_pubsub_listener(pool, app.state)
    )
    app.state.pubsub_task = pubsub_task

    yield

    # Cancel pub/sub listener
    pubsub_task.cancel()
    try:
        await pubsub_task
    except asyncio.CancelledError:
        pass

    # ... existing cleanup ...
```

### Anti-Patterns to Avoid
- **Generating all JSON in single request:** Use background job to avoid timeout
- **Skipping atomic swap:** Partial uploads cause client inconsistency
- **Hardcoding CDN URLs:** Use storage abstraction for portability
- **Polling for build completion:** Use pub/sub for real-time notification
- **Building on Stage changes:** Per CONTEXT.md, only Lesson and above trigger builds
- **Ignoring malformed content:** Per CONTEXT.md, skip with warning, don't fail entire build

## Don't Hand-Roll

Problems that look simple but have existing solutions:

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Debouncing | Custom timer logic | Redis SET NX EX | Distributed, survives restarts |
| Background jobs | Thread spawning | Frappe scheduler_events | Managed, logged, restartable |
| S3-compatible upload | HTTP requests | boto3 | Handles auth, retries, multipart |
| Atomic file writes | Direct write | tempfile + os.replace | POSIX atomic guarantee |
| JSON serialization | Manual string building | json.dumps | Handles escaping, encoding |
| Real-time notifications | Polling | Redis pub/sub | Low latency, scalable |

**Key insight:** The Frappe framework already provides background job infrastructure via scheduler_events. Using Redis for debouncing ensures the debounce state survives Frappe restarts and works across multiple workers. boto3 handles all the complexity of S3-compatible uploads including authentication, retries, and multipart uploads.

## Common Pitfalls

### Pitfall 1: Debounce Key Not Deleted After Build
**What goes wrong:** Builds stop triggering after first successful build
**Why it happens:** Debounce key TTL not cleared after processing
**How to avoid:** Redis TTL handles cleanup automatically (2-minute expiry)
**Warning signs:** Content changes don't trigger new builds

### Pitfall 2: Partial Upload State on CDN
**What goes wrong:** Mobile app fetches mix of old and new JSON files
**Why it happens:** Upload interrupted mid-way, no atomic swap
**How to avoid:** Upload all to temp location, swap atomically on success
**Warning signs:** Client shows inconsistent hierarchy data

### Pitfall 3: FastAPI Cache Not Invalidated
**What goes wrong:** FastAPI serves stale hierarchy data after build
**Why it happens:** Pub/sub message not received or processed
**How to avoid:** Verify pub/sub subscription on startup, log all invalidations
**Warning signs:** Mobile app shows old content after admin edits

### Pitfall 4: Build Queue Flooding
**What goes wrong:** Hundreds of pending builds accumulate
**Why it happens:** No debouncing, each edit creates new build
**How to avoid:** Redis SET NX EX pattern merges edits within window
**Warning signs:** Memora Build Queue list shows many pending items

### Pitfall 5: Missing Bit Index in Generated JSON
**What goes wrong:** Progress tracking fails for some lessons
**Why it happens:** Lessons created without assigned bit_index
**How to avoid:** Ensure bit_index assigned on lesson creation (Phase 4 dependency)
**Warning signs:** Null bit_index in bitmap JSON

### Pitfall 6: Notification Spam on Rapid Edits
**What goes wrong:** Admin receives many build notifications for single edit session
**Why it happens:** Notification sent per build, not debounced
**How to avoid:** Notifications sent only after debounce window closes (once per merged build)
**Warning signs:** Bell icon shows many redundant build notifications

## Code Examples

Verified patterns from official sources:

### Frappe hooks.py doc_events Configuration
```python
# Source: Frappe doc_events documentation + existing access_sync.py pattern
# hooks.py addition

doc_events = {
    # ... existing events ...
    "Memora Subject": {
        "on_update": "memora_admin.events.build_trigger.on_content_updated",
    },
    "Memora Track": {
        "on_update": "memora_admin.events.build_trigger.on_content_updated",
    },
    "Memora Unit": {
        "on_update": "memora_admin.events.build_trigger.on_content_updated",
    },
    "Memora Topic": {
        "on_update": "memora_admin.events.build_trigger.on_content_updated",
    },
    "Memora Lesson": {
        "on_update": "memora_admin.events.build_trigger.on_content_updated",
    },
}
```

### Frappe hooks.py scheduler_events Configuration
```python
# Source: Frappe scheduler_events documentation
# hooks.py addition

scheduler_events = {
    "cron": {
        "*/2 * * * *": [  # Every 2 minutes
            "memora_admin.tasks.build_worker.process_pending_builds"
        ]
    }
}
```

### Force Build Button (Subject DocType JS)
```javascript
// Source: Frappe form API documentation
// memora_admin/doctype/memora_subject/memora_subject.js

frappe.ui.form.on("Memora Subject", {
    refresh(frm) {
        if (!frm.is_new()) {
            frm.add_custom_button(__("Force Build"), function() {
                frappe.call({
                    method: "memora_admin.api.build.queue_manual_build",
                    args: {
                        subject_id: frm.doc.name
                    },
                    callback: function(r) {
                        if (r.message) {
                            frappe.show_alert({
                                message: __("Build queued successfully"),
                                indicator: "green"
                            });
                        }
                    }
                });
            }, __("Actions"));
        }
    }
});
```

### Manual Build API Endpoint
```python
# Source: Frappe whitelist API pattern
# memora_admin/api/build.py

import frappe


@frappe.whitelist()
def queue_manual_build(subject_id: str) -> dict:
    """
    Queue a manual build for a subject.

    Per CONTEXT.md:
    - Force Build button on Subject DocType form
    - Bypasses debounce window
    """
    if not frappe.db.exists("Memora Subject", subject_id):
        frappe.throw(f"Subject {subject_id} not found")

    # Create build queue entry directly (no debounce)
    build_queue = frappe.new_doc("Memora Build Queue")
    build_queue.target_type = "Memora Subject"
    build_queue.target_name = subject_id
    build_queue.trigger_reason = "manual"
    build_queue.triggered_by = frappe.session.user
    build_queue.status = "Pending"
    build_queue.insert(ignore_permissions=True)

    return {"success": True, "build_id": build_queue.name}
```

### Redis Pub/Sub Async Pattern
```python
# Source: redis-py asyncio documentation
import redis.asyncio as redis
import asyncio

async def subscribe_example():
    """Example of async pub/sub subscription."""
    client = await redis.from_url("redis://localhost")

    async with client.pubsub() as pubsub:
        await pubsub.subscribe("memora:cache:invalidate")

        async for message in pubsub.listen():
            if message["type"] == "message":
                # message["data"] contains the published content
                print(f"Received: {message['data']}")

async def publish_example():
    """Example of publishing to channel."""
    client = await redis.from_url("redis://localhost")
    await client.publish("memora:cache:invalidate", '{"type": "hierarchy", "subject_id": "SUBJ-001"}')
    await client.close()
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Webhook-based CDN invalidation | Redis pub/sub | Standard practice | Lower latency, no HTTP overhead |
| Single monolithic JSON file | Separate files per level | CONTEXT.md decision | Smaller downloads, incremental updates |
| Database polling for builds | Event-driven with doc_events | Frappe standard | Instant trigger on save |
| Synchronous JSON generation | Background job via scheduler | Standard practice | No request timeout issues |

**Deprecated/outdated:**
- `RPOPLPUSH` command: Still works but `LMOVE` is the newer equivalent in Redis 6.2+
- Direct S3 API calls: Use boto3 for proper authentication and retry handling
- Synchronous build in doc_events: Will timeout on large subjects

## Open Questions

Things that couldn't be fully resolved:

1. **Exponential Backoff Intervals**
   - What we know: CONTEXT.md says "auto-requeue with exponential backoff"
   - Claude's discretion per CONTEXT.md
   - Recommendation: Use 1min, 2min, 4min backoff (base 2, starting at 60s)
   - Rationale: Standard exponential pattern, caps at reasonable delay

2. **Temp Directory Structure for Atomic Swap**
   - What we know: Need temp location before final swap
   - Claude's discretion per CONTEXT.md
   - Recommendation: Use `_temp_{timestamp}/` prefix in same CDN bucket/directory
   - Rationale: Same location ensures atomic rename works on POSIX

3. **Log Format and Detail Level**
   - What we know: Logs only, no Build Log DocType
   - Claude's discretion per CONTEXT.md
   - Recommendation: Use structlog with JSON format, INFO level for success/failure, DEBUG for file details
   - Rationale: Structured logs enable easy searching and monitoring

4. **Pub/sub Channel Naming**
   - What we know: Need channel for FastAPI cache invalidation
   - Claude's discretion per CONTEXT.md
   - Recommendation: `memora:cache:invalidate` with message `{"type": "hierarchy", "subject_id": "..."}`
   - Rationale: Namespaced, extensible for future invalidation types

## Sources

### Primary (HIGH confidence)
- [Frappe Background Jobs Documentation](https://docs.frappe.io/framework/user/en/api/background_jobs) - scheduler_events, frappe.enqueue
- [redis-py Asyncio Examples](https://redis.readthedocs.io/en/stable/examples/asyncio_examples.html) - Pub/sub patterns
- [Cloudflare R2 boto3 Documentation](https://developers.cloudflare.com/r2/examples/aws/boto3/) - S3-compatible upload
- Existing codebase: `events/access_sync.py`, `api/hierarchy.py`, `hooks.py`

### Secondary (MEDIUM confidence)
- [Frappe publish_realtime Gist](https://gist.github.com/esafwan/35ca19e0de44fa64a17e8ec5dc86317c) - Real-time notifications
- [Python Atomic File Writes](https://code.activestate.com/recipes/579097-safely-and-atomically-write-to-a-file/) - temp-then-rename pattern
- [Redis Deduplication Patterns](https://redis.io/solutions/deduplication/) - SET NX EX for debounce

### Tertiary (LOW confidence)
- WebSearch results on debounce patterns - needs validation against Redis docs

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - Uses established Frappe patterns, redis-py, boto3
- Architecture: HIGH - Follows existing codebase patterns (events/, api/, services/)
- Doc_events hooks: HIGH - Same pattern as existing access_sync.py
- JSON generation: HIGH - Extends existing hierarchy.py logic
- CDN abstraction: MEDIUM - Storage interface pattern is standard, R2 specifics verified
- Pub/sub cache invalidation: HIGH - redis-py async documented, pattern from official examples
- Debounce pattern: MEDIUM - Redis SET NX EX is standard, exact timing is project-specific

**Research date:** 2026-02-02
**Valid until:** 2026-03-02 (30 days - stable patterns, Frappe v15 API)
