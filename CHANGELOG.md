# Changelog

All notable changes to this package. Projects pin a tag, so read every entry
between your tag and the new one before upgrading, and do what its
**Upgrade notes** say. How to release and how to upgrade: [UPDATING.md](UPDATING.md).

## v0.1.0

First release: Nass Payment Gateway card payments for Django, built against
Nass's UAT gateway and its integration manual (V2.3).

- Create a Nass transaction for a pending receipt and return the card page to
  send the customer to (`url`) and when it stops being payable (`expiresAt`).
- Public `notifyUrl` callback that re-reads every status from Nass
  (`checkStatus`); the body is never trusted.
- Status read as `PAID`, `FAILED` or `PENDING` (`client.transaction_state()`);
  an unpaid transaction's `-24` stays pending.
- Receipt model, reference field and status field set by settings; optional
  JSON field that keeps the verified transaction (its `rrn` outlives Nass's
  7-day `orderId`); optional hooks on completion and on failure.
- Client for Nass's login, create and status calls, caching the JWT for its
  own lifetime and logging in again on a `401`.
- Logs to the `nass_payment` logger, and to `NASS_LOG_FILE` when set.

Upgrade notes: none (first release).
