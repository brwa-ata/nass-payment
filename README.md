# nass-payment

Nass Payment Gateway for Django. A project creates a pending receipt, this
package creates the Nass transaction for it and hands back the address of
Nass's card page (Visa / Mastercard, with 3-D Secure), and the receipt is
completed only once Nass itself confirms the payment.

- Nass API documentation: *Nass Merchant Payment Gateway, Integration Manual
  for UAT and PRD, V2.3* (from Nass). Where the gateway answers differently
  from the manual, the package follows the gateway; see
  [Nass API contract](#nass-api-contract).
- Import name: `nass_payment`. Distribution name: `nass-payment`.
- Requires Python 3.10+, Django 4.2+, Django REST framework 3.14+, requests.

`client.py` has no Django dependency. If all you need is a raw Nass API
wrapper, that one file (plus `exceptions.py`) is enough.

## Contents

- [How a payment works](#how-a-payment-works)
- [What is in the package](#what-is-in-the-package)
- [Install into a project](#install-into-a-project)
- [Settings reference](#settings-reference)
- [HTTP endpoints](#http-endpoints)
- [Reading a status](#reading-a-status)
- [Tracking the status](#tracking-the-status)
- [Python API](#python-api)
- [Nass API contract](#nass-api-contract)
- [Logging](#logging)
- [Testing](#testing)
- [Updating and releasing](#updating-and-releasing)
- [Known limits](#known-limits)

## How a payment works

```
1. The project creates a pending receipt              is_completed = False
2. service.start_payment(receipt, callback_url=…, return_url=…)
   -> Nass create-transaction, with an orderId of ours:
      <NASS_ORDER_PREFIX><receipt pk><unix time>, digits only
   -> the orderId is stored in receipt.ref_no
   -> returns url (Nass's card page) and expiresAt
3. The customer opens url and pays by card; their bank runs 3-D Secure
4. Nass sends the customer back to return_url, and posts the result to
   the callback  POST api/nass/callback/ {orderId, actionCode, responseCode, rrn, …}
   -> the status is re-read from Nass (checkStatus), never trusted from the body
   -> on paid: is_completed = True, the transaction is kept (optional),
      and the completion hook runs once
5. Meanwhile the client polls a status endpoint, which re-reads the status
   from Nass too, so the payment completes even if the callback never arrives
```

A transaction is payable for **an hour**: Nass's e-Gateway refuses one whose
timestamp is older than that (its error `-20`). Nass answers `checkStatus`
for 24 hours and keeps an `orderId` for 7 days; the transaction's `rrn` is the
bank's lasting reference, so keep it (`NASS_RECEIPT_DETAILS_FIELD`). Nass has
no cancel or refund call in its merchant API.

## What is in the package

| File | What it does |
| --- | --- |
| `client.py` | `NassPaymentClient`: login, create, status; `transaction_state()`. No Django |
| `conf.py` | Reads the `NASS_*` settings once and builds one shared client |
| `service.py` | Ties the client to your receipt model: start, sync, complete, callback |
| `views.py` / `urls.py` | The callback Nass calls, plus two staff endpoints (see [HTTP endpoints](#http-endpoints)) |
| `logs.py` | The `nass_payment` logger, and the optional log file |
| `exceptions.py` | `NassError`, `NassAuthError`, `NassAPIError` |

The package has no models and no migrations.

## Install into a project

### 1. Install the package

Projects pin an exact tag, so nothing changes until someone moves the pin.

```bash
# uv
uv add git+https://github.com/brwa-ata/nass-payment --tag v0.1.0

# pip (requirements.txt)
nass-payment @ git+https://github.com/brwa-ata/nass-payment@v0.1.0
```

The server that installs it needs `git` and outbound HTTPS to github.com.

### 2. Register it and route it

```python
# settings.py
INSTALLED_APPS += ['nass_payment']

# urls.py
path('api/nass/', include('nass_payment.urls')),
```

The callback must be reachable by Nass without a login; if the project has
middleware that refuses anonymous requests, let `api/nass/callback/` through.

### 3. Add the credentials

```bash
# .env
NASS_BASE_URL=https://uat-gateway.nass.iq:9746
NASS_USERNAME=...
NASS_PASSWORD=...
# Optional. Blank = built from the request as <host>/api/nass/callback/
NASS_CALLBACK_URL=
# Optional. Where Nass sends the customer after paying, when the caller gives none
NASS_RETURN_URL=
```

```python
# settings.py (python-decouple shown; os.environ works the same)
NASS_BASE_URL = config('NASS_BASE_URL')
NASS_USERNAME = config('NASS_USERNAME')
NASS_PASSWORD = config('NASS_PASSWORD')
NASS_CALLBACK_URL = config('NASS_CALLBACK_URL', default='')
NASS_RETURN_URL = config('NASS_RETURN_URL', default='')
```

| Environment | `NASS_BASE_URL` | Card page |
| --- | --- | --- |
| UAT (testing) | `https://uat-gateway.nass.iq:9746` | `3dsecure.nass.iq` |
| Production | `https://gateway.nass.iq:9746` | given in `url` |

The two environments have separate merchant credentials. UAT takes only the
test cards listed in Nass's manual (section 1.5).

**Set `NASS_CALLBACK_URL` in production.** Built from the request, the URL is
only right if Django knows it is behind HTTPS (`SECURE_PROXY_SSL_HEADER`) and
sees the public host; otherwise Nass is given an `http://` or internal
address.

**Nass refuses `localhost`.** It answers `422 backRef must be a URL address`
for a callback or return URL whose host is `localhost`. An IP address is
accepted, so in local development run the backend and the site on
`127.0.0.1` (the callback cannot reach your machine anyway; polling the
status completes the payment).

### 4. Point the app at your receipt model

The package never imports your model. It needs a model with a field for our
`orderId` and a boolean that stays `False` until Nass confirms the payment:

```python
NASS_RECEIPT_MODEL = 'shop.Receipt'         # required
NASS_RECEIPT_REF_FIELD = 'ref_no'           # default
NASS_RECEIPT_STATUS_FIELD = 'is_completed'  # default
# Optional: a JSONField the verified transaction is kept in, under "nass"
NASS_RECEIPT_DETAILS_FIELD = 'meta'

# Optional: run once, on the pending -> completed transition only
NASS_ON_PAYMENT_COMPLETED = 'shop.payments.on_nass_paid'
# Optional: run whenever Nass reports a failed transaction
NASS_ON_PAYMENT_FAILED = 'shop.payments.on_nass_failed'
```

A hook is `fn(receipt, status)`, where `status` is Nass's verified
`checkStatus` answer (`rrn`, `card`, `amount`, `tranDate`, ...). An exception
inside a hook is logged and swallowed: it never undoes a payment Nass has
already confirmed. Only the completion hook is guaranteed to run once; the
failed hook runs each time a failed status is read (a callback and a poll can
both see it), so make it idempotent.

With `NASS_RECEIPT_DETAILS_FIELD` set, the field becomes
`{..., "nass": {<the checkStatus answer>}}` when a payment completes or
fails; its other keys are left alone, and a completed receipt's transaction
is never replaced by a failed one.

### 5. Choose where the log goes (optional)

```python
NASS_LOG_FILE = BASE_DIR / 'logs' / 'nass_payment.log'   # or 'nass_payment.log'
```

See [Logging](#logging); without it the package writes no file.

### 6. Write the "start payment" and "status" endpoints

Creating the receipt stays in the project, because only the project knows who
pays, how much, and what the receipt means. A customer-facing pair looks like
this:

```python
from django.db import transaction
from nass_payment import service as nass_service
from nass_payment.exceptions import NassError

@transaction.atomic
def start(request):
    receipt = Receipt.objects.create(
        customer=request.user, amount=amount, is_completed=False, ...
    )
    try:
        payment = nass_service.start_payment(
            receipt,
            callback_url=settings.NASS_CALLBACK_URL
            or request.build_absolute_uri('/api/nass/callback/'),
            return_url='https://shop.example/payment/return',
        )
    except NassError as exc:
        # a returned response does not roll back the atomic block by itself
        transaction.set_rollback(True)
        return Response({'message': f'Nass payment failed: {exc}'}, 422)

    return Response({
        'receipt_id': receipt.id,
        'ref_no': receipt.ref_no,              # our orderId
        'redirect_url': payment['url'],        # open it for the customer
        'expires_at': payment['expiresAt'],
    }, 201)

def status(request):
    # scope it to the caller, so nobody can read another customer's payment
    receipt = get_object_or_404(Receipt, pk=..., customer=request.user)
    if not receipt.is_completed:
        try:
            nass_service.sync_status(receipt)
        except NassError:
            pass  # transient: keep it pending, the client polls again
    return Response({'is_completed': receipt.is_completed})
```

Pass `amount=` to `start_payment` to charge something other than
`receipt.amount`, e.g. the amount plus a fee the customer pays on top.

Open `url` from a link the customer clicks (or a plain redirect), not from
script after the request: browsers block a new tab opened that late.

## Settings reference

| Setting | Default | Purpose |
| --- | --- | --- |
| `NASS_BASE_URL` | *(none, required)* | Nass environment base URL |
| `NASS_USERNAME` / `NASS_PASSWORD` | *(none, required)* | Merchant credentials |
| `NASS_TIMEOUT` | `30` | HTTP timeout, seconds |
| `NASS_CALLBACK_URL` | *(blank)* | Absolute callback URL (Nass's `notifyUrl`); blank builds `<host>/api/nass/callback/` from the request |
| `NASS_RETURN_URL` | *(blank)* | Where Nass sends the customer afterwards (`backRef`), for the staff endpoint when the request gives none |
| `NASS_ORDER_PREFIX` | *(blank)* | Digits that start every `orderId`; anything else is refused at startup |
| `NASS_PAYMENT_LIFETIME` | `60` | Minutes a transaction stays payable; sets `expiresAt` |
| `NASS_RECEIPT_MODEL` | *(none, required)* | `app_label.Model` holding payments |
| `NASS_RECEIPT_REF_FIELD` | `ref_no` | Field that stores our `orderId` |
| `NASS_RECEIPT_STATUS_FIELD` | `is_completed` | Boolean completed by a paid status |
| `NASS_RECEIPT_DETAILS_FIELD` | *(blank)* | JSONField that keeps the verified transaction under `"nass"`. Blank: not kept |
| `NASS_ON_PAYMENT_COMPLETED` | *(blank)* | Dotted path to `fn(receipt, status)`, run once on completion |
| `NASS_ON_PAYMENT_FAILED` | *(blank)* | Dotted path to `fn(receipt, status)`, run on a failed status |
| `NASS_LOG_FILE` | *(blank)* | File the package writes its log to; relative paths start at `BASE_DIR`. Blank: no file |
| `NASS_LOG_LEVEL` | `INFO` | Lowest level written to `NASS_LOG_FILE` |
| `NASS_VIEW_PERMISSION_CLASSES` | `['rest_framework.permissions.IsAdminUser']` | Dotted paths of the DRF permissions guarding the two `payments/` endpoints |

Settings are read once per process (`conf.get_conf()`), so restart the app
server and every worker after changing them. In tests, call
`nass_payment.conf.reset()` after overriding one.

## HTTP endpoints

Mounted under whatever prefix the project gives `nass_payment.urls`
(`api/nass/` below).

| Endpoint | Who | What |
| --- | --- | --- |
| `POST api/nass/callback/` | Nass (public) | Nass's result body; only its `orderId` (or the raw `ORDER`) is used. Re-reads the status from Nass and applies it. `200` done (an unknown order too), `400` no order, `503` Nass could not be reached |
| `POST api/nass/payments/` | Staff | `{"receipt_id": ..., "return_url": ...}`: starts a payment for an existing pending receipt (`return_url` defaults to `NASS_RETURN_URL`). Answers `orderId`, `url` and `expiresAt`; `422` without a receipt or a return URL, `502` if Nass refuses |
| `GET api/nass/payments/<receipt pk>/status/` | Staff | Re-syncs and answers `{state, is_completed, transaction}`; `502` if Nass refuses |

The two `payments/` endpoints find a receipt by its id alone, so they are for
staff: by default only `is_staff` users may call them. Set
`NASS_VIEW_PERMISSION_CLASSES` to change who may, and give customers
endpoints of your own that scope the receipt to the caller (step 6 above).

## Reading a status

Nass answers a status as two codes, `actionCode` and `responseCode`, plus a
`statusMsg`. `client.transaction_state()` reads them as one of three states:

| State | When | What the service does |
| --- | --- | --- |
| `PAID` | `actionCode` `0` and `responseCode` `00` (Approved) | Completes the receipt, keeps the transaction, runs the completion hook once |
| `FAILED` | `actionCode` `2` (declined by the bank, e.g. `05`, `51`), or `responseCode` `-19` (3-D Secure failed), `-22` (invalid authentication), `-25` (cancelled by the customer), `-30` (fraud), `-32` (repeated decline) | Keeps the transaction, runs the failed hook |
| `PENDING` | Anything else | Nothing |

A transaction **nobody has paid yet** answers `actionCode` `3` with
`responseCode` `-24` ("Transaction context mismatch"). It looks like an error
and is not one: it stays `PENDING`, and so does one Nass is still processing
(`-31`, `-39`, `-40`, ...). An unpaid transaction never turns into a failure
on its own when it expires; after `expiresAt` it simply cannot be paid.

## Tracking the status

Nass posts the result to the callback, but the callback alone is not enough:
it can be late, or never reach a server that is not public (local
development). Combine it with polling:

1. Pass a callback URL when creating the transaction (the package always does).
2. While the customer is on Nass's page, poll the status **every 5 seconds**.
3. Stop when the payment is paid or failed, or once `expiresAt` has passed.

Every status read goes back to Nass, so polling and the callback can run at
the same time; the receipt is completed and the hook runs exactly once
either way (the receipt row is locked while it is completed).

## Python API

### `nass_payment.service` (needs Django)

```python
from nass_payment import service

# Create a Nass transaction for a pending receipt.
# Sets receipt.<ref field> = orderId and receipt.<status field> = False.
payment = service.start_payment(
    receipt,
    callback_url='https://shop.example/api/nass/callback/',
    return_url='https://shop.example/payment/return',
    amount=None,        # default: receipt.amount
    description=None,   # default: "Payment <invoice_no>" or "Payment #<pk>"
)
# payment == {'url': 'https://3dsecure.nass.iq/gateway?token=...',
#             'orderId': '...', 'expiresAt': '<ISO time>',
#             'pSign': ..., 'transactionParams': {...}}

# Re-read the status from Nass and apply it; returns Nass's status answer.
status = service.sync_status(receipt)
# status == {'actionCode': '0', 'responseCode': '00', 'statusMsg': 'Approved',
#            'rrn': ..., 'intRef': ..., 'card': '5299XXXXXXXX9709', ...}

# Apply a status you already hold; returns 'PAID', 'FAILED' or 'PENDING'.
service.apply_status(receipt, status)

# What the callback view calls; returns the receipt, or None if unknown.
service.handle_callback(order_id, claimed=None)
```

Every Nass failure raises a `NassError` (below), including an answer to
create-transaction that carries no `url`. Calling `sync_status` on a receipt
with no reference raises `ValueError`: that is a bug in the caller, not
something Nass said.

### `nass_payment.client.NassPaymentClient` (no Django)

```python
from nass_payment.conf import get_client   # the shared client from settings
client = get_client()

# or standalone, anywhere:
from nass_payment.client import NassPaymentClient, transaction_state
client = NassPaymentClient('https://uat-gateway.nass.iq:9746', 'username', 'password')

created = client.create_transaction(
    '11791458071', 15000,
    return_url='https://shop.example/payment/return',
    callback_url='https://shop.example/api/nass/callback/',
    description='Order 12',
)
created['url']                     # send the customer here
status = client.check_status('11791458071')
transaction_state(status)          # 'PAID' | 'FAILED' | 'PENDING'
```

The token is cached for as long as the JWT Nass issues says (10 minutes),
refreshed 30 seconds early, and fetched again once on any `401`. Fetching it
is guarded by a lock, so one client can be shared between threads.

An `orderId` that is not 9 to 31 digits raises `ValueError` before anything
is sent. Amounts go out as whole dinars, rounded half up, and descriptions
are cut to 119 characters, as Nass asks.

### Exceptions

| Exception | Raised when |
| --- | --- |
| `NassError` | Base class; catch this one |
| `NassAuthError` | Missing credentials, or the login failed or was refused |
| `NassAPIError` | A call failed or Nass answered `4xx`/`5xx` (or `success: false`); carries `status_code` and `payload`, and its message ends with Nass's reason |

## Nass API contract

| Call | Request |
| --- | --- |
| Login | `POST {base}/auth/merchant/login`, JSON `username`, `password` |
| Create | `POST {base}/transaction`, JSON `orderId`, `orderDesc`, `amount`, `currency` (`"368"`), `transactionType` (`"1"`), `backRef`, `notifyUrl` |
| Status | `GET {base}/transaction/{orderId}/checkStatus` |
| Callback | Nass `POST`s JSON to `notifyUrl`: `terminal`, `actionCode`, `responseCode`, `card`, `amount`, `currency`, `tranDate`, `rrn`, `intRef`, `transactionOrigin`, `orderId`, `timestamp` |

Every call but the login carries `Authorization: Bearer <access_token>`.
What the UAT gateway actually does, where the manual is silent or differs:

- Every answer, the login's included, is wrapped as
  `{"success", "code", "status_code", "data"}`; the token is
  `data.access_token`, a JWT that lives 10 minutes.
- Create answers `201` with `data.url`, a plain link to the card page
  (`https://3dsecure.nass.iq/gateway?token=...`): no form needs posting.
  `data.transactionParams` echoes what Nass signed.
- `orderId` must be digits only (`422 orderId must contain only numbers`) and
  never reused (`409 Transaction with this Order ID already exists`).
- `backRef` and `notifyUrl` must be URLs with a real host or an IP address;
  `localhost` is refused (`422`).
- An unpaid transaction's status is `actionCode` `3`, `responseCode` `-24`;
  an unknown order answers `404 Transaction not found.`
- A refusal's reason is `data.message`, or `data.<field>: [problems]`; a bad
  token answers `401 {"message": "Unauthorized"}`.
- UAT throttles: a handful of create calls in a few seconds answers
  `429 Too Many Requests`.

## Logging

Everything is logged to the **`nass_payment`** logger: transactions created,
completed or failed, every callback (what it claimed and what Nass
confirmed), and hook failures. Pick one of:

- **Let the package write a file**, wherever the project keeps its logs:

  ```python
  NASS_LOG_FILE = BASE_DIR / 'logs' / 'nass_payment.log'   # a logs/ folder
  NASS_LOG_FILE = 'nass_payment.log'                        # the project root
  NASS_LOG_LEVEL = 'INFO'                                   # default
  ```

  A relative path starts at `BASE_DIR`, a missing folder is created, and the
  file is only created once something is logged. Those records then go to
  that file only, not to the root logger.

- **Route the logger yourself** in `LOGGING`, e.g. into a file the project
  already has:

  ```python
  LOGGING['loggers']['nass_payment'] = {
      'handlers': ['file'], 'level': 'INFO', 'propagate': False,
  }
  ```

- **Neither**: no file is written. Python drops `INFO` records and prints
  warnings and errors to stderr, as for any logger nobody configured.

## Testing

### In your project

Mock Nass at the client the service uses:

```python
from unittest.mock import MagicMock, patch

nass = MagicMock()
nass.create_transaction.return_value = {'url': 'https://3dsecure.example/gateway?token=T'}
nass.check_status.return_value = {'actionCode': '0', 'responseCode': '00', 'rrn': '1'}
with patch('nass_payment.service.get_client', return_value=nass):
    ...  # start / sync / callback
```

An unpaid transaction is
`{'actionCode': '3', 'responseCode': '-24', 'statusMsg': 'Transaction context mismatch'}`.

### The package's own tests

```bash
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

The tests never call Nass. GitHub Actions runs them on Python 3.10 with the
oldest supported dependencies and on Python 3.13 with the newest.

### Against Nass's UAT environment

From a project that has UAT credentials in its `.env`:

```bash
uv run python manage.py shell -c "
import time
from nass_payment.conf import get_client
from nass_payment.client import transaction_state
c = get_client()
order_id = f'9{int(time.time())}'
p = c.create_transaction(order_id, 1000, return_url='https://example.com/return',
                         callback_url='https://example.com/api/nass/callback/')
print(p['url'])                                          # open it, pay with a UAT test card
print(transaction_state(c.check_status(order_id)))       # PENDING, then PAID
"
```

Add `--with-editable ../nass-payment` after `uv run` to try an unreleased
change (see [UPDATING.md](UPDATING.md)).

## Updating and releasing

How to release a new version and move a project onto it:
[UPDATING.md](UPDATING.md). Every release is listed in
[CHANGELOG.md](CHANGELOG.md).

## Known limits

- Nass's merchant API has no cancel, void or refund call; refunds go through
  Nass.
- One currency: the Iraqi dinar (`368`), in whole dinars.
- The status is read from Nass on every poll and callback; the package keeps
  only the last verified transaction (in `NASS_RECEIPT_DETAILS_FIELD`), not a
  history.
- Nass's callback carries no signature, so it is only ever a hint to re-read
  the status.
- Which negative codes are final failures is the package's reading of the
  e-Gateway codes in Nass's manual; any other code stays `PENDING`, so the
  worst a wrong reading does is leave a failed payment pending.
