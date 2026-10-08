from django.urls import include, path

urlpatterns = [
    path('api/nass/', include('nass_payment.urls')),
]
