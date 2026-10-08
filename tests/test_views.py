"""The HTTP surface: the public callback, and the two payment endpoints."""

import pytest
from django.contrib.auth.models import User
from rest_framework.test import APIClient

from nass_payment import conf
from nass_payment.exceptions import NassAPIError

from .conftest import PAID as PAID_STATUS
from .conftest import UNPAID
from .testapp.models import Receipt

pytestmark = pytest.mark.django_db

CALLBACK_URL = '/api/nass/callback/'
PAYMENTS_URL = '/api/nass/payments/'
RETURN_URL = 'https://shop.example/payment/return'

# Nass's callback body, as its manual documents it
CALLBACK_BODY = {
    'terminal': '00049546',
    'actionCode': '0',
    'responseCode': '00',
    'card': '5299XXXXXXXX9709',
    'amount': '25000',
    'currency': '368',
    'tranDate': '2026.10.08 14:20:11',
    'rrn': '628112345678',
    'intRef': 'E40C8FBE2194C57B',
    'transactionOrigin': 'local',
    'orderId': '11791458071',
    'timestamp': '20261008111431',
}


@pytest.fixture
def receipt():
    return Receipt.objects.create(amount=1, ref_no='11791458071')


@pytest.fixture
def staff():
    client = APIClient()
    client.force_authenticate(User.objects.create_user('staff', is_staff=True))
    return client


@pytest.fixture
def customer():
    client = APIClient()
    client.force_authenticate(User.objects.create_user('customer'))
    return client


def completed(receipt):
    receipt.refresh_from_db()
    return receipt.is_completed


def test_the_callback_is_public_and_re_verified(client, nass, receipt):
    nass.check_status.return_value = dict(PAID_STATUS)

    response = client.post(CALLBACK_URL, CALLBACK_BODY, content_type='application/json')

    assert response.status_code == 200
    nass.check_status.assert_called_once_with('11791458071')
    assert completed(receipt)


def test_a_forged_callback_completes_nothing(client, nass, receipt):
    """The body says approved; Nass, asked, says nobody paid."""
    response = client.post(CALLBACK_URL, CALLBACK_BODY, content_type='application/json')

    assert response.status_code == 200
    assert not completed(receipt)


def test_the_callback_takes_the_raw_gateway_field_too(client, nass, receipt):
    nass.check_status.return_value = dict(PAID_STATUS)

    client.post(CALLBACK_URL, {'ORDER': '11791458071'})

    assert completed(receipt)


def test_a_callback_without_an_order_is_refused(client, nass):
    response = client.post(CALLBACK_URL, {}, content_type='application/json')

    assert response.status_code == 400


def test_a_callback_nass_cannot_confirm_asks_for_a_retry(client, nass, receipt):
    nass.check_status.side_effect = NassAPIError('down')

    response = client.post(CALLBACK_URL, CALLBACK_BODY, content_type='application/json')

    assert response.status_code == 503
    assert not completed(receipt)


def test_the_payment_endpoints_need_a_login(client):
    assert client.post(PAYMENTS_URL, {'receipt_id': 1}).status_code in (401, 403)
    assert client.get(f'{PAYMENTS_URL}1/status/').status_code in (401, 403)


def test_starting_returns_the_page_to_send_the_customer_to(staff, nass):
    receipt = Receipt.objects.create(amount=5000)

    response = staff.post(
        PAYMENTS_URL,
        {'receipt_id': receipt.pk, 'return_url': RETURN_URL},
        format='json',
    )

    assert response.status_code == 201
    data = response.json()
    receipt.refresh_from_db()
    assert set(data) == {'orderId', 'url', 'expiresAt'}
    assert data['orderId'] == receipt.ref_no
    assert data['url'] == 'https://3dsecure.nass.example/gateway?token=T0K3N'
    kwargs = nass.create_transaction.call_args.kwargs
    assert kwargs['return_url'] == RETURN_URL
    # built from the request when NASS_CALLBACK_URL is blank
    assert kwargs['callback_url'] == 'http://testserver/api/nass/callback/'


def test_the_url_settings_win(staff, nass, settings):
    settings.NASS_CALLBACK_URL = 'https://shop.example/api/nass/callback/'
    settings.NASS_RETURN_URL = RETURN_URL
    conf.reset()
    receipt = Receipt.objects.create(amount=5000)

    response = staff.post(PAYMENTS_URL, {'receipt_id': receipt.pk}, format='json')

    assert response.status_code == 201
    kwargs = nass.create_transaction.call_args.kwargs
    assert kwargs['callback_url'] == 'https://shop.example/api/nass/callback/'
    assert kwargs['return_url'] == RETURN_URL


def test_starting_needs_a_receipt_and_a_return_url(staff, nass):
    receipt = Receipt.objects.create(amount=5000)

    def start(**body):
        return staff.post(PAYMENTS_URL, body, format='json').status_code

    assert start(return_url=RETURN_URL) == 422
    assert start(receipt_id=receipt.pk) == 422
    assert start(receipt_id=999, return_url=RETURN_URL) == 404
    nass.create_transaction.assert_not_called()


def test_a_failed_start_is_a_502(staff, nass):
    nass.create_transaction.side_effect = NassAPIError('down')
    receipt = Receipt.objects.create(amount=5000)

    response = staff.post(
        PAYMENTS_URL,
        {'receipt_id': receipt.pk, 'return_url': RETURN_URL},
        format='json',
    )

    assert response.status_code == 502


def test_the_status_endpoint_syncs_and_reports(staff, nass, receipt):
    nass.check_status.return_value = dict(PAID_STATUS)

    response = staff.get(f'{PAYMENTS_URL}{receipt.pk}/status/')

    assert response.status_code == 200
    assert response.json() == {
        'state': 'PAID',
        'is_completed': True,
        'transaction': PAID_STATUS,
    }


def test_the_status_endpoint_reports_a_pending_payment(staff, nass, receipt):
    response = staff.get(f'{PAYMENTS_URL}{receipt.pk}/status/')

    assert response.json() == {
        'state': 'PENDING',
        'is_completed': False,
        'transaction': UNPAID,
    }


def test_a_failed_status_check_is_a_502(staff, nass, receipt):
    nass.check_status.side_effect = NassAPIError('down')

    response = staff.get(f'{PAYMENTS_URL}{receipt.pk}/status/')

    assert response.status_code == 502


def test_the_payment_endpoints_are_for_staff_by_default(customer, nass, receipt):
    """They find a receipt by id alone, so a customer must not reach them."""
    started = customer.post(
        PAYMENTS_URL, {'receipt_id': receipt.pk, 'return_url': RETURN_URL}
    )

    assert started.status_code == 403
    assert customer.get(f'{PAYMENTS_URL}{receipt.pk}/status/').status_code == 403
    nass.create_transaction.assert_not_called()


def test_who_may_call_them_is_a_setting(customer, nass, receipt, settings):
    settings.NASS_VIEW_PERMISSION_CLASSES = [
        'rest_framework.permissions.IsAuthenticated'
    ]
    conf.reset()

    response = customer.get(f'{PAYMENTS_URL}{receipt.pk}/status/')

    assert response.status_code == 200
