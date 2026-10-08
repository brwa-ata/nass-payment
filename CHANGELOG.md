# Changelog

All notable changes to this package. Projects pin a tag, so read every entry
between your tag and the new one before upgrading, and do what its
**Upgrade notes** say. How to release and how to upgrade: [UPDATING.md](UPDATING.md).

## v0.1.2

- Fixed: a return or callback URL that is not a public address (`localhost`,
  `127.0.0.1`, a private IP, a `.local` / `.test` domain) is refused with a
  `NassError` before anything is sent. Nass created such a transaction, then
  the payment failed at "Pay Now" with a "404 Not Found!" page, so a payment
  started from a local machine could never be completed.
- Added: `client.is_local_url(url)`.

Upgrade notes:

- Set `NASS_RETURN_URL` and `NASS_CALLBACK_URL` to public addresses wherever
  the project builds them from a local request, development included.

## v0.1.1

- Fixed: the log line for a completed or failed payment names the order from
  the receipt. It read the order from Nass's status answer and printed
  `None` when that answer did not repeat it.

Upgrade notes: none.

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
