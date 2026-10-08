"""Nass Payment Gateway for Django: create a card payment, send the customer
to Nass's 3-D Secure page, and complete a receipt once Nass confirms it. See
the README for setup."""

from importlib.metadata import version

__version__ = version('nass-payment')
