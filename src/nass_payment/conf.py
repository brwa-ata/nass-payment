"""Configuration bridge between Django settings and the Nass client.

A project must set the three credentials (``NASS_BASE_URL``,
``NASS_USERNAME``, ``NASS_PASSWORD``) and ``NASS_RECEIPT_MODEL``, the model
that records its payments. Everything else has a default.

The receipt binding is what makes the app reusable: point
``NASS_RECEIPT_MODEL`` / ``NASS_RECEIPT_REF_FIELD`` /
``NASS_RECEIPT_STATUS_FIELD`` at whatever model and fields a project uses.
"""

from dataclasses import dataclass, field

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .client import NassPaymentClient


@dataclass
class NassConf:
    base_url: str
    username: str
    password: str
    timeout: int = 30
    callback_url: str = ''
    return_url: str = ''
    # starts every orderId: <prefix><receipt pk><unix time>, digits only
    order_prefix: str = ''
    # minutes a transaction stays payable; Nass's e-Gateway refuses a
    # transaction an hour after it was created
    payment_lifetime: int = 60
    # receipt binding
    receipt_model: str = ''
    ref_field: str = 'ref_no'
    status_field: str = 'is_completed'
    # optional JSON field the verified transaction is kept in, under "nass"
    details_field: str = ''
    # optional dotted paths to callables(receipt, transaction)
    on_completed: str = ''
    on_failed: str = ''
    # optional file the package writes its log to, and from which level
    log_file: str = ''
    log_level: str = 'INFO'
    # who may call the payments/ endpoints (the callback is always public)
    view_permission_classes: tuple = ('rest_framework.permissions.IsAdminUser',)
    _client: NassPaymentClient = field(default=None, repr=False, compare=False)


_conf = None


def get_conf():
    """Build (once) and return the Nass configuration from Django settings."""
    global _conf
    if _conf is None:
        order_prefix = str(getattr(settings, 'NASS_ORDER_PREFIX', '') or '')
        if order_prefix and not order_prefix.isdigit():
            raise ImproperlyConfigured(
                'NASS_ORDER_PREFIX must be digits: Nass takes numeric orderIds only.'
            )

        _conf = NassConf(
            base_url=getattr(settings, 'NASS_BASE_URL', ''),
            username=getattr(settings, 'NASS_USERNAME', ''),
            password=getattr(settings, 'NASS_PASSWORD', ''),
            timeout=int(getattr(settings, 'NASS_TIMEOUT', 30)),
            callback_url=getattr(settings, 'NASS_CALLBACK_URL', ''),
            return_url=getattr(settings, 'NASS_RETURN_URL', ''),
            order_prefix=order_prefix,
            payment_lifetime=int(getattr(settings, 'NASS_PAYMENT_LIFETIME', 60)),
            receipt_model=getattr(settings, 'NASS_RECEIPT_MODEL', ''),
            ref_field=getattr(settings, 'NASS_RECEIPT_REF_FIELD', 'ref_no'),
            status_field=getattr(settings, 'NASS_RECEIPT_STATUS_FIELD', 'is_completed'),
            details_field=getattr(settings, 'NASS_RECEIPT_DETAILS_FIELD', ''),
            on_completed=getattr(settings, 'NASS_ON_PAYMENT_COMPLETED', ''),
            on_failed=getattr(settings, 'NASS_ON_PAYMENT_FAILED', ''),
            log_file=str(getattr(settings, 'NASS_LOG_FILE', '') or ''),
            log_level=getattr(settings, 'NASS_LOG_LEVEL', 'INFO'),
            view_permission_classes=tuple(
                getattr(
                    settings,
                    'NASS_VIEW_PERMISSION_CLASSES',
                    ('rest_framework.permissions.IsAdminUser',),
                )
            ),
        )
    return _conf


def get_client():
    """Return a process-wide Nass client (the token cache is shared)."""
    conf = get_conf()
    if conf._client is None:
        conf._client = NassPaymentClient(
            base_url=conf.base_url,
            username=conf.username,
            password=conf.password,
            timeout=conf.timeout,
        )
    return conf._client


def reset():
    """Drop cached config/client. Useful in tests and after settings override."""
    global _conf
    _conf = None
