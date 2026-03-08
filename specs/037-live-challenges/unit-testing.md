# Live Challenges — Unit Testing

**Test file**: `fastapi_app/tests/test_live_challenge_grading.py`
**Total tests**: 28
**Run**: `python3 -m pytest fastapi_app/tests/test_live_challenge_grading.py -v`

---

## Bug Found & Fixed

### Root Cause: `frappe.logger()` crashes on PermissionError

The scheduled transition task (`memora_admin/tasks/live_challenge_transitions.py`) used `frappe.logger().info(...)` for logging. `frappe.logger()` creates a `RotatingFileHandler` that writes to `/home/corex/aurevia-bench/logs/frappe.log`. When this file had wrong ownership (`root:root` instead of `corex:corex`), every `frappe.logger()` call threw `PermissionError`.

**Impact**: The transition functions (`_transition_to_waiting`, `_transition_to_active`, `_transition_to_ended`) all had `frappe.logger().info(...)` as their **last line**. Even though the DB save succeeded, the exception on the logger call propagated up to the `try/except` in `process_live_challenge_transitions()`, which caught it and logged via `frappe.log_error()`. But since the transition function raised, the code flow skipped the `frappe.db.commit()` at line 86 — so **all DB changes were rolled back**.

**Result**: Events were stuck permanently in `Draft` status. The transition task ran every minute but crashed on every attempt.

**Fix**:
1. Fixed file permissions: `sudo chown corex:corex logs/frappe.log`
2. Removed all `frappe.logger()` calls from the transition module (they are informational only and should never crash the task)

---

## Test Cases Covered

### 1. Score Calculation (`TestScoreCalculation`) — 5 tests

| # | Test | What it verifies |
|---|------|-----------------|
| 1 | `test_all_correct` | 3/3 correct = 100.0%, correct_count=3 |
| 2 | `test_none_correct` | 0/2 correct = 0.0%, correct_count=0 |
| 3 | `test_partial_correct` | 15/20 correct = 75.0% |
| 4 | `test_single_question_correct` | Single question event scores 100% |
| 5 | `test_empty_questions_zero_score` | Zero questions = 0% (no crash) |

### 2. Corrections List (`TestCorrections`) — 3 tests

| # | Test | What it verifies |
|---|------|-----------------|
| 6 | `test_corrections_only_wrong_answers` | Only wrong answers appear in corrections, with correct selected/correct_answer |
| 7 | `test_all_correct_empty_corrections` | All correct = empty corrections list (not null) |
| 8 | `test_missing_answer_treated_as_wrong` | Missing question_idx in answers = treated as wrong (selected=null in corrections) |

### 3. Null/Unanswered Handling (`TestNullUnanswered`) — 2 tests

| # | Test | What it verifies |
|---|------|-----------------|
| 9 | `test_null_selected_is_incorrect` | `selected: null` counts as wrong, appears in corrections |
| 10 | `test_all_null_zero_score` | All null selections = 0% score |

### 4. Show Correct Answers Toggle (`TestShowCorrectAnswers`) — 2 tests

| # | Test | What it verifies |
|---|------|-----------------|
| 11 | `test_show_false_returns_null_corrections` | `show_correct_answers=false` → corrections is `null` |
| 12 | `test_show_true_returns_corrections_list` | `show_correct_answers=true` → corrections is a list |

### 5. Standard Competition Ranking (`TestComputeRanking`) — 10 tests

| # | Test | What it verifies |
|---|------|-----------------|
| 13 | `test_simple_ranking` | Distinct scores → ranks 1, 2, 3 |
| 14 | `test_tied_scores_share_rank` | Tied scores share rank, next rank skips (1, 1, 3) |
| 15 | `test_all_same_score` | All same score → all rank 1 |
| 16 | `test_complex_tie_pattern` | Multiple tie groups: 1, 1, 3, 3, 5 |
| 17 | `test_top_20_limit` | 30 participants → top_20 has exactly 20 entries |
| 18 | `test_empty_participants` | Empty input → empty output |
| 19 | `test_single_participant` | Single participant → rank 1 |
| 20 | `test_display_name_fallback` | Missing display_name → falls back to player ID |
| 21 | `test_unsorted_input_sorts_correctly` | Function sorts internally regardless of input order |
| 22 | `test_top_20_has_correct_fields` | top_20 entries have exactly: rank, player, display_name, score |

### 6. XP Awards (`TestComputeXpAwards`) — 6 tests

| # | Test | What it verifies |
|---|------|-----------------|
| 23 | `test_standard_distribution` | Ranks 1-4 get correct XP (participation + rank bonus) |
| 24 | `test_tied_first_place_both_get_first_xp` | Two rank-1 players both get first_place_xp |
| 25 | `test_zero_xp_config` | All XP=0 → everyone gets 0 |
| 26 | `test_participation_only_no_rank_bonus` | Only participation XP, no rank bonuses → all equal |
| 27 | `test_empty_ranked` | Empty input → empty output |
| 28 | `test_rank_beyond_third_gets_default` | Ranks 4+ get default_xp bonus |
