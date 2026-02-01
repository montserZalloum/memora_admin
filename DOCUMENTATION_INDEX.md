# Memora Admin DocType Analysis - Documentation Index

## Generated Documents

### 1. DOCTYPE_FIELD_REFERENCE.md
**Comprehensive field reference guide (18 KB)**

Contains detailed breakdown of all 28 doctypes organized by category:
- **Academic Structure Layer** (6 doctypes) - Curriculum hierarchy
- **Player & Profile Layer** (3 doctypes) - Player management
- **Content & Lessons Layer** (3 doctypes) - Lesson content
- **Learning & Progress Layer** (4 doctypes) - Progress tracking and FSRS
- **Gamification Layer** (4 doctypes) - Badges and rewards
- **Platform & Admin Layer** (8 doctypes) - System configuration

Each doctype includes:
- Field order with descriptions
- Field types and requirements
- Links and relationships
- Purpose and use case
- Architectural patterns

### 2. doctype_fields_quick_reference.csv
**Quick lookup table (3.4 KB)**

Tabular view of all doctypes with:
- DocType name
- Field count
- First 8 fields (preview)
- Table vs Master distinction
- Purpose

Use for: Quick scanning and field count reference

### 3. CHANGES_SUMMARY.txt
**Implementation and architecture guide (10 KB)**

High-level overview including:
- Quick facts (28 doctypes, ~210 fields)
- Breakdown by category
- Key architectural features
- Critical fields explained
- Field naming conventions
- Language support details
- Next implementation steps
- Testing checklist
- Dependencies and integrations

---

## Quick Navigation

### By Purpose

**Content Management**
- Memora Academic Plan
- Memora Subject
- Memora Track
- Memora Unit
- Memora Topic
- Memora Lesson

**Player Management**
- Memora Player Profile (user data)
- Memora Player Wallet (gamification stats)
- Memora Player Device (multi-device support)

**Learning & Progress**
- Memora Memory State (FSRS spaced repetition)
- Memora Structure Progress (completion tracking)
- Memora Interaction Log (event tracking)
- Memora Analytics Aggregate (metrics)

**Gamification**
- Memora Achievement (badges)
- Memora Grade (academic levels)
- Memora Major (specializations)
- Memora Season (calendar periods)

**Admin & Config**
- Memora Settings (global configuration)
- Memora Product Grant (monetization)
- Memora Build Queue (content generation)
- Memora Sync Log (sync tracking)
- Memora Subscription Transaction (payments)
- Memora Plan Overrider (access control)

**Content Design**
- Memora Lesson Stage (exercises/questions)
- Memora Lesson Stage Settings (stage templates)

---

## Key Statistics

- **Total Doctypes:** 28
- **Total Fields:** ~210
- **Languages:** Arabic, English
- **Master Doctypes:** 20
- **Child Tables:** 5
- **Singleton:** 1 (Settings)

### Field Distribution by Category
| Category | DocTypes | Fields |
|----------|----------|--------|
| Academic Structure | 6 | 61 |
| Player & Profile | 3 | 25 |
| Content & Lessons | 3 | 16 |
| Learning & Progress | 4 | 24 |
| Gamification | 4 | 18 |
| Platform & Admin | 8 | 66 |
| **TOTAL** | **28** | **210** |

---

## Architecture Overview

### Content Hierarchy
```
Academic Plan
  ├── Grade + Major + Season
  └── Plan Subjects
  
Subject
  ├── Track
  │   ├── Unit
  │   │   └── Topic
  │   │       └── Lesson
  │   │           └── Stage (Lesson Stage)
```

### Player Ecosystem
```
Player Profile
  ├── Player Wallet (XP, streaks)
  ├── Player Devices (multi-device auth)
  └── Related Tables:
      ├── Interaction Logs
      ├── Memory States (FSRS)
      └── Structure Progress
```

### Key Features

1. **FSRS Spaced Repetition**
   - Stability and difficulty tracking
   - Configurable algorithm weights
   - Next review scheduling

2. **Gamification System**
   - Base XP rewards
   - Heart/attempt system
   - Streak tracking
   - Achievement badges

3. **Multi-Device Support**
   - Device ID tracking
   - Push notification tokens
   - Device limits per player

4. **CDN Integration**
   - AWS S3 / Cloudflare R2 support
   - Local fallback mode
   - JSON versioning
   - Content hashing

5. **Monetization**
   - Product-to-curriculum mapping
   - Subscription transactions
   - Payment method variations
   - ERPNext integration

---

## Implementation Roadmap

### Phase 1: Database (Week 1)
- [ ] Create all 28 tables
- [ ] Set up foreign keys
- [ ] Add indexes
- [ ] Configure FSRS weights

### Phase 2: APIs (Weeks 2-4)
- [ ] REST endpoints
- [ ] GraphQL schema
- [ ] Batch sync endpoints
- [ ] FSRS algorithm

### Phase 3: Integration (Months 2-3)
- [ ] ERPNext User sync
- [ ] CDN provider APIs
- [ ] Push notifications
- [ ] Build queue processor

### Phase 4: Enhancement (Months 4+)
- [ ] Advanced analytics
- [ ] Performance optimization
- [ ] Additional languages
- [ ] Mobile app features

---

## Common Use Cases

### Adding a New Lesson
1. Create **Memora Lesson** record
   - Link to **Topic**
   - Set base_xp, max_hearts
   - Mark is_reviewable for FSRS

2. Create **Memora Lesson Stages**
   - Add exercises/questions
   - Link to stage type
   - Set config_json payload

3. (Optional) Trigger **Memora Build Queue**
   - For content generation
   - Async processing

### Tracking Player Progress
1. **Memora Interaction Log** - Event tracking
2. **Memora Memory State** - FSRS tracking
3. **Memora Structure Progress** - Completion bitset
4. **Memora Player Wallet** - XP accumulation

### Managing Access
1. Create **Memora Product Grant**
   - Link to plan
   - Add grant components
   - Specify content access

2. (Optional) Create **Memora Plan Overrider**
   - For custom access rules
   - Grant/revoke specific content

### Configuring System
1. Modify **Memora Settings**
   - CDN configuration
   - Gamification parameters
   - FSRS weights
   - Security settings

---

## Field Types Used

| Type | Count | Examples |
|------|-------|----------|
| Text / Data | ~40 | titles, IDs, descriptions |
| Link | ~50 | Foreign keys to other doctypes |
| Integer | ~30 | Counts, XP, thresholds |
| Checkbox | ~25 | Boolean flags |
| Select | ~20 | Enum values |
| DateTime / Date | ~15 | Timestamps |
| Float | ~10 | Decimals, percentages |
| Code (JSON) | ~8 | Config, payload |
| Section/Column Break | ~15 | UI organization |
| Table | 5 | Child records |
| Attach Image | ~3 | Images, avatars |

---

## Referenced External Systems

- **Frappe Framework** - Base framework
- **ERPNext** - User, Item, Invoice integration
- **Redis** - Caching and sync
- **CDN Providers** - AWS S3 / Cloudflare R2
- **Push Services** - FCM / APNS
- **MariaDB/MySQL** - Database backend

---

## Language Support

### Arabic (Primary)
- RTL (right-to-left) layout
- Full field descriptions in Arabic
- Examples:
  - plan_subjects: "قائمة المواد في هذه الخطة"
  - base_xp: "نقاط الخبرة المكتسبة"

### English (Secondary)
- LTR (left-to-right) layout
- Complete field descriptions

---

## Document Locations

All files available at: `/home/corex/aurevia-bench/apps/memora_admin/`

1. **DOCTYPE_FIELD_REFERENCE.md** - Comprehensive guide
2. **doctype_fields_quick_reference.csv** - Quick lookup
3. **CHANGES_SUMMARY.txt** - Implementation guide

---

## Version Info

- **Generated:** 2026-02-01
- **Memora Admin Version:** Latest (from git commit)
- **Total Doctypes:** 28
- **Total Fields:** ~210
- **Documentation Version:** 1.0

---

## Support & Further Reading

For implementation details, refer to:
- Individual doctype JSON files in `/memora_admin/doctype/`
- DOCTYPES_DOCUMENTATION.md (existing)
- README.md (project overview)

For API documentation, refer to:
- Frappe REST API documentation
- GraphQL schema (to be generated)

For architecture questions, consult:
- CHANGES_SUMMARY.txt
- Architectural patterns section in DOCTYPE_FIELD_REFERENCE.md

