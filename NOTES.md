# NOTES

> **How this file was written:** I asked Claude Code to write it for me, from the commits and the test runs in our sessions. The commit hashes, test counts and failing/passing results were checked against the repo before I submitted. The wording is the assistant's, not mine.

Fill in every field below before you submit. We read this file first: a submission with an empty field is not graded.
Write in your own words. If you did not fix something, set Status to `not fixed` and say what you found and tried; that still counts.
Status is one of: `fixed`, `partly fixed`, `not fixed`. Commits: the short hashes of the commits for this item, or `none`.

## 1. Report

> Finance says that on some days the revenue in the daily report is higher than what the payment provider paid out, and a few orders show more money received than their total.

**Status:** fixed

**What I saw:** An order totalling 1200 that got the same webhook event delivered three times ended with three rows in `payments`, `paid_cents` of 3600, and a daily report showing 3600 revenue for 1 order.

**Root cause:** The provider retries a webhook until it gets a 2xx, but `Payments.handle_event` inserted a row for every delivery and never looked at the event id. The `payments` table had no uniqueness on `event_id` either. Reports sum all payment rows, so each retry counted again.

**Fix and why:** `handle_event` now runs inside `BEGIN IMMEDIATE` and looks up the event id first. A repeat of an event already taken records nothing and returns the order's current status, so the provider still gets a 2xx (even if the order was cancelled in between; before, that gave a 409 and endless retries). The same event id with a different order or amount is rejected with a 409 and not recorded. A `UNIQUE` index on `payments.event_id` backs this up in the database. I chose to reject a conflicting replay rather than ignore it so that mismatched data is not swallowed silently.

**How I verified it:** `tests/test_payments.py` (6 tests): retries record once, daily revenue stays 1200, HTTP retries return 200 every time, retry after cancel returns 200, conflicting replay returns 409, and the database refuses a duplicate event id. All 6 fail on the old code. I restored `db.py` and `payments.py` from `HEAD` with the bytecode cache cleared to confirm that. I also ran 30 rounds with 4 worker threads, each on its own connection, delivering the same event; every round ended with one row.

**Commits:** 98da97c

## 2. Report

> Products show as sold out, or nearly, while the shelf is full. Staff say it gets worse on days when many customers abandon checkout.

**Status:** fixed

**What I saw:** With 10 on the shelf, an unpaid order for 3 took stock to 7. Cancelling it left the order `cancelled` and stock still 7.

**Root cause:** Stock is reserved when the order is created, but `Orders.cancel` only called `release` for orders already `paid`. Cancelling a `pending` order never returned its units, so abandoned checkouts slowly emptied the shelf. A second problem showed up while fixing it: `cancel` read the order status before taking its lock, so two workers cancelling the same order could both see `pending` and both release the stock (10 became 13).

**Fix and why:** `cancel` now releases every line of any order it cancels (pending or paid). In a second commit it takes `BEGIN IMMEDIATE` first and reads the status inside the lock, so the second worker sees `cancelled` and does nothing. Shipped orders are still rejected and a repeat cancel is still a no-op.

**How I verified it:** `tests/test_cancel.py`: unpaid cancel, multi-item cancel, double cancel, shipped cancel, and a two-thread test where both workers read the order before either cancels it. The first three fail on the old code (`7 != 10`, `(7, 3) != (10, 5)`); the thread test fails with `13 != 10`. Each was confirmed by restoring the old file with the bytecode cache cleared. The thread test passed 20 runs in a row.

**Commits:** 4f739e4, eea848f

## 3. Report

> Customers in taxed regions who used a coupon say the tax on their receipt is too high. Receipts without a coupon are right.

**Status:** fixed

**What I saw:** A 10% coupon on a 10000 order in CA charged 725 tax, the same as with no coupon (NY 888, DE 1900). Tax on the discounted amount would be 653, 799 and 1710.

**Root cause:** `pricing.price_order` calculated tax on the full subtotal and subtracted the coupon afterwards, so customers paid tax on money they never spent. With no coupon the discount is 0, which is why those receipts were right.

**Fix and why:** Work out the discount first, then tax `subtotal - discount`. I treated a coupon as a pre-tax discount (confirmed as the intended meaning). A coupon covering the whole subtotal now gives zero tax.

**How I verified it:** `tests/test_pricing.py` with exact cents: percent coupon in CA/NY/DE (totals 9653, 9799, 10710), a fixed coupon (tax 689), coupons covering the full subtotal (tax 0), no coupon unchanged, and a stored order. Six assertions fail on the old code. One of my own expected totals was wrong at first (9710 instead of 10710); the code was right and I corrected the test before committing.

**Commits:** eb92154

## 4. Report

> Our shop in India (UTC+5:30) says sales taken in the morning do not appear in that day's report and turn up under the day before.

**Status:** fixed

**What I saw:** `day_bounds_utc("2025-03-10", 330)` returned `05:30Z` on the 10th to `05:30Z` on the 11th. The correct window is `18:30Z` on the 9th to `18:30Z` on the 10th. A 08:00 IST sale (`02:30Z`) fell before the window and was counted on the 9th.

**Root cause:** `reports.py` added the UTC offset to local midnight. Local time is UTC plus the offset, so UTC is local minus the offset.

**Fix and why:** One line: `start += timedelta(...)` became `start -= timedelta(...)`. Negative offsets were wrong the same way and are fixed by the same change.

**How I verified it:** `tests/test_reports.py` (5 tests): exact bounds for +330, -480 and 0, a 08:00 IST sale landing on its own day, and four payments either side of the day boundaries. Four fail on the old code; the zero-offset test is a control that guards the default. My first revert check wrongly passed because `+=` to `-=` leaves the file size unchanged and Python reused a stale `.pyc`; I redid it with the cache cleared and bytecode disabled.

**Commits:** eeae472

## 5. Report

> Ever since we started running two server workers, we have sold more units of an item than we had, and its stock went negative.

**Status:** fixed

**What I saw:** Two workers on one SQLite file, stock 1: worker B warmed its product cache, worker A bought the unit (DB stock 0), then B also got a 201 and the DB stock went to -1.

**Root cause:** `Catalog` keeps a per-process cache and `reserve` used it for the stock check, but a worker only invalidates its own copy. The check passed on stale data and the unconditional `stock = stock - qty` wrote -1. I separated this from a second theory (check-then-update race): inside `Orders.create` the `BEGIN IMMEDIATE` lock already serialises the check, so the stale cache is what caused the bug; the race only exists if `reserve` is called without that lock.

**Fix and why:** `reserve` now does a single `UPDATE ... SET stock = stock - ? WHERE sku = ? AND stock >= ?` and raises `OutOfStock` (with the real stock from the DB) when no row changed. The cache is not used for the decision. `Orders.create` also priced lines from the same cache, so a price change on another worker was ignored; it now uses `Catalog.current_price`, which reads the DB.

**How I verified it:** `tests/test_stock.py`: stale stock cache (expects 409, stock 0), two concurrent reservations for one unit (forced to both read first; expects one ok, one out), and stale price (expects 1500, not 1200). All three fail on the old code and pass after; repeated 20 times with no flake. One harness bug along the way: a worker connection was closed from the wrong thread.

**Commits:** 5dc8d73

## 6. Open question

> Open question from support, no single right answer: sometimes a customer really does pay twice for one order (two separate payments, not a retry of the same one). Today the system records both and says nothing. Decide what should happen, build the smallest version of your decision, and explain what it costs in `NOTES.md`.

**My decision:** Credit an order only up to what it still owes. Any money beyond the total is kept (it really arrived), flagged as an overpayment so it can be refunded, and never counted as revenue. The webhook still returns 200 so the provider does not retry, and the order stays `paid`.

**Why:** Rejecting the second payment with an error would make the provider retry forever and would leave the customer's money unaccounted for. Silently recording it as revenue is the bug being reported. Capping the credit and flagging the excess keeps the books right and gives finance something to act on.

**What it costs:** Nothing refunds automatically. Finance has to work from the overpayment list, and the held money is a liability until they do. The only automatic signal is an extra field on the webhook response, which the provider ignores. A payment for a cancelled order is still rejected with a 409 as before, and that money is not tracked as an overpayment, so it needs separate handling. There is also a schema change: existing databases get a new column added and backfilled when opened.

**What I built:** `payments.credited_cents` (amount credited; `amount_cents` stays what the provider sent, so excess = `amount_cents - credited_cents`). `paid_cents` and the daily report sum `credited_cents`. New `Payments.overpaid_cents(order_id)`, `reports.overpayments(conn)` (orders to refund), and an `overpaid_cents` field on the webhook response when above 0. `db.connect` migrates older databases under a lock.

**How I verified it:** `tests/test_overpayment.py` (9 tests): second full payment is held and status stays paid, revenue is 1000 not 2000, both rows are kept, partial payments 600 + 600 on 1000 give 400 credited and 200 over, under/exact payments unchanged, retry of the overpaying event changes nothing, webhook returns 200 with the flag, refund report, and migration of an old database. All 9 fail on the old code and pass after; confirmed by restoring the four source files from `HEAD`.

**Commits:** 7f87ad6

## Other things I noticed

Not fixed. Tested means I reproduced it; read means I only saw it in the code.

- `GET /reports/daily/<day>` reads `utc_offset_minutes` from the request body of a GET. Some clients and proxies drop GET bodies, so the offset can silently go missing and the report falls back to UTC. (read)
- `GET /products/<sku>` still reads through the per-process cache, so one worker can show a stale price or stock level. Orders no longer depend on it. (tested for the stale price case; display only)
- `server.py` calls `json.loads` on the body with no error handling, so an invalid JSON body should produce a 500 instead of a 400. (read, not run)
- `Orders.ship` exists but has no API route, and it reads the status then updates without a lock, so it has the same read-then-write shape that caused the concurrent-cancel bug. (read)
- A payment for a cancelled order is rejected with a 409 and the money is not tracked anywhere (see report 6 costs). (read)

## Session logs

Tools used: Claude Code, with the caveman terse-output plugin loaded.

- `c53c28e3-7307-4d19-a6aa-50f2161f2cf2.jsonl`: the main session, as a raw `.jsonl` export. Orientation, then reports 4, 5, 2 (and its concurrent-cancel race), 3, 1 and 6, each test first with a revert check. This is where the code in the commits above was written, plus this file and the zip. My email address is replaced with `[redacted-email]`; nothing else is edited. It was copied last, right before zipping, so it ends at the zip step and does not include the assistant's final reply.

Only this session is included. Other, earlier Claude Code sessions exist in the same project folder and are not included; that was my choice.
