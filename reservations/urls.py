from django.urls import path
from . import views

urlpatterns = [
    path("check-availability/", views.check_availability, name="check_availability"),
    path("create-reservations/", views.create_reservation, name="create_reservation"),
    path(
        "reservations/<uuid:reservation_id>/cancel/",
        views.cancel_reservation,
        name="cancel_reservation",
    ),
    path(
        "reservations/<uuid:reservation_id>/checkout/",
        views.create_checkout_session,
        name="create_checkout_session",
    ),
    path("payment-success/", views.payment_success, name="payment_success"),
    path("payment-cancelled/", views.payment_cancelled, name="payment_cancelled"),
    path("stripe-webhook/", views.stripe_webhook, name="stripe_webhook"),
    path(
        "customer-reservation-history/",
        views.customer_reservation_history,
        name="customer_reservation_history",
    ),
    path(
        "reservations/<uuid:reservation_id>/details/",
        views.reservation_details,
        name="reservation_details",
    ),
]
