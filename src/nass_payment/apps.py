from django.apps import AppConfig


class NassPaymentConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'nass_payment'
    verbose_name = 'Nass Payment'

    def ready(self):
        from . import logs

        logs.configure()
