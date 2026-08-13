"""Input adapters: processor-specific data in, ledger-core models out.

The core of the library names no processor (§7). Everything that knows what a
Stripe balance transaction looks like lives here, behind this boundary, so the
generators and the balance check only ever see a Transaction or a Payout.

Adapters are the only place a field name from an outside system appears.
"""
