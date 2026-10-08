"""Bridge between the Nass client and a project's receipt model.

The only assumptions about the receipt model are that it has:

* a *reference* field to store our ``orderId`` (default ``ref_no``)
* a boolean *status* field that gates whether the receipt counts
  (default ``is_completed``) -- ``False`` until Nass confirms the payment.

Optionally, a JSON field keeps the verified transaction
(``NASS_RECEIPT_DETAILS_FIELD``): Nass forgets an ``orderId`` after 7 days,
and the transaction's ``rrn`` is the bank's lasting reference for it.

The model and the field names are all configurable (see
:mod:`nass_payment.conf`), so this file never imports the project's model.

Nothing Nass *sends* us is trusted: the callback only names an order, and the
status is always read back through ``checkStatus`` before a receipt moves.
"""

import time
from datetime import timedelta

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.utils import timezone
from django.utils.module_loading import import_string

from .client import FAILED, PAID, transaction_state
from .conf import get_client, get_conf
from .exceptions import NassAPIError
from .logs import logger


def get_receipt_model():
    model = get_conf().receipt_model
    if not model:
        raise ImproperlyConfigured(
            'Set NASS_RECEIPT_MODEL to the model that records payments, '
            "e.g. 'shop.Receipt'."
        )
    return apps.get_model(model)


def new_order_id(receipt):
    """``<prefix><receipt pk><unix time>``: digits only, as Nass wants.

    The pk makes it unique here; the time keeps it unique across the
    databases (local, staging, live) that share one UAT merchant, and lets a
    receipt be paid again after a failed attempt.
    """
    return f'{get_conf().order_prefix}{receipt.pk}{int(time.time())}'


def start_payment(receipt, *, callback_url, return_url, amount=None, description=None):
    """Create a Nass transaction for ``receipt`` and store its ``orderId``.

    Marks the receipt pending (status field -> ``False``) and returns Nass's
    answer -- ``url``, the 3-D Secure page to send the customer to, plus
    ``pSign`` and ``transactionParams`` -- with two keys of ours added:
    ``orderId``, and ``expiresAt``, the ISO time after which the transaction
    can no longer be paid.
    """
    conf = get_conf()
    order_id = new_order_id(receipt)

    response = get_client().create_transaction(
        order_id,
        amount if amount is not None else receipt.amount,
        return_url=return_url,
        callback_url=callback_url,
        description=description or _default_description(receipt),
    )

    if not response.get('url'):
        # a NassError, so the caller's own `except NassError` refuses it
        raise NassAPIError(
            f'Nass create-transaction returned no url: {response}',
            payload=response,
        )

    setattr(receipt, conf.ref_field, order_id)
    setattr(receipt, conf.status_field, False)
    receipt.save(update_fields=[conf.ref_field, conf.status_field])
    logger.info('Nass transaction %s created for receipt %s', order_id, receipt.pk)

    expires_at = timezone.now() + timedelta(minutes=conf.payment_lifetime)
    return {**response, 'orderId': order_id, 'expiresAt': expires_at.isoformat()}


def sync_status(receipt):
    """Re-read the transaction from Nass and apply it to ``receipt``.

    Returns Nass's status answer (``actionCode``, ``responseCode``,
    ``statusMsg``, ``rrn``, ...); ``client.transaction_state()`` reads it.
    """
    order_id = getattr(receipt, get_conf().ref_field)
    if not order_id:
        raise ValueError('Receipt has no Nass orderId to sync.')
    status = get_client().check_status(order_id)
    apply_status(receipt, status)
    return status


@transaction.atomic
def apply_status(receipt, status):
    """Apply a Nass status (a ``checkStatus`` answer) to ``receipt``.

    * paid sets the status field to ``True``, keeps the transaction in the
      details field, and runs the completion hook -- once, on the pending ->
      completed transition. The row is locked first, since the callback and
      a status poll race each other;
    * failed keeps the transaction (on a receipt not completed) and runs the
      failed hook. That hook runs every time the status is applied (a
      callback and a poll can both report it), so write it to be idempotent;
    * pending changes nothing.

    Returns the state: ``PAID``, ``FAILED`` or ``PENDING``.
    """
    conf = get_conf()
    state = transaction_state(status)

    if state == PAID:
        locked = (
            type(receipt).objects.select_for_update().filter(pk=receipt.pk).first()
            or receipt
        )
        if not getattr(locked, conf.status_field):
            setattr(locked, conf.status_field, True)
            fields = [conf.status_field, *_keep_details(locked, status)]
            locked.save(update_fields=fields)
            _run_hook(conf.on_completed, locked, status)
            logger.info(
                'Nass payment %s for receipt %s marked completed (rrn %s)',
                status.get('orderId'),
                receipt.pk,
                status.get('rrn'),
            )
        if locked is not receipt:
            receipt.refresh_from_db(fields=[conf.status_field, *_details_field(conf)])
    elif state == FAILED:
        if not getattr(receipt, conf.status_field):
            fields = _keep_details(receipt, status)
            if fields:
                receipt.save(update_fields=fields)
        _run_hook(conf.on_failed, receipt, status)
        logger.info(
            'Nass payment %s for receipt %s failed: %s %s',
            status.get('orderId'),
            receipt.pk,
            status.get('responseCode'),
            status.get('statusMsg'),
        )
    return state


def handle_callback(order_id, claimed=None):
    """Handle Nass's ``notifyUrl`` call for ``order_id``.

    The callback body is not trusted for the outcome -- the status is always
    re-read from Nass before the receipt is updated. ``claimed`` is the body,
    for the log. Returns the receipt, or ``None`` when no receipt carries that
    order.
    """
    conf = get_conf()
    receipt = get_receipt_model().objects.filter(**{conf.ref_field: order_id}).first()
    if receipt is None:
        logger.warning('Nass callback for unknown order %s', order_id)
        return None

    status = get_client().check_status(order_id)
    claimed = claimed or {}
    logger.info(
        'Nass callback order=%s claimed=%s/%s verified=%s/%s %s',
        order_id,
        claimed.get('actionCode'),
        claimed.get('responseCode'),
        status.get('actionCode'),
        status.get('responseCode'),
        transaction_state(status),
    )
    apply_status(receipt, status)
    return receipt


def _default_description(receipt):
    invoice = getattr(receipt, 'invoice_no', None)
    return f'Payment {invoice}' if invoice else f'Payment #{receipt.pk}'


def _details_field(conf):
    return [conf.details_field] if conf.details_field else []


def _keep_details(receipt, status):
    """Put the transaction under ``"nass"`` in the details field, if one is
    set, leaving the field's other keys alone. Returns the fields changed."""
    field_name = get_conf().details_field
    if not field_name:
        return []

    current = getattr(receipt, field_name, None) or {}
    if not isinstance(current, dict):
        logger.warning(
            'Receipt %s: %s holds no JSON object; the Nass transaction is not kept',
            receipt.pk,
            field_name,
        )
        return []

    setattr(receipt, field_name, {**current, 'nass': dict(status)})
    return [field_name]


def _run_hook(dotted_path, receipt, status):
    if not dotted_path:
        return
    try:
        import_string(dotted_path)(receipt, status)
    except Exception:  # noqa: BLE001 - a hook must never break payment flow
        logger.exception('Nass payment hook %s failed', dotted_path)
