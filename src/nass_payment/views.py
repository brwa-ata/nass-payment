"""HTTP surface for Nass payments.

* ``POST nass/payments/``            -> start a Nass payment for an existing receipt
* ``GET  nass/payments/<pk>/status`` -> re-sync + return the payment status
* ``POST nass/callback/``            -> Nass's ``notifyUrl`` (public, re-verified)

The payment endpoints operate on a receipt that the project has already
created (pending, ``is_completed=False``). Creating that receipt stays in the
project so this app never needs to know how receipts are built. They look a
receipt up by id alone, so they are for staff: ``NASS_VIEW_PERMISSION_CLASSES``
decides who may call them (``IsAdminUser`` unless the project says otherwise).
"""

from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.module_loading import import_string
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import service
from .client import transaction_state
from .conf import get_conf
from .exceptions import NassError
from .logs import logger


def _callback_url(request):
    conf = get_conf()
    if conf.callback_url:
        return conf.callback_url
    return request.build_absolute_uri(reverse('nass-callback'))


class ProjectPermissionsMixin:
    """Take the permission classes from ``NASS_VIEW_PERMISSION_CLASSES``."""

    def get_permissions(self):
        return [import_string(path)() for path in get_conf().view_permission_classes]


class NassStartPaymentView(ProjectPermissionsMixin, APIView):
    """Start a Nass payment for an existing, pending receipt."""

    def post(self, request):
        receipt_id = request.data.get('receipt_id')
        if not receipt_id:
            return Response({'message': 'receipt_id is required'}, status=422)

        return_url = request.data.get('return_url') or get_conf().return_url
        if not return_url:
            return Response({'message': 'return_url is required'}, status=422)

        receipt = get_object_or_404(service.get_receipt_model(), pk=receipt_id)
        try:
            payment = service.start_payment(
                receipt,
                callback_url=_callback_url(request),
                return_url=return_url,
                description=request.data.get('description'),
            )
        except NassError as exc:
            logger.error('Nass start payment failed: %s', exc)
            return Response({'message': str(exc)}, status=502)

        return Response(
            {
                'orderId': payment['orderId'],
                'url': payment['url'],
                'expiresAt': payment['expiresAt'],
            },
            status=status.HTTP_201_CREATED,
        )


class NassPaymentStatusView(ProjectPermissionsMixin, APIView):
    """Re-sync a receipt's payment against Nass and return the status."""

    def get(self, request, pk):
        conf = get_conf()
        receipt = get_object_or_404(service.get_receipt_model(), pk=pk)
        try:
            payload = service.sync_status(receipt)
        except NassError as exc:
            return Response({'message': str(exc)}, status=502)

        return Response(
            {
                'state': transaction_state(payload),
                'is_completed': getattr(receipt, conf.status_field),
                'transaction': payload,
            }
        )


class NassCallbackView(APIView):
    """Public endpoint Nass posts a transaction's result to (``notifyUrl``)."""

    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request):
        data = request.data
        # Nass sends `orderId`; the raw e-Gateway field is `ORDER`
        order_id = data.get('orderId') or data.get('ORDER')
        if not order_id:
            logger.warning('Nass callback without an orderId: %s', dict(data))
            return Response({'message': 'missing orderId'}, status=400)

        try:
            service.handle_callback(str(order_id), claimed=data)
        except NassError as exc:
            # tell Nass to retry later rather than swallow a transient failure
            logger.error('Nass callback processing failed: %s', exc)
            return Response({'message': str(exc)}, status=503)

        return Response({'message': 'ok'})
