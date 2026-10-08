from unittest.mock import MagicMock, patch

import pytest

from nass_payment import conf

from . import hooks

# checkStatus, as Nass answers it for a transaction nobody has paid yet
UNPAID = {
    'terminal': '00049546',
    'actionCode': '3',
    'responseCode': '-24',
    'statusMsg': 'Transaction context mismatch',
    'tr_type': '1',
    'orderId': '11791458071',
    'timestamp': '20261008111431',
}
# and once the customer has paid
PAID = {
    'terminal': '00049546',
    'actionCode': '0',
    'responseCode': '00',
    'statusMsg': 'Approved',
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
DECLINED = {**UNPAID, 'actionCode': '2', 'responseCode': '05', 'statusMsg': 'Declined'}


@pytest.fixture(autouse=True)
def fresh_state():
    """Settings are cached per process; every test starts from its own."""
    conf.reset()
    hooks.calls.clear()
    hooks.failed_calls.clear()
    yield
    conf.reset()


@pytest.fixture
def nass():
    """Nass as the service sees it."""
    client = MagicMock()
    client.create_transaction.return_value = {
        'url': 'https://3dsecure.nass.example/gateway?token=T0K3N',
        'pSign': 'f00d',
        'transactionParams': {'TERMINAL': '00049546', 'TRTYPE': '1'},
    }
    client.check_status.return_value = dict(UNPAID)
    with patch('nass_payment.service.get_client', return_value=client):
        yield client
