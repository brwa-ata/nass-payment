"""Standalone client for the Nass Merchant Payment Gateway API.

This module has **no Django dependency** on purpose: it only needs ``requests``.
Copy it into any project and drive it with plain config values.

Contract (verified against Nass's UAT gateway; where Nass's integration
manual, V2.3, says otherwise, the gateway's actual answer is what is coded):

* Login (the token is a JWT that lives 10 minutes)::

      POST {base_url}/auth/merchant/login    {"username", "password"}

* Create a transaction, then send the customer to ``data.url``, Nass's
  3-D Secure card page (``https://3dsecure.nass.iq/gateway?token=...``)::

      POST {base_url}/transaction

* Check its status (within 24 hours of creating it)::

      GET  {base_url}/transaction/{orderId}/checkStatus

Every answer is wrapped as ``{"success", "code", "status_code", "data"}`` --
the login's too, although the manual shows ``access_token`` at the top level --
and the client returns ``data``. A refusal carries its reason in ``data``,
either ``{"message": ...}`` or ``{"<field>": ["<problem>", ...]}``.

A status is a pair of codes: ``actionCode`` ``"0"`` with ``responseCode``
``"00"`` is paid. A transaction nobody has paid *yet* answers ``actionCode``
``"3"`` with ``responseCode`` ``"-24"`` ("Transaction context mismatch"), so a
negative code is not a failure by itself; :func:`transaction_state` reads it.
"""

import base64
import json
import re
import threading
import time
from decimal import ROUND_HALF_UP, Decimal

import requests

from .exceptions import NassAPIError, NassAuthError

DEFAULT_TIMEOUT = 30
# ISO 4217 numeric code of the Iraqi dinar, the only currency Nass takes
CURRENCY_IQD = '368'
# a purchase; the only transaction type the merchant API offers
TRANSACTION_PURCHASE = '1'
# Nass: "must be less than 120 characters"
MAX_DESCRIPTION = 119
# Nass: digits only, "more than 8 and less than 32 digits long"
ORDER_ID_PATTERN = re.compile(r'\d{9,31}')

# how long to trust a token whose lifetime cannot be read from it
DEFAULT_TOKEN_LIFETIME = 600
# log in again a little before the token actually expires
TOKEN_EXPIRY_SKEW = 30

# what transaction_state() makes of a status
PAID = 'PAID'
FAILED = 'FAILED'
PENDING = 'PENDING'

ACTION_APPROVED = '0'
ACTION_DECLINED = '2'
RESPONSE_APPROVED = '00'
# e-Gateway response codes that end a transaction unpaid. Every other code
# (-24 for one nobody has paid yet, -31/-39/-40 for one being processed, ...)
# leaves it pending.
FAILED_RESPONSE_CODES = frozenset(
    {
        '-19',  # 3-D Secure authentication failed
        '-22',  # invalid authentication information
        '-25',  # cancelled by the customer
        '-30',  # declined as fraud
        '-32',  # repeated declined transaction
    }
)


class NassPaymentClient:
    """Thin, reusable wrapper around the Nass merchant endpoints.

    The access token is cached in-memory for as long as its own ``exp`` says
    (and fetched again once on any ``401``).
    """

    def __init__(self, base_url, username, password, *, timeout=DEFAULT_TIMEOUT):
        if not base_url or not username or not password:
            raise NassAuthError('Nass client requires base_url, username and password.')
        self.base_url = base_url.rstrip('/')
        self.username = username
        self.password = password
        self.timeout = timeout

        self._token = None
        self._token_expiry = 0.0
        self._lock = threading.Lock()

    # -- urls ---------------------------------------------------------------
    @property
    def login_url(self):
        return f'{self.base_url}/auth/merchant/login'

    def _url(self, *parts):
        return '/'.join([self.base_url, *(str(p).strip('/') for p in parts)])

    # -- auth ---------------------------------------------------------------
    def _fetch_token(self):
        try:
            resp = requests.post(
                self.login_url,
                json={'username': self.username, 'password': self.password},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise NassAuthError(f'Nass login request failed: {exc}') from exc

        body = _safe_json(resp)
        if resp.status_code >= 400 or body.get('success') is False:
            raise NassAuthError(
                f'Nass login returned {resp.status_code}: {_reason(body, resp)}'
            )

        data = _unwrap(body)
        token = (data.get('access_token') if isinstance(data, dict) else None) or (
            body.get('access_token')
        )
        if not token:
            raise NassAuthError('Nass login answer did not contain access_token.')

        self._token = token
        self._token_expiry = time.monotonic() + _token_lifetime(token)
        return token

    def _get_token(self, force=False):
        with self._lock:
            valid = (
                self._token
                and time.monotonic() < self._token_expiry - TOKEN_EXPIRY_SKEW
            )
            if force or not valid:
                return self._fetch_token()
            return self._token

    # -- requests -----------------------------------------------------------
    def _request(self, method, url, *, json=None):
        headers = {'Authorization': f'Bearer {self._get_token()}'}

        try:
            resp = requests.request(
                method, url, json=json, headers=headers, timeout=self.timeout
            )
            # the token may have expired between calls -> log in once more
            if resp.status_code == 401:
                headers['Authorization'] = f'Bearer {self._get_token(force=True)}'
                resp = requests.request(
                    method, url, json=json, headers=headers, timeout=self.timeout
                )
        except requests.RequestException as exc:
            raise NassAPIError(f'Nass request to {url} failed: {exc}') from exc

        body = _safe_json(resp)
        if resp.status_code >= 400 or body.get('success') is False:
            raise NassAPIError(
                f'Nass {method} {url} returned {resp.status_code}: '
                f'{_reason(body, resp)}',
                status_code=resp.status_code,
                payload=body,
            )

        return _unwrap(body)

    # -- public api ---------------------------------------------------------
    def create_transaction(
        self, order_id, amount, *, return_url, callback_url, description=''
    ):
        """Create a purchase and return Nass's answer.

        ``order_id`` is ours: 9 to 31 digits, never used before. ``return_url``
        is where Nass's page sends the customer afterwards (Nass's
        ``backRef``) and ``callback_url`` where Nass posts the result
        (``notifyUrl``); Nass refuses both unless they are URLs with a real
        host or an IP address, so not ``localhost``.

        Answer keys: ``url`` (the 3-D Secure page to send the customer to),
        ``pSign`` and ``transactionParams``.
        """
        order_id = str(order_id)
        if not ORDER_ID_PATTERN.fullmatch(order_id):
            raise ValueError(f'A Nass orderId is 9 to 31 digits, not {order_id!r}.')

        body = {
            'orderId': order_id,
            'orderDesc': (description or f'Order {order_id}')[:MAX_DESCRIPTION],
            'amount': _format_amount(amount),
            'currency': CURRENCY_IQD,
            'transactionType': TRANSACTION_PURCHASE,
            'backRef': return_url,
            'notifyUrl': callback_url,
        }
        return _answer(self._request('POST', self._url('transaction'), json=body))

    def check_status(self, order_id):
        """Return the transaction's status (``actionCode``, ``responseCode``,
        ``statusMsg``, and once paid ``rrn``, ``intRef``, ``card``, ...).

        Nass answers ``404`` for an order it does not know.
        """
        return _answer(
            self._request('GET', self._url('transaction', order_id, 'checkStatus'))
        )


def transaction_state(data):
    """``PAID``, ``FAILED`` or ``PENDING`` for a status or callback body."""
    data = data or {}
    action = str(data.get('actionCode', '')).strip()
    code = str(data.get('responseCode', '')).strip()

    if action == ACTION_APPROVED and code == RESPONSE_APPROVED:
        return PAID
    if action == ACTION_DECLINED or code in FAILED_RESPONSE_CODES:
        return FAILED
    return PENDING


def _format_amount(amount):
    """Whole dinars, rounded half up: IQD has no minor units in use."""
    return int(Decimal(str(amount)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _token_lifetime(token):
    """Seconds the JWT says it lives (``exp - iat``), or the default."""
    try:
        claims = token.split('.')[1]
        claims = json.loads(base64.urlsafe_b64decode(claims + '=' * (-len(claims) % 4)))
        if 'iat' in claims:
            return max(0, int(claims['exp']) - int(claims['iat']))
        return max(0, int(claims['exp']) - int(time.time()))
    except (IndexError, KeyError, TypeError, ValueError):
        return DEFAULT_TOKEN_LIFETIME


def _safe_json(resp):
    if not resp.content:
        return {}
    try:
        body = resp.json()
    except ValueError:
        return {'raw': resp.text}
    return body if isinstance(body, dict) else {'data': body}


def _unwrap(body):
    """``data`` out of Nass's ``{success, code, status_code, data}`` wrapper."""
    return body['data'] if 'success' in body and 'data' in body else body


def _answer(data):
    """An answer is a JSON object; anything else is no answer at all."""
    if not isinstance(data, dict):
        raise NassAPIError(f'Nass answered no JSON object: {data!r}', payload=data)
    return data


def _reason(body, resp):
    """The readable part of a refusal, whichever shape Nass gave it."""
    data = body.get('data', body)
    if isinstance(data, dict):
        if data.get('message'):
            return str(data['message'])
        problems = [
            str(problem)
            for problems in data.values()
            for problem in (problems if isinstance(problems, list) else [problems])
            if problem
        ]
        if problems:
            return '; '.join(problems)
    if isinstance(data, str) and data:
        return data
    return resp.text or str(resp.status_code)
