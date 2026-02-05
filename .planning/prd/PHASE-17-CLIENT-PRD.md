# Client PRD: Progress API Optimization (Phase 17)

**Version:** 1.0
**Date:** 2026-02-05
**Backend Version:** v1.3 (Phase 17 completed)

## Overview

Phase 17 introduces optimized progress tracking APIs to support large subjects (50K+ lessons) with sub-10ms response times and progressive streaming for responsive UX. The client can now choose between:

1. **REST Endpoint** - Traditional request/response with O(1) cached stats
2. **SSE Streaming Endpoint** - Progressive data delivery for large subjects

This PRD documents the API contracts, integration patterns, and recommended UI/UX approaches for mobile and web clients.

---

## Business Context

### Problem Statement

Large educational subjects (e.g., comprehensive math curricula) can contain 10,000-50,000+ individual lessons. Previous progress APIs required O(N) operations to compute completion statistics, resulting in:

- Slow response times (100ms-500ms for large subjects)
- UI freezing while waiting for full data load
- Poor perceived performance for students

### Solution

Backend Phase 17 implements:

- **Pre-computed stats caching** - Redis hash stores completion counts at all hierarchy levels
- **Atomic updates** - Stats increment O(1) on each lesson completion
- **SSE streaming** - Progressive delivery: subject summary first (10ms), then tracks incrementally

### Target Metrics

| Metric | Target | Notes |
|--------|--------|-------|
| First meaningful paint | <50ms | Subject summary available |
| Full data load | <200ms | All tracks streamed |
| REST response time | <10ms | Cached stats |
| SSE first chunk | <10ms | Subject event |

---

## API Reference

### Authentication

All endpoints require JWT authentication via `Authorization: Bearer <token>` header.

### Base URL

```
Production: https://api.memora.app/api/v1
Development: http://localhost:8001/api/v1
```

---

## Endpoint 1: REST Progress (Existing, Optimized)

### `GET /progress/{subject_id}`

Returns full progress breakdown for a subject with cached statistics.

**When to use:** Simple integration, single subjects, offline-first apps that cache responses.

#### Request

```http
GET /api/v1/progress/MATH-G5 HTTP/1.1
Authorization: Bearer <access_token>
```

#### Response

```json
{
  "subject_id": "MATH-G5",
  "completed": 1250,
  "total": 5000,
  "percentage": 25.0,
  "tracks": [
    {
      "track_id": "TRK-ALGEBRA",
      "completed": 500,
      "total": 2000,
      "percentage": 25.0,
      "unlocked": true,
      "units": [
        {
          "unit_id": "UNIT-ALGEBRA-001",
          "completed": 100,
          "total": 400,
          "percentage": 25.0,
          "unlocked": true,
          "topics": [
            {
              "topic_id": "TOPIC-EQ-001",
              "completed": 25,
              "total": 100,
              "percentage": 25.0,
              "unlocked": true
            }
          ]
        }
      ]
    }
  ]
}
```

#### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| `subject_id` | string | Subject identifier |
| `completed` | int | Total lessons completed in subject |
| `total` | int | Total lessons in subject |
| `percentage` | float | Completion percentage (0.0-100.0) |
| `tracks[]` | array | Track progress with nested units/topics |
| `tracks[].unlocked` | bool | Whether track is accessible |
| `units[].unlocked` | bool | Whether unit is accessible |
| `topics[].unlocked` | bool | Whether topic is accessible |

#### Error Responses

| Status | Code | Description |
|--------|------|-------------|
| 401 | `UNAUTHORIZED` | Missing or invalid token |
| 403 | `NO_ACCESS` | No subscription/grant for subject |
| 404 | `SUBJECT_NOT_FOUND` | Subject ID doesn't exist |

---

## Endpoint 2: SSE Streaming Progress (New)

### `GET /progress/stream/{subject_id}`

Streams progress data via Server-Sent Events for progressive UI rendering.

**When to use:** Large subjects, instant perceived performance, progressive UI updates.

#### Request

```http
GET /api/v1/progress/stream/MATH-G5 HTTP/1.1
Authorization: Bearer <access_token>
Accept: text/event-stream
```

#### Event Types

The stream emits three event types in order:

##### 1. `subject` Event (First, within 10ms)

Subject-level summary for immediate header rendering.

```
event: subject
data: {"subject_id":"MATH-G5","completed":1250,"total":5000,"percentage":25.0}
```

**Payload:**

| Field | Type | Description |
|-------|------|-------------|
| `subject_id` | string | Subject identifier |
| `completed` | int | Total lessons completed |
| `total` | int | Total lessons in subject |
| `percentage` | float | Completion percentage |

##### 2. `track` Events (Progressive)

One event per track, streamed incrementally.

```
event: track
data: {"track_id":"TRK-ALGEBRA","completed":500,"total":2000,"percentage":25.0,"units":[...]}
```

**Payload:**

| Field | Type | Description |
|-------|------|-------------|
| `track_id` | string | Track identifier |
| `completed` | int | Lessons completed in track |
| `total` | int | Total lessons in track |
| `percentage` | float | Track completion percentage |
| `units[]` | array | Unit progress with nested topics |

**Unit structure:**

```json
{
  "unit_id": "UNIT-ALGEBRA-001",
  "completed": 100,
  "total": 400,
  "percentage": 25.0,
  "topics": [
    {
      "topic_id": "TOPIC-EQ-001",
      "completed": 25,
      "total": 100,
      "percentage": 25.0
    }
  ]
}
```

##### 3. `complete` Event (Final)

Signals end of stream. No payload.

```
event: complete
data:
```

#### Error Handling

SSE errors return HTTP error before stream starts:

| Status | Code | Description |
|--------|------|-------------|
| 401 | `UNAUTHORIZED` | Missing or invalid token |
| 403 | `NO_ACCESS` | No subscription/grant for subject |
| 404 | `SUBJECT_NOT_FOUND` | Subject ID doesn't exist |

Connection drops mid-stream should trigger reconnection with full re-fetch (no resume support).

---

## TypeScript Type Definitions

```typescript
// REST Response Types
interface SubjectProgress {
  subject_id: string;
  completed: number;
  total: number;
  percentage: number;
  tracks: TrackProgress[];
}

interface TrackProgress {
  track_id: string;
  completed: number;
  total: number;
  percentage: number;
  unlocked: boolean;
  units: UnitProgress[];
}

interface UnitProgress {
  unit_id: string;
  completed: number;
  total: number;
  percentage: number;
  unlocked: boolean;
  topics: TopicProgress[];
}

interface TopicProgress {
  topic_id: string;
  completed: number;
  total: number;
  percentage: number;
  unlocked: boolean;
}

// SSE Event Types
interface SSESubjectEvent {
  subject_id: string;
  completed: number;
  total: number;
  percentage: number;
}

interface SSETrackEvent {
  track_id: string;
  completed: number;
  total: number;
  percentage: number;
  units: SSEUnitData[];
}

interface SSEUnitData {
  unit_id: string;
  completed: number;
  total: number;
  percentage: number;
  topics: SSETopicData[];
}

interface SSETopicData {
  topic_id: string;
  completed: number;
  total: number;
  percentage: number;
}

// SSE Event Discriminated Union
type ProgressEvent =
  | { event: 'subject'; data: SSESubjectEvent }
  | { event: 'track'; data: SSETrackEvent }
  | { event: 'complete'; data: null };
```

---

## Client Implementation Patterns

### Pattern 1: React Native / Web SSE Client

```typescript
class ProgressStreamClient {
  private eventSource: EventSource | null = null;

  streamProgress(
    subjectId: string,
    token: string,
    callbacks: {
      onSubject: (data: SSESubjectEvent) => void;
      onTrack: (data: SSETrackEvent) => void;
      onComplete: () => void;
      onError: (error: Error) => void;
    }
  ): () => void {
    const url = `${API_BASE}/progress/stream/${subjectId}`;

    // Note: Native EventSource doesn't support custom headers
    // Use fetch-event-source or polyfill for mobile
    this.eventSource = new EventSource(url, {
      headers: { Authorization: `Bearer ${token}` },
    });

    this.eventSource.addEventListener('subject', (e) => {
      callbacks.onSubject(JSON.parse(e.data));
    });

    this.eventSource.addEventListener('track', (e) => {
      callbacks.onTrack(JSON.parse(e.data));
    });

    this.eventSource.addEventListener('complete', () => {
      callbacks.onComplete();
      this.eventSource?.close();
    });

    this.eventSource.onerror = (e) => {
      callbacks.onError(new Error('Stream connection failed'));
      this.eventSource?.close();
    };

    // Return cleanup function
    return () => this.eventSource?.close();
  }
}
```

### Pattern 2: React Hook with Progressive State

```typescript
import { useState, useEffect, useCallback } from 'react';

interface UseProgressStreamResult {
  subject: SSESubjectEvent | null;
  tracks: Map<string, SSETrackEvent>;
  isLoading: boolean;
  isComplete: boolean;
  error: Error | null;
}

function useProgressStream(
  subjectId: string,
  token: string
): UseProgressStreamResult {
  const [subject, setSubject] = useState<SSESubjectEvent | null>(null);
  const [tracks, setTracks] = useState<Map<string, SSETrackEvent>>(new Map());
  const [isLoading, setIsLoading] = useState(true);
  const [isComplete, setIsComplete] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    setIsLoading(true);
    setIsComplete(false);
    setError(null);
    setSubject(null);
    setTracks(new Map());

    const client = new ProgressStreamClient();
    const cleanup = client.streamProgress(subjectId, token, {
      onSubject: (data) => {
        setSubject(data);
        setIsLoading(false); // First paint ready
      },
      onTrack: (data) => {
        setTracks((prev) => new Map(prev).set(data.track_id, data));
      },
      onComplete: () => {
        setIsComplete(true);
      },
      onError: (err) => {
        setError(err);
        setIsLoading(false);
      },
    });

    return cleanup;
  }, [subjectId, token]);

  return { subject, tracks, isLoading, isComplete, error };
}
```

### Pattern 3: Flutter Stream Client

```dart
import 'dart:async';
import 'dart:convert';
import 'package:http/http.dart' as http;

class ProgressStreamClient {
  StreamController<ProgressEvent>? _controller;
  http.Client? _client;

  Stream<ProgressEvent> streamProgress(String subjectId, String token) {
    _controller = StreamController<ProgressEvent>();
    _client = http.Client();

    _connect(subjectId, token);

    return _controller!.stream;
  }

  Future<void> _connect(String subjectId, String token) async {
    try {
      final request = http.Request(
        'GET',
        Uri.parse('$apiBase/progress/stream/$subjectId'),
      );
      request.headers['Authorization'] = 'Bearer $token';
      request.headers['Accept'] = 'text/event-stream';

      final response = await _client!.send(request);

      if (response.statusCode != 200) {
        _controller!.addError(
          ProgressError(response.statusCode, 'Failed to connect'),
        );
        return;
      }

      await for (final chunk in response.stream.transform(utf8.decoder)) {
        _parseSSE(chunk);
      }
    } catch (e) {
      _controller!.addError(e);
    }
  }

  void _parseSSE(String chunk) {
    final lines = chunk.split('\n');
    String? eventType;
    String? data;

    for (final line in lines) {
      if (line.startsWith('event: ')) {
        eventType = line.substring(7);
      } else if (line.startsWith('data: ')) {
        data = line.substring(6);
      } else if (line.isEmpty && eventType != null) {
        _emitEvent(eventType, data ?? '');
        eventType = null;
        data = null;
      }
    }
  }

  void _emitEvent(String eventType, String data) {
    switch (eventType) {
      case 'subject':
        _controller!.add(SubjectEvent(jsonDecode(data)));
        break;
      case 'track':
        _controller!.add(TrackEvent(jsonDecode(data)));
        break;
      case 'complete':
        _controller!.add(CompleteEvent());
        _controller!.close();
        break;
    }
  }

  void dispose() {
    _client?.close();
    _controller?.close();
  }
}
```

---

## UI/UX Recommendations

### Progressive Loading Pattern

```
┌─────────────────────────────────────┐
│ Math Grade 5                        │
│ ████████░░░░░░░░░░░░  25%           │ ← Subject header (10ms)
├─────────────────────────────────────┤
│ ▼ Algebra         ████░░░░  25%     │ ← Track 1 (50ms)
│   ▼ Equations     ██░░░░░░  15%     │
│   ▼ Functions     ████░░░░  30%     │
├─────────────────────────────────────┤
│ ▼ Geometry        ░░░░░░░░  Loading │ ← Track 2 (100ms)
│   ⋮                                  │
├─────────────────────────────────────┤
│ ▼ Statistics      ░░░░░░░░  Loading │ ← Track 3 (150ms)
│   ⋮                                  │
└─────────────────────────────────────┘
```

### Recommended UI States

1. **Initial** - Show skeleton/placeholder for subject header
2. **Subject Received** - Render subject header with percentage
3. **Tracks Streaming** - Add tracks as they arrive, show loading indicator for remaining
4. **Complete** - Remove loading indicators, enable interactions

### Skeleton Loading Example

```tsx
function SubjectProgressView({ subjectId }: Props) {
  const { subject, tracks, isLoading, isComplete } = useProgressStream(
    subjectId,
    token
  );

  if (isLoading && !subject) {
    return <SubjectSkeleton />;
  }

  return (
    <View>
      {/* Subject header renders immediately after first event */}
      <SubjectHeader data={subject} />

      {/* Tracks render progressively */}
      {Array.from(tracks.values()).map((track) => (
        <TrackCard key={track.track_id} data={track} />
      ))}

      {/* Show loading indicator while more tracks expected */}
      {!isComplete && <TrackLoadingIndicator />}
    </View>
  );
}
```

### Offline/Caching Strategy

```typescript
// Cache REST response for offline access
async function fetchProgressWithCache(subjectId: string): Promise<SubjectProgress> {
  const cacheKey = `progress:${subjectId}`;
  const cached = await AsyncStorage.getItem(cacheKey);

  // Return cached data immediately for offline
  if (!navigator.onLine && cached) {
    return JSON.parse(cached);
  }

  // Fetch fresh data
  const response = await fetch(`${API_BASE}/progress/${subjectId}`, {
    headers: { Authorization: `Bearer ${token}` },
  });

  const data = await response.json();

  // Update cache
  await AsyncStorage.setItem(cacheKey, JSON.stringify(data));

  return data;
}
```

---

## Decision Matrix: REST vs SSE

| Factor | REST | SSE |
|--------|------|-----|
| **First paint time** | 10ms (full response) | <10ms (subject event) |
| **Total load time** | 10ms | ~100-200ms (streaming) |
| **Implementation complexity** | Low | Medium |
| **Browser support** | Universal | Modern browsers |
| **Mobile native support** | Universal | Requires polyfill |
| **Offline caching** | Easy | Complex |
| **Recommended for** | Small subjects, offline-first | Large subjects, perceived performance |

### Recommendation

- **Use REST** for subjects with <1000 lessons or offline-first apps
- **Use SSE** for subjects with >1000 lessons where perceived performance matters
- **Hybrid approach**: Check lesson count from cached hierarchy, choose endpoint dynamically

---

## Error Handling

### HTTP Errors (Both Endpoints)

```typescript
async function handleProgressError(response: Response): Promise<never> {
  const error = await response.json();

  switch (error.detail?.code) {
    case 'UNAUTHORIZED':
      // Token expired - trigger refresh flow
      await refreshToken();
      throw new RetryableError('Token refreshed, retry request');

    case 'NO_ACCESS':
      // Show subscription prompt
      throw new AccessDeniedError('Subscription required');

    case 'SUBJECT_NOT_FOUND':
      // Invalid subject - likely stale cache
      throw new NotFoundError('Subject not found');

    default:
      throw new UnknownError(error.message);
  }
}
```

### SSE Connection Errors

```typescript
eventSource.onerror = (event) => {
  // Connection lost mid-stream
  if (eventSource.readyState === EventSource.CLOSED) {
    // Reconnect with exponential backoff
    setTimeout(() => reconnect(), backoffMs);
  }
};
```

---

## Testing Checklist

### Functional Tests

- [ ] REST endpoint returns correct progress data
- [ ] SSE stream emits subject event first
- [ ] SSE stream emits all track events
- [ ] SSE stream emits complete event at end
- [ ] Unauthorized requests return 401
- [ ] No-access requests return 403
- [ ] Invalid subject returns 404

### Performance Tests

- [ ] REST response < 10ms for cached subject
- [ ] SSE first event < 10ms
- [ ] SSE full stream < 200ms for 10-track subject

### Edge Cases

- [ ] Empty subject (0 lessons) handles gracefully
- [ ] Client disconnect mid-SSE stream (no server errors)
- [ ] Network timeout during SSE (reconnection works)
- [ ] Token refresh during SSE (handles gracefully)

---

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2026-02-05 | Initial PRD based on Phase 17 implementation |

---

## Appendix: Full Example Response

### REST Full Response

```json
{
  "subject_id": "MATH-G5",
  "completed": 1250,
  "total": 5000,
  "percentage": 25.0,
  "tracks": [
    {
      "track_id": "TRK-ALGEBRA",
      "completed": 500,
      "total": 2000,
      "percentage": 25.0,
      "unlocked": true,
      "units": [
        {
          "unit_id": "UNIT-ALG-001",
          "completed": 100,
          "total": 400,
          "percentage": 25.0,
          "unlocked": true,
          "topics": [
            {
              "topic_id": "TOPIC-EQ-001",
              "completed": 25,
              "total": 100,
              "percentage": 25.0,
              "unlocked": true
            },
            {
              "topic_id": "TOPIC-EQ-002",
              "completed": 25,
              "total": 100,
              "percentage": 25.0,
              "unlocked": true
            }
          ]
        }
      ]
    },
    {
      "track_id": "TRK-GEOMETRY",
      "completed": 750,
      "total": 3000,
      "percentage": 25.0,
      "unlocked": true,
      "units": [
        {
          "unit_id": "UNIT-GEO-001",
          "completed": 150,
          "total": 600,
          "percentage": 25.0,
          "unlocked": true,
          "topics": [
            {
              "topic_id": "TOPIC-SHAPES-001",
              "completed": 50,
              "total": 200,
              "percentage": 25.0,
              "unlocked": true
            }
          ]
        }
      ]
    }
  ]
}
```

### SSE Full Stream

```
event: subject
data: {"subject_id":"MATH-G5","completed":1250,"total":5000,"percentage":25.0}

event: track
data: {"track_id":"TRK-ALGEBRA","completed":500,"total":2000,"percentage":25.0,"units":[{"unit_id":"UNIT-ALG-001","completed":100,"total":400,"percentage":25.0,"topics":[{"topic_id":"TOPIC-EQ-001","completed":25,"total":100,"percentage":25.0}]}]}

event: track
data: {"track_id":"TRK-GEOMETRY","completed":750,"total":3000,"percentage":25.0,"units":[{"unit_id":"UNIT-GEO-001","completed":150,"total":600,"percentage":25.0,"topics":[{"topic_id":"TOPIC-SHAPES-001","completed":50,"total":200,"percentage":25.0}]}]}

event: complete
data:
```
