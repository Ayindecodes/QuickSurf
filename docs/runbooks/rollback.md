# Rolling back

## 1. Is money at risk? Stop it first

These switch off new purchases and top-ups without a code change. Saving environment variables makes
Render redeploy, which takes a minute or two. Payments already made are still credited.

```
PURCHASES_ENABLED=False
TOPUPS_ENABLED=False
```

## 2. Roll back the code

- **Backend:**
  1. In Render, open the web service, then **Events**, pick the last good deploy and choose **Rollback**.
  2. Do the same for the cron jobs: they deploy from the same repo. Alternatively, suspend them until
     you're done.
  3. Turn off auto-deploy until the fix is merged, or the next merge will deploy again.
- **Frontend:** in Vercel, open **Deployments**, pick the last good one and use **Instant Rollback**.

**When a rollback is safe:** our migrations are additive (see CONTRIBUTING.md), so the previous release
works against the newer schema. Before rolling back past a release, check its migrations. If one changed
something the old code relies on, don't roll back: fix forward with the kill switches on.

### The first launch is different

The launch applies the migrations from PRs #3–#10: the ledger, new statuses, PINs and request IDs. Code
from before the launch doesn't understand that data. **Don't roll back to pre-launch code.** Keep the
kill switches on and fix forward.

Restoring the pre-launch backup and redeploying the old commit is the last resort, because everything
since the backup is lost. Before choosing it, list the Paystack payments and VTpass purchases made since
the backup: they must be re-credited or reconciled by hand ([money-mismatch.md](money-mismatch.md)).

## 3. Afterwards

1. Run `python manage.py check_wallets`, then `python manage.py audit_money`.
2. Turn the kill switches back on once you're confident.
3. Write down what happened, what was rolled back, and any customer impact.
