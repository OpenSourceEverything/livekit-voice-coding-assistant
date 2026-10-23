# Billing and usage, in plain language

Use the LiveKit Cloud project's **Billing** page to answer “what does LiveKit
say I owe?” Use `make status` or `make usage` to answer “what did this local
worker report using?” They are different meters.

## Check the account's current charges

Open the relevant project's Billing page in LiveKit Cloud to check the current
plan, next invoice, included allowances, and posted usage. The project Usage
page also shows limit peaks; those are capacity/usage limits and do not by
themselves mean that amount was billed. Account totals, credits, included
minutes, and pricing can change, so check the account directly before a new
billing cycle or a paid configuration change.

## Why the local report can differ

The local ledger records numeric cumulative model counters from sessions handled
by this worker. `make status` gives the compact view; `make usage` gives per-model
details. Run the report after a call ends so its final SDK snapshot is included.
By default, synthetic debugger/simulation rows are hidden. If you want to inspect
those rows, run:

```sh
.tools/bin/uv run python tools/report_usage.py --include-synthetic
```

The local estimate prices only the recognized Gemma input/output tokens and
Fish TTS characters. It does not calculate the LiveKit invoice. It does not
include STT, telephony, number rental, hosting, data/egress, observability,
other projects/workers, free allowances, taxes, credits, or plan adjustments.
It also does not fetch an account balance or reconcile monthly totals. Therefore
an unknown balance is not zero, and an omitted synthetic row is not necessarily
free; the account Billing page is authoritative for charges.

## Simple check before and after testing

1. Open the project Billing page and note the plan, next invoice, and current
   month total.
2. Run `make status` before a test and after the call has ended. This is the
   local worker's recorded-use estimate, not an invoice.
3. Reopen Billing after usage posts to confirm the account's actual charge and
   included minutes/credits.
4. If a new phone number, paid plan, or payment method is proposed, review the
   price and confirmation screen before accepting it.

LiveKit references: [billing meters](https://docs.livekit.io/deploy/admin/billing/),
[quotas and limits](https://docs.livekit.io/deploy/admin/quotas-and-limits/),
and [inference pricing](https://livekit.com/pricing/inference/).
