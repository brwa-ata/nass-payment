"""Logging: to the `nass_payment` logger, and to a file only when asked."""

import logging

import pytest

from nass_payment import conf, logs, service

from .testapp.models import Receipt


@pytest.fixture(autouse=True)
def clean_logger():
    """Leave the logger as the next test expects it."""
    before = list(logs.logger.handlers), logs.logger.propagate, logs.logger.level
    yield
    for handler in logs.logger.handlers:
        if handler not in before[0]:
            handler.close()
    logs.logger.handlers[:] = before[0]
    logs.logger.propagate, logs.logger.level = before[1], before[2]


def set_log_file(settings, value):
    settings.NASS_LOG_FILE = value
    conf.reset()


def test_without_a_file_nothing_is_attached(settings):
    set_log_file(settings, '')

    assert logs.configure() is None
    assert logs.logger.name == 'nass_payment'


@pytest.mark.django_db
def test_the_file_gets_the_packages_log(settings, tmp_path, nass):
    set_log_file(settings, tmp_path / 'logs' / 'nass.log')

    logs.configure()
    receipt = Receipt.objects.create(amount=1)
    payment = service.start_payment(
        receipt, callback_url='https://x/cb/', return_url='https://x/back/'
    )

    text = (tmp_path / 'logs' / 'nass.log').read_text()
    assert (
        f'INFO nass_payment: Nass transaction {payment["orderId"]} created for '
        f'receipt {receipt.pk}'
    ) in text


def test_a_relative_file_is_under_base_dir(settings, tmp_path):
    settings.BASE_DIR = tmp_path
    set_log_file(settings, 'nass.log')

    handler = logs.configure()

    assert handler.baseFilename == str((tmp_path / 'nass.log').resolve())
    assert logs.logger.propagate is False


def test_configuring_twice_adds_one_handler(settings, tmp_path):
    set_log_file(settings, tmp_path / 'nass.log')

    first = logs.configure()
    second = logs.configure()

    assert first is second
    assert logs.logger.handlers.count(first) == 1


def test_the_level_is_a_setting(settings, tmp_path):
    settings.NASS_LOG_LEVEL = 'WARNING'
    set_log_file(settings, tmp_path / 'nass.log')

    logs.configure()

    assert logs.logger.level == logging.WARNING


@pytest.mark.django_db
def test_a_completion_is_logged_with_the_receipts_order(settings, tmp_path, nass):
    """Nass's status may not repeat the orderId; the receipt always has it."""
    set_log_file(settings, tmp_path / 'nass.log')
    logs.configure()
    receipt = Receipt.objects.create(amount=1, ref_no='11791458071')

    service.apply_status(
        receipt, {'actionCode': '0', 'responseCode': '00', 'rrn': '628112345678'}
    )

    text = (tmp_path / 'nass.log').read_text()
    assert (
        f'Nass payment 11791458071 for receipt {receipt.pk} marked completed '
        '(rrn 628112345678)'
    ) in text
