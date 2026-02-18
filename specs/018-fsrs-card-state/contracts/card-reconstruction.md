# Contract: FSRS Card Reconstruction & Persistence

**Feature**: 018-fsrs-card-state
**Date**: 2026-02-18

## Card Reconstruction (DB → Card)

Applied in both `fsrs_processor.py` and `reviews.py` before calling `scheduler.review_card()`.

### Pseudocode

```python
from fsrs import Card, State

def reconstruct_card(db_row, now):
    """Reconstruct FSRS Card from Memory State DB row.

    Handles both new records (all NULL) and existing records
    (with or without the new state/step/last_review fields).
    """
    card = Card()

    if db_row and db_row.stability and db_row.stability > 0:
        # Existing card with prior reviews
        card.stability = db_row.stability
        card.difficulty = db_row.difficulty

        # Set due from next_review (date -> datetime)
        if db_row.next_review:
            if isinstance(db_row.next_review, date) and not isinstance(db_row.next_review, datetime):
                card.due = datetime.combine(db_row.next_review, time.min, tzinfo=timezone.utc)
            else:
                card.due = db_row.next_review
        else:
            card.due = now

        # NEW: Restore state (NULL = Learning, same as Card() default)
        if db_row.state is not None:
            card.state = State(int(db_row.state))
        # else: card.state remains Learning (Card() default)

        # NEW: Restore step (NULL preserved as-is)
        if db_row.step is not None:
            card.step = int(db_row.step)
        # else: card.step remains 0 (Card() default)

        # NEW: Restore last_review (NULL = never reviewed)
        if db_row.last_review is not None:
            lr = db_row.last_review
            if lr.tzinfo is None:
                lr = lr.replace(tzinfo=timezone.utc)
            card.last_review = lr
        # else: card.last_review remains None (Card() default)

    # If stability is 0/NULL, leave card as default Card() (Learning, step=0)
    return card
```

### Edge Cases

| Scenario | state | step | last_review | Behavior |
|----------|-------|------|-------------|----------|
| Pre-migration record (all NULL new fields) | NULL | NULL | NULL | Treat as Learning(1), step=0. Re-initializes on next review. |
| Record with inflated stability (e.g., 4550) | NULL | NULL | NULL | Uses inflated stability as-is. FSRS will gradually correct it over reviews. |
| Learning card mid-steps | 1 | 1 | datetime | Correctly restores mid-learning state. Next review continues from step 1. |
| Review card (graduated) | 2 | NULL | datetime | Correctly restores graduated state. Intervals grow normally. |
| Relearning card (lapsed) | 3 | 0 | datetime | Correctly restores relearning state. Goes through relearning steps. |

## Card Persistence (Card → DB)

Applied in both `fsrs_processor.py` and `reviews.py` after calling `scheduler.review_card()`.

### Pseudocode

```python
def extract_card_state(card):
    """Extract persistable fields from FSRS Card.

    Returns dict suitable for SQL parameterization.
    """
    return {
        "stability": card.stability,
        "difficulty": card.difficulty,
        "next_review": clamped_next_review_date,  # date, min tomorrow
        "state": card.state.value,                 # int: 1, 2, or 3
        "step": card.step,                         # int or None
        "last_review": (
            card.last_review.replace(tzinfo=None)
            if card.last_review else None
        ),  # naive datetime for MariaDB
    }
```

### Datetime Handling

- **last_review write**: `card.last_review` is tz-aware (UTC). Strip timezone with `.replace(tzinfo=None)` before writing to MariaDB DATETIME(6) column (MariaDB stores naive datetimes).
- **last_review read**: DB returns naive datetime. Add UTC timezone with `.replace(tzinfo=timezone.utc)` when reconstructing the Card.
- **Consistency**: Same pattern as Frappe's `creation`/`modified` columns (stored naive, interpreted as server timezone).

## Redis Cache Contract

### Write (after review)

```python
fsrs_data = json.dumps({
    "stability": card.stability,
    "difficulty": card.difficulty,
    "next_review": next_review_date.isoformat(),
    "state": card.state.value,
    "step": card.step,
    "last_review": card.last_review.isoformat() if card.last_review else None,
    "lesson": lesson,
    "stage_id": stage_id,
})
r.setex(f"memora:fsrs:{player}:{item_id}", 86400, fsrs_data)
```

### Read (display purposes only)

```python
data = json.loads(r.get(cache_key))
state = data.get("state")       # None if old cache entry
step = data.get("step")         # None if old cache entry
last_review = data.get("last_review")  # None if old cache entry
```
