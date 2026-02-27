# Voucher Security Regression Runbook

## Purpose
Quick checklist to verify voucher redemption/allocation security fixes after code changes.

## Environment Preconditions
- Frappe/Bench site is running and reachable.
- FastAPI sidecar is running on `127.0.0.1:8002`.
- Site used in examples: `x.conanacademy.com`.

## Canonical Security Test Command (Bench)
```bash
bench --site x.conanacademy.com run-tests \
  --app memora_admin \
  --module memora_admin.memora_admin.tests.test_security_audit
```

## FastAPI Restart and Health Check
```bash
pkill -f "uvicorn fastapi_app.main:app"
sleep 3
curl http://127.0.0.1:8002/api/v1/health/live
```

Expected: HTTP 200 health response.

## Frappe Worker Restart (for voucher.py changes)
```bash
bench restart
```

## Local Static Sanity Checks (no Bench runtime required)
```bash
python3 -m py_compile \
  memora_admin/memora_admin/api/voucher.py \
  fastapi_app/api/v1/endpoints/voucher.py
```

## Optional FastAPI Unit Check (no Frappe bench required)
```bash
pytest -q fastapi_app/tests/test_voucher_service.py -q
```

## Notes
- If `bench run-tests` fails with `ModuleNotFoundError: frappe.tests`, run inside the proper Bench/Frappe environment.
- Keep this file updated when adding new voucher error codes or auth checks.
