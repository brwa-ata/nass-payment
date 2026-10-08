"""The service: a receipt goes pending, and only Nass's answer completes it."""

from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone

from nass_payment import conf, service
from nass_payment.client import FAILED, PAID, PENDING
from nass_payment.exceptions import NassAPIError, NassError

from . import hooks
from .conftest import DECLINED, UNPAID
from .conftest import PAID as PAID_STATUS
from .testapp.models import Receipt

pytestmark = pytest.mark.django_db

CALLBACK_URL = 'https://shop.example/api/nass/callback/'
RETURN_URL = 'https://shop.example/payment/return'


def start(receipt, **kwargs):
    return service.start_payment(
        receipt, callback_url=CALLBACK_URL, return_url=RETURN_URL, **kwargs
    )


def completed(receipt):
    receipt.refresh_from_db()
    return receipt.is_completed


def test_starting_a_payment_leaves_the_receipt_pending(nass):
    receipt = Receipt.objects.create(
        amount=Decimal(25000), invoice_no='RV7', is_completed=True
    )

    with patch('nass_payment.service.time.time', return_value=1791458071.6):
        payment = start(receipt)

    receipt.refresh_from_db()
    assert payment['url'] == 'https://3dsecure.nass.example/gateway?token=T0K3N'
    assert payment['orderId'] == f'{receipt.pk}1791458071'
    assert receipt.ref_no == payment['orderId']
    assert receipt.is_completed is False
    nass.create_transaction.assert_called_once_with(
        payment['orderId'],
        Decimal(25000),
        return_url=RETURN_URL,
        callback_url=CALLBACK_URL,
        description='Payment RV7',
    )


def test_the_payment_says_when_it_stops_being_payable(nass, settings):
    settings.NASS_PAYMENT_LIFETIME = 30
    conf.reset()

    payment = start(Receipt.objects.create(amount=1000))

    expires_at = datetime.fromisoformat(payment['expiresAt'])
    expected = timezone.now() + timedelta(minutes=30)
    assert abs((expires_at - expected).total_seconds()) < 5


def test_the_order_id_takes_the_configured_prefix(nass, settings):
    settings.NASS_ORDER_PREFIX = '77'
    conf.reset()
    receipt = Receipt.objects.create(amount=1000)

    payment = start(receipt)

    assert payment['orderId'].startswith(f'77{receipt.pk}')
    assert payment['orderId'].isdigit()


def test_a_prefix_that_is_not_digits_is_refused(settings):
    """Nass takes numeric orderIds only; better told at once than per payment."""
    settings.NASS_ORDER_PREFIX = 'LAV'
    conf.reset()

    with pytest.raises(ImproperlyConfigured):
        conf.get_conf()


def test_the_amount_and_description_can_differ_from_the_receipt(nass):
    """E.g. the receipt's amount plus a fee the customer pays on top."""
    receipt = Receipt.objects.create(amount=Decimal(10000))

    start(receipt, amount=Decimal(10250), description='x')

    args, kwargs = nass.create_transaction.call_args
    assert args[1] == Decimal(10250)
    assert kwargs['description'] == 'x'


def test_a_receipt_without_an_invoice_is_described_by_its_id(nass):
    receipt = Receipt.objects.create(amount=1)

    start(receipt)

    assert nass.create_transaction.call_args.kwargs['description'] == (
        f'Payment #{receipt.pk}'
    )


def test_an_answer_without_a_url_is_a_nass_error(nass):
    """Callers catch NassError; a malformed answer must not escape as a 500."""
    nass.create_transaction.return_value = {'unexpected': True}
    receipt = Receipt.objects.create(amount=1, is_completed=True)

    with pytest.raises(NassError) as raised:
        start(receipt)

    assert isinstance(raised.value, NassAPIError)
    receipt.refresh_from_db()
    assert receipt.ref_no is None
    assert receipt.is_completed is True


def test_paid_completes_the_receipt_and_runs_the_hook_once(nass):
    receipt = Receipt.objects.create(amount=1, ref_no='11791458071')
    nass.check_status.return_value = dict(PAID_STATUS)

    service.sync_status(receipt)
    service.sync_status(receipt)

    assert completed(receipt)
    assert hooks.calls == [(receipt.pk, '00')]


def test_the_callers_receipt_sees_the_completion(nass):
    receipt = Receipt.objects.create(amount=1, ref_no='11791458071')
    nass.check_status.return_value = dict(PAID_STATUS)

    service.sync_status(receipt)

    assert receipt.is_completed is True


def test_unpaid_leaves_the_receipt_pending(nass):
    """Nass reports -24 for a transaction nobody has paid yet: not a failure."""
    receipt = Receipt.objects.create(amount=1, ref_no='11791458071')

    payload = service.sync_status(receipt)

    assert payload['responseCode'] == '-24'
    assert service.apply_status(receipt, payload) == PENDING
    assert not completed(receipt)
    assert hooks.calls == []
    assert hooks.failed_calls == []


def test_declined_runs_the_failed_hook_and_stays_pending(nass):
    receipt = Receipt.objects.create(amount=1, ref_no='11791458071')
    nass.check_status.return_value = dict(DECLINED)

    service.sync_status(receipt)

    assert not completed(receipt)
    assert hooks.failed_calls == [(receipt.pk, '05')]


def test_apply_status_answers_the_state(nass):
    receipt = Receipt.objects.create(amount=1, ref_no='11791458071')

    assert service.apply_status(receipt, UNPAID) == PENDING
    assert service.apply_status(receipt, DECLINED) == FAILED
    assert service.apply_status(receipt, PAID_STATUS) == PAID


def test_a_receipt_without_a_reference_cannot_be_synced(nass):
    with pytest.raises(ValueError):
        service.sync_status(Receipt.objects.create(amount=1))


def test_a_failing_hook_does_not_undo_the_payment(nass, settings):
    settings.NASS_ON_PAYMENT_COMPLETED = 'tests.hooks.explode'
    conf.reset()
    receipt = Receipt.objects.create(amount=1, ref_no='11791458071')

    service.apply_status(receipt, PAID_STATUS)

    assert completed(receipt)


def test_the_transaction_is_kept_in_the_details_field(nass, settings):
    """Nass forgets an orderId after 7 days; the rrn is what lasts."""
    settings.NASS_RECEIPT_DETAILS_FIELD = 'meta'
    conf.reset()
    receipt = Receipt.objects.create(
        amount=1, ref_no='11791458071', meta={'note': 'kept'}
    )

    service.apply_status(receipt, PAID_STATUS)

    receipt.refresh_from_db()
    assert receipt.meta == {'note': 'kept', 'nass': PAID_STATUS}


def test_a_failure_is_kept_but_never_over_a_completed_receipt(nass, settings):
    settings.NASS_RECEIPT_DETAILS_FIELD = 'meta'
    conf.reset()
    pending = Receipt.objects.create(amount=1, ref_no='1')
    done = Receipt.objects.create(
        amount=1, ref_no='2', is_completed=True, meta={'nass': PAID_STATUS}
    )

    service.apply_status(pending, DECLINED)
    service.apply_status(done, DECLINED)

    pending.refresh_from_db()
    done.refresh_from_db()
    assert pending.meta == {'nass': DECLINED}
    assert done.meta == {'nass': PAID_STATUS}


def test_without_a_details_field_nothing_is_kept(nass):
    receipt = Receipt.objects.create(amount=1, ref_no='11791458071')

    service.apply_status(receipt, PAID_STATUS)

    receipt.refresh_from_db()
    assert receipt.meta is None


def test_the_callback_trusts_nass_not_the_body(nass):
    """A forged "paid" callback does nothing while Nass says unpaid."""
    receipt = Receipt.objects.create(amount=1, ref_no='11791458071')

    service.handle_callback('11791458071', claimed=PAID_STATUS)

    nass.check_status.assert_called_once_with('11791458071')
    assert not completed(receipt)


def test_the_callback_completes_what_nass_confirms(nass):
    receipt = Receipt.objects.create(amount=1, ref_no='11791458071')
    nass.check_status.return_value = dict(PAID_STATUS)

    assert service.handle_callback('11791458071', claimed=PAID_STATUS) == receipt
    assert completed(receipt)


def test_a_callback_for_an_unknown_order_is_ignored(nass):
    assert service.handle_callback('99999999999') is None
    nass.check_status.assert_not_called()


def test_the_receipt_model_and_fields_come_from_settings():
    assert service.get_receipt_model() is Receipt
    assert conf.get_conf().ref_field == 'ref_no'
    assert conf.get_conf().status_field == 'is_completed'


def test_the_receipt_model_must_be_set(settings):
    settings.NASS_RECEIPT_MODEL = ''
    conf.reset()

    with pytest.raises(ImproperlyConfigured):
        service.get_receipt_model()
