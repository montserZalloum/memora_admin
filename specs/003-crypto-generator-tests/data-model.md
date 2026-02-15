# Data Model: Voucher Crypto & Generator Unit Tests

**Phase 1 Output** | **Date**: 2026-02-15

## Test Data Entities

This feature is a test suite — no new data models are created. Instead, this document defines the **test data structures** used by the 18 unit tests.

### 1. PIN Test Data

| Field | Type | Example | Used By |
|-------|------|---------|---------|
| `length` | int | `12` (default), `14`, `16`, `1` (edge) | `generate_pin(length)` |
| `pin` | str | `"ABCDEF234567"` | Output validation |

**Validation rules**:
- Length matches requested parameter
- All characters in `PIN_ALPHABET` (`ABCDEFGHJKMNPQRSTUVWXYZ23456789`)
- No ambiguous characters: `0`, `O`, `1`, `I`, `L`

### 2. HMAC Test Data

| Field | Type | Example | Used By |
|-------|------|---------|---------|
| `pin` | str | `"ABCDEF123456"` | `compute_hmac(pin, secret)` |
| `secret` | str | `"test-secret"` | `compute_hmac(pin, secret)` |
| `hmac_digest` | str | 64-char hex | Output validation |

**Validation rules**:
- Output is exactly 64 characters
- Output contains only hex characters (`[0-9a-f]`)
- Deterministic: same inputs → same output
- Sensitive: different inputs → different output

### 3. Serial Reservation Test Data

| Field | Type | Example | Used By |
|-------|------|---------|---------|
| `count` | int | `5`, `0` (edge) | `reserve_serial_block(count)` |
| `serial_no` | str | `"VCH-000001"` | Output validation |

**Validation rules**:
- Format: `VCH-` prefix + 6-digit zero-padded number
- Contiguous: block of N serials has no gaps
- Count: exactly N serials returned for request of N
- Regex: `^VCH-\d{6}$`

### 4. CSV Export Test Data

| Field | Type | Example | Used By |
|-------|------|---------|---------|
| `cards_data` | list[dict] | `[{"serial_no": "VCH-000001", "pin": "ABC123"}]` | `build_export_csv(cards_data, ...)` |
| `product_names` | str | `"Test Product"` | CSV column value |
| `face_value` | str | `"10.00"` | CSV column value |

**Validation rules**:
- Header row: `serial_no,pin,product_names,face_value`
- Row count: N+1 for N cards
- Content: each row matches corresponding input dict

### 5. Encryption Test Data

| Field | Type | Example | Used By |
|-------|------|---------|---------|
| `plaintext` | bytes | `b"serial_no,pin\nVCH-000001,ABC123"` | `encrypt_data(data, secret)` |
| `hmac_secret` | str | `"test-secret"` | Key derivation |
| `ciphertext` | bytes | Fernet token bytes | `decrypt_data(encrypted, secret)` |

**Validation rules**:
- Roundtrip: `decrypt(encrypt(data, secret), secret) == data`
- Ciphertext differs from plaintext
- Wrong secret raises `cryptography.fernet.InvalidToken`

## Entity Relationships

```
generate_pin(length) → pin
                          ↓
compute_hmac(pin, secret) → hmac_digest
                          ↓
build_export_csv([{serial_no, pin}], ...) → csv_bytes
                                              ↓
encrypt_data(csv_bytes, secret) → ciphertext
                                     ↓
decrypt_data(ciphertext, secret) → csv_bytes (roundtrip)
```

These functions form a pipeline in production (`generate_cards_job`), but each is tested independently in Phase 3.
