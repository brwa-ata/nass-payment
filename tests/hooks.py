"""Hooks the tests point NASS_ON_PAYMENT_COMPLETED / _FAILED at."""

calls = []
failed_calls = []


def record(receipt, status):
    calls.append((receipt.pk, status.get('responseCode')))


def record_failed(receipt, status):
    failed_calls.append((receipt.pk, status.get('responseCode')))


def explode(receipt, status):
    raise RuntimeError('a bug in the project hook')
