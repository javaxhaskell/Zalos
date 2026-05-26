# Problem report — invoice aging agent (v2)

When we ran the aging report this morning, some invoices that are
**31 days overdue** are showing up in the **1-30** aging bucket
instead of the **31-60** bucket. We use this report to decide which
invoices to chase before the AR review, so they need to be in the
correct severity tier.

Specifically the customers we noticed:

- **Epsilon Co** (`INV-0005`) — due 2026-03-31, 31 days overdue,
  $990.00, currently in 1-30 (should be in 31-60).
- **Nu Studios** (`INV-0013`) — due 2026-03-31, 31 days overdue,
  $1450.00, currently in 1-30 (should be in 31-60).
- **Sigma Co** (`INV-0018`) — due 2026-03-31, 31 days overdue,
  $210.00, currently in 1-30 (should be in 31-60).

Could someone investigate and repair the agent? The other buckets
(Paid / Current / 31-60 / 61-90 / 90+) look right and the high-risk
flag on the large overdue amounts also looks right — it really seems
to be just the boundary at 31 days.

— AR clerk
