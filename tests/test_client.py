"""The raw client: the token, what is sent to Nass, and reading its answers.

The answers mocked here are the shapes Nass's UAT gateway gave, not the
manual's: every one is wrapped in ``{success, code, status_code, data}``.
"""

import base64
import json
from unittest.mock import MagicMock, patch

import pytest
import requests

from nass_payment import client as nass_client
from nass_payment.client import (
    FAILED,
    PAID,
    PENDING,
    NassPaymentClient,
    transaction_state,
)
from nass_payment.exceptions import NassAPIError, NassAuthError

from .conftest import DECLINED, UNPAID
from .conftest import PAID as PAID_STATUS

LOGIN_URL = 'https://nass.example/auth/merchant/login'
TRANSACTION_URL = 'https://nass.example/transaction'


def jwt(**claims):
    """A token shaped like Nass's (only the claims are ever read)."""

    def part(data):
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip('=')

    return f'{part({"alg": "HS256"})}.{part(claims)}.signature'


def make_client(token='T', **kwargs):
    client = NassPaymentClient(
        'https://nass.example/', 'merchant@example.com', 'secret', **kwargs
    )
    if token:
        client._token = token
        client._token_expiry = 10**12
    return client


def answer(status_code=200, body=None, **data):
    """Nass's wrapper around ``data``, or ``body`` as given."""
    if body is None:
        body = {'success': True, 'code': 0, 'status_code': status_code, 'data': data}
    response = MagicMock(status_code=status_code, content=b'x', text=json.dumps(body))
    response.json.return_value = body
    return response


def refusal(status_code, data):
    return answer(
        status_code,
        body={'success': False, 'code': 1, 'status_code': status_code, 'data': data},
    )


def login(token='T1'):
    return answer(200, access_token=token)


# -- the token ----------------------------------------------------------------


def test_the_login_posts_the_merchant_credentials_as_json():
    with (
        patch('nass_payment.client.requests.post', return_value=login()) as post,
        patch('nass_payment.client.requests.request', return_value=answer()),
    ):
        make_client(token=None).check_status('11791458071')

    assert post.call_args.args == (LOGIN_URL,)
    assert post.call_args.kwargs['json'] == {
        'username': 'merchant@example.com',
        'password': 'secret',
    }


def test_the_token_is_read_from_inside_data():
    with (
        patch('nass_payment.client.requests.post', return_value=login('T1')),
        patch('nass_payment.client.requests.request', return_value=answer()) as req,
    ):
        make_client(token=None).check_status('11791458071')

    assert req.call_args.kwargs['headers']['Authorization'] == 'Bearer T1'


def test_a_token_at_the_top_level_is_read_too():
    """Where Nass's manual puts it."""
    with (
        patch(
            'nass_payment.client.requests.post',
            return_value=answer(body={'access_token': 'TOP'}),
        ),
        patch('nass_payment.client.requests.request', return_value=answer()) as req,
    ):
        make_client(token=None).check_status('11791458071')

    assert req.call_args.kwargs['headers']['Authorization'] == 'Bearer TOP'


def test_the_token_is_reused_until_it_expires():
    with (
        patch('nass_payment.client.requests.post', return_value=login()) as post,
        patch('nass_payment.client.requests.request', return_value=answer()),
    ):
        client = make_client(token=None)
        client.check_status('11791458071')
        client.check_status('11791458071')

    assert post.call_count == 1


@pytest.mark.parametrize(
    ('token', 'lifetime'),
    [
        (jwt(sub=209, iat=1791458059, exp=1791458659), 600),
        ('not-a-jwt', nass_client.DEFAULT_TOKEN_LIFETIME),
        (jwt(sub=209), nass_client.DEFAULT_TOKEN_LIFETIME),
    ],
    ids=['jwt exp - iat', 'opaque', 'no exp'],
)
def test_the_token_lives_as_long_as_it_says(token, lifetime):
    with patch('nass_payment.client.time.monotonic', return_value=1000.0):
        client = make_client(token=None)
        with patch('nass_payment.client.requests.post', return_value=login(token)):
            client._get_token()

    assert client._token_expiry == 1000.0 + lifetime


def test_an_expired_token_is_fetched_again():
    with (
        patch('nass_payment.client.requests.post', return_value=login('T2')) as post,
        patch('nass_payment.client.requests.request', return_value=answer()),
    ):
        client = make_client(token='OLD')
        client._token_expiry = 0
        client.check_status('11791458071')

    assert post.call_count == 1
    assert client._token == 'T2'


def test_a_401_logs_in_again_once_and_retries():
    unauthorized = answer(401, body={'message': 'Unauthorized', 'statusCode': 401})
    with (
        patch('nass_payment.client.requests.post', return_value=login('FRESH')),
        patch(
            'nass_payment.client.requests.request',
            side_effect=[unauthorized, answer(**PAID_STATUS)],
        ) as req,
    ):
        result = make_client(token='STALE').check_status('11791458071')

    assert result == PAID_STATUS
    assert req.call_count == 2
    assert req.call_args.kwargs['headers']['Authorization'] == 'Bearer FRESH'


@pytest.mark.parametrize(
    ('login_answer', 'reason'),
    [
        (refusal(401, {'message': 'Password is incorrect.'}), 'Password is incorrect.'),
        (answer(200, something='else'), 'did not contain access_token'),
    ],
    ids=['refused', 'no access_token'],
)
def test_a_bad_login_is_an_auth_error(login_answer, reason):
    with (
        patch('nass_payment.client.requests.post', return_value=login_answer),
        pytest.raises(NassAuthError, match=reason),
    ):
        make_client(token=None).check_status('11791458071')


def test_a_login_network_failure_is_an_auth_error():
    with (
        patch(
            'nass_payment.client.requests.post',
            side_effect=requests.ConnectionError('down'),
        ),
        pytest.raises(NassAuthError),
    ):
        make_client(token=None).check_status('11791458071')


def test_credentials_are_required():
    with pytest.raises(NassAuthError):
        NassPaymentClient('https://nass.example', 'merchant@example.com', '')


# -- creating a transaction ---------------------------------------------------


def test_a_transaction_is_created_with_nass_body():
    created = answer(201, url='https://3dsecure.nass.example/gateway?token=X')
    with patch('nass_payment.client.requests.request', return_value=created) as req:
        result = make_client().create_transaction(
            '11791458071',
            25000,
            return_url='https://shop.example/payment/return',
            callback_url='https://shop.example/api/nass/callback/',
            description='Payment RV7',
        )

    assert result == {'url': 'https://3dsecure.nass.example/gateway?token=X'}
    assert req.call_args.args == ('POST', TRANSACTION_URL)
    assert req.call_args.kwargs['json'] == {
        'orderId': '11791458071',
        'orderDesc': 'Payment RV7',
        'amount': 25000,
        # fixed by Nass: Iraqi dinar, a purchase
        'currency': '368',
        'transactionType': '1',
        'backRef': 'https://shop.example/payment/return',
        'notifyUrl': 'https://shop.example/api/nass/callback/',
    }


@pytest.mark.parametrize(
    ('amount', 'sent'),
    [('15000.50', 15001), (15000.49, 15000), (2.5, 3), (1000, 1000)],
)
def test_amounts_are_whole_dinars_rounded_half_up(amount, sent):
    with patch('nass_payment.client.requests.request', return_value=answer(201)) as req:
        make_client().create_transaction(
            '11791458071', amount, return_url='r', callback_url='c'
        )

    assert req.call_args.kwargs['json']['amount'] == sent


def test_the_description_is_kept_under_120_characters():
    with patch('nass_payment.client.requests.request', return_value=answer(201)) as req:
        make_client().create_transaction(
            '11791458071', 1000, return_url='r', callback_url='c', description='x' * 200
        )

    assert len(req.call_args.kwargs['json']['orderDesc']) == 119


def test_a_missing_description_names_the_order():
    with patch('nass_payment.client.requests.request', return_value=answer(201)) as req:
        make_client().create_transaction(
            '11791458071', 1000, return_url='r', callback_url='c'
        )

    assert req.call_args.kwargs['json']['orderDesc'] == 'Order 11791458071'


@pytest.mark.parametrize('order_id', ['12345678', 'LAV11791458071', '1' * 32, ''])
def test_an_order_id_nass_would_refuse_is_never_sent(order_id):
    with (
        patch('nass_payment.client.requests.request') as req,
        pytest.raises(ValueError),
    ):
        make_client().create_transaction(
            order_id, 1000, return_url='r', callback_url='c'
        )

    req.assert_not_called()


# -- reading the status --------------------------------------------------------


def test_the_status_is_read_from_its_order():
    with patch(
        'nass_payment.client.requests.request', return_value=answer(**UNPAID)
    ) as req:
        result = make_client().check_status('11791458071')

    assert result == UNPAID
    assert req.call_args.args == (
        'GET',
        'https://nass.example/transaction/11791458071/checkStatus',
    )


@pytest.mark.parametrize(
    ('status', 'state'),
    [
        (PAID_STATUS, PAID),
        (UNPAID, PENDING),
        (DECLINED, FAILED),
        ({'actionCode': '3', 'responseCode': '-25'}, FAILED),  # cancelled
        ({'actionCode': '3', 'responseCode': '-19'}, FAILED),  # 3-D Secure failed
        ({'actionCode': '3', 'responseCode': '-39'}, PENDING),  # being confirmed
        ({'actionCode': '0', 'responseCode': '-24'}, PENDING),  # not both approved
        ({'actionCode': 0, 'responseCode': '00'}, PAID),  # codes as numbers
        ({}, PENDING),
        (None, PENDING),
    ],
)
def test_a_status_is_read_as_paid_failed_or_pending(status, state):
    assert transaction_state(status) == state


# -- refusals ------------------------------------------------------------------


@pytest.mark.parametrize(
    ('nass_answer', 'reason'),
    [
        (
            refusal(409, {'message': 'Transaction with this Order ID already exists'}),
            'Transaction with this Order ID already exists',
        ),
        (
            refusal(
                422,
                {
                    'backRef': ['backRef must be a URL address'],
                    'notifyUrl': ['notifyUrl must be a URL address'],
                },
            ),
            'backRef must be a URL address; notifyUrl must be a URL address',
        ),
        (refusal(404, 'Transaction not found.'), 'Transaction not found.'),
        (
            answer(429, body={'statusCode': 429, 'message': 'Too Many Requests'}),
            'Too Many Requests',
        ),
    ],
    ids=['duplicate order', 'invalid fields', 'unknown order', 'throttled'],
)
def test_a_refusal_raises_with_its_reason_status_and_body(nass_answer, reason):
    with (
        patch('nass_payment.client.requests.request', return_value=nass_answer),
        pytest.raises(NassAPIError) as raised,
    ):
        make_client().check_status('11791458071')

    assert str(raised.value).endswith(reason)
    assert raised.value.status_code == nass_answer.status_code
    assert raised.value.payload == nass_answer.json.return_value


def test_success_false_is_a_refusal_whatever_the_http_status():
    with (
        patch(
            'nass_payment.client.requests.request',
            return_value=answer(200, body={'success': False, 'data': 'nope'}),
        ),
        pytest.raises(NassAPIError, match='nope'),
    ):
        make_client().check_status('11791458071')


def test_an_answer_that_is_no_object_is_a_nass_error():
    with (
        patch(
            'nass_payment.client.requests.request',
            return_value=answer(200, body={'success': True, 'data': 'ok'}),
        ),
        pytest.raises(NassAPIError),
    ):
        make_client().check_status('11791458071')


def test_a_network_failure_is_a_nass_error():
    with (
        patch(
            'nass_payment.client.requests.request',
            side_effect=requests.ConnectionError('down'),
        ),
        pytest.raises(NassAPIError),
    ):
        make_client().check_status('11791458071')
