# Frontend Integration Guide

This document provides complete API integration instructions for the Memora frontend application.

> **Target Audience**: Frontend AI Agent / Developer
> **Stack**: React + TypeScript + Zustand + Vite
> **Backend**: FastAPI sidecar at `https://x.conanacademy.com`

## Recent Updates (Phase 15 - JWT Simplification)

**Key Changes:**
1. **Identifier-Based Login**: Login now accepts `identifier` field (email OR mobile number) instead of just `email`
2. **Enriched Login Response**: Login response now includes profile data (`display_name`, `avatar`, `gender`, `xp`) directly - no separate profile endpoint needed
3. **JWT Payload Simplified**: Access token now contains `plan` (plan ID) instead of `role` and `tz` fields
4. **Plan ID Source**: Extract `plan_id` from JWT payload (`payload.plan`), not from a separate API call
5. **Session Invalidation**: When a player's plan is changed by admin, their session is automatically invalidated (requires re-login)

**Migration Notes:**
- Update login form to accept email OR mobile number in a single field
- Remove the separate `GET /api/v1/players/me` call after login - profile data is in login response
- Decode JWT access token to extract `plan_id` from `payload.plan` field
- Update profile store to use `LoginProfile` type instead of old `PlayerProfile` type

---

## Table of Contents

1. [Vite Configuration](#vite-configuration)
2. [Authentication Flow](#authentication-flow)
3. [TypeScript Interfaces](#typescript-interfaces)
4. [API Endpoints](#api-endpoints)
   - [Login](#1-login)
   - [Token Refresh](#2-token-refresh)
   - [Extract User Data from JWT](#3-extract-user-data-from-jwt)
   - [Get Plan Manifest](#4-get-plan-manifest)
   - [Get Subject Hierarchy](#5-get-subject-hierarchy)
   - [Get Subject Progress](#6-get-subject-progress)
5. [Page Implementation Guide](#page-implementation-guide)
6. [Error Handling](#error-handling)
7. [State Management (Zustand)](#state-management-zustand)

---

## Vite Configuration

Update your `vite.config.ts`:

```typescript
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: 'https://x.conanacademy.com',
        changeOrigin: true,
        secure: false,
        cookieDomainRewrite: 'localhost',
      },
      '/memora_content': {
        target: 'https://x.conanacademy.com',
        changeOrigin: true,
        secure: false,
        cookieDomainRewrite: 'localhost',
      },
    },
  },
});
```

**Notes:**
- `/api` proxy handles all FastAPI endpoints
- `/memora_content` proxy handles static JSON files (hierarchy data)

---

## Authentication Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│                         LOGIN FLOW                                   │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  1. User enters email/mobile + password                              │
│                    │                                                 │
│                    ▼                                                 │
│  2. POST /api/v1/auth/login                                         │
│     Headers: X-Device-ID (required)                                  │
│     Body: { identifier, password }                                   │
│     Note: identifier can be email OR mobile number                   │
│                    │                                                 │
│                    ▼                                                 │
│  3. Login returns tokens AND profile in single response              │
│     - access_token (short-lived, ~15 min)                           │
│     - refresh_token (long-lived, ~30 days)                          │
│     - profile: { display_name, avatar, gender, xp }                 │
│                    │                                                 │
│                    ▼                                                 │
│  4. Store tokens and profile data (localStorage)                    │
│                    │                                                 │
│                    ▼                                                 │
│  5. Decode access_token to extract plan_id from JWT payload         │
│                    │                                                 │
│                    ▼                                                 │
│  6. Redirect to Plan page (subjects list)                           │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### Token Management

```typescript
// Check if token is expired (decode JWT without verification)
function isTokenExpired(token: string): boolean {
  try {
    const payload = JSON.parse(atob(token.split('.')[1]));
    return payload.exp * 1000 < Date.now();
  } catch {
    return true;
  }
}

// Refresh token when access token expires
async function refreshAccessToken(): Promise<string | null> {
  const refreshToken = localStorage.getItem('refresh_token');
  if (!refreshToken || isTokenExpired(refreshToken)) {
    // Redirect to login
    return null;
  }

  const response = await fetch('/api/v1/auth/refresh', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh_token: refreshToken }),
  });

  if (!response.ok) {
    // Redirect to login
    return null;
  }

  const data = await response.json();
  localStorage.setItem('access_token', data.access_token);
  return data.access_token;
}
```

### Device ID Generation

Generate a unique device ID on first app launch and persist it:

```typescript
function getOrCreateDeviceFingerPrint(): string {
  // using fingerprint.js or something similar
}
```

---

## TypeScript Interfaces

Create a file `src/types/api.ts`:

```typescript
// ============================================
// AUTH TYPES
// ============================================

export interface LoginRequest {
  identifier: string;  // Email OR mobile number
  password: string;
}

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: 'bearer';
}

export interface LoginProfile {
  display_name: string;
  avatar: string;
  gender: string | null;  // Optional - may not be set
  xp: number;
}

export interface EnrichedTokenResponse extends TokenResponse {
  profile: LoginProfile;
}

export interface RefreshRequest {
  refresh_token: string;
}

// ============================================
// TOKEN PAYLOAD TYPE
// ============================================

export interface TokenPayload {
  sub: string;        // User ID
  fid: string;        // Family ID (session identifier)
  type: string;       // "access" or "refresh"
  exp: number;        // Expiration timestamp
  jti: string;        // JWT ID (unique identifier)

  // Access token specific fields (null for refresh tokens)
  email: string | null;
  plan: string | null;    // Plan ID (e.g., 'PLAN-00001')
  name: string | null;    // Display name

  // Optional fields
  iat: number | null;     // Issued at timestamp
}

// ============================================
// PLAN TYPES
// ============================================

export interface PlanSubject {
  id: string;
  title: string;
  alias_title: string | null;
  image: string | null;
  total_lessons: number;
  total_tracks: number;
  is_premium: boolean;
  is_free_preview: boolean;
  hierarchy_url: string;
}

export interface PlanManifest {
  schema_version: number;
  version: number;
  generated_at: string;
  plan_id: string;
  title: string;
  grade_id: string | null;
  grade_title: string;
  major_id: string | null;
  major_title: string;
  season_id: string | null;
  subjects: PlanSubject[];
}

// ============================================
// HIERARCHY TYPES (Static JSON)
// ============================================

export interface LessonInfo {
  lesson_id: string;
  title: string;
  bit_index: number;
  xp: number;
}

export interface TopicInfo {
  topic_id: string;
  title: string;
  is_linear: boolean;
  lessons: LessonInfo[];
}

export interface UnitInfo {
  unit_id: string;
  title: string;
  is_linear: boolean;
  is_free: boolean;
  topics: TopicInfo[];
}

export interface TrackInfo {
  track_id: string;
  title: string;
  is_linear: boolean;
  units: UnitInfo[];
}

export interface SubjectHierarchy {
  subject_id: string;
  title: string;
  version: number;
  bit_range: number;
  excluded_bits: number[];
  is_linear: boolean;
  tracks: TrackInfo[];
}

// ============================================
// PROGRESS TYPES
// ============================================

export interface TopicProgress {
  topic_id: string;
  completed: number;
  total: number;
  percentage: number;
  unlocked: boolean;
}

export interface UnitProgress {
  unit_id: string;
  completed: number;
  total: number;
  percentage: number;
  unlocked: boolean;
  topics: TopicProgress[];
}

export interface TrackProgress {
  track_id: string;
  completed: number;
  total: number;
  percentage: number;
  unlocked: boolean;
  units: UnitProgress[];
}

export interface SubjectProgress {
  subject_id: string;
  completed: number;
  total: number;
  percentage: number;
  tracks: TrackProgress[];
}

// ============================================
// ERROR TYPES
// ============================================

export interface ApiError {
  detail: string | {
    code: string;
    message: string;
  };
}

export interface RateLimitError {
  detail: string;
  retry_after: number;
}

export interface DeviceLimitError {
  code: 'DEVICE_LIMIT_EXCEEDED';
  message: string;
}
```

---

## API Endpoints

### 1. Login

**Endpoint:** `POST /api/v1/auth/login`

**Important:** Rebuild login page logic from scratch. Keep the existing design, but replace all previous login implementation.

#### Request

```typescript
// Headers (REQUIRED)
{
  'Content-Type': 'application/json',
  'X-Device-ID': string,        // Required - unique device identifier
  'X-Platform': 'Web' | 'iOS' | 'Android'  // Optional
}

// Body
{
  "identifier": "student@example.com",  // Email OR mobile number
  "password": "securepassword"
}
```

**Note:** The `identifier` field accepts either:
- Email address (contains `@`)
- Mobile number (digits only)

The backend automatically detects the type and handles accordingly.

#### Response (Success - 200)

```typescript
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "refresh_token": "eyJhbGciOiJIUzI1NiIs...",
  "token_type": "bearer",
  "profile": {
    "display_name": "أحمد محمد",
    "avatar": "avatar 1",
    "gender": "male",              // Optional - may be null
    "xp": 1500
  }
}
```

#### Response (Error Cases)

| Status | Scenario | Response |
|--------|----------|----------|
| 400 | Missing X-Device-ID header | `{ "detail": { "code": "DEVICE_ID_REQUIRED", "message": "X-Device-ID header required" } }` |
| 401 | Invalid credentials (wrong email/mobile/password) | `{ "detail": "Invalid credentials" }` |
| 401 | Player has no plan assigned | `{ "detail": "Player must have a plan assigned" }` |
| 429 | Rate limited | `{ "detail": "Too many login attempts", "retry_after": 60 }` |
| 429 | Device limit exceeded | `{ "code": "DEVICE_LIMIT_EXCEEDED", "message": "Device limit reached (3/3)..." }` |

#### Implementation Example

```typescript
async function login(identifier: string, password: string): Promise<EnrichedTokenResponse> {
  const deviceId = getOrCreateDeviceFingerPrint();

  const response = await fetch('/api/v1/auth/login', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Device-ID': deviceId,
      'X-Platform': 'Web',
    },
    body: JSON.stringify({ identifier, password }),
  });

  if (response.status === 429) {
    const error = await response.json();
    if (error.code === 'DEVICE_LIMIT_EXCEEDED') {
      throw new Error(error.message);
    }
    throw new Error(`Too many attempts. Retry in ${error.retry_after} seconds.`);
  }

  if (response.status === 401) {
    const error = await response.json();
    if (error.detail === 'Player must have a plan assigned') {
      throw new Error('لم يتم تعيين خطة لحسابك. يرجى التواصل مع الدعم.');
    }
    throw new Error('البريد الإلكتروني أو رقم الهاتف أو كلمة المرور غير صحيحة');
  }

  if (!response.ok) {
    throw new Error('حدث خطأ. حاول مرة أخرى.');
  }

  const data: EnrichedTokenResponse = await response.json();

  // Store tokens
  localStorage.setItem('access_token', data.access_token);
  localStorage.setItem('refresh_token', data.refresh_token);

  // Store profile data
  localStorage.setItem('profile', JSON.stringify(data.profile));

  // Decode access token to get plan_id
  const payload = JSON.parse(atob(data.access_token.split('.')[1]));
  localStorage.setItem('plan_id', payload.plan);

  return data;
}
```

---

### 2. Token Refresh

**Endpoint:** `POST /api/v1/auth/refresh`

Use this when the access token expires. The refresh token is reusable (not rotated).

#### Request

```typescript
// Body
{
  "refresh_token": "eyJhbGciOiJIUzI1NiIs..."
}
```

#### Response (Success - 200)

```typescript
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",  // New access token
  "refresh_token": "eyJhbGciOiJIUzI1NiIs...", // Same refresh token (not rotated)
  "token_type": "bearer"
}
```

#### Response (Error - 401)

Session invalidated (user logged in from another device):

```typescript
{
  "detail": "Invalid credentials"
}
```

**Action:** Redirect to login page.

---

### 3. Extract User Data from JWT

Since the login response now includes profile data, you don't need a separate API call to get user information. However, you need to extract `plan_id` from the JWT access token.

#### Decoding JWT Access Token

```typescript
// Helper function to decode JWT payload
function decodeAccessToken(token: string): TokenPayload {
  try {
    const payload = JSON.parse(atob(token.split('.')[1]));
    return payload;
  } catch {
    throw new Error('Invalid token format');
  }
}

// After login, extract plan_id
const payload = decodeAccessToken(data.access_token);
const planId = payload.plan;  // e.g., "PLAN-00001"
const userId = payload.sub;    // User ID (email)
const displayName = payload.name;  // Display name

// Store for later use
localStorage.setItem('plan_id', planId);
localStorage.setItem('user_id', userId);
```

#### JWT Payload Structure

The access token contains:
- `sub`: User ID (email address)
- `fid`: Family ID (session identifier)
- `plan`: Plan ID (e.g., "PLAN-00001")
- `name`: Display name
- `email`: Email address
- `type`: "access"
- `exp`: Expiration timestamp
- `iat`: Issued at timestamp
- `jti`: JWT ID

**Important:** The refresh token has a minimal payload (only `sub`, `fid`, `type`, `exp`, `jti`) for security. Always use the access token for user information.

---

### 4. Get Plan Manifest

**Endpoint:** `GET /api/v1/plans/{plan_id}/manifest`

**Purpose:** Get list of subjects in the player's plan. This is the data for the **Plan Page (Subjects List)**.

#### Request

```typescript
// URL
GET /api/v1/plans/PLAN-00001/manifest

// Headers
{
  'Authorization': 'Bearer {access_token}'
}
```

#### Response (Success - 200)

```typescript
{
  "schema_version": 1,
  "version": 1706275200,
  "generated_at": "2026-02-03T14:30:00Z",
  "plan_id": "PLAN-00001",
  "title": "الثالث الثانوي - علمي",
  "grade_id": "GRD-00001",
  "grade_title": "الثالث الثانوي",
  "major_id": "MJR-00001",
  "major_title": "علمي",
  "season_id": "SEASON-2026-1",
  "subjects": [
    {
      "id": "SUBJ-MATH-G12",
      "title": "الرياضيات",
      "alias_title": null,
      "image": "/memora_content/images/subjects/math.png",
      "total_lessons": 245,
      "total_tracks": 4,
      "is_premium": true,
      "is_free_preview": true,
      "hierarchy_url": "/memora_content/hierarchies/SUBJ-MATH-G12.json"
    },
    {
      "id": "SUBJ-PHYS-G12",
      "title": "الفيزياء",
      "alias_title": null,
      "image": "/memora_content/images/subjects/physics.png",
      "total_lessons": 180,
      "total_tracks": 3,
      "is_premium": true,
      "is_free_preview": false,
      "hierarchy_url": "/memora_content/hierarchies/SUBJ-PHYS-G12.json"
    }
  ]
}
```

#### Response (Error - 404)

```typescript
{
  "detail": "Plan PLAN-00001 not found"
}
```

---

### 5. Get Subject Hierarchy

**Endpoint:** `GET {hierarchy_url}` (Static JSON file)

**Purpose:** Get the complete structure of a subject (tracks → units → topics → lessons). Use the `hierarchy_url` from the plan manifest.

#### Request

```typescript
// Direct fetch - no auth needed (public static file)
GET /memora_content/hierarchies/SUBJ-MATH-G12.json
```

#### Response (Success - 200)

```typescript
{
  "subject_id": "SUBJ-MATH-G12",
  "title": "الرياضيات",
  "version": 1,
  "bit_range": 245,
  "excluded_bits": [],
  "is_linear": false,
  "tracks": [
    {
      "track_id": "TRK-MATH-01",
      "title": "الجبر",
      "is_linear": true,
      "units": [
        {
          "unit_id": "UNIT-MATH-01-01",
          "title": "المعادلات الخطية",
          "is_linear": true,
          "is_free": true,
          "topics": [
            {
              "topic_id": "TOPIC-001",
              "title": "مقدمة في المعادلات",
              "is_linear": true,
              "lessons": [
                {
                  "lesson_id": "LESSON-001",
                  "title": "ما هي المعادلة؟",
                  "bit_index": 0,
                  "xp": 100
                },
                {
                  "lesson_id": "LESSON-002",
                  "title": "حل المعادلات البسيطة",
                  "bit_index": 1,
                  "xp": 100
                }
              ]
            }
          ]
        }
      ]
    }
  ]
}
```

**Key Fields:**
- `is_linear`: If `true`, items must be completed in order
- `is_free`: If `true`, unit is accessible without subscription
- `bit_index`: Used internally for progress tracking
- `xp`: XP awarded on first completion

---

### 6. Get Subject Progress

**Endpoint:** `GET /api/v1/progress/{subject_id}`

**Purpose:** Get completion percentages and unlock states to overlay on hierarchy data.

#### Request

```typescript
// URL
GET /api/v1/progress/SUBJ-MATH-G12

// Headers
{
  'Authorization': 'Bearer {access_token}'
}
```

#### Response (Success - 200)

```typescript
{
  "subject_id": "SUBJ-MATH-G12",
  "completed": 15,
  "total": 245,
  "percentage": 6.1,
  "tracks": [
    {
      "track_id": "TRK-MATH-01",
      "completed": 15,
      "total": 60,
      "percentage": 25.0,
      "unlocked": true,
      "units": [
        {
          "unit_id": "UNIT-MATH-01-01",
          "completed": 10,
          "total": 20,
          "percentage": 50.0,
          "unlocked": true,
          "topics": [
            {
              "topic_id": "TOPIC-001",
              "completed": 5,
              "total": 5,
              "percentage": 100.0,
              "unlocked": true
            },
            {
              "topic_id": "TOPIC-002",
              "completed": 5,
              "total": 10,
              "percentage": 50.0,
              "unlocked": true
            }
          ]
        },
        {
          "unit_id": "UNIT-MATH-01-02",
          "completed": 5,
          "total": 20,
          "percentage": 25.0,
          "unlocked": true,
          "topics": [
            {
              "topic_id": "TOPIC-003",
              "completed": 5,
              "total": 8,
              "percentage": 62.5,
              "unlocked": true
            },
            {
              "topic_id": "TOPIC-004",
              "completed": 0,
              "total": 12,
              "percentage": 0.0,
              "unlocked": false
            }
          ]
        }
      ]
    },
    {
      "track_id": "TRK-MATH-02",
      "completed": 0,
      "total": 50,
      "percentage": 0.0,
      "unlocked": false,
      "units": []
    }
  ]
}
```

#### Response (Error - 403)

No access to subject:

```typescript
{
  "detail": {
    "code": "NO_ACCESS",
    "message": "Content access required"
  }
}
```

#### Response (Error - 404)

Subject not found:

```typescript
{
  "detail": {
    "code": "SUBJECT_NOT_FOUND",
    "message": "Subject not found"
  }
}
```

---

## Page Implementation Guide

### Login Page

**Design:** Keep existing design unchanged.

**Logic:** Rebuild completely with new implementation.

```typescript
// Login page flow
1. User enters email/mobile + password
2. Call login() function
3. On success:
   - Tokens are stored automatically
   - Profile data (display_name, avatar, gender, xp) stored from response
   - Extract plan_id from access token JWT payload
   - Store profile and plan_id in Zustand
   - Navigate to Plan page
4. On error:
   - Show appropriate Arabic error message
   - Handle rate limiting (show countdown)
   - Handle device limit (show contact support message)
   - Handle "no plan assigned" error (show contact support message)
```

**Key Changes from Previous Version:**
- Login form accepts **identifier** (email OR mobile) instead of just email
- Profile data is returned directly in login response (no separate API call needed)
- `plan_id` is extracted from JWT payload, not from a separate profile endpoint

### Plan Page (Subjects List)

**Route:** `/plan` or `/subjects`

**Data Flow:**

```typescript
1. Get plan_id from localStorage or decode from access_token JWT
2. Fetch plan manifest: GET /api/v1/plans/{plan_id}/manifest
3. Display subjects as cards/list
4. Each subject shows:
   - Subject image (from subject.image)
   - Subject title (from subject.title)
   - Total lessons count (from subject.total_lessons)
   - Premium badge if is_premium && !is_free_preview
   - Free preview badge if is_free_preview
5. On subject click → Navigate to Tracks page
```

**Note:** The `plan_id` comes from the JWT access token payload (`payload.plan`), not from a separate profile endpoint.

### Tracks Page

**Route:** `/subjects/:subjectId/tracks`

**Data Flow:**

```typescript
1. Get subject from plan manifest (already cached)
2. Fetch hierarchy: GET {subject.hierarchy_url}
3. Fetch progress: GET /api/v1/progress/{subjectId}
4. Merge hierarchy with progress data
5. Display tracks list:
   - Track title
   - Progress percentage
   - Locked state (grayed out if unlocked: false)
6. On track click → Navigate to Units page
```

### Units Page

**Route:** `/subjects/:subjectId/tracks/:trackId/units`

**Data Flow:**

```typescript
1. Get track from cached hierarchy
2. Get track progress from cached progress
3. Display units list:
   - Unit title
   - Progress percentage
   - Free badge if is_free
   - Locked state (grayed out if unlocked: false)
4. On unit click → Navigate to Topics page
```

### Topics Page

**Route:** `/subjects/:subjectId/tracks/:trackId/units/:unitId/topics`

**Data Flow:**

```typescript
1. Get unit from cached hierarchy
2. Get unit progress from cached progress
3. Display topics list:
   - Topic title
   - Progress (completed/total lessons)
   - Progress percentage
   - Locked state (grayed out if unlocked: false)
4. On topic click → Navigate to Topic Lessons page
```

### Topic Lessons Page

**Route:** `/subjects/:subjectId/tracks/:trackId/units/:unitId/topics/:topicId/lessons`

**Data Flow:**

```typescript
1. Get topic from cached hierarchy
2. Calculate lesson unlock states (based on is_linear and completion)
3. Display lessons list:
   - Lesson title
   - XP reward
   - Completed state (checkmark)
   - Locked state (grayed out)
4. On lesson click → (Next phase - lesson player)
```

---

## Error Handling

### HTTP Status Codes

| Status | Meaning | Action |
|--------|---------|--------|
| 200 | Success | Process response |
| 400 | Bad request | Show validation error |
| 401 | Unauthorized | Refresh token or redirect to login |
| 403 | Forbidden | Show access denied message |
| 404 | Not found | Show not found message |
| 429 | Rate limited | Show retry countdown |
| 500 | Server error | Show generic error, retry |

### API Wrapper with Auto-Refresh

```typescript
async function apiCall<T>(
  url: string,
  options: RequestInit = {}
): Promise<T> {
  let accessToken = localStorage.getItem('access_token');

  // Check if token needs refresh
  if (accessToken && isTokenExpired(accessToken)) {
    accessToken = await refreshAccessToken();
    if (!accessToken) {
      // Redirect to login
      window.location.href = '/login';
      throw new Error('Session expired');
    }
  }

  const response = await fetch(url, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(accessToken && { 'Authorization': `Bearer ${accessToken}` }),
      ...options.headers,
    },
  });

  if (response.status === 401) {
    // Try refresh once
    accessToken = await refreshAccessToken();
    if (!accessToken) {
      window.location.href = '/login';
      throw new Error('Session expired');
    }

    // Retry request
    const retryResponse = await fetch(url, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${accessToken}`,
        ...options.headers,
      },
    });

    if (!retryResponse.ok) {
      throw new Error('Request failed');
    }

    return retryResponse.json();
  }

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail?.message || error.detail || 'Request failed');
  }

  return response.json();
}
```

---

## State Management (Zustand)

### Recommended Store Structure

```typescript
// src/stores/authStore.ts
import { create } from 'zustand';
import { persist } from 'zustand/middleware';

interface AuthState {
  accessToken: string | null;
  refreshToken: string | null;
  isAuthenticated: boolean;
  setTokens: (access: string, refresh: string) => void;
  clearTokens: () => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      accessToken: null,
      refreshToken: null,
      isAuthenticated: false,
      setTokens: (access, refresh) =>
        set({ accessToken: access, refreshToken: refresh, isAuthenticated: true }),
      clearTokens: () =>
        set({ accessToken: null, refreshToken: null, isAuthenticated: false }),
    }),
    { name: 'auth-storage' }
  )
);
```

```typescript
// src/stores/profileStore.ts
import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { LoginProfile } from '../types/api';

interface ProfileState {
  profile: LoginProfile | null;
  planId: string | null;
  userId: string | null;
  setProfile: (profile: LoginProfile, planId: string, userId: string) => void;
  clearProfile: () => void;
}

export const useProfileStore = create<ProfileState>()(
  persist(
    (set) => ({
      profile: null,
      planId: null,
      userId: null,
      setProfile: (profile, planId, userId) =>
        set({ profile, planId, userId }),
      clearProfile: () =>
        set({ profile: null, planId: null, userId: null }),
    }),
    { name: 'profile-storage' }
  )
);
```

```typescript
// src/stores/contentStore.ts
import { create } from 'zustand';
import { PlanManifest, SubjectHierarchy, SubjectProgress } from '../types/api';

interface ContentState {
  planManifest: PlanManifest | null;
  hierarchies: Record<string, SubjectHierarchy>;
  progress: Record<string, SubjectProgress>;

  setPlanManifest: (manifest: PlanManifest) => void;
  setHierarchy: (subjectId: string, hierarchy: SubjectHierarchy) => void;
  setProgress: (subjectId: string, progress: SubjectProgress) => void;
  clearContent: () => void;
}

export const useContentStore = create<ContentState>((set) => ({
  planManifest: null,
  hierarchies: {},
  progress: {},

  setPlanManifest: (manifest) => set({ planManifest: manifest }),
  setHierarchy: (subjectId, hierarchy) =>
    set((state) => ({
      hierarchies: { ...state.hierarchies, [subjectId]: hierarchy },
    })),
  setProgress: (subjectId, progress) =>
    set((state) => ({
      progress: { ...state.progress, [subjectId]: progress },
    })),
  clearContent: () => set({ planManifest: null, hierarchies: {}, progress: {} }),
}));
```

---

## Data Merging Helper

When displaying tracks/units/topics, merge hierarchy (structure) with progress (completion):

```typescript
// src/utils/mergeData.ts
import { TrackInfo, TrackProgress, UnitInfo, UnitProgress } from '../types/api';

interface MergedTrack extends TrackInfo {
  completed: number;
  total: number;
  percentage: number;
  unlocked: boolean;
}

export function mergeTracksWithProgress(
  tracks: TrackInfo[],
  progressTracks: TrackProgress[]
): MergedTrack[] {
  return tracks.map((track) => {
    const progress = progressTracks.find((p) => p.track_id === track.track_id);
    return {
      ...track,
      completed: progress?.completed ?? 0,
      total: progress?.total ?? 0,
      percentage: progress?.percentage ?? 0,
      unlocked: progress?.unlocked ?? true,
    };
  });
}

// Similar helpers for units, topics, lessons...
```

---

## Locked Content Display

Show locked content grayed out with a lock icon:

```typescript
// Component example
function TrackCard({ track, progress }: { track: TrackInfo; progress: TrackProgress }) {
  const isLocked = !progress.unlocked;

  return (
    <div className={`track-card ${isLocked ? 'locked' : ''}`}>
      {isLocked && <LockIcon className="lock-icon" />}
      <h3>{track.title}</h3>
      <ProgressBar percentage={progress.percentage} />
      <span>{progress.completed}/{progress.total} دروس</span>
    </div>
  );
}
```

```css
.track-card.locked {
  opacity: 0.5;
  pointer-events: none;
  position: relative;
}

.lock-icon {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
}
```

---

## Summary: API Calls Per Page

| Page | API Calls |
|------|-----------|
| Login | `POST /api/v1/auth/login` (returns tokens + profile data) |
| Plan (Subjects) | `GET /api/v1/plans/{plan_id}/manifest` |
| Tracks | `GET {hierarchy_url}` + `GET /api/v1/progress/{subject}` |
| Units | (Use cached hierarchy + progress) |
| Topics | (Use cached hierarchy + progress) |
| Topic Lessons | (Use cached hierarchy + progress) |

**Note:** The login flow has been simplified - profile data is returned directly in the login response, eliminating the need for a separate profile endpoint call.

---

## Next Steps (Future Documentation)

The following will be documented in a future update:

- Lesson player flow (sessions API)
- Wallet and XP display
- Leaderboards
- Device management (v1.3)
- Notifications
