# Quickstart: Fix Export For Print Includes Redeemed Cards

**Branch**: `020-fix-export-redeemed-cards`

## Manual Testing

### Prerequisites

```bash
# Ensure you're on the right branch
cd /home/corex/aurevia-bench/apps/memora_admin
git checkout 020-fix-export-redeemed-cards

# Ensure voucher_hmac_secret is configured
bench --site x.conanacademy.com show-config | grep voucher_hmac_secret
```

### Test Scenario: Export After Redemption

1. Create a new Voucher Batch (Draft, quantity=5) in the admin panel
2. Click **Generate Cards** → wait for completion
3. Allocate some cards to a library
4. Redeem 1-2 cards (via `redeem_voucher` API or test helper)
5. Go back to the batch → click **Export for Print**
6. Open the downloaded CSV — verify it **only** contains Available cards
7. Check the export_log child table — verify `card_count` matches CSV row count

### Automated Tests

```bash
# Run the new export filtering tests
bench --site x.conanacademy.com run-tests \
    --app memora_admin \
    --module memora_admin.memora_admin.tests.test_export_filtering

# Run existing export tests (regression check)
bench --site x.conanacademy.com run-tests \
    --app memora_admin \
    --module memora_admin.memora_admin.doctype.memora_voucher_batch.test_memora_voucher_batch
```

### Expected Results

| Scenario | Before Fix | After Fix |
|----------|-----------|-----------|
| 10 cards, 3 redeemed → export | CSV has 10 rows | CSV has 7 rows |
| 10 cards, all redeemed → export | CSV has 10 rows | Error: "No available cards" |
| 10 cards, none redeemed → export | CSV has 10 rows | CSV has 10 rows (no change) |
| Export log card_count | Always = generated_count | = actual available count |
