from django.urls import path

from .views import NassCallbackView, NassPaymentStatusView, NassStartPaymentView

urlpatterns = [
    path('payments/', NassStartPaymentView.as_view(), name='nass-start-payment'),
    path(
        'payments/<int:pk>/status/',
        NassPaymentStatusView.as_view(),
        name='nass-payment-status',
    ),
    path('callback/', NassCallbackView.as_view(), name='nass-callback'),
]
