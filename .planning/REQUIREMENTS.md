# Requirements: Memora Platform v2.0

**Defined:** 2026-02-12
**Core Value:** Students can track their learning progress and earn rewards (XP, streaks) with instant feedback and sub-second response times, even at 100K concurrent users.

## v2.0 Requirements

Requirements for mobile-first player authentication migration. Each maps to roadmap phases.

### Schema & DocType

- [x] **SCHEMA-01**: Player Profile autoname changed from `field:user` to `PLAYER-.#####.`
- [x] **SCHEMA-02**: `mobile` field added (Data, unique, required) as primary player identifier
- [x] **SCHEMA-03**: `password` field added (Password fieldtype, hidden) with `flags.ignore_save_passwords` bypass
- [x] **SCHEMA-04**: Phone normalization in `validate()` — strips non-digits, validates 9-15 digit length
- [x] **SCHEMA-05**: Password hashing via `update_password()` in `after_insert`/`on_update` (PBKDF2-SHA256, not Fernet)
- [x] **SCHEMA-06**: `user` field kept temporarily (nullable, not required) for backward compatibility

### Player Authentication

- [x] **AUTH-01**: Player can log in with phone number + password via `POST /auth/player/login`
- [x] **AUTH-02**: Login calls Frappe whitelisted API (`verify_player_password`) — single HTTP call, no Frappe session
- [x] **AUTH-03**: Login returns enriched response (tokens + display_name, avatar, gender, XP)
- [x] **AUTH-04**: Admin can log in with email + password via `POST /auth/admin/login` (existing Frappe User flow)
- [x] **AUTH-05**: Token refresh works for both player and admin tokens via `POST /auth/refresh`

### Registration

- [x] **REG-01**: Player can self-register via `POST /auth/player/register` (sends OTP)
- [x] **REG-02**: Player verifies OTP via `POST /auth/player/register/verify` (creates account)
- [x] **REG-03**: OTP stored in Redis with 5-minute TTL, static "1111" stub with pluggable provider interface
- [x] **REG-04**: Phone reservation during OTP window prevents duplicate registration attempts
- [x] **REG-05**: OTP resend with 60-second cooldown via `POST /auth/player/register/resend`
- [x] **REG-06**: Player Profile, wallet, and initial Redis state created on successful registration

### Password Reset

- [x] **RESET-01**: Player requests OTP via `POST /auth/player/password-reset/request`
- [x] **RESET-02**: Player verifies OTP and receives temp token via `POST /auth/player/password-reset/verify`
- [x] **RESET-03**: Player sets new password with temp token via `POST /auth/player/password-reset/confirm`
- [x] **RESET-04**: Temp token is cryptographically random (secrets.token_urlsafe(32)), bound to phone, 10-min TTL, single-use
- [x] **RESET-05**: All existing sessions invalidated on password change (OWASP requirement)
- [x] **RESET-06**: Admin can reset player password from Frappe Desk (triggers session invalidation)

### Security

- [x] **SEC-01**: OTP send rate limiting — 3 per phone per 10 min, 10 per IP per 10 min
- [x] **SEC-02**: OTP verification attempt limiting — max 3 incorrect attempts, then invalidate OTP
- [x] **SEC-03**: Password policy — minimum 8 characters enforced in validate() and FastAPI
- [x] **SEC-04**: Pluggable OTP provider interface (OTPProvider protocol) with StaticOTPProvider default
- [x] **SEC-05**: 60-second cooldown between OTP resends to same number

### Code Migration

- [x] **MIGR-01**: JWT `sub` = Player Profile docname (PLAYER-00001), `mobile` claim for phone number
- [x] **MIGR-02**: `create_access_token()` updated — `email` optional, `mobile` added
- [ ] **MIGR-03**: Event handlers updated — `doc.user` replaced with `doc.name` in access_sync, device_sync, plan_change_sync, profile_sync
- [ ] **MIGR-04**: Frappe APIs updated — purchase.py, profile.py, subscriptions.py, devices.py remove `{"user": player_id}` lookups
- [x] **MIGR-05**: Frappe whitelisted auth API created (`memora_admin/api/auth.py`) with verify_player_password, register_player, set_player_password
- [ ] **MIGR-06**: Fix pre-existing bug: plan_change_sync.py and profile_sync.py use wrong Redis client (frappe.cache() instead of get_fastapi_redis())
- [x] **MIGR-07**: Old single `/auth/login` endpoint removed, replaced by separate player/admin endpoints

## Future Requirements

Deferred to later milestones. Tracked but not in current roadmap.

### SMS/WhatsApp Integration (v2.1+)

- **SMS-01**: Real SMS OTP delivery via Twilio/Unifonic/WhatsApp Business API
- **SMS-02**: SMS delivery monitoring and analytics
- **SMS-03**: Phone number change flow (player-initiated via OTP on new number)

### Enhanced Security (v2.1+)

- **ESEC-01**: Common password list checking (top 10K passwords blocked)
- **ESEC-02**: Device attestation (SafetyNet/App Attest) for bot prevention

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| Real SMS/WhatsApp OTP delivery | Static "1111" stub for now; pluggable interface ready for future |
| Phone number change flow | Admin-only via Frappe Desk; PLAYER-.#####. autoname avoids rename |
| Existing player data migration | Fresh start; handle manually if needed later |
| CAPTCHA/reCAPTCHA | Rate limiting sufficient; poor mobile app UX |
| TOTP/authenticator app | Overkill for student audience; SMS OTP appropriate |
| Password expiry/history | NIST 800-63B recommends against periodic expiry |
| Email fallback auth for players | Clean break: players=phone only, admins=email only |
| Country code auto-detection | Known audience (Saudi/Jordan); player enters digits directly |
| "Remember me" toggle | 30-day refresh token already provides this behavior |
| Biometric login (server-side) | Client-side concern; server contract unchanged |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| SCHEMA-01 | Phase 29 | Complete |
| SCHEMA-02 | Phase 29 | Complete |
| SCHEMA-03 | Phase 29 | Complete |
| SCHEMA-04 | Phase 29 | Complete |
| SCHEMA-05 | Phase 29 | Complete |
| SCHEMA-06 | Phase 29 | Complete |
| AUTH-01 | Phase 31 | Complete |
| AUTH-02 | Phase 31 | Complete |
| AUTH-03 | Phase 31 | Complete |
| AUTH-04 | Phase 31 | Complete |
| AUTH-05 | Phase 31 | Complete |
| REG-01 | Phase 31 | Complete |
| REG-02 | Phase 31 | Complete |
| REG-03 | Phase 31 | Complete |
| REG-04 | Phase 31 | Complete |
| REG-05 | Phase 31 | Complete |
| REG-06 | Phase 31 | Complete |
| RESET-01 | Phase 31 | Complete |
| RESET-02 | Phase 31 | Complete |
| RESET-03 | Phase 31 | Complete |
| RESET-04 | Phase 31 | Complete |
| RESET-05 | Phase 31 | Complete |
| RESET-06 | Phase 30 | Complete |
| SEC-01 | Phase 31 | Complete |
| SEC-02 | Phase 31 | Complete |
| SEC-03 | Phase 29 | Complete |
| SEC-04 | Phase 31 | Complete |
| SEC-05 | Phase 31 | Complete |
| MIGR-01 | Phase 31 | Complete |
| MIGR-02 | Phase 31 | Complete |
| MIGR-03 | Phase 32 | Pending |
| MIGR-04 | Phase 32 | Pending |
| MIGR-05 | Phase 30 | Complete |
| MIGR-06 | Phase 32 | Pending |
| MIGR-07 | Phase 31 | Complete |

**Coverage:**
- v2.0 requirements: 35 total
- Mapped to phases: 35
- Unmapped: 0

**Coverage by phase:**
- Phase 29: 7 requirements (SCHEMA-01..06, SEC-03)
- Phase 30: 2 requirements (MIGR-05, RESET-06)
- Phase 31: 23 requirements (AUTH-01..05, REG-01..06, RESET-01..05, SEC-01, SEC-02, SEC-04, SEC-05, MIGR-01, MIGR-02, MIGR-07)
- Phase 32: 3 requirements (MIGR-03, MIGR-04, MIGR-06)

---
*Requirements defined: 2026-02-12*
*Last updated: 2026-02-12 after Phase 31 completion (AUTH-01..05, REG-01..06, RESET-01..05, SEC-01, SEC-02, SEC-04, SEC-05, MIGR-01, MIGR-02, MIGR-07 → Complete)*
