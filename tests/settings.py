"""The smallest Django project that can host the app, for the test suite."""

SECRET_KEY = 'nass-payment-tests'
USE_TZ = True
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

INSTALLED_APPS = [
    'django.contrib.contenttypes',
    'django.contrib.auth',
    'rest_framework',
    'nass_payment',
    'tests.testapp',
]

DATABASES = {
    'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'},
}

ROOT_URLCONF = 'tests.urls'

NASS_BASE_URL = 'https://nass.example'
NASS_USERNAME = 'merchant@example.com'
NASS_PASSWORD = 'merchant-password'
NASS_RECEIPT_MODEL = 'testapp.Receipt'
NASS_ON_PAYMENT_COMPLETED = 'tests.hooks.record'
NASS_ON_PAYMENT_FAILED = 'tests.hooks.record_failed'
