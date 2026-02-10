# Profile Page API Documentation

**Version:** v1.7
**Base URL:** `http://127.0.0.1:8002/api/v1` (development) | `https://api.memora.app/api/v1` (production)
**Authentication:** JWT Bearer token required for all endpoints

## Overview

The Profile Page API provides all backend functionality needed to build a rich player profile page with:
- **Hero Section**: Avatar, username, level progression, and XP tracking
- **Stats Grid**: Streak, items learned, and total XP (filterable by subject)
- **Memory Mastery**: FSRS-based breakdown of learning progress (mature/learning/new items)
- **Weekly Activity**: XP earned per day for the current week with visualization data
- **Avatar Selection**: Predefined avatar options with update functionality
- **Logout**: Session and device cleanup

All endpoints support **subject-level filtering** where applicable, allowing the UI to show both global stats and per-subject breakdowns.

---

## Table of Contents

1. [Authentication](#authentication)
2. [Endpoints](#endpoints)
   - [GET /profile - Hero Section](#get-profile)
   - [GET /profile/stats - Stats Grid](#get-profilestats)
   - [GET /profile/mastery - Memory Mastery](#get-profilemastery)
   - [GET /profile/activity - Weekly Activity](#get-profileactivity)
   - [GET /profile/avatars - Avatar Options](#get-profileavatars)
   - [PUT /profile/avatar - Update Avatar](#put-profileavatar)
   - [POST /profile/logout - Logout](#post-profilelogout)
3. [Data Models](#data-models)
4. [Error Handling](#error-handling)
5. [Performance & Caching](#performance--caching)
6. [Implementation Guide](#implementation-guide)

---

## Authentication

All endpoints require a valid JWT token in the Authorization header:

```http
Authorization: Bearer <jwt_token>
```

**Obtaining a JWT token:**
- Use the `/auth/login` endpoint (see Authentication API docs)
- Token contains the user ID (`sub` claim) used to identify the player

**Token expiry:**
- Tokens expire after 7 days
- Implement token refresh logic or prompt re-login on 401 responses

---

## Endpoints

### GET /profile

**Purpose:** Retrieve hero section data including avatar, username, level, and XP progression.

**Request:**
```http
GET /api/v1/profile
Authorization: Bearer <jwt_token>
```

**Response:** `200 OK`
```json
{
  "display_name": "أحمد محمد",
  "avatar": "avatar_01.png",
  "level": 10,
  "level_title": "Grandmaster",
  "current_xp": 5234,
  "xp_in_level": 734,
  "xp_for_next_level": 266,
  "xp_level_start": 4500,
  "xp_level_end": 5500
}
```

**Response Fields:**
| Field | Type | Description |
|-------|------|-------------|
| `display_name` | string | Player's display name (Arabic supported) |
| `avatar` | string | Avatar filename or URL |
| `level` | integer | Current level (1-15) |
| `level_title` | string | Level title (e.g., "Beginner", "Grandmaster", "Transcendent") |
| `current_xp` | integer | Total XP earned across all time |
| `xp_in_level` | integer | XP earned within current level |
| `xp_for_next_level` | integer | XP remaining to reach next level (0 if max level) |
| `xp_level_start` | integer | XP threshold where current level starts |
| `xp_level_end` | integer | XP threshold where next level starts (0 if max level) |

**Level Thresholds:**
```javascript
// 15 levels with increasing XP requirements
const LEVEL_THRESHOLDS = [
  0,      // Level 1: Beginner
  100,    // Level 2: Learner
  300,    // Level 3: Explorer
  600,    // Level 4: Scholar
  1000,   // Level 5: Achiever
  1500,   // Level 6: Expert
  2100,   // Level 7: Master
  2800,   // Level 8: Champion
  3600,   // Level 9: Legend
  4500,   // Level 10: Grandmaster
  5500,   // Level 11: Sage
  6700,   // Level 12: Titan
  8000,   // Level 13: Mythic
  9500,   // Level 14: Immortal
  11000   // Level 15: Transcendent
];
```

**UI Implementation Notes:**
- Display progress bar: `(xp_in_level / (xp_level_end - xp_level_start)) * 100`
- Show "MAX LEVEL" badge when `xp_for_next_level === 0`
- Avatar path: Prefix with CDN base URL or use relative path

**Example (TypeScript):**
```typescript
interface HeroSection {
  display_name: string;
  avatar: string;
  level: number;
  level_title: string;
  current_xp: number;
  xp_in_level: number;
  xp_for_next_level: number;
  xp_level_start: number;
  xp_level_end: number;
}

async function fetchHeroSection(token: string): Promise<HeroSection> {
  const response = await fetch('http://127.0.0.1:8002/api/v1/profile', {
    headers: { 'Authorization': `Bearer ${token}` }
  });
  return response.json();
}
```

---

### GET /profile/stats

**Purpose:** Retrieve stats grid data (streak, items learned, total XP) with optional subject filtering.

**Request:**
```http
GET /api/v1/profile/stats?subject={subject_id}
Authorization: Bearer <jwt_token>
```

**Query Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `subject` | string | No | Subject ID to filter stats. Omit for global stats. |

**Response:** `200 OK`

**Example 1: Global stats (no subject filter)**
```json
{
  "subject": null,
  "streak": 7,
  "items_learned": 142,
  "total_xp": 5234
}
```

**Example 2: Subject-filtered stats**
```json
{
  "subject": "arabic-grammar",
  "streak": 7,
  "items_learned": 89,
  "total_xp": 3120
}
```

**Response Fields:**
| Field | Type | Description |
|-------|------|-------------|
| `subject` | string \| null | Subject ID if filtered, null for global stats |
| `streak` | integer | Consecutive days with lesson completions (always global, not per-subject) |
| `items_learned` | integer | Total stages completed (subject-filtered or global sum) |
| `total_xp` | integer | Total XP earned (subject-filtered or global) |

**Important Notes:**
- **Streak is always global** — it doesn't change when filtering by subject
- **Items learned** when filtered: stages completed in that subject only
- **Items learned** when global: sum across all subjects
- **Total XP** when filtered: XP earned in that subject only
- **Total XP** when global: matches `current_xp` from hero section

**UI Implementation:**
```typescript
interface StatsGrid {
  subject: string | null;
  streak: number;
  items_learned: number;
  total_xp: number;
}

async function fetchStats(token: string, subjectId?: string): Promise<StatsGrid> {
  const url = new URL('http://127.0.0.1:8002/api/v1/profile/stats');
  if (subjectId) url.searchParams.set('subject', subjectId);

  const response = await fetch(url.toString(), {
    headers: { 'Authorization': `Bearer ${token}` }
  });
  return response.json();
}
```

---

### GET /profile/mastery

**Purpose:** Retrieve memory mastery breakdown based on FSRS spaced repetition algorithm.

**Request:**
```http
GET /api/v1/profile/mastery?subject={subject_id}
Authorization: Bearer <jwt_token>
```

**Query Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `subject` | string | No | Subject ID to filter mastery. Omit for combined stats. |

**Response:** `200 OK`
```json
{
  "subject": "arabic-grammar",
  "mature": 45,
  "learning": 23,
  "new_items": 12,
  "total": 80
}
```

**Response Fields:**
| Field | Type | Description |
|-------|------|-------------|
| `subject` | string \| null | Subject ID if filtered, null for all subjects combined |
| `mature` | integer | Memory states with stability ≥ 21.0 days (well-learned) |
| `learning` | integer | Memory states with 0 < stability < 21.0 days (in progress) |
| `new_items` | integer | Memory states with stability = 0 (never reviewed) |
| `total` | integer | Sum of mature + learning + new_items |

**FSRS Classification:**
- **Mature (21+ days stability)**: Items that are well-learned and have long review intervals
- **Learning (0-21 days stability)**: Items currently being learned with shorter intervals
- **New (0 days stability)**: Items created but never reviewed yet

**Caching:** Cached for 5 minutes, invalidated on review submission

**UI Implementation:**
```typescript
interface MemoryMastery {
  subject: string | null;
  mature: number;
  learning: number;
  new_items: number;
  total: number;
}

async function fetchMastery(token: string, subjectId?: string): Promise<MemoryMastery> {
  const url = new URL('http://127.0.0.1:8002/api/v1/profile/mastery');
  if (subjectId) url.searchParams.set('subject', subjectId);

  const response = await fetch(url.toString(), {
    headers: { 'Authorization': `Bearer ${token}` }
  });
  return response.json();
}

// Display as donut chart or stacked bar
function renderMasteryChart(mastery: MemoryMastery) {
  const maturePercent = (mastery.mature / mastery.total) * 100;
  const learningPercent = (mastery.learning / mastery.total) * 100;
  const newPercent = (mastery.new_items / mastery.total) * 100;
  // ... render chart
}
```

---

### GET /profile/activity

**Purpose:** Retrieve XP earned per day for the current week (Monday-Sunday) for activity chart visualization.

**Request:**
```http
GET /api/v1/profile/activity?subject={subject_id}
Authorization: Bearer <jwt_token>
```

**Query Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `subject` | string | No | Subject ID to filter activity. Omit for global XP. |

**Response:** `200 OK`
```json
{
  "subject": null,
  "week_start": "2026-02-10",
  "days": [
    {
      "date": "2026-02-10",
      "day_name": "Mon",
      "xp": 120
    },
    {
      "date": "2026-02-11",
      "day_name": "Tue",
      "xp": 95
    },
    {
      "date": "2026-02-12",
      "day_name": "Wed",
      "xp": 0
    },
    {
      "date": "2026-02-13",
      "day_name": "Thu",
      "xp": 150
    },
    {
      "date": "2026-02-14",
      "day_name": "Fri",
      "xp": 200
    },
    {
      "date": "2026-02-15",
      "day_name": "Sat",
      "xp": 0
    },
    {
      "date": "2026-02-16",
      "day_name": "Sun",
      "xp": 0
    }
  ],
  "total_xp": 565
}
```

**Response Fields:**
| Field | Type | Description |
|-------|------|-------------|
| `subject` | string \| null | Subject ID if filtered, null for global activity |
| `week_start` | string | Monday's date in YYYY-MM-DD format (week start) |
| `days` | array | Array of 7 days (Mon-Sun) with XP data |
| `days[].date` | string | Date in YYYY-MM-DD format |
| `days[].day_name` | string | Short day name (Mon, Tue, Wed, Thu, Fri, Sat, Sun) |
| `days[].xp` | integer | XP earned on that day (0 if no activity) |
| `total_xp` | integer | Sum of XP across all 7 days |

**Important Notes:**
- Week always starts on Monday (Asia/Amman timezone)
- Always returns exactly 7 days (current week)
- Future days (today+) return `xp: 0`
- Days array is ordered chronologically (Monday first)

**UI Implementation:**
```typescript
interface DailyXP {
  date: string;
  day_name: string;
  xp: number;
}

interface WeeklyActivity {
  subject: string | null;
  week_start: string;
  days: DailyXP[];
  total_xp: number;
}

async function fetchWeeklyActivity(token: string, subjectId?: string): Promise<WeeklyActivity> {
  const url = new URL('http://127.0.0.1:8002/api/v1/profile/activity');
  if (subjectId) url.searchParams.set('subject', subjectId);

  const response = await fetch(url.toString(), {
    headers: { 'Authorization': `Bearer ${token}` }
  });
  return response.json();
}

// Render as bar chart
function renderActivityChart(activity: WeeklyActivity) {
  const maxXP = Math.max(...activity.days.map(d => d.xp));
  activity.days.forEach(day => {
    const barHeight = (day.xp / maxXP) * 100; // percentage
    // Render bar with height `barHeight`%
    // Label: day.day_name
    // Value: day.xp
  });
}
```

---

### GET /profile/avatars

**Purpose:** Retrieve list of available avatar options that the player can choose from.

**Request:**
```http
GET /api/v1/profile/avatars
Authorization: Bearer <jwt_token>
```

**Response:** `200 OK`
```json
{
  "avatars": [
    "avatar_01.png",
    "avatar_02.png",
    "avatar_03.png",
    "avatar_04.png",
    "avatar_05.png",
    "avatar_06.png",
    "avatar_07.png",
    "avatar_08.png"
  ]
}
```

**Response Fields:**
| Field | Type | Description |
|-------|------|-------------|
| `avatars` | array[string] | List of valid avatar filenames or identifiers |

**Important Notes:**
- Avatar options are read from DocType definition (not hardcoded)
- Use these exact values when calling `PUT /profile/avatar`
- Invalid avatars will be rejected with 400 error

**UI Implementation:**
```typescript
interface AvatarOptions {
  avatars: string[];
}

async function fetchAvatarOptions(token: string): Promise<AvatarOptions> {
  const response = await fetch('http://127.0.0.1:8002/api/v1/profile/avatars', {
    headers: { 'Authorization': `Bearer ${token}` }
  });
  return response.json();
}

// Display avatar selection grid
function renderAvatarSelector(avatars: string[], currentAvatar: string) {
  avatars.forEach(avatar => {
    const isSelected = avatar === currentAvatar;
    // Render avatar thumbnail with selection indicator
    // On click: call updateAvatar(avatar)
  });
}
```

---

### PUT /profile/avatar

**Purpose:** Update the player's avatar selection.

**Request:**
```http
PUT /api/v1/profile/avatar
Authorization: Bearer <jwt_token>
Content-Type: application/json

{
  "avatar": "avatar_03.png"
}
```

**Request Body:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `avatar` | string | Yes | Avatar identifier from `/profile/avatars` list |

**Response:** `200 OK`
```json
{
  "avatar": "avatar_03.png",
  "success": true
}
```

**Error Response:** `400 Bad Request`
```json
{
  "detail": "Invalid avatar option"
}
```

**Side Effects:**
- Player's profile cache is invalidated
- Next `/profile` call will return updated avatar

**UI Implementation:**
```typescript
interface AvatarUpdateRequest {
  avatar: string;
}

interface AvatarUpdateResponse {
  avatar: string;
  success: boolean;
}

async function updateAvatar(token: string, avatar: string): Promise<AvatarUpdateResponse> {
  const response = await fetch('http://127.0.0.1:8002/api/v1/profile/avatar', {
    method: 'PUT',
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({ avatar })
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || 'Failed to update avatar');
  }

  return response.json();
}

// After successful update, refresh hero section to show new avatar
```

---

### POST /profile/logout

**Purpose:** Logout the player by invalidating their session and optionally removing their device registration.

**Request:**
```http
POST /api/v1/profile/logout
Authorization: Bearer <jwt_token>
X-Device-ID: device_abc123
```

**Headers:**
| Header | Required | Description |
|--------|----------|-------------|
| `Authorization` | Yes | JWT bearer token |
| `X-Device-ID` | No | Device ID to remove (frees up device slot) |

**Response:** `200 OK`
```json
{
  "success": true,
  "message": "Logged out successfully"
}
```

**Side Effects:**
- Session is invalidated (JWT token becomes invalid)
- If `X-Device-ID` provided: Device is removed from player's device list
- Player must re-login to access protected endpoints

**UI Implementation:**
```typescript
interface LogoutResponse {
  success: boolean;
  message: string;
}

async function logout(token: string, deviceId?: string): Promise<LogoutResponse> {
  const headers: HeadersInit = {
    'Authorization': `Bearer ${token}`
  };

  if (deviceId) {
    headers['X-Device-ID'] = deviceId;
  }

  const response = await fetch('http://127.0.0.1:8002/api/v1/profile/logout', {
    method: 'POST',
    headers
  });

  const result = await response.json();

  // Clear local storage / session storage
  localStorage.removeItem('jwt_token');
  localStorage.removeItem('device_id');

  // Redirect to login page
  window.location.href = '/login';

  return result;
}
```

---

## Data Models

### TypeScript Interfaces

```typescript
// Hero Section
interface HeroSection {
  display_name: string;
  avatar: string;
  level: number;
  level_title: string;
  current_xp: number;
  xp_in_level: number;
  xp_for_next_level: number;
  xp_level_start: number;
  xp_level_end: number;
}

// Stats Grid
interface StatsGrid {
  subject: string | null;
  streak: number;
  items_learned: number;
  total_xp: number;
}

// Memory Mastery
interface MemoryMastery {
  subject: string | null;
  mature: number;
  learning: number;
  new_items: number;
  total: number;
}

// Weekly Activity
interface DailyXP {
  date: string; // YYYY-MM-DD
  day_name: string; // Mon, Tue, etc.
  xp: number;
}

interface WeeklyActivity {
  subject: string | null;
  week_start: string; // YYYY-MM-DD
  days: DailyXP[];
  total_xp: number;
}

// Avatar Management
interface AvatarOptions {
  avatars: string[];
}

interface AvatarUpdateRequest {
  avatar: string;
}

interface AvatarUpdateResponse {
  avatar: string;
  success: boolean;
}

// Logout
interface LogoutResponse {
  success: boolean;
  message: string;
}
```

### Kotlin/Java Data Classes

```kotlin
// Hero Section
data class HeroSection(
    val displayName: String,
    val avatar: String,
    val level: Int,
    val levelTitle: String,
    val currentXp: Int,
    val xpInLevel: Int,
    val xpForNextLevel: Int,
    val xpLevelStart: Int,
    val xpLevelEnd: Int
)

// Stats Grid
data class StatsGrid(
    val subject: String?,
    val streak: Int,
    val itemsLearned: Int,
    val totalXp: Int
)

// Memory Mastery
data class MemoryMastery(
    val subject: String?,
    val mature: Int,
    val learning: Int,
    val newItems: Int,
    val total: Int
)

// Weekly Activity
data class DailyXP(
    val date: String,
    val dayName: String,
    val xp: Int
)

data class WeeklyActivity(
    val subject: String?,
    val weekStart: String,
    val days: List<DailyXP>,
    val totalXp: Int
)

// Avatar Management
data class AvatarOptions(
    val avatars: List<String>
)

data class AvatarUpdateRequest(
    val avatar: String
)

data class AvatarUpdateResponse(
    val avatar: String,
    val success: Boolean
)

// Logout
data class LogoutResponse(
    val success: Boolean,
    val message: String
)
```

---

## Error Handling

### Standard Error Responses

All endpoints may return these standard HTTP error codes:

**401 Unauthorized**
```json
{
  "detail": "Not authenticated"
}
```
**Cause:** Missing or invalid JWT token
**Action:** Redirect to login page

**403 Forbidden**
```json
{
  "detail": "Forbidden"
}
```
**Cause:** Token is valid but user doesn't have permission
**Action:** Show error message or redirect to home

**404 Not Found**
```json
{
  "detail": "Player not found"
}
```
**Cause:** Player profile doesn't exist in the system
**Action:** This shouldn't happen for authenticated users — contact support

**400 Bad Request**
```json
{
  "detail": "Invalid avatar option"
}
```
**Cause:** Validation failed (e.g., avatar not in allowed list)
**Action:** Show error message to user, reset to previous state

**500 Internal Server Error**
```json
{
  "detail": "Internal server error"
}
```
**Cause:** Server-side error
**Action:** Show generic error message, retry after delay

### Error Handling Example

```typescript
async function fetchWithErrorHandling<T>(url: string, options: RequestInit): Promise<T> {
  try {
    const response = await fetch(url, options);

    if (response.status === 401) {
      // Token expired or invalid - redirect to login
      localStorage.removeItem('jwt_token');
      window.location.href = '/login';
      throw new Error('Unauthorized');
    }

    if (response.status === 403) {
      throw new Error('Access forbidden');
    }

    if (response.status === 404) {
      throw new Error('Resource not found');
    }

    if (response.status === 400) {
      const error = await response.json();
      throw new Error(error.detail || 'Bad request');
    }

    if (!response.ok) {
      throw new Error('Network response was not ok');
    }

    return await response.json();
  } catch (error) {
    console.error('API Error:', error);
    throw error;
  }
}
```

---

## Performance & Caching

### Response Times

**Target Performance (95th percentile):**
- Hero Section: < 50ms (profile cached, wallet cached)
- Stats Grid: < 50ms (wallet cached, leaderboard in-memory)
- Mastery: < 50ms on cache hit, < 200ms on cache miss
- Weekly Activity: < 50ms (Redis pipeline, single round-trip)
- Avatar Options: < 20ms (DocType meta read)
- Avatar Update: < 100ms (write + cache invalidation)
- Logout: < 50ms (session + device removal)

### Caching Behavior

| Endpoint | Cache | TTL | Invalidation |
|----------|-------|-----|--------------|
| `/profile` | Profile cache | 1 hour | On avatar update |
| `/profile/stats` | Multiple (wallet, stats, leaderboard) | Varies | On XP earn, lesson complete |
| `/profile/mastery` | Mastery cache | 5 minutes | On review submit |
| `/profile/activity` | Leaderboard ZSETs | No TTL | Updated on XP earn |
| `/profile/avatars` | None | N/A | N/A |

**Client-Side Caching Strategy:**
```typescript
// Cache hero section for 5 minutes
const CACHE_DURATION = 5 * 60 * 1000; // 5 minutes

interface CacheEntry<T> {
  data: T;
  timestamp: number;
}

class ProfileCache {
  private cache = new Map<string, CacheEntry<any>>();

  get<T>(key: string): T | null {
    const entry = this.cache.get(key);
    if (!entry) return null;

    const age = Date.now() - entry.timestamp;
    if (age > CACHE_DURATION) {
      this.cache.delete(key);
      return null;
    }

    return entry.data;
  }

  set<T>(key: string, data: T): void {
    this.cache.set(key, {
      data,
      timestamp: Date.now()
    });
  }

  invalidate(key: string): void {
    this.cache.delete(key);
  }
}

const profileCache = new ProfileCache();

async function fetchHeroWithCache(token: string): Promise<HeroSection> {
  const cached = profileCache.get<HeroSection>('hero');
  if (cached) return cached;

  const data = await fetchHeroSection(token);
  profileCache.set('hero', data);
  return data;
}
```

**Cache Invalidation Events:**
- **Avatar update**: Invalidate hero section cache
- **XP earned**: Invalidate stats, activity caches
- **Review submitted**: Invalidate mastery cache
- **Lesson completed**: Invalidate stats cache

---

## Implementation Guide

### Complete Profile Page Component

Here's a complete example of a React profile page component using all endpoints:

```typescript
import React, { useEffect, useState } from 'react';

interface ProfilePageProps {
  token: string;
  userId: string;
}

const ProfilePage: React.FC<ProfilePageProps> = ({ token, userId }) => {
  const [hero, setHero] = useState<HeroSection | null>(null);
  const [stats, setStats] = useState<StatsGrid | null>(null);
  const [mastery, setMastery] = useState<MemoryMastery | null>(null);
  const [activity, setActivity] = useState<WeeklyActivity | null>(null);
  const [avatarOptions, setAvatarOptions] = useState<string[]>([]);
  const [selectedSubject, setSelectedSubject] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    loadProfileData();
  }, [selectedSubject]);

  async function loadProfileData() {
    try {
      setLoading(true);

      // Fetch all data in parallel
      const [heroData, statsData, masteryData, activityData, avatarsData] = await Promise.all([
        fetchHeroSection(token),
        fetchStats(token, selectedSubject),
        fetchMastery(token, selectedSubject),
        fetchWeeklyActivity(token, selectedSubject),
        fetchAvatarOptions(token)
      ]);

      setHero(heroData);
      setStats(statsData);
      setMastery(masteryData);
      setActivity(activityData);
      setAvatarOptions(avatarsData.avatars);
      setError(null);
    } catch (err) {
      setError('Failed to load profile data');
      console.error(err);
    } finally {
      setLoading(false);
    }
  }

  async function handleAvatarChange(newAvatar: string) {
    try {
      await updateAvatar(token, newAvatar);
      // Reload hero section to show new avatar
      const heroData = await fetchHeroSection(token);
      setHero(heroData);
    } catch (err) {
      alert('Failed to update avatar');
    }
  }

  async function handleLogout() {
    try {
      const deviceId = localStorage.getItem('device_id');
      await logout(token, deviceId || undefined);
    } catch (err) {
      console.error('Logout failed:', err);
    }
  }

  if (loading) return <div>Loading profile...</div>;
  if (error) return <div>Error: {error}</div>;
  if (!hero || !stats || !mastery || !activity) return null;

  return (
    <div className="profile-page">
      {/* Hero Section */}
      <section className="hero">
        <img src={`/avatars/${hero.avatar}`} alt="Avatar" className="avatar" />
        <h1>{hero.display_name}</h1>
        <div className="level">
          <span className="level-number">Level {hero.level}</span>
          <span className="level-title">{hero.level_title}</span>
        </div>
        <div className="xp-progress">
          <div className="progress-bar">
            <div
              className="progress-fill"
              style={{
                width: `${(hero.xp_in_level / (hero.xp_level_end - hero.xp_level_start)) * 100}%`
              }}
            />
          </div>
          <span>{hero.xp_in_level} / {hero.xp_level_end - hero.xp_level_start} XP</span>
          {hero.xp_for_next_level > 0 && (
            <span className="next-level">{hero.xp_for_next_level} XP to next level</span>
          )}
          {hero.xp_for_next_level === 0 && (
            <span className="max-level">MAX LEVEL REACHED!</span>
          )}
        </div>
      </section>

      {/* Subject Filter */}
      <div className="subject-filter">
        <button onClick={() => setSelectedSubject(null)}>All Subjects</button>
        {/* Add subject buttons from your subject list */}
      </div>

      {/* Stats Grid */}
      <section className="stats-grid">
        <div className="stat-card">
          <div className="stat-icon">🔥</div>
          <div className="stat-value">{stats.streak}</div>
          <div className="stat-label">Day Streak</div>
        </div>
        <div className="stat-card">
          <div className="stat-icon">📚</div>
          <div className="stat-value">{stats.items_learned}</div>
          <div className="stat-label">Items Learned</div>
        </div>
        <div className="stat-card">
          <div className="stat-icon">⭐</div>
          <div className="stat-value">{stats.total_xp}</div>
          <div className="stat-label">Total XP</div>
        </div>
      </section>

      {/* Memory Mastery */}
      <section className="mastery">
        <h2>Memory Mastery</h2>
        <div className="mastery-chart">
          <div className="mastery-bar">
            <div
              className="mature"
              style={{ width: `${(mastery.mature / mastery.total) * 100}%` }}
            >
              {mastery.mature}
            </div>
            <div
              className="learning"
              style={{ width: `${(mastery.learning / mastery.total) * 100}%` }}
            >
              {mastery.learning}
            </div>
            <div
              className="new"
              style={{ width: `${(mastery.new_items / mastery.total) * 100}%` }}
            >
              {mastery.new_items}
            </div>
          </div>
          <div className="mastery-legend">
            <span>🟢 Mature: {mastery.mature}</span>
            <span>🟡 Learning: {mastery.learning}</span>
            <span>⚪ New: {mastery.new_items}</span>
          </div>
        </div>
      </section>

      {/* Weekly Activity */}
      <section className="activity">
        <h2>This Week's Activity</h2>
        <div className="activity-chart">
          {activity.days.map(day => (
            <div key={day.date} className="activity-bar">
              <div
                className="bar"
                style={{
                  height: `${(day.xp / Math.max(...activity.days.map(d => d.xp))) * 100}%`
                }}
              />
              <span className="day-label">{day.day_name}</span>
              <span className="xp-value">{day.xp} XP</span>
            </div>
          ))}
        </div>
        <div className="total-xp">Total this week: {activity.total_xp} XP</div>
      </section>

      {/* Avatar Selector */}
      <section className="avatar-selector">
        <h2>Choose Your Avatar</h2>
        <div className="avatar-grid">
          {avatarOptions.map(avatar => (
            <img
              key={avatar}
              src={`/avatars/${avatar}`}
              alt={avatar}
              className={hero.avatar === avatar ? 'selected' : ''}
              onClick={() => handleAvatarChange(avatar)}
            />
          ))}
        </div>
      </section>

      {/* Logout */}
      <button className="logout-button" onClick={handleLogout}>
        Logout
      </button>
    </div>
  );
};

export default ProfilePage;
```

### Mobile App (Flutter/Dart Example)

```dart
class ProfileService {
  final String baseUrl;
  final String token;

  ProfileService(this.baseUrl, this.token);

  Future<HeroSection> fetchHeroSection() async {
    final response = await http.get(
      Uri.parse('$baseUrl/profile'),
      headers: {'Authorization': 'Bearer $token'},
    );

    if (response.statusCode == 200) {
      return HeroSection.fromJson(jsonDecode(response.body));
    } else {
      throw Exception('Failed to load hero section');
    }
  }

  Future<StatsGrid> fetchStats({String? subjectId}) async {
    final uri = Uri.parse('$baseUrl/profile/stats')
        .replace(queryParameters: subjectId != null ? {'subject': subjectId} : null);

    final response = await http.get(
      uri,
      headers: {'Authorization': 'Bearer $token'},
    );

    if (response.statusCode == 200) {
      return StatsGrid.fromJson(jsonDecode(response.body));
    } else {
      throw Exception('Failed to load stats');
    }
  }

  // ... other methods
}
```

---

## Testing

### Manual Testing Checklist

**Hero Section:**
- [ ] Display name shows correctly (supports Arabic)
- [ ] Avatar displays correctly
- [ ] Level and title match current XP
- [ ] Progress bar calculates correctly
- [ ] "MAX LEVEL" badge shows when at level 15

**Stats Grid:**
- [ ] Global stats show combined data
- [ ] Subject filter updates all three stats
- [ ] Streak remains constant regardless of subject filter
- [ ] Items learned and XP update when filtered

**Memory Mastery:**
- [ ] Mature/learning/new counts are reasonable
- [ ] Total equals sum of three categories
- [ ] Subject filter works correctly
- [ ] Visual representation (chart) displays correctly

**Weekly Activity:**
- [ ] Shows exactly 7 days (Mon-Sun)
- [ ] Current week data is accurate
- [ ] Future days show 0 XP
- [ ] Chart scales appropriately
- [ ] Subject filter works

**Avatar Selection:**
- [ ] All avatar options display
- [ ] Current avatar is highlighted
- [ ] Clicking an avatar updates it
- [ ] Hero section refreshes with new avatar
- [ ] Invalid avatars are rejected

**Logout:**
- [ ] Session is invalidated
- [ ] Device is removed (if header sent)
- [ ] Subsequent API calls return 401
- [ ] User is redirected to login

### cURL Test Commands

```bash
# Set your JWT token
TOKEN="your_jwt_token_here"

# Test hero section
curl -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:8002/api/v1/profile

# Test stats (global)
curl -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:8002/api/v1/profile/stats

# Test stats (filtered by subject)
curl -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8002/api/v1/profile/stats?subject=arabic-grammar"

# Test mastery
curl -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:8002/api/v1/profile/mastery

# Test weekly activity
curl -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:8002/api/v1/profile/activity

# Test avatar options
curl -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:8002/api/v1/profile/avatars

# Test avatar update
curl -X PUT \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"avatar":"avatar_02.png"}' \
  http://127.0.0.1:8002/api/v1/profile/avatar

# Test logout
curl -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Device-ID: device_123" \
  http://127.0.0.1:8002/api/v1/profile/logout
```

---

## FAQ

**Q: Why is streak always global and not per-subject?**
A: Streak measures consecutive days of learning activity across the entire platform. This encourages players to engage with multiple subjects and maintains a single, meaningful streak metric.

**Q: What happens to XP when a player reaches max level (15)?**
A: XP continues to accumulate, but `xp_for_next_level` will be 0 and `xp_level_end` will be 0. The UI should display a "MAX LEVEL" indicator.

**Q: How often should I refresh profile data?**
A:
- Hero section: Every 5 minutes or after XP-earning events
- Stats: After lesson completions or XP-earning events
- Mastery: Every 5 minutes (backend caches for 5 min anyway)
- Activity: Daily or when returning to profile page

**Q: Can I filter stats by multiple subjects?**
A: No, the API only supports single-subject filtering or global stats. For multi-subject aggregation, fetch each subject separately and sum on the client side.

**Q: What timezone is used for weekly activity?**
A: Asia/Amman (GMT+3). Week starts on Monday at 00:00:00 in that timezone.

**Q: How do I handle avatar images?**
A: Avatar values are identifiers (e.g., "avatar_01.png"). Prepend your CDN base URL or relative path to construct the full image URL.

**Q: What happens if mastery returns total: 0?**
A: This means the player has no memory states yet (hasn't reviewed any content). Display a "Start learning to see your progress" message.

---

## Support

For questions or issues:
- Backend API issues: Contact backend team or file an issue
- Client implementation help: Refer to code examples above
- Performance concerns: Check caching strategy and network conditions

**API Version:** v1.7
**Last Updated:** 2026-02-10
