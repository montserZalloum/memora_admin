---
status: resolved
trigger: "CDN file uploads failing with Permission denied on temp directories when creating/updating lessons or subjects"
created: 2026-02-08T00:00:00Z
updated: 2026-02-08T16:25:00Z
---

## Current Focus

hypothesis: CONFIRMED AND FIXED
test: Reset 3 failed builds (BLD-00360, 362, 363) -> all completed with 6 files each
expecting: N/A - verified
next_action: Archive session

## Symptoms

expected: JSON files (plans/manifests) should be built and uploaded to CDN directory successfully
actual: Files added to Build Queue but upload fails with permission errors on temp directories
errors: "CDN Upload Failed: plans/PLAN-00052/manifest.json, Failed to upload after 3 attempts. Last error: [Errno 13] Permission denied: 'x.conanacademy.com/public/files/cdn/_temp_1770567111430'"
reproduction: Triggered when creating or updating lessons, subjects, or other content
started: Ongoing/blocking issue

## Eliminated

- hypothesis: Code fix not applied (unstaged changes)
  evidence: Code on disk has _ensure_directory and delete_directory, git diff confirms changes present
  timestamp: 2026-02-08T16:10:00Z

- hypothesis: Wrong bench workers processing the queue
  evidence: corex bench workers (PID 3131571, 3131572) run from /home/corex/aurevia-bench/sites, correct CWD
  timestamp: 2026-02-08T16:12:00Z

- hypothesis: Wrong site path resolution
  evidence: frappe.get_site_path returns ./x.conanacademy.com/... which resolves correctly from worker CWD
  timestamp: 2026-02-08T16:14:00Z

## Evidence

- timestamp: 2026-02-08T16:08:00Z
  checked: CDN directory ownership and permissions
  found: drwxrwsr-x (2775) www-data:www-data - setgid bit is set
  implication: Group write works for www-data members only

- timestamp: 2026-02-08T16:10:00Z
  checked: corex user group membership
  found: uid=1003(corex) gid=1004(corex) groups=1004(corex),27(sudo),122(redis) - NOT in www-data group
  implication: corex gets "other" permissions (r-x) on CDN dir - cannot write

- timestamp: 2026-02-08T16:12:00Z
  checked: Worker process identity
  found: Workers PID 3131571/3131572 run as user corex from /home/corex/aurevia-bench/sites
  implication: Build queue tasks execute as corex, who cannot write to CDN dir

- timestamp: 2026-02-08T16:14:00Z
  checked: Direct write test
  found: sudo -u corex touch cdn/test_write -> Permission denied
  implication: Confirms corex cannot create files in CDN directory

- timestamp: 2026-02-08T16:24:00Z
  checked: Post-fix verification
  found: BLD-00360, BLD-00362, BLD-00363 all completed with 6 files generated, no new errors in worker.error.log
  implication: Fix is working - CDN uploads succeed

## Resolution

root_cause: CDN directory was owned by www-data:www-data with 2775 permissions. Worker processes run as user corex (via frappe_user config), but corex was NOT a member of the www-data group. This meant corex got "other" permissions (r-x only, no write), causing Permission denied on tempfile.mkstemp() and mkdir operations.

fix: Three-part fix applied:
1. Changed CDN directory ownership from www-data:www-data to corex:www-data (chown -R corex:www-data cdn/) so corex has owner write access
2. Applied setgid bit on all CDN directories (chmod 2775) so new files/dirs inherit www-data group
3. Updated _ensure_directory() in local.py to use 0o2775 instead of 0o775 to preserve setgid on newly created subdirectories
4. Added corex to www-data group (usermod -aG www-data corex) for future-proofing
5. Restarted workers and reset 48 failed builds to Pending

verification: Reset 3 failed builds to Pending -> all completed successfully with 6 files each. No new Permission denied errors in worker.error.log. Direct write test as corex user passes.

files_changed:
- /home/corex/aurevia-bench/apps/memora_admin/memora_admin/memora_admin/services/build/storage/local.py (0o775 -> 0o2775 in _ensure_directory)
- System: chown -R corex:www-data cdn/
- System: chmod 2775 on all CDN directories
- System: usermod -aG www-data corex
