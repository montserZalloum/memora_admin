# Memora Platform - Technical PRD
## Part 2: Business Logic & APIs
### Version 1.0 | February 2026

---

# Table of Contents

1. [Access Control System](#1-access-control-system)
2. [FastAPI Implementation](#2-fastapi-implementation)
3. [Frappe APIs](#3-frappe-apis)
4. [Build Pipeline](#4-build-pipeline)
5. [Scheduled Tasks](#5-scheduled-tasks)
6. [Sync Mechanisms](#6-sync-mechanisms)

---

# 1. Access Control System

## 1.1 Double-Gate Architecture

The access control system uses a two-gate approach for maximum performance and flexibility:

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                         DOUBLE-GATE ACCESS CHECK                                │
│                                                                                 │
│  Request: "Can player X access subject Y?"                                      │
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │ GATE 1: Season Validation (Global Kill-Switch)                          │   │
│  │                                                                          │   │
│  │ Redis: HGETALL memora:season:{season_id}:meta                           │   │
│  │                                                                          │   │
│  │ Check:                                                                   │   │
│  │   - status == "active"                                                  │   │
│  │   - current_time < end_ts                                               │   │
│  │                                                                          │   │
│  │ If FAIL → Return "Season ended" (affects ALL players instantly)         │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                     │                                           │
│                                     ▼ PASS                                      │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │ GATE 2: Player Access Validation (Personal Key-Ring)                    │   │
│  │                                                                          │   │
│  │ Redis: SISMEMBER memora:access:{player_id} {subject_id}                 │   │
│  │                                                                          │   │
│  │ Check (in order):                                                       │   │
│  │   1. Direct subject access: SUB-MATH-101 in set?                        │   │
│  │   2. Plan access: PLAN-XXX in set → check plan includes subject         │   │
│  │   3. Track access: TRK-XXX in set? (for sold-separately tracks)         │   │
│  │                                                                          │   │
│  │ If FAIL → Return "Access denied"                                        │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                     │                                           │
│                                     ▼ PASS                                      │
│                              ┌──────────────┐                                   │
│                              │ ACCESS GRANTED│                                  │
│                              └──────────────┘                                   │
│                                                                                 │
│  Total Time: < 2ms (two Redis operations)                                      │
└─────────────────────────────────────────────────────────────────────────────────┘
```

┌─────────────────────────────────────────────────────────────────────────────────┐
│                         ACCESS + FREE PREVIEW LOGIC                             │
│                                                                                 │
│  Player requests Subject progress                                              │
│         │                                                                       │
│         ▼                                                                       │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │ GATE 1: Season Check                                                    │   │
│  │ Is season active? → If NO → Reject ALL                                  │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│         │ YES                                                                   │
│         ▼                                                                       │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │ GATE 2: Subject Access Check                                            │   │
│  │ Did player purchase this subject/plan?                                  │   │
│  │                                                                          │   │
│  │ → YES: has_access = True  (everything unlockable)                       │   │
│  │ → NO:  has_access = False (check is_free at each level)                 │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│         │                                                                       │
│         ▼                                                                       │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │ UNLOCKER ENGINE: Calculate states for each item                         │   │
│  │                                                                          │   │
│  │ For each Unit:                                                          │   │
│  │   can_access = has_access OR unit.is_free                               │   │
│  │   if can_access → "unlocked" (clickable)                                │   │
│  │   else → "locked" (visible but not clickable)                           │   │
│  │                                                                          │   │
│  │ For each Topic:                                                         │   │
│  │   can_access = has_access OR topic.is_free                              │   │
│  │   if can_access → "unlocked"                                            │   │
│  │   else → "locked"                                                       │   │
│  │                                                                          │   │
│  │ For each Lesson (inherits from parent Topic):                           │   │
│  │   if parent_unlocked → "unlocked"                                       │   │
│  │   else → "locked"                                                       │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## 📊 مثال توضيحي:
```
Subject: الرياضيات (Premium)
Player: لم يشترِ المادة (has_access = False)

Track 1: الجبر
├── Unit 1: المعادلات (is_free: true) ← ✅ UNLOCKED
│   ├── Topic 1: معادلات الدرجة الأولى (is_free: true) ← ✅ UNLOCKED
│   │   ├── Lesson 1 ← ✅ UNLOCKED
│   │   └── Lesson 2 ← ✅ UNLOCKED
│   └── Topic 2: تطبيقات (is_free: false) ← 🔒 LOCKED
│       └── Lesson 3 ← 🔒 LOCKED
│
├── Unit 2: المتباينات (is_free: false) ← 🔒 LOCKED
│   └── Topic 3 ← 🔒 LOCKED
│       └── Lesson 4 ← 🔒 LOCKED

## 1.2 Season Management

### Season States

| Status | Meaning | Effect |
|--------|---------|--------|
| `active` | Season is running | Normal access |
| `paused` | Temporarily suspended | All access blocked |
| `ended` | Season finished | All access blocked |

### Season Extension Flow

```
Admin changes Season.end_date in Frappe
         │
         ▼
Frappe Hook (on_update) triggers
         │
         ▼
Redis: HSET memora:season:{id}:meta end_ts {new_timestamp}
         │
         ▼
Instantly, 100K+ players get extended access
(No individual record updates needed)
```

## 1.3 Access Grant Flow (On Purchase)

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                         PURCHASE → ACCESS GRANT FLOW                            │
│                                                                                 │
│  1. Payment Gateway sends webhook                                              │
│         │                                                                       │
│         ▼                                                                       │
│  2. Frappe receives & validates webhook                                        │
│         │                                                                       │
│         ▼                                                                       │
│  3. Create/Update Memora Subscription Transaction (status: processing)         │
│         │                                                                       │
│         ▼                                                                       │
│  4. Lookup Memora Product Grant by item_code                                   │
│         │                                                                       │
│         ▼                                                                       │
│  5. For each Grant Component:                                                  │
│     ┌─────────────────────────────────────────────────────────────────────┐    │
│     │ a. Redis: SADD memora:access:{player_id} {target_id}                │    │
│     │ b. Redis: EXPIREAT memora:access:{player_id} {season_end + 7 days}  │    │
│     │ c. MariaDB: INSERT Memora Player Subscription record                 │    │
│     └─────────────────────────────────────────────────────────────────────┘    │
│         │                                                                       │
│         ▼                                                                       │
│  6. Update Transaction status: completed                                       │
│         │                                                                       │
│         ▼                                                                       │
│  7. Player has INSTANT access (< 100ms from webhook)                           │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

## 1.4 Access Revocation

### Automatic (Season End)
- Redis keys have TTL set to `season.end_date + 7 days`
- Keys auto-expire, no cleanup needed
- Grace period allows for renewals

### Manual (Admin/Refund)
```python
# Remove specific access
SREM memora:access:{player_id} {target_id}

# Remove all access (ban/full refund)
DEL memora:access:{player_id}
```

---

# 2. FastAPI Implementation

## 2.1 Project Structure

```
fastapi_app/
├── main.py                    # Application entry point
├── config.py                  # Configuration management
├── dependencies.py            # Dependency injection
│
├── models/
│   ├── __init__.py
│   ├── requests.py            # Request models
│   └── responses.py           # Response models
│
├── routers/
│   ├── __init__.py
│   ├── progress.py            # Progress endpoints
│   ├── game.py                # Game session endpoints
│   ├── wallet.py              # Wallet endpoints
│   └── leaderboard.py         # Leaderboard endpoints
│
├── services/
│   ├── __init__.py
│   ├── unlocker.py            # Unlock state calculation engine
│   ├── access.py              # Access control (Double-Gate)
│   └── bitmap.py              # Bitmap operations
│
└── utils/
    ├── __init__.py
    └── redis_client.py        # Redis connection utilities
```

## 2.2 Main Application

```python
# File: fastapi_app/main.py

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import redis.asyncio as redis

from .config import settings
from .routers import progress, game, wallet, leaderboard
from .services.unlocker import UnlockerEngine
from .services.access import AccessChecker

# Global instances
redis_pool = None
unlocker_engine = None
access_checker = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown events."""
    global redis_pool, unlocker_engine, access_checker
    
    # === STARTUP ===
    redis_pool = redis.ConnectionPool.from_url(
        settings.redis_url,
        max_connections=100,
        decode_responses=False
    )
    
    redis_client = redis.Redis(connection_pool=redis_pool)
    
    unlocker_engine = UnlockerEngine(
        redis_client=redis_client,
        bitmaps_path=settings.bitmaps_path
    )
    
    access_checker = AccessChecker(redis_client=redis_client)
    
    # Subscribe to cache invalidation
    pubsub = redis_client.pubsub()
    await pubsub.subscribe("memora:bitmap_invalidate")
    
    import asyncio
    async def invalidation_listener():
        async for message in pubsub.listen():
            if message["type"] == "message":
                subject_id = message["data"].decode()
                unlocker_engine.invalidate_cache(subject_id)
    
    asyncio.create_task(invalidation_listener())
    
    yield
    
    # === SHUTDOWN ===
    await pubsub.unsubscribe("memora:bitmap_invalidate")
    await redis_pool.disconnect()


app = FastAPI(
    title="Memora Game API",
    description="High-performance API for Memora educational platform",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(progress.router, prefix="/api/v1/progress", tags=["Progress"])
app.include_router(game.router, prefix="/api/v1/game", tags=["Game"])
app.include_router(wallet.router, prefix="/api/v1/wallet", tags=["Wallet"])
app.include_router(leaderboard.router, prefix="/api/v1/leaderboard", tags=["Leaderboard"])

@app.get("/health")
async def health_check():
    return {"status": "healthy", "version": "1.0.0"}
```

## 2.3 Configuration

```python
# File: fastapi_app/config.py

import json
from pydantic_settings import BaseSettings
from functools import lru_cache
from typing import List

class Settings(BaseSettings):
    # Redis
    redis_url: str = "redis://localhost:6379"
    
    # JWT
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    
    # Paths
    bitmaps_path: str = "/home/corex/aurevia-bench/sites/x.conanacademy.com/private/memora_bitmaps"
    frappe_config_path: str = "/home/corex/aurevia-bench/sites/common_site_config.json"
    
    # CORS
    cors_origins: List[str] = ["https://app.memora.com", "http://localhost:3000"]
    
    # Rate Limiting
    rate_limit_enabled: bool = True
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._load_frappe_config()
    
    def _load_frappe_config(self):
        try:
            with open(self.frappe_config_path) as f:
                config = json.load(f)
                if "redis_cache" in config:
                    self.redis_url = config["redis_cache"]
                if "memora_jwt_secret" in config:
                    self.jwt_secret = config["memora_jwt_secret"]
        except FileNotFoundError:
            pass

@lru_cache()
def get_settings() -> Settings:
    return Settings()

settings = get_settings()
```

## 2.4 Dependencies

```python
# File: fastapi_app/dependencies.py

import jwt
from fastapi import Depends, HTTPException, Header
from dataclasses import dataclass
import redis.asyncio as redis

from .config import settings

@dataclass
class Player:
    """Authenticated player information."""
    id: str
    device_id: str
    season_id: str

async def get_redis() -> redis.Redis:
    """Get Redis client from connection pool."""
    from .main import redis_pool
    return redis.Redis(connection_pool=redis_pool)

async def get_unlocker():
    """Get Unlocker Engine instance."""
    from .main import unlocker_engine
    return unlocker_engine

async def get_access_checker():
    """Get Access Checker instance."""
    from .main import access_checker
    return access_checker

async def get_current_player(
    authorization: str = Header(..., description="Bearer token")
) -> Player:
    """
    Verify JWT and extract player information.
    STATELESS - no database hit required.
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    
    token = authorization[7:]
    
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm]
        )
        
        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="Invalid token type")
        
        return Player(
            id=payload["sub"],
            device_id=payload["device"],
            season_id=payload["season"]
        )
        
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

async def check_rate_limit(
    player: Player = Depends(get_current_player),
    r: redis.Redis = Depends(get_redis)
):
    """Check and enforce rate limits."""
    if not settings.rate_limit_enabled:
        return
    
    key = f"ratelimit:{player.id}:global"
    current = await r.incr(key)
    
    if current == 1:
        await r.expire(key, 60)
    
    if current > 120:  # 120 requests per minute
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
```

## 2.5 Access Checker Service

```python
# File: fastapi_app/services/access.py

import time
import redis.asyncio as redis
from typing import Optional

class AccessChecker:
    """
    Double-Gate Access Control System.
    Gate 1: Season validation
    Gate 2: Player access set
    """
    
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
    
    async def check_access(
        self,
        player_id: str,
        season_id: str,
        target_id: str,
        target_type: str = "subject"  # subject, track, plan
    ) -> tuple[bool, Optional[str]]:
        """
        Check if player has access to target.
        
        Returns: (has_access: bool, denial_reason: Optional[str])
        """
        # Gate 1: Season Check
        season_valid, reason = await self._check_season(season_id)
        if not season_valid:
            return False, reason
        
        # Gate 2: Player Access Check
        has_access = await self._check_player_access(player_id, target_id, target_type)
        if not has_access:
            return False, "access_denied"
        
        return True, None
    
    async def _check_season(self, season_id: str) -> tuple[bool, Optional[str]]:
        """Gate 1: Validate season is active."""
        season_key = f"memora:season:{season_id}:meta"
        season_meta = await self.redis.hgetall(season_key)
        
        if not season_meta:
            return False, "season_not_found"
        
        status = season_meta.get(b"status", b"").decode()
        end_ts = int(season_meta.get(b"end_ts", 0))
        
        if status != "active":
            return False, "season_inactive"
        
        if time.time() > end_ts:
            return False, "season_ended"
        
        return True, None
    
    async def _check_player_access(
        self,
        player_id: str,
        target_id: str,
        target_type: str
    ) -> bool:
        """Gate 2: Check player's access set."""
        access_key = f"memora:access:{player_id}"
        
        # Direct access check
        if await self.redis.sismember(access_key, target_id):
            return True
        
        # For subjects, check if any owned plan includes it
        if target_type == "subject":
            player_grants = await self.redis.smembers(access_key)
            for grant in player_grants:
                grant_str = grant.decode() if isinstance(grant, bytes) else grant
                
                # Check plan membership
                if grant_str.startswith("PLAN-"):
                    plan_subjects_key = f"memora:plan_subjects:{grant_str}"
                    if await self.redis.sismember(plan_subjects_key, target_id):
                        return True
        
        return False
    
    async def grant_access(
        self,
        player_id: str,
        target_id: str,
        season_end_ts: int
    ):
        """Grant access to a player (called after purchase)."""
        access_key = f"memora:access:{player_id}"
        
        # Add to access set
        await self.redis.sadd(access_key, target_id)
        
        # Set expiry (season end + 7 days buffer)
        expire_ts = season_end_ts + (7 * 24 * 3600)
        await self.redis.expireat(access_key, expire_ts)
    
    async def revoke_access(self, player_id: str, target_id: str):
        """Revoke specific access (refund scenario)."""
        access_key = f"memora:access:{player_id}"
        await self.redis.srem(access_key, target_id)
```

## 2.6 Unlocker Engine Service

```python
# File: fastapi_app/services/unlocker.py

import json
import redis.asyncio as redis
from typing import Dict, Literal, List, Set
from pathlib import Path
from dataclasses import dataclass

LockState = Literal["passed", "unlocked", "locked"]

@dataclass
class ProgressStats:
    completed: int
    total: int
    percentage: float

class UnlockerEngine:
    """
    Core engine for calculating unlock states.
    Uses bit_range with excluded_bits for scalability.
    """
    
    def __init__(self, redis_client: redis.Redis, bitmaps_path: str):
        self.redis = redis_client
        self.bitmaps_path = Path(bitmaps_path)
        self._structure_cache: Dict[str, dict] = {}
    
    async def get_progress(
        self,
        player_id: str,
        subject_id: str,
        has_access: bool
    ) -> Dict[str, LockState]:
        """
        Calculate lock state for ALL items in a subject.
        Returns: {"LSN-001": "passed", "TPC-001": "unlocked", ...}
        """
        structure = self._load_structure(subject_id)
        bitmap = await self.redis.get(f"progress:{player_id}:{subject_id}") or b''
        
        result: Dict[str, LockState] = {}
        
        # Process tracks
        tracks = structure["structure"]["tracks"]
        sorted_tracks = sorted(tracks.items(), key=lambda x: x[1]["sort_order"])
        
        prev_track_passed = True
        
        for track_id, track_data in sorted_tracks:
            track_passed = self._is_range_passed(track_data, bitmap)
            
            if track_passed:
                result[track_id] = "passed"
            elif has_access and (not track_data.get("is_linear", True) or prev_track_passed):
                result[track_id] = "unlocked"
            else:
                result[track_id] = "locked"
            
            # Process units in this track
            self._process_units(
                result, structure, track_id, bitmap,
                parent_unlocked=(result[track_id] != "locked"),
                has_access=has_access
            )
            
            prev_track_passed = track_passed
        
        return result
    
    async def get_stats(self, player_id: str, subject_id: str) -> ProgressStats:
        """Get completion statistics."""
        structure = self._load_structure(subject_id)
        bitmap = await self.redis.get(f"progress:{player_id}:{subject_id}") or b''
        
        total = structure.get("total_lessons", 0)
        completed = await self.redis.bitcount(f"progress:{player_id}:{subject_id}")
        percentage = round(completed / max(total, 1) * 100, 1)
        
        return ProgressStats(completed=completed, total=total, percentage=percentage)
    
    async def mark_lesson_complete(
        self,
        player_id: str,
        subject_id: str,
        bit_index: int
    ) -> dict:
        """Mark a lesson as complete."""
        bitmap_key = f"progress:{player_id}:{subject_id}"
        was_set = await self.redis.setbit(bitmap_key, bit_index, 1)
        
        # Add to dirty set for DB sync
        await self.redis.sadd("dirty:progress", f"{player_id}:{subject_id}")
        
        return {
            "success": True,
            "already_complete": bool(was_set),
            "bit_index": bit_index
        }
    
    def invalidate_cache(self, subject_id: str):
        """Remove cached structure (called when content rebuilt)."""
        if subject_id in self._structure_cache:
            del self._structure_cache[subject_id]
    
    # ========== PRIVATE METHODS ==========
    
    def _load_structure(self, subject_id: str) -> dict:
        """Load bitmap JSON from disk (cached in memory)."""
        if subject_id not in self._structure_cache:
            path = self.bitmaps_path / f"{subject_id}_b.json"
            with open(path, 'r', encoding='utf-8') as f:
                self._structure_cache[subject_id] = json.load(f)
        return self._structure_cache[subject_id]
    
    def _check_bit(self, bitmap: bytes, bit_index: int) -> bool:
        """Check if a specific bit is set."""
        if not bitmap or bit_index < 0:
            return False
        byte_index = bit_index // 8
        bit_position = 7 - (bit_index % 8)
        if byte_index >= len(bitmap):
            return False
        return bool(bitmap[byte_index] & (1 << bit_position))
    
    def _is_range_passed(self, data: dict, bitmap: bytes) -> bool:
        """Check if all bits in range (excluding excluded_bits) are set."""
        bit_range = data.get("bit_range", [0, -1])
        excluded = set(data.get("excluded_bits", []))
        
        start, end = bit_range
        if end < start:
            return True  # Empty range
        
        for bit in range(start, end + 1):
            if bit in excluded:
                continue
            if not self._check_bit(bitmap, bit):
                return False
        return True
    
    def _count_in_range(self, data: dict, bitmap: bytes) -> tuple[int, int]:
        """Count completed and total in range."""
        bit_range = data.get("bit_range", [0, -1])
        excluded = set(data.get("excluded_bits", []))
        
        start, end = bit_range
        if end < start:
            return 0, 0
        
        completed = 0
        total = 0
        
        for bit in range(start, end + 1):
            if bit in excluded:
                continue
            total += 1
            if self._check_bit(bitmap, bit):
                completed += 1
        
        return completed, total
    
    def _process_units(
        self,
        result: Dict[str, LockState],
        structure: dict,
        track_id: str,
        bitmap: bytes,
        parent_unlocked: bool,
        has_access: bool
    ):
        """Process all units in a track."""
        units = structure["structure"]["units"]
        track_units = [
            (uid, udata) for uid, udata in units.items()
            if udata.get("track") == track_id
        ]
        track_units.sort(key=lambda x: x[1]["sort_order"])
        
        prev_passed = True
        
        for unit_id, unit_data in track_units:
            unit_passed = self._is_range_passed(unit_data, bitmap)
            unit_is_free = unit_data.get("is_free", False)
            unit_is_linear = unit_data.get("is_linear", True)
            
            can_access = has_access or unit_is_free
            
            if unit_passed:
                result[unit_id] = "passed"
            elif parent_unlocked and can_access and (not unit_is_linear or prev_passed):
                result[unit_id] = "unlocked"
            else:
                result[unit_id] = "locked"
            
            # Process topics
            self._process_topics(
                result, structure, unit_id, bitmap,
                parent_unlocked=(result[unit_id] != "locked"),
                has_access=can_access
            )
            
            prev_passed = unit_passed
    
    def _process_topics(
        self,
        result: Dict[str, LockState],
        structure: dict,
        unit_id: str,
        bitmap: bytes,
        parent_unlocked: bool,
        has_access: bool
    ):
        """Process all topics in a unit."""
        topics = structure["structure"]["topics"]
        unit_topics = [
            (tid, tdata) for tid, tdata in topics.items()
            if tdata.get("unit") == unit_id
        ]
        unit_topics.sort(key=lambda x: x[1]["sort_order"])
        
        prev_passed = True
        
        for topic_id, topic_data in unit_topics:
            topic_passed = self._is_range_passed(topic_data, bitmap)
            topic_is_free = topic_data.get("is_free", False)
            topic_is_linear = topic_data.get("is_linear", True)
            
            can_access = has_access or topic_is_free
            
            if topic_passed:
                result[topic_id] = "passed"
            elif parent_unlocked and can_access and (not topic_is_linear or prev_passed):
                result[topic_id] = "unlocked"
            else:
                result[topic_id] = "locked"
            
            prev_passed = topic_passed
```

## 2.7 Progress Router

```python
# File: fastapi_app/routers/progress.py

from fastapi import APIRouter, Depends, HTTPException
from typing import Dict
from pydantic import BaseModel

from ..dependencies import (
    get_current_player, get_redis, get_unlocker,
    get_access_checker, Player
)
from ..services.unlocker import UnlockerEngine
from ..services.access import AccessChecker

router = APIRouter()

class ProgressResponse(BaseModel):
    subject_id: str
    completed: int
    total: int
    percentage: float
    states: Dict[str, str]
    has_access: bool

@router.get("/subjects/{subject_id}", response_model=ProgressResponse)
async def get_subject_progress(
    subject_id: str,
    player: Player = Depends(get_current_player),
    unlocker: UnlockerEngine = Depends(get_unlocker),
    access_checker: AccessChecker = Depends(get_access_checker)
):
    """
    Get complete progress and unlock states for a subject.
    Response time target: < 20ms
    """
    # Check access (Double-Gate)
    has_access, denial_reason = await access_checker.check_access(
        player_id=player.id,
        season_id=player.season_id,
        target_id=subject_id,
        target_type="subject"
    )
    
    # If season is invalid, reject completely
    if denial_reason in ["season_not_found", "season_inactive", "season_ended"]:
        raise HTTPException(status_code=403, detail=denial_reason)
    
    try:
        # Get unlock states (access affects what's unlocked vs locked)
        states = await unlocker.get_progress(player.id, subject_id, has_access)
        stats = await unlocker.get_stats(player.id, subject_id)
        
        return ProgressResponse(
            subject_id=subject_id,
            completed=stats.completed,
            total=stats.total,
            percentage=stats.percentage,
            states=states,
            has_access=has_access
        )
    
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Subject not found")
```

## 2.8 Game Router

```python
# File: fastapi_app/routers/game.py

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from datetime import datetime, timedelta
from typing import Optional, List
import json

from ..dependencies import (
    get_current_player, get_redis, get_unlocker,
    get_access_checker, Player
)
from ..services.unlocker import UnlockerEngine
from ..services.access import AccessChecker

router = APIRouter()

class StartLessonRequest(BaseModel):
    lesson_id: str
    subject_id: str
    bit_index: int

class StartLessonResponse(BaseModel):
    success: bool
    session_id: str

class CompleteStageRequest(BaseModel):
    session_id: str
    stage_index: int
    is_correct: bool
    time_spent_ms: int

class EndLessonRequest(BaseModel):
    session_id: str
    completed: bool

class EndLessonResponse(BaseModel):
    success: bool
    completed: bool
    xp_earned: int = 0
    total_xp: int = 0
    streak: int = 0
    streak_updated: bool = False

@router.post("/start-lesson", response_model=StartLessonResponse)
async def start_lesson(
    request: StartLessonRequest,
    player: Player = Depends(get_current_player),
    redis = Depends(get_redis),
    access_checker: AccessChecker = Depends(get_access_checker)
):
    """Start a lesson session."""
    # Check access
    has_access, denial_reason = await access_checker.check_access(
        player_id=player.id,
        season_id=player.season_id,
        target_id=request.subject_id,
        target_type="subject"
    )
    
    if not has_access:
        raise HTTPException(status_code=403, detail=denial_reason or "access_denied")
    
    # Create session
    timestamp = int(datetime.utcnow().timestamp())
    session_id = f"{player.id}:{request.lesson_id}:{timestamp}"
    
    session_data = {
        "player_id": player.id,
        "lesson_id": request.lesson_id,
        "subject_id": request.subject_id,
        "bit_index": request.bit_index,
        "started_at": datetime.utcnow().isoformat(),
        "stages_completed": [],
        "status": "active"
    }
    
    await redis.setex(
        f"lesson_session:{session_id}",
        3600,  # 1 hour TTL
        json.dumps(session_data)
    )
    
    return StartLessonResponse(success=True, session_id=session_id)

@router.post("/complete-stage")
async def complete_stage(
    request: CompleteStageRequest,
    player: Player = Depends(get_current_player),
    redis = Depends(get_redis)
):
    """Record stage completion."""
    session_key = f"lesson_session:{request.session_id}"
    session_raw = await redis.get(session_key)
    
    if not session_raw:
        raise HTTPException(status_code=400, detail="Invalid or expired session")
    
    session = json.loads(session_raw)
    
    if session["player_id"] != player.id:
        raise HTTPException(status_code=403, detail="Session belongs to another player")
    
    if session["status"] != "active":
        raise HTTPException(status_code=400, detail="Session is not active")
    
    # Record stage
    session["stages_completed"].append({
        "index": request.stage_index,
        "is_correct": request.is_correct,
        "time_spent_ms": request.time_spent_ms,
        "completed_at": datetime.utcnow().isoformat()
    })
    
    await redis.setex(session_key, 3600, json.dumps(session))
    
    # Buffer interaction for batch insert
    interaction = {
        "player": player.id,
        "lesson": session["lesson_id"],
        "stage_index": request.stage_index,
        "is_correct": request.is_correct,
        "time_spent_ms": request.time_spent_ms,
        "timestamp": datetime.utcnow().isoformat()
    }
    await redis.lpush("buffer:interactions", json.dumps(interaction))
    
    return {"success": True, "is_correct": request.is_correct}

@router.post("/end-lesson", response_model=EndLessonResponse)
async def end_lesson(
    request: EndLessonRequest,
    player: Player = Depends(get_current_player),
    redis = Depends(get_redis),
    unlocker: UnlockerEngine = Depends(get_unlocker)
):
    """End a lesson session and award XP if completed."""
    session_key = f"lesson_session:{request.session_id}"
    session_raw = await redis.getdel(session_key)
    
    if not session_raw:
        raise HTTPException(status_code=400, detail="Invalid or expired session")
    
    session = json.loads(session_raw)
    
    if session["player_id"] != player.id:
        raise HTTPException(status_code=403, detail="Session belongs to another player")
    
    response = EndLessonResponse(success=True, completed=request.completed)
    
    if request.completed:
        # Mark lesson complete
        result = await unlocker.mark_lesson_complete(
            player.id,
            session["subject_id"],
            session["bit_index"]
        )
        
        # Get XP (from lesson info cache or default)
        lesson_info_raw = await redis.hget("memora:lesson_info", session["lesson_id"])
        xp = 10  # default
        if lesson_info_raw:
            lesson_info = json.loads(lesson_info_raw)
            xp = lesson_info.get("xp_reward", 10)
        
        # Reduce XP for replay
        if result.get("already_complete"):
            xp = max(xp // 2, 5)
        
        # Add XP
        wallet_key = f"wallet:{player.id}"
        new_total_xp = await redis.hincrby(wallet_key, "xp", xp)
        
        response.xp_earned = xp
        response.total_xp = new_total_xp
        
        # Update leaderboards
        today = datetime.utcnow().strftime("%Y-%m-%d")
        await redis.zincrby(f"leaderboard:xp:daily:{today}", xp, player.id)
        await redis.zincrby("leaderboard:xp:alltime", xp, player.id)
        await redis.expire(f"leaderboard:xp:daily:{today}", 172800)
        
        # Update streak
        streak_result = await update_streak(player.id, redis)
        response.streak = streak_result["streak"]
        response.streak_updated = streak_result["updated"]
        
        # Mark wallet dirty
        await redis.sadd("dirty:wallet", player.id)
    
    return response

async def update_streak(player_id: str, redis) -> dict:
    """Update player's streak."""
    wallet_key = f"wallet:{player_id}"
    
    today = datetime.utcnow().strftime("%Y-%m-%d")
    yesterday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
    
    streak_date_raw = await redis.hget(wallet_key, "streak_date")
    streak_date = streak_date_raw.decode() if streak_date_raw else ""
    current_streak = int(await redis.hget(wallet_key, "streak") or 0)
    
    if streak_date == today:
        return {"streak": current_streak, "updated": False}
    
    if streak_date == yesterday:
        new_streak = current_streak + 1
    else:
        new_streak = 1
    
    await redis.hset(wallet_key, "streak", new_streak)
    await redis.hset(wallet_key, "streak_date", today)
    
    return {"streak": new_streak, "updated": True}
```

## 2.9 Wallet Router

```python
# File: fastapi_app/routers/wallet.py

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional

from ..dependencies import get_current_player, get_redis, Player

router = APIRouter()

class WalletResponse(BaseModel):
    xp: int
    streak: int
    streak_date: Optional[str] = None

@router.get("", response_model=WalletResponse)
async def get_wallet(
    player: Player = Depends(get_current_player),
    redis = Depends(get_redis)
):
    """Get player's wallet information."""
    wallet_key = f"wallet:{player.id}"
    wallet_data = await redis.hgetall(wallet_key)
    
    xp = int(wallet_data.get(b"xp", 0))
    streak = int(wallet_data.get(b"streak", 0))
    streak_date = wallet_data.get(b"streak_date", b"").decode() or None
    
    return WalletResponse(
        xp=xp,
        streak=streak,
        streak_date=streak_date
    )
```

## 2.10 Leaderboard Router

```python
# File: fastapi_app/routers/leaderboard.py

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

from ..dependencies import get_current_player, get_redis, Player

router = APIRouter()

class LeaderboardEntry(BaseModel):
    rank: int
    player_id: str
    display_name: str
    score: int
    is_current_user: bool = False

class LeaderboardResponse(BaseModel):
    type: str
    period: str
    entries: List[LeaderboardEntry]
    current_user_rank: Optional[int] = None
    current_user_score: Optional[int] = None
    total_participants: int

@router.get("/xp/{period}", response_model=LeaderboardResponse)
async def get_xp_leaderboard(
    period: str,
    limit: int = Query(default=100, le=100),
    player: Player = Depends(get_current_player),
    redis = Depends(get_redis)
):
    """Get XP leaderboard for a period (daily, weekly, monthly, alltime)."""
    if period == "daily":
        today = datetime.utcnow().strftime("%Y-%m-%d")
        key = f"leaderboard:xp:daily:{today}"
    elif period == "weekly":
        week = datetime.utcnow().strftime("%Y-W%V")
        key = f"leaderboard:xp:weekly:{week}"
    elif period == "monthly":
        month = datetime.utcnow().strftime("%Y-%m")
        key = f"leaderboard:xp:monthly:{month}"
    else:
        key = "leaderboard:xp:alltime"
    
    entries_raw = await redis.zrevrange(key, 0, limit - 1, withscores=True)
    
    # Get display names
    player_ids = [entry[0].decode() for entry in entries_raw]
    display_names = {}
    if player_ids:
        names_raw = await redis.hmget("memora:player_names", *player_ids)
        for i, name in enumerate(names_raw):
            display_names[player_ids[i]] = name.decode() if name else f"Player #{player_ids[i][-4:]}"
    
    entries = []
    for rank, (player_id_bytes, score) in enumerate(entries_raw, 1):
        pid = player_id_bytes.decode()
        entries.append(LeaderboardEntry(
            rank=rank,
            player_id=pid,
            display_name=display_names.get(pid, "Unknown"),
            score=int(score),
            is_current_user=(player.id == pid)
        ))
    
    # Current user's rank
    current_rank = await redis.zrevrank(key, player.id)
    current_score = None
    if current_rank is not None:
        current_rank += 1
        current_score = int(await redis.zscore(key, player.id) or 0)
    
    total = await redis.zcard(key)
    
    return LeaderboardResponse(
        type="xp",
        period=period,
        entries=entries,
        current_user_rank=current_rank,
        current_user_score=current_score,
        total_participants=total
    )

@router.get("/streak", response_model=LeaderboardResponse)
async def get_streak_leaderboard(
    limit: int = Query(default=100, le=100),
    player: Player = Depends(get_current_player),
    redis = Depends(get_redis)
):
    """Get current streak leaderboard."""
    key = "leaderboard:streak:current"
    
    entries_raw = await redis.zrevrange(key, 0, limit - 1, withscores=True)
    
    player_ids = [entry[0].decode() for entry in entries_raw]
    display_names = {}
    if player_ids:
        names_raw = await redis.hmget("memora:player_names", *player_ids)
        for i, name in enumerate(names_raw):
            display_names[player_ids[i]] = name.decode() if name else f"Player #{player_ids[i][-4:]}"
    
    entries = []
    for rank, (player_id_bytes, score) in enumerate(entries_raw, 1):
        pid = player_id_bytes.decode()
        entries.append(LeaderboardEntry(
            rank=rank,
            player_id=pid,
            display_name=display_names.get(pid, "Unknown"),
            score=int(score),
            is_current_user=(player.id == pid)
        ))
    
    current_rank = await redis.zrevrank(key, player.id)
    current_score = None
    if current_rank is not None:
        current_rank += 1
        current_score = int(await redis.zscore(key, player.id) or 0)
    
    total = await redis.zcard(key)
    
    return LeaderboardResponse(
        type="streak",
        period="current",
        entries=entries,
        current_user_rank=current_rank,
        current_user_score=current_score,
        total_participants=total
    )
```

## 2.11 API Endpoints Summary

| Method | Endpoint | Purpose | Auth |
|--------|----------|---------|------|
| GET | `/health` | Health check | None |
| GET | `/api/v1/progress/subjects/{id}` | Get subject progress & states | JWT |
| POST | `/api/v1/game/start-lesson` | Start lesson session | JWT |
| POST | `/api/v1/game/complete-stage` | Record stage completion | JWT |
| POST | `/api/v1/game/end-lesson` | End lesson, award XP | JWT |
| GET | `/api/v1/wallet` | Get wallet (XP, streak) | JWT |
| GET | `/api/v1/leaderboard/xp/{period}` | XP leaderboard | JWT |
| GET | `/api/v1/leaderboard/streak` | Streak leaderboard | JWT |

---

# 3. Frappe APIs

## 3.1 Authentication API

```python
# File: memora_admin/memora_admin/api/auth.py

import frappe
import jwt
import hashlib
import redis
import json
from datetime import datetime, timedelta

@frappe.whitelist(allow_guest=True)
def login(email: str, password: str, device_id: str, device_name: str = None):
    """Authenticate user and issue JWT tokens."""
    
    # Verify credentials
    try:
        frappe.local.login_manager.authenticate(email, password)
    except frappe.AuthenticationError:
        frappe.throw("Invalid email or password", frappe.AuthenticationError)
    
    user = frappe.session.user
    
    # Get or create player
    player_id = get_or_create_player(user)
    
    # Get player's season
    player = frappe.get_doc("Memora Player Profile", player_id)
    season_id = player.season
    
    # Register device
    register_device(player_id, device_id, device_name)
    
    # Issue tokens
    tokens = issue_tokens(player_id, device_id, season_id)
    
    return {
        "access_token": tokens["access_token"],
        "refresh_token": tokens["refresh_token"],
        "expires_in": tokens["expires_in"],
        "player": {
            "id": player_id,
            "display_name": player.display_name or user,
            "season": season_id
        }
    }


def get_or_create_player(user: str) -> str:
    """Get existing player or create new one."""
    existing = frappe.db.get_value("Memora Player Profile", {"user": user}, "name")
    
    if existing:
        return existing
    
    # Get default season
    default_season = frappe.db.get_value(
        "Memora Season",
        {"is_published": 1},
        "name",
        order_by="start_date desc"
    )
    
    # Create player
    player = frappe.get_doc({
        "doctype": "Memora Player Profile",
        "user": user,
        "display_name": frappe.db.get_value("User", user, "full_name"),
        "season": default_season
    })
    player.insert(ignore_permissions=True)
    
    # Create wallet in MariaDB
    wallet = frappe.get_doc({
        "doctype": "Memora Player Wallet",
        "player": player.name,
        "total_xp": 0,
        "current_streak": 0,
        "status": "active"
    })
    wallet.insert(ignore_permissions=True)
    
    # Initialize Redis wallet
    r = redis.Redis.from_url(frappe.conf.redis_cache)
    r.hset(f"wallet:{player.name}", mapping={
        "xp": 0,
        "streak": 0,
        "streak_date": ""
    })
    
    # Cache player name
    r.hset("memora:player_names", player.name, player.display_name or user)
    
    frappe.db.commit()
    return player.name


def register_device(player_id: str, device_id: str, device_name: str = None):
    """Register or update device."""
    player = frappe.get_doc("Memora Player Profile", player_id)
    
    # Find existing device
    existing = None
    for device in player.authorized_devices:
        if device.device_id == device_id:
            existing = device
            break
    
    if existing:
        existing.last_login = datetime.now()
        if device_name:
            existing.device_name = device_name
    else:
        # Check limit (max 3)
        max_devices = frappe.db.get_single_value("Memora Settings", "max_devices_per_player") or 3
        
        if len(player.authorized_devices) >= max_devices:
            oldest = min(player.authorized_devices, key=lambda d: d.last_login or datetime.min)
            player.remove(oldest)
        
        player.append("authorized_devices", {
            "device_id": device_id,
            "device_name": device_name or "Unknown Device",
            "last_login": datetime.now(),
            "platform": "unknown"
        })
    
    player.save(ignore_permissions=True)


def issue_tokens(player_id: str, device_id: str, season_id: str) -> dict:
    """Issue JWT access and refresh tokens."""
    config = frappe.conf
    secret = config.get("memora_jwt_secret")
    algorithm = config.get("memora_jwt_algorithm", "HS256")
    access_expiry = config.get("memora_jwt_access_expiry", 900)
    refresh_expiry = config.get("memora_jwt_refresh_expiry", 604800)
    
    now = datetime.utcnow()
    
    # Access Token (includes season for fast access check)
    access_payload = {
        "sub": player_id,
        "device": device_id,
        "season": season_id,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(seconds=access_expiry)
    }
    access_token = jwt.encode(access_payload, secret, algorithm=algorithm)
    
    # Refresh Token
    refresh_payload = {
        "sub": player_id,
        "device": device_id,
        "season": season_id,
        "type": "refresh",
        "iat": now,
        "exp": now + timedelta(seconds=refresh_expiry)
    }
    refresh_token = jwt.encode(refresh_payload, secret, algorithm=algorithm)
    
    # Store session in Redis
    r = redis.Redis.from_url(config.redis_cache)
    refresh_hash = hashlib.sha256(refresh_token.encode()).hexdigest()
    
    session_data = {
        "refresh_token_hash": refresh_hash,
        "device_id": device_id,
        "created_at": now.isoformat()
    }
    
    r.setex(
        f"session:{player_id}:{device_id}",
        refresh_expiry,
        json.dumps(session_data)
    )
    
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_in": access_expiry
    }


@frappe.whitelist(allow_guest=True)
def refresh(refresh_token: str):
    """Exchange refresh token for new access token."""
    config = frappe.conf
    secret = config.get("memora_jwt_secret")
    algorithm = config.get("memora_jwt_algorithm", "HS256")
    
    try:
        payload = jwt.decode(refresh_token, secret, algorithms=[algorithm])
    except jwt.ExpiredSignatureError:
        frappe.throw("Refresh token expired", frappe.AuthenticationError)
    except jwt.InvalidTokenError:
        frappe.throw("Invalid refresh token", frappe.AuthenticationError)
    
    if payload.get("type") != "refresh":
        frappe.throw("Invalid token type", frappe.AuthenticationError)
    
    player_id = payload["sub"]
    device_id = payload["device"]
    season_id = payload["season"]
    
    # Verify session
    r = redis.Redis.from_url(config.redis_cache)
    session_raw = r.get(f"session:{player_id}:{device_id}")
    
    if not session_raw:
        frappe.throw("Session not found", frappe.AuthenticationError)
    
    session_data = json.loads(session_raw)
    refresh_hash = hashlib.sha256(refresh_token.encode()).hexdigest()
    
    if session_data["refresh_token_hash"] != refresh_hash:
        frappe.throw("Invalid refresh token", frappe.AuthenticationError)
    
    # Issue new access token only
    now = datetime.utcnow()
    access_expiry = config.get("memora_jwt_access_expiry", 900)
    
    access_payload = {
        "sub": player_id,
        "device": device_id,
        "season": season_id,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(seconds=access_expiry)
    }
    access_token = jwt.encode(access_payload, secret, algorithm=algorithm)
    
    return {
        "access_token": access_token,
        "expires_in": access_expiry
    }
```

## 3.2 Payment Webhook API

```python
# File: memora_admin/memora_admin/api/payment.py

import frappe
import redis
import hmac
import hashlib
from datetime import datetime

@frappe.whitelist(allow_guest=True)
def payment_webhook():
    """
    Handle payment gateway webhook.
    Grants instant access to purchased content.
    """
    data = frappe.request.json
    
    # Verify webhook signature
    verify_webhook_signature(data)
    
    item_code = data.get("item_code")
    player_id = data.get("player_id")
    transaction_id = data.get("transaction_id")
    amount = data.get("amount")
    
    # Create transaction record
    transaction = frappe.get_doc({
        "doctype": "Memora Subscription Transaction",
        "player": player_id,
        "transaction_id": transaction_id,
        "amount_paid": amount,
        "payment_method": data.get("payment_method", "card"),
        "status": "processing"
    })
    transaction.insert(ignore_permissions=True)
    
    try:
        # Find Product Grant
        grant = frappe.get_doc("Memora Product Grant", {
            "item_code": item_code,
            "is_published": 1
        })
        
        if not grant:
            raise ValueError(f"No active grant for item: {item_code}")
        
        # Get season
        season = frappe.get_doc("Memora Season", {"is_published": 1})
        season_end_ts = int(season.end_date.timestamp())
        
        r = redis.Redis.from_url(frappe.conf.redis_cache)
        access_key = f"memora:access:{player_id}"
        
        # Process each grant component
        for component in grant.grant_components:
            target_id = component.target_name
            
            # 1. Add to Redis (instant access)
            r.sadd(access_key, target_id)
            
            # 2. Create permanent record
            frappe.get_doc({
                "doctype": "Memora Player Subscription",
                "player": player_id,
                "access_key": target_id,
                "grant_type": component.target_doctype.replace("Memora ", ""),
                "season": season.name,
                "expires_at": season.end_date,
                "granted_at": datetime.now(),
                "source_transaction": transaction.name
            }).insert(ignore_permissions=True)
        
        # Set TTL (season end + 7 days)
        expire_ts = season_end_ts + (7 * 24 * 3600)
        r.expireat(access_key, expire_ts)
        
        # Update transaction status
        transaction.status = "completed"
        transaction.related_grant = grant.name
        transaction.save(ignore_permissions=True)
        
        frappe.db.commit()
        
        return {"success": True, "transaction_id": transaction.name}
    
    except Exception as e:
        transaction.status = "failed"
        transaction.save(ignore_permissions=True)
        frappe.db.commit()
        frappe.throw(str(e))


def verify_webhook_signature(data: dict):
    """Verify webhook signature from payment gateway."""
    settings = frappe.get_single("Memora Settings")
    secret = settings.get_password("webhook_secret")
    
    if not secret:
        return  # Skip if not configured
    
    signature = frappe.request.headers.get("X-Webhook-Signature")
    if not signature:
        frappe.throw("Missing webhook signature", frappe.AuthenticationError)
    
    import json
    payload = json.dumps(data, separators=(',', ':'), sort_keys=True)
    expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    
    if not hmac.compare_digest(signature, expected):
        frappe.throw("Invalid webhook signature", frappe.AuthenticationError)
```

## 3.3 Season Hooks

```python
# File: memora_admin/memora_admin/doctype/memora_season/memora_season.py

import frappe
from frappe.model.document import Document
import redis

class MemoraSeason(Document):
    def on_update(self):
        """Update Redis when season is modified."""
        self.sync_to_redis()
    
    def after_insert(self):
        """Initialize Redis key for new season."""
        self.sync_to_redis()
    
    def sync_to_redis(self):
        """Sync season meta to Redis."""
        r = redis.Redis.from_url(frappe.conf.redis_cache)
        
        key = f"memora:season:{self.name}:meta"
        
        status = "active" if self.is_published else "paused"
        end_ts = int(self.end_date.timestamp()) if self.end_date else 0
        
        r.hset(key, mapping={
            "status": status,
            "end_ts": end_ts
        })
        
        frappe.msgprint(
            f"Season synced to Redis: status={status}, end_ts={end_ts}",
            indicator="green",
            alert=True
        )
```

---

# 4. Build Pipeline

## 4.1 Build Flow Overview

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              BUILD PIPELINE                                     │
│                                                                                 │
│  Content Change (Lesson/Topic/Unit/Track saved)                                │
│         │                                                                       │
│         ▼                                                                       │
│  Frappe Hook: queue_build()                                                    │
│         │                                                                       │
│         ▼                                                                       │
│  Redis: SADD memora:pending_builds {subject_id}                                │
│  Redis: SADD memora:pending_lessons:{subject_id} {lesson_id}                   │
│         │                                                                       │
│         │ (Debounce: collect changes for 2 minutes)                            │
│         ▼                                                                       │
│  Scheduler: Every 2 min → process_pending_builds()                             │
│         │                                                                       │
│         ▼                                                                       │
│  For each pending subject:                                                     │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │ 1. Create Build Queue record (status: processing)                       │   │
│  │ 2. Generate _h.json (hierarchy)                                         │   │
│  │ 3. Generate _b.json (bitmap with bit_range)                             │   │
│  │ 4. Generate unit content JSONs                                          │   │
│  │ 5. Generate lesson JSONs (only changed ones)                            │   │
│  │ 6. Upload to CDN (R2)                                                   │   │
│  │ 7. Invalidate FastAPI cache                                             │   │
│  │ 8. Update Build Queue (status: completed)                               │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│         │                                                                       │
│         ▼                                                                       │
│  Redis: SREM memora:pending_builds {subject_id}                                │
│  Redis: DEL memora:pending_lessons:{subject_id}                                │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

## 4.2 Build Hooks

```python
# File: memora_admin/hooks.py

doc_events = {
    "Memora Lesson": {
        "after_insert": "memora_admin.utils.build.queue_build",
        "on_update": "memora_admin.utils.build.queue_build",
        "on_trash": "memora_admin.utils.build.queue_build_on_delete"
    },
    "Memora Topic": {
        "on_update": "memora_admin.utils.build.queue_build_parent"
    },
    "Memora Unit": {
        "on_update": "memora_admin.utils.build.queue_build_parent"
    },
    "Memora Track": {
        "on_update": "memora_admin.utils.build.queue_build_parent"
    },
    "Memora Subject": {
        "on_update": "memora_admin.utils.build.queue_subject_build"
    },
    "Memora Plan Overrider": {
        "after_insert": "memora_admin.utils.build.queue_plan_build",
        "on_update": "memora_admin.utils.build.queue_plan_build",
        "on_trash": "memora_admin.utils.build.queue_plan_build"
    }
}
```

## 4.3 Build Queue Utilities

```python
# File: memora_admin/memora_admin/utils/build.py

import frappe
import redis

def queue_build(doc, method):
    """Queue subject for rebuild when lesson changes."""
    r = redis.Redis.from_url(frappe.conf.redis_cache)
    
    subject_id = doc.subject if hasattr(doc, 'subject') else get_subject_from_hierarchy(doc)
    
    if subject_id:
        r.sadd("memora:pending_builds", subject_id)
        r.sadd(f"memora:pending_lessons:{subject_id}", doc.name)

def queue_build_on_delete(doc, method):
    """Handle lesson deletion - add to excluded_bits."""
    r = redis.Redis.from_url(frappe.conf.redis_cache)
    
    subject_id = doc.subject
    if subject_id:
        r.sadd("memora:pending_builds", subject_id)
        # Mark bit_index for exclusion
        r.sadd(f"memora:deleted_bits:{subject_id}", doc.bit_index)

def queue_build_parent(doc, method):
    """Queue build when parent entity changes."""
    subject_id = get_subject_from_hierarchy(doc)
    if subject_id:
        r = redis.Redis.from_url(frappe.conf.redis_cache)
        r.sadd("memora:pending_builds", subject_id)

def queue_subject_build(doc, method):
    """Queue build when subject itself changes."""
    r = redis.Redis.from_url(frappe.conf.redis_cache)
    r.sadd("memora:pending_builds", doc.name)

def queue_plan_build(doc, method):
    """Queue build when plan override changes."""
    r = redis.Redis.from_url(frappe.conf.redis_cache)
    # Rebuild all subjects in the plan
    subjects = frappe.get_all(
        "Memora Plan Subject",
        filters={"parent": doc.plan},
        pluck="subject"
    )
    for subject_id in subjects:
        r.sadd("memora:pending_builds", subject_id)

def get_subject_from_hierarchy(doc):
    """Get subject ID from any level in hierarchy."""
    if doc.doctype == "Memora Topic":
        unit = frappe.db.get_value("Memora Unit", doc.unit, "track")
        return frappe.db.get_value("Memora Track", unit, "subject") if unit else None
    elif doc.doctype == "Memora Unit":
        return frappe.db.get_value("Memora Track", doc.track, "subject")
    elif doc.doctype == "Memora Track":
        return doc.subject
    return None
```

## 4.4 Build Worker

```python
# File: memora_admin/memora_admin/tasks/build.py

import frappe
import redis
import json
import os
from datetime import datetime

def process_pending_builds():
    """
    Scheduled task: runs every 2 minutes.
    Processes all pending build requests.
    """
    r = redis.Redis.from_url(frappe.conf.redis_cache)
    
    pending = r.smembers("memora:pending_builds")
    if not pending:
        return
    
    for subject_id in pending:
        subject_id = subject_id.decode() if isinstance(subject_id, bytes) else subject_id
        
        build_record = None
        try:
            build_record = create_build_record(subject_id)
            files_count = build_subject_files(subject_id, r)
            complete_build_record(build_record, files_count)
            
            # Clear pending
            r.srem("memora:pending_builds", subject_id)
            r.delete(f"memora:pending_lessons:{subject_id}")
            r.delete(f"memora:deleted_bits:{subject_id}")
            
        except Exception as e:
            frappe.log_error(f"Build failed for {subject_id}: {str(e)}")
            if build_record:
                fail_build_record(build_record, str(e))
    
    frappe.db.commit()


def build_subject_files(subject_id: str, r) -> int:
    """Build all JSON files for a subject."""
    files_count = 0
    subject = frappe.get_doc("Memora Subject", subject_id)
    version = int(datetime.now().timestamp())
    
    # Get deleted bits
    deleted_bits = r.smembers(f"memora:deleted_bits:{subject_id}")
    deleted_bits = {int(b) for b in deleted_bits}
    
    # 1. Build hierarchy JSON
    hierarchy = build_hierarchy(subject, version)
    save_public_json(f"subjects/{subject_id}/_h.json", hierarchy)
    files_count += 1
    
    # 2. Build bitmap JSON (with bit_range and excluded_bits)
    bitmap_data = build_bitmap(subject, deleted_bits, version)
    save_private_json(f"{subject_id}_b.json", bitmap_data)
    files_count += 1
    
    # 3. Build unit content JSONs
    for unit in get_units(subject_id):
        content = build_unit_content(unit, version)
        save_public_json(f"subjects/{subject_id}/units/{unit}_c.json", content)
        files_count += 1
    
    # 4. Build lesson JSONs
    for lesson in get_lessons(subject_id):
        content = build_lesson_content(lesson, version)
        save_public_json(f"lessons/{lesson}.json", content)
        files_count += 1
        
        # Cache lesson info
        cache_lesson_info(lesson, subject_id, r)
    
    # 5. Upload to CDN
    upload_to_cdn(subject_id)
    
    # 6. Invalidate FastAPI cache
    r.publish("memora:bitmap_invalidate", subject_id)
    
    # 7. Update version
    r.hset("memora:versions", subject_id, version)
    
    return files_count


def build_bitmap(subject, deleted_bits: set, version: int) -> dict:
    """Build bitmap JSON with bit_range structure."""
    bitmap = {
        "subject_id": subject.name,
        "version": version,
        "total_lessons": 0,
        "structure": {
            "tracks": {},
            "units": {},
            "topics": {}
        }
    }
    
    tracks = frappe.get_all("Memora Track",
        filters={"subject": subject.name, "is_published": 1},
        fields=["name", "sort_order", "is_linear"],
        order_by="sort_order"
    )
    
    for track in tracks:
        track_bits = []
        track_units = []
        
        units = frappe.get_all("Memora Unit",
            filters={"track": track.name, "is_published": 1},
            fields=["name", "sort_order", "is_linear", "is_free"],
            order_by="sort_order"
        )
        
        for unit in units:
            unit_bits = []
            unit_topics = []
            
            topics = frappe.get_all("Memora Topic",
                filters={"unit": unit.name, "is_published": 1},
                fields=["name", "sort_order", "is_linear", "is_free"],
                order_by="sort_order"
            )
            
            for topic in topics:
                lessons = frappe.get_all("Memora Lesson",
                    filters={"topic": topic.name},
                    fields=["bit_index"],
                    order_by="bit_index"
                )
                
                if lessons:
                    topic_bits = [l.bit_index for l in lessons]
                    excluded = [b for b in topic_bits if b in deleted_bits]
                    
                    bitmap["structure"]["topics"][topic.name] = {
                        "unit": unit.name,
                        "sort_order": topic.sort_order,
                        "is_linear": topic.is_linear,
                        "is_free": topic.is_free,
                        "bit_range": [min(topic_bits), max(topic_bits)],
                        "excluded_bits": excluded
                    }
                    
                    unit_bits.extend(topic_bits)
                    unit_topics.append(topic.name)
            
            if unit_bits:
                excluded = [b for b in unit_bits if b in deleted_bits]
                bitmap["structure"]["units"][unit.name] = {
                    "track": track.name,
                    "sort_order": unit.sort_order,
                    "is_linear": unit.is_linear,
                    "is_free": unit.is_free,
                    "bit_range": [min(unit_bits), max(unit_bits)],
                    "excluded_bits": excluded,
                    "topics": unit_topics
                }
                
                track_bits.extend(unit_bits)
                track_units.append(unit.name)
        
        if track_bits:
            excluded = [b for b in track_bits if b in deleted_bits]
            bitmap["structure"]["tracks"][track.name] = {
                "sort_order": track.sort_order,
                "is_linear": track.is_linear,
                "bit_range": [min(track_bits), max(track_bits)],
                "excluded_bits": excluded,
                "units": track_units
            }
    
    # Calculate total (excluding deleted)
    total = subject.next_bit_index or 0
    bitmap["total_lessons"] = total - len(deleted_bits)
    
    return bitmap


def cache_lesson_info(lesson_id: str, subject_id: str, r):
    """Cache lesson info for FastAPI."""
    lesson = frappe.get_doc("Memora Lesson", lesson_id)
    info = {
        "subject": subject_id,
        "bit_index": lesson.bit_index,
        "xp_reward": lesson.base_xp or 10,
        "topic": lesson.topic
    }
    r.hset("memora:lesson_info", lesson_id, json.dumps(info))


def save_public_json(path: str, data: dict):
    """Save to public directory."""
    base = frappe.get_site_path("public", "memora_content")
    full_path = os.path.join(base, path)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    
    with open(full_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, separators=(',', ':'))


def save_private_json(filename: str, data: dict):
    """Save to private directory."""
    base = frappe.get_site_path("private", "memora_bitmaps")
    os.makedirs(base, exist_ok=True)
    
    with open(os.path.join(base, filename), 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)


def create_build_record(subject_id: str):
    """Create build queue record."""
    return frappe.get_doc({
        "doctype": "Memora Build Queue",
        "target_type": "Subject",
        "target_name": subject_id,
        "trigger_reason": "content_update",
        "triggered_by": frappe.session.user,
        "triggered_at": datetime.now(),
        "status": "processing",
        "started_at": datetime.now()
    }).insert(ignore_permissions=True)


def complete_build_record(record, files_count: int):
    """Mark build as completed."""
    record.status = "completed"
    record.completed_at = datetime.now()
    record.files_generated = files_count
    record.duration_sec = (record.completed_at - record.started_at).total_seconds()
    record.save(ignore_permissions=True)


def fail_build_record(record, error: str):
    """Mark build as failed."""
    record.status = "failed"
    record.completed_at = datetime.now()
    record.error_message = error[:500]
    record.save(ignore_permissions=True)
```

---

# 5. Scheduled Tasks

## 5.1 Task Schedule

```python
# File: memora_admin/hooks.py

scheduler_events = {
    "cron": {
        # Every minute: Sync dirty data
        "* * * * *": [
            "memora_admin.tasks.sync.sync_dirty_progress",
            "memora_admin.tasks.sync.sync_dirty_wallets",
            "memora_admin.tasks.sync.flush_interaction_buffer"
        ],
        
        # Every 2 minutes: Process build queue
        "*/2 * * * *": [
            "memora_admin.tasks.build.process_pending_builds"
        ],
        
        # Every hour: Maintenance
        "0 * * * *": [
            "memora_admin.tasks.maintenance.cleanup_old_sessions"
        ],
        
        # Daily at 2 AM: Aggregation
        "0 2 * * *": [
            "memora_admin.tasks.aggregate.aggregate_daily_stats",
            "memora_admin.tasks.maintenance.reset_broken_streaks"
        ],
        
        # Weekly: Cleanup
        "0 4 * * 0": [
            "memora_admin.tasks.maintenance.cleanup_old_logs"
        ]
    }
}
```

---

# 6. Sync Mechanisms

## 6.1 Progress Sync

```python
# File: memora_admin/memora_admin/tasks/sync.py

import frappe
import redis
import json
from datetime import datetime

def sync_dirty_progress():
    """Sync progress bitmaps from Redis to MariaDB."""
    r = redis.Redis.from_url(frappe.conf.redis_cache)
    
    dirty_items = r.smembers("dirty:progress")
    if not dirty_items:
        return
    
    synced = 0
    
    for item in dirty_items:
        item = item.decode() if isinstance(item, bytes) else item
        
        try:
            player_id, subject_id = item.split(":")
            
            bitmap = r.get(f"progress:{player_id}:{subject_id}")
            completed = r.bitcount(f"progress:{player_id}:{subject_id}")
            
            total = r.hget("memora:subject_totals", subject_id)
            total = int(total) if total else 0
            percentage = (completed / max(total, 1)) * 100
            
            bitmap_hex = bitmap.hex() if bitmap else ""
            
            existing = frappe.db.get_value(
                "Memora Structure Progress",
                {"player": player_id, "subject": subject_id},
                "name"
            )
            
            if existing:
                frappe.db.set_value("Memora Structure Progress", existing, {
                    "passed_lessons_bitset": bitmap_hex,
                    "completion_percentage": percentage
                }, update_modified=False)
            else:
                frappe.get_doc({
                    "doctype": "Memora Structure Progress",
                    "player": player_id,
                    "subject": subject_id,
                    "passed_lessons_bitset": bitmap_hex,
                    "completion_percentage": percentage
                }).insert(ignore_permissions=True)
            
            r.srem("dirty:progress", item)
            synced += 1
            
        except Exception as e:
            frappe.log_error(f"Sync progress failed for {item}: {str(e)}")
    
    if synced:
        frappe.db.commit()
        log_sync("progress", synced)


def sync_dirty_wallets():
    """Sync wallets from Redis to MariaDB."""
    r = redis.Redis.from_url(frappe.conf.redis_cache)
    
    dirty = r.smembers("dirty:wallet")
    if not dirty:
        return
    
    synced = 0
    
    for player_id in dirty:
        player_id = player_id.decode() if isinstance(player_id, bytes) else player_id
        
        try:
            wallet_data = r.hgetall(f"wallet:{player_id}")
            if not wallet_data:
                r.srem("dirty:wallet", player_id)
                continue
            
            total_xp = int(wallet_data.get(b"xp", 0))
            current_streak = int(wallet_data.get(b"streak", 0))
            
            wallet_name = frappe.db.get_value(
                "Memora Player Wallet",
                {"player": player_id},
                "name"
            )
            
            if wallet_name:
                frappe.db.set_value("Memora Player Wallet", wallet_name, {
                    "total_xp": total_xp,
                    "current_streak": current_streak,
                    "dirty_flag": 0
                }, update_modified=False)
                synced += 1
            
            r.srem("dirty:wallet", player_id)
            
        except Exception as e:
            frappe.log_error(f"Sync wallet failed for {player_id}: {str(e)}")
    
    if synced:
        frappe.db.commit()
        log_sync("wallet", synced)


def flush_interaction_buffer():
    """Batch insert interactions to MariaDB."""
    r = redis.Redis.from_url(frappe.conf.redis_cache)
    
    items = []
    for _ in range(1000):
        item = r.rpop("buffer:interactions")
        if not item:
            break
        items.append(json.loads(item))
    
    if not items:
        return
    
    for item in items:
        try:
            frappe.get_doc({
                "doctype": "Memora Interaction Log",
                "player": item["player"],
                "lesson": item["lesson"],
                "stage_id": str(item.get("stage_index", "")),
                "event_type": "stage_completed",
                "time_spent": item.get("time_spent_ms", 0) // 1000,
                "errors_count": 0 if item.get("is_correct") else 1,
                "timestamp": item["timestamp"]
            }).insert(ignore_permissions=True)
        except:
            pass
    
    frappe.db.commit()
    log_sync("interactions", len(items))


def log_sync(sync_type: str, count: int):
    """Log sync operation."""
    frappe.get_doc({
        "doctype": "Memora Sync Log",
        "job_id": f"{sync_type}-{int(datetime.now().timestamp())}",
        "sync_type": sync_type,
        "records_processed": count,
        "status": "completed"
    }).insert(ignore_permissions=True)
```

## 6.2 Maintenance Tasks

```python
# File: memora_admin/memora_admin/tasks/maintenance.py

import frappe
import redis
from datetime import datetime, timedelta

def cleanup_old_sessions():
    """Remove expired sessions from Redis."""
    # Redis handles TTL automatically, but we can log cleanup
    pass

def reset_broken_streaks():
    """Reset streaks for players who missed yesterday."""
    r = redis.Redis.from_url(frappe.conf.redis_cache)
    
    yesterday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
    
    # Get all wallet keys
    for key in r.scan_iter("wallet:*"):
        streak_date = r.hget(key, "streak_date")
        if streak_date:
            streak_date = streak_date.decode()
            if streak_date < yesterday:
                # Streak is broken
                r.hset(key, "streak", 0)
                
                player_id = key.decode().replace("wallet:", "")
                r.sadd("dirty:wallet", player_id)

def cleanup_old_logs():
    """Delete interaction logs older than retention period."""
    settings = frappe.get_single("Memora Settings")
    retention_days = settings.request_retention_days or 90
    
    cutoff = datetime.now() - timedelta(days=retention_days)
    
    frappe.db.delete("Memora Interaction Log", {
        "timestamp": ["<", cutoff]
    })
    
    frappe.db.commit()
```

---

# End of Part 2

**Next: Part 3 - Operations & Deployment**
- CDN & Caching Strategy
- Security & Authentication (JWT)
- Deployment Architecture
- File Structure
- Cost Estimation
- Future Roadmap