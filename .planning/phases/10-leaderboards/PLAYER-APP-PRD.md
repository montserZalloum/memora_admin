# Product Requirements Document: Leaderboard Page (Player App)

## Executive Summary

The Memora backend now provides **competitive XP leaderboards** via REST API. Players can view global rankings (all-time/daily/weekly) and check their personal rank with context (neighbors ±2, distance to next tier).

**Your mission:** Build a leaderboard UI that displays rankings and motivates players through competitive features.

---

## Background & Context

### Current State (Before This Feature)

Players have no way to:
- See their XP rank compared to peers
- Find out if they're winning
- Get motivated by competition
- Track progress against others

### New User Flow (With Leaderboards)

1. Player opens app → sees "Leaderboards" tab in navigation
2. Player selects leaderboard type: **All-Time** / **Daily** / **Weekly**
3. Player optionally filters by **Subject** (e.g., Biology, Chemistry)
4. Player sees:
   - **Top 10 players** with rank, name, avatar, XP
   - **Their own rank** with neighbors (±2 players around them)
   - **Distance to next tier** (XP needed to pass the player above)
5. Player gets motivated to earn more XP to climb ranks

### Why This Matters

- **Competitive motivation:** Players compete for top spots
- **Social feature:** See peer progress in real-time
- **Subject-specific:** Class competitions (biology leaderboard, chemistry leaderboard)
- **Real-time updates:** Leaderboards refresh as XP is earned
- **Tied players:** Multiple players at same XP share the same rank (fair scoring)

---

## Technical Architecture

### Backend Components (Already Built ✅)

```
┌─────────────────────────────────────────┐
│ Player earns XP                         │
│ (lesson complete, quiz pass, etc.)      │
└─────────────────────────────────────────┘
           ↓
┌─────────────────────────────────────────┐
│ FastAPI: POST /session/end              │
│ - Awards XP to player                   │
│ - Updates leaderboards in Redis         │
│   (all-time, daily, weekly, subject)    │
└─────────────────────────────────────────┘
           ↓
┌─────────────────────────────────────────┐
│ Redis ZSET Leaderboards                 │
│ Keys:                                   │
│ - memora:lb:alltime                     │
│ - memora:lb:daily:{YYYY-MM-DD}          │
│ - memora:lb:weekly:{YYYY-Www}           │
│ - memora:lb:alltime:subject:{id}        │
└─────────────────────────────────────────┘
           ↓
┌─────────────────────────────────────────┐
│ Player App (REST Client) ← YOUR WORK    │
│ Fetches leaderboard data & displays UI  │
└─────────────────────────────────────────┘
```

---

## Requirements

### 1. Leaderboard Types

**Three leaderboard time periods:**

| Type | Description | Reset Time | Use Case |
|------|-------------|-----------|----------|
| **all-time** | Total XP earned since account creation | Never | Career ranking |
| **daily** | XP earned today | Midnight Asia/Amman timezone | Daily competition |
| **weekly** | XP earned this week | Friday midnight Asia/Amman | Weekly sprint |

**Note:** Asia/Amman timezone is `UTC+3` (no DST). Friday midnight = start of weekend in Middle East.

### 2. Main Leaderboard View (`GET /leaderboard/{type}`)

#### 2.1 UI Layout

```
┌──────────────────────────────────────────────────┐
│  LEADERBOARDS                              ☰    │
├──────────────────────────────────────────────────┤
│                                                  │
│  ┌─ All-Time ─┬─ Daily ─┬─ Weekly ─┐  Subject: ▼│
│                                                  │
│  Rank │ Player           │ XP      │ Trend      │
│  ─────┼──────────────────┼─────────┼────────────┤
│   1   │ 👨 Ahmed Ali     │ 2,450   │ ↑ +50      │
│   1   │ 👩 Fatima Khan   │ 2,450   │ ↑ +50  ← Tied │
│   3   │ 👨 Omar Hassan   │ 2,400   │ ↓ -10      │
│   4   │ 👩 Leila Ahmed   │ 2,350   │ ↑ +100     │
│   5   │ 👨 You           │ 2,300   │ ↑ +75      │
│        │                  │         │            │
│  [Load More ▼]                                   │
│                                                  │
└──────────────────────────────────────────────────┘
```

**Components:**

1. **Tab selector** (All-Time / Daily / Weekly)
   - Switch between leaderboard types
   - Preserve scroll position when switching

2. **Subject filter** (optional)
   - Dropdown: "All Subjects" / "Biology" / "Chemistry" / etc.
   - Refetch leaderboard when changed
   - Show which subject is selected

3. **Leaderboard entries** (top N)
   - **Rank**: Dense ranking (tied players share rank, e.g., two #5s, then #7)
   - **Avatar**: Player profile picture (32x32 px)
   - **Display name**: Player's chosen display name
   - **XP**: Current XP for this leaderboard period
   - **Trend**: Previous 24h change (↑ +50 / ↓ -10 / → 0)
   - **Highlight**: Your row different background color (light blue)

4. **Pagination**
   - Show top 10 by default
   - "Load More" button to fetch next 10
   - Or infinite scroll to auto-load

5. **Empty state**
   - If no entries: "No leaderboard data yet. Earn XP to appear!"

#### 2.2 API Endpoint

```http
GET /api/v1/leaderboard/{type}
  ?limit=10
  &subject_id=SUBJ-00028
  &offset=0

Authorization: Bearer {JWT_ACCESS_TOKEN}

Response:
{
  "leaderboard_type": "alltime",
  "subject_id": null,
  "entries": [
    {
      "rank": 1,
      "player_id": "player-001",
      "display_name": "Ahmed Ali",
      "xp": 2450,
      "avatar": "avatar-001",
      "is_me": false
    },
    {
      "rank": 1,
      "player_id": "player-002",
      "display_name": "Fatima Khan",
      "xp": 2450,
      "avatar": "avatar-002",
      "is_me": false
    },
    {
      "rank": 3,
      "player_id": "current-user",
      "display_name": "You",
      "xp": 2300,
      "avatar": "your-avatar",
      "is_me": true
    }
  ],
  "total_players": 1250
}
```

#### 2.3 Request Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `{type}` | string | — | Path param: `daily` / `weekly` / `alltime` |
| `limit` | integer | 10 | How many entries to return (1-100) |
| `subject_id` | string | null | Optional subject filter (e.g., `SUBJ-00028`) |
| `offset` | integer | 0 | For pagination (0, 10, 20, ...) |

### 3. My Rank View (`GET /leaderboard/{type}/me`)

#### 3.1 UI Layout

```
┌──────────────────────────────────────────────────┐
│  YOUR RANK                                   ☰   │
├──────────────────────────────────────────────────┤
│                                                  │
│  ALL-TIME LEADERBOARD                           │
│                                                  │
│  ┌────────────────────────────────────────────┐ │
│  │  YOUR POSITION                             │ │
│  │                                            │ │
│  │  Rank: #5                                  │ │
│  │  XP: 2,300                                 │ │
│  │  To beat next player: +51 XP               │ │
│  │                                            │ │
│  │  [Complete a Lesson] [View All]            │ │
│  └────────────────────────────────────────────┘ │
│                                                  │
│  PLAYERS AROUND YOU                             │
│  ┌────────────────────────────────────────────┐ │
│  │ Rank │ Player              │ XP             │ │
│  │ ────┼─────────────────────┼────────────── │ │
│  │  3   │ 👨 Omar Hassan      │ 2,400         │ │
│  │  4   │ 👩 Leila Ahmed      │ 2,350         │ │
│  │  5   │ 👨 YOU              │ 2,300 ← You   │ │
│  │  6   │ 👨 Mohamed Saeed    │ 2,200         │ │
│  │  7   │ 👩 Noor Khalifa     │ 2,100         │ │
│  └────────────────────────────────────────────┘ │
│                                                  │
│  TIP: Earn 51 more XP to pass Leila Ahmed!      │
│                                                  │
└──────────────────────────────────────────────────┘
```

**Components:**

1. **Your Position Card** (prominent)
   - Big rank number (e.g., #5)
   - Your current XP
   - XP needed to pass the player above you
   - "Complete a Lesson" CTA button (navigates to learning)
   - "View All" link (shows full leaderboard)

2. **Motivational tip**
   - "Earn {xp_to_next} more XP to beat {player_above_name}!"
   - Updates in real-time as you earn XP

3. **Neighbors list** (±2 players around you)
   - 2 players above you
   - You
   - 2 players below you
   - Highlight your row

4. **Empty state** (if unranked)
   - "You haven't earned any XP yet!"
   - "Complete your first lesson to appear on the leaderboard."
   - CTA: "Start Learning"

#### 3.2 API Endpoint

```http
GET /api/v1/leaderboard/{type}/me
  ?subject_id=SUBJ-00028

Authorization: Bearer {JWT_ACCESS_TOKEN}

Response:
{
  "rank": 5,
  "xp": 2300,
  "xp_to_next": 51,
  "neighbors": [
    {
      "rank": 3,
      "player_id": "player-003",
      "display_name": "Omar Hassan",
      "xp": 2400,
      "avatar": "avatar-003",
      "is_me": false
    },
    {
      "rank": 4,
      "player_id": "player-004",
      "display_name": "Leila Ahmed",
      "xp": 2350,
      "avatar": "avatar-004",
      "is_me": false
    },
    {
      "rank": 5,
      "player_id": "current-user",
      "display_name": "You",
      "xp": 2300,
      "avatar": "your-avatar",
      "is_me": true
    },
    {
      "rank": 6,
      "player_id": "player-006",
      "display_name": "Mohamed Saeed",
      "xp": 2200,
      "avatar": "avatar-006",
      "is_me": false
    },
    {
      "rank": 7,
      "player_id": "player-007",
      "display_name": "Noor Khalifa",
      "xp": 2100,
      "avatar": "avatar-007",
      "is_me": false
    }
  ],
  "total_players": 1250
}
```

#### 3.3 Request Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `{type}` | string | — | Path param: `daily` / `weekly` / `alltime` |
| `subject_id` | string | null | Optional subject filter |

### 4. Key Concepts

#### 4.1 Dense Ranking

**What it is:** Tied players share the same rank number.

**Example:**
```
Rank  Player    XP
─────────────────
 1    Ahmed     2450  ← Rank 1
 1    Fatima    2450  ← Also Rank 1 (tied)
 3    Omar      2400  ← Rank 3 (not 2!)
 4    Leila     2350  ← Rank 4
```

**Not standard ranking:**
```
Rank  Player    XP
─────────────────
 1    Ahmed     2450
 2    Fatima    2450  ← WRONG: Shows as rank 2
 3    Omar      2400
 4    Leila     2350
```

**Why this matters:** Dense ranking feels fairer — you don't get demoted by someone else tying with you.

#### 4.2 Tie-Breaking: Earlier Achiever Wins

**Rule:** When two players have the same XP, the one who reached that XP **first** ranks higher.

**Example:**
```
Player A: Earned 2,450 XP at 2:00 PM → Rank 1
Player B: Earned 2,450 XP at 2:05 PM → Rank 2 (same XP, but slower)
```

**Why:** The API uses a **composite score** that encodes both XP and a timestamp. This is invisible to you — just know earlier = higher rank at equal XP.

#### 4.3 Subject Filtering

**Global leaderboards** rank all players across all subjects.

**Subject-specific leaderboards** rank players within a single subject (e.g., "Biology All-Time").

**Important:** A player can be ranked differently in different subjects:
- Overall all-time: Rank #5 (2,300 XP)
- Biology all-time: Rank #12 (1,500 XP)
- Chemistry all-time: Rank #3 (800 XP)

#### 4.4 Trends (Daily XP Change)

**Trend arrow** shows how much you earned in the last 24 hours relative to this leaderboard:

```
↑ +50   You earned 50 XP in the last 24h (moving up)
↓ -10   You earned 10 fewer XP than yesterday (falling)
→ 0     Same as 24h ago (holding steady)
```

**Note:** Backend doesn't provide trends; calculate locally:
- Fetch daily leaderboard twice (24h apart)
- Or use historical data if you cache it

#### 4.5 Unranked Players

If a player has **0 XP**, they don't appear on any leaderboard.

**Your rank view when unranked:**
- Rank: Last place + 1 (e.g., if 1,250 total players, your rank is 1,251)
- XP: 0
- xp_to_next: XP needed to beat the current last-place player
- Neighbors: Show bottom players + you

---

## UI/UX Requirements

### 1. Navigation

**Add "Leaderboards" tab** to main navigation (alongside Home, Learn, Profile, etc.)

**Optional:** Add leaderboard icon to each subject page (e.g., Biology → "View Biology Leaderboard")

### 2. Leaderboard Type Selector

```
Tab-based switcher:
[All-Time] [Daily] [Weekly]
```

**Behavior:**
- Selected tab is highlighted
- Click to switch (refetch data)
- Preserve scroll position when switching back
- Cache each type's data locally

### 3. Subject Filter

```
Dropdown: "All Subjects" ▼
```

**Behavior:**
- Shows list of subjects you have access to
- Selecting a subject filters leaderboard to that subject
- Shows "(Subject Name)" label above leaderboard
- "Clear filter" option to go back to global

**Data source:**
- Fetch from `GET /api/v1/progress/{subject_id}` to get subject list
- Or cache from your existing subject navigation

### 4. Player Entry Design

**Each row shows:**
- Rank (bold, left-aligned)
- Avatar (32×32 px, circular)
- Display name (truncate if too long)
- XP (bold, right-aligned)
- Trend (optional, small arrow)

**Highlight your entry:**
- Light blue or accent color background
- Bold text
- "You" label or different avatar border

**Make tappable:**
- Tap entry → Navigate to player profile (if profile page exists)
- Or show profile modal with more details

### 5. "Your Rank" Card

**Style options:**
1. **Card at top of leaderboard** (integrate with list)
2. **Separate dedicated page** (Leaderboards → Your Rank tab)
3. **Modal that slides up** (tap "Your Rank" button)

**Recommend:** Separate page/tab (cleaner, less crowded)

**Content:**
```
YOUR RANK IN [LEADERBOARD TYPE]

Rank: #5 out of 1,250 players
XP: 2,300

To beat next player:
📈 Earn 51 more XP to beat Leila Ahmed

[Complete a Lesson] [View Leaderboard] [Share]
```

### 6. Real-Time Updates

**When leaderboard changes:**
- After you complete a lesson → Your rank may change
- After someone else earns XP → Another player may move ahead

**Behavior:**
- Show refresh button or auto-refresh every 30 seconds
- Or refresh only when navigating TO leaderboard page
- Or use WebSocket (from Phase 24) to push updates

**Simplest approach:**
- Refresh every 30 seconds when tab is visible
- Or refresh on-demand when user pulls down to refresh

### 7. Empty States

**No leaderboard data:**
```
🏅 No Rankings Yet

Earn XP by completing lessons to appear on the leaderboard!

[Start Learning] [View Subjects]
```

**User unranked (0 XP):**
```
📊 You're Not Ranked Yet

Complete your first lesson to join the leaderboard.

[Choose a Subject]
```

---

## Data Synchronization

### 1. When to Fetch

**Fetch leaderboard data:**

1. **When opening leaderboards page**
   - Fetch top 10 (all-time) + your rank
   - Load subject filter options

2. **When switching leaderboard type** (all-time/daily/weekly)
   - Refetch top 10
   - Refetch your rank

3. **When filtering by subject**
   - Refetch top 10 for that subject
   - Refetch your rank for that subject

4. **On page refresh** (pull-to-refresh)
   - Refetch everything

5. **Periodically** (every 30 seconds)
   - Auto-refresh leaderboard if page is visible
   - Optional, depends on how real-time you want

### 2. Caching Strategy

**Cache each combination separately:**
```
cache_key = `leaderboard:{type}:{subject_id || 'global'}`
cache_key = `myrank:{type}:{subject_id || 'global'}`
```

**TTL (Time-To-Live):**
- 30 seconds for real-time feel
- Or 5 minutes for less API load
- Clear cache on "pull to refresh"

**When to invalidate:**
- After you earn XP (session completes)
- When user switches leaderboard type
- When user switches subject filter
- On manual refresh

### 3. Pagination / Load More

**Option 1: Offset-based (recommended)**
```
GET /api/v1/leaderboard/alltime?limit=10&offset=0
GET /api/v1/leaderboard/alltime?limit=10&offset=10
GET /api/v1/leaderboard/alltime?limit=10&offset=20
```

**Option 2: Infinite scroll**
- Auto-fetch next page when user scrolls to bottom
- Use offset parameter for pagination

**Option 3: "Load More" button**
- User taps button to fetch next 10
- Cleaner UX, less auto-fetch overhead

---

## Error Handling

### 1. API Errors

| Status | Meaning | Action |
|--------|---------|--------|
| 200 | Success | Display leaderboard |
| 400 | Bad request (invalid params) | Show error message, retry with defaults |
| 401 | Unauthorized (token expired) | Refresh token, retry request |
| 403 | Forbidden (no access to subject) | Show error, hide that subject from filter |
| 404 | Subject not found | Remove from subject list |
| 500 | Server error | Show "Leaderboard unavailable" message, retry in 30s |

### 2. Network Errors

```javascript
async function fetchLeaderboard(type, subjectId) {
  try {
    const response = await fetch(`/leaderboard/${type}?subject_id=${subjectId}`);
    if (!response.ok) {
      handleAPIError(response.status);
      return;
    }
    return await response.json();
  } catch (error) {
    // Network timeout, no internet, etc.
    showError("Cannot reach leaderboard. Check your connection.");
  }
}
```

### 3. Missing Data

**If avatar URL is null:**
- Use default avatar (initials, generic icon, or fallback image)

**If display_name is empty:**
- Show player_id instead
- Or "Anonymous Player"

**If xp_to_next is null:**
- You're #1 — show "You're #1! 🏆"

---

## Testing Requirements

### 1. Manual Test Checklist

**Prerequisites:**
- Test user account with some earned XP
- Access to multiple subjects
- At least 10 other users with XP

**Test Cases:**

- [ ] **Leaderboard displays top 10**
  - Open leaderboards → all-time
  - Verify top 10 players shown with correct rank, name, XP
  - Verify your row highlighted

- [ ] **Dense ranking works**
  - Find two tied players (same XP)
  - Verify they have same rank number (e.g., both #5)
  - Verify next player is #7, not #6

- [ ] **Subject filter works**
  - Select Biology subject filter
  - Verify leaderboard shows only Biology XP
  - Verify rankings different from global

- [ ] **Daily/weekly reset**
  - View daily leaderboard at morning
  - Verify XP is today's only (not cumulative)
  - Complete a lesson
  - Verify your XP increases

- [ ] **Your rank endpoint**
  - Tap "Your Rank"
  - Verify rank correct, neighbors shown
  - Verify xp_to_next displays correctly

- [ ] **Load more**
  - Scroll to bottom
  - Tap "Load More"
  - Verify next 10 players loaded

- [ ] **Unranked player**
  - Create test account with 0 XP
  - Verify not on leaderboard
  - Verify "You're not ranked" message

- [ ] **Trends display** (if implemented)
  - View all-time leaderboard
  - Compare with yesterday's data
  - Verify arrows (↑/↓/→) correct

### 2. Automated Tests

**Unit tests:**
- Rank calculation (dense ranking logic)
- Trend calculation (compare to 24h ago)
- XP to next calculation

**Integration tests:**
- Fetch leaderboard → display correctly
- Fetch my rank → highlight self
- Switch types (all-time → daily) → correct data shown

---

## Implementation Guidance

### Step-by-Step Plan

**Phase 1: Basic Layout (Days 1-2)**
1. Create Leaderboard tab in navigation
2. Build basic leaderboard UI (list of players)
3. Fetch from GET /leaderboard/{type}
4. Display rank, name, XP per entry
5. Highlight your row

**Phase 2: My Rank & Filtering (Days 3-4)**
6. Build "Your Rank" card
7. Implement leaderboard type selector (all-time/daily/weekly)
8. Implement subject filter dropdown
9. Fetch subject list from backend

**Phase 3: Polish & Features (Days 5-6)**
10. Add trends (↑/↓/→ arrows)
11. Implement pagination or infinite scroll
12. Add loading states & error messages
13. Cache leaderboard data locally
14. Auto-refresh every 30 seconds

**Phase 4: Testing & Edge Cases (Days 7-8)**
15. Test with real users
16. Handle edge cases (unranked, network errors)
17. Optimize performance
18. Add accessibility (screen reader support)

### Code Example (React Native / TypeScript)

```typescript
// leaderboard.service.ts
import axios from 'axios';

export type LeaderboardEntry = {
  rank: number;
  player_id: string;
  display_name: string;
  xp: number;
  avatar: string | null;
  is_me: boolean;
};

export type LeaderboardResponse = {
  leaderboard_type: 'alltime' | 'daily' | 'weekly';
  subject_id: string | null;
  entries: LeaderboardEntry[];
  total_players: number;
};

export type MyRankResponse = {
  rank: number;
  xp: number;
  xp_to_next: number | null;
  neighbors: LeaderboardEntry[];
  total_players: number;
};

class LeaderboardService {
  async getLeaderboard(
    type: 'alltime' | 'daily' | 'weekly',
    limit = 10,
    subjectId?: string
  ): Promise<LeaderboardResponse> {
    const params = { limit };
    if (subjectId) params.subject_id = subjectId;

    const response = await axios.get(
      `/api/v1/leaderboard/${type}`,
      { params }
    );
    return response.data;
  }

  async getMyRank(
    type: 'alltime' | 'daily' | 'weekly',
    subjectId?: string
  ): Promise<MyRankResponse> {
    const params = {};
    if (subjectId) params.subject_id = subjectId;

    const response = await axios.get(
      `/api/v1/leaderboard/${type}/me`,
      { params }
    );
    return response.data;
  }
}

export const leaderboardService = new LeaderboardService();
```

```typescript
// LeaderboardScreen.tsx
import React, { useEffect, useState } from 'react';
import { View, FlatList, Text, TouchableOpacity, RefreshControl } from 'react-native';
import { LeaderboardService, LeaderboardResponse } from './leaderboard.service';

type LeaderboardType = 'alltime' | 'daily' | 'weekly';

export function LeaderboardScreen() {
  const [lbType, setLbType] = useState<LeaderboardType>('alltime');
  const [subjectId, setSubjectId] = useState<string | null>(null);
  const [data, setData] = useState<LeaderboardResponse | null>(null);
  const [loading, setLoading] = useState(false);

  const fetchLeaderboard = async () => {
    setLoading(true);
    try {
      const response = await LeaderboardService.getLeaderboard(
        lbType,
        10,
        subjectId
      );
      setData(response);
    } catch (error) {
      console.error('Failed to fetch leaderboard:', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchLeaderboard();
  }, [lbType, subjectId]);

  return (
    <View style={{ flex: 1 }}>
      {/* Type selector */}
      <View style={{ flexDirection: 'row', paddingVertical: 12 }}>
        {(['alltime', 'daily', 'weekly'] as LeaderboardType[]).map(type => (
          <TouchableOpacity
            key={type}
            onPress={() => setLbType(type)}
            style={{
              paddingHorizontal: 12,
              paddingVertical: 8,
              borderBottomWidth: lbType === type ? 2 : 0,
              borderBottomColor: lbType === type ? '#007AFF' : 'transparent'
            }}
          >
            <Text style={{ fontWeight: lbType === type ? 'bold' : 'normal' }}>
              {type.charAt(0).toUpperCase() + type.slice(1)}
            </Text>
          </TouchableOpacity>
        ))}
      </View>

      {/* Leaderboard list */}
      <FlatList
        data={data?.entries}
        keyExtractor={item => item.player_id}
        refreshControl={
          <RefreshControl refreshing={loading} onRefresh={fetchLeaderboard} />
        }
        renderItem={({ item }) => (
          <View
            style={{
              flexDirection: 'row',
              padding: 12,
              backgroundColor: item.is_me ? '#E3F2FD' : 'white',
              borderBottomWidth: 1,
              borderBottomColor: '#E0E0E0'
            }}
          >
            <Text style={{ width: 40, fontWeight: 'bold' }}>{item.rank}</Text>
            <Text style={{ flex: 1, paddingLeft: 12 }}>
              {item.display_name}
            </Text>
            <Text style={{ fontWeight: 'bold' }}>{item.xp} XP</Text>
          </View>
        )}
      />
    </View>
  );
}
```

---

## Performance Targets

- Leaderboard fetch: < 500ms
- Leaderboard display (render): < 100ms
- Type switch: < 200ms (cached)
- Subject filter: < 300ms

---

## Analytics & Monitoring

**Track these events:**

| Event | When | Payload |
|-------|------|---------|
| `leaderboard_viewed` | Open leaderboard tab | type, subject_id |
| `leaderboard_type_switched` | Switch all-time/daily/weekly | from_type, to_type |
| `leaderboard_subject_filtered` | Select subject filter | subject_id |
| `leaderboard_entry_tapped` | Tap player (if navigates to profile) | player_id, rank |
| `my_rank_viewed` | Open "Your Rank" section | type, subject_id, rank |
| `load_more_tapped` | Load more players | type, current_count, new_count |

**Metrics:**
- Daily active users viewing leaderboards
- Average time spent on leaderboard page
- Completion rate (% of learners who view leaderboards)

---

## Security Considerations

**DO:**
- ✅ Use JWT token from secure storage (from login)
- ✅ Only show public profile info (display_name, avatar, XP)
- ✅ Don't expose player_id in URLs (could allow enumeration)

**DON'T:**
- ❌ Send personal data (email, phone, payment info)
- ❌ Cache API response with PII for long periods
- ❌ Share leaderboard data to external services

---

## API Reference

### GET /api/v1/leaderboard/{type}

```http
GET /api/v1/leaderboard/alltime?limit=10&subject_id=SUBJ-00028
Authorization: Bearer {JWT_ACCESS_TOKEN}

Response: 200 OK
{
  "leaderboard_type": "alltime",
  "subject_id": "SUBJ-00028",
  "entries": [
    {
      "rank": 1,
      "player_id": "player-001",
      "display_name": "Ahmed Ali",
      "xp": 2450,
      "avatar": "avatar-key-1",
      "is_me": false
    }
  ],
  "total_players": 1250
}
```

**Parameters:**
- `type` (path): `alltime` / `daily` / `weekly`
- `limit` (query): 1-100, default 10
- `subject_id` (query): optional subject filter

**Status Codes:**
- 200: Success
- 400: Invalid type or parameters
- 401: Not authenticated
- 500: Server error

---

### GET /api/v1/leaderboard/{type}/me

```http
GET /api/v1/leaderboard/alltime/me?subject_id=SUBJ-00028
Authorization: Bearer {JWT_ACCESS_TOKEN}

Response: 200 OK
{
  "rank": 5,
  "xp": 2300,
  "xp_to_next": 51,
  "neighbors": [
    {
      "rank": 3,
      "player_id": "player-003",
      "display_name": "Omar Hassan",
      "xp": 2400,
      "avatar": "avatar-3",
      "is_me": false
    },
    {
      "rank": 5,
      "player_id": "current-user",
      "display_name": "You",
      "xp": 2300,
      "avatar": "your-avatar",
      "is_me": true
    }
  ],
  "total_players": 1250
}
```

**Status Codes:**
- 200: Success (even if unranked, returns rank > total_players)
- 401: Not authenticated
- 500: Server error

---

## FAQ

**Q: What if I don't see my rank in the top 10?**
A: You're ranked outside the top 10. Tap "Your Rank" to see where you stand with neighbors for context.

**Q: How often do leaderboards update?**
A: The backend updates in real-time as players earn XP. Your app should refresh every 30 seconds or on-demand.

**Q: Can I compete only in my class?**
A: Yes! Use the subject filter to see leaderboards for your class/subject only.

**Q: Do all players have the same daily/weekly leaderboard?**
A: Yes, daily reset is at midnight Asia/Amman time for everyone. Weekly resets on Friday at midnight.

**Q: What if two players have the same XP?**
A: They share the same rank. The one who reached that XP first ranks higher (earlier achiever wins).

**Q: Can I mute leaderboard notifications?**
A: Currently, leaderboards are opt-in (you navigate to them). No notifications are pushed automatically.

**Q: Will my profile picture show?**
A: Yes, from your profile avatar. If you haven't uploaded one, a default avatar is shown.

---

## Success Criteria

**This feature is complete when:**

✅ Leaderboard fetches from backend API successfully
✅ Top 10 players display with correct rank, name, avatar, XP
✅ Dense ranking works (tied players share rank)
✅ Daily, weekly, all-time tabs switch correctly
✅ Subject filter reduces leaderboard to that subject
✅ "Your Rank" shows personal rank with neighbors
✅ "XP to next" motivates players to earn more
✅ Load more / pagination works
✅ Refresh is sub-500ms
✅ Error handling for network/API errors
✅ Manual testing passes all cases

---

**Backend Team Contacts:**
- API questions: ask me