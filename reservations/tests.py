import hashlib
import hmac
import json
import time
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import PaymongoEvent, Reservation
from .paymongo import to_centavos


def signed_event(payload, secret="whsec_test"):
    body = json.dumps(payload).encode("utf-8")
    timestamp = str(int(time.time()))
    signature = hmac.new(
        secret.encode("utf-8"),
        timestamp.encode("utf-8") + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    return body, f"t={timestamp},te={signature},li="


def paid_event(reservation, session_id="cs_test", payment_id="pay_test"):
    return {
        "data": {
            "id": "evt_test_1",
            "type": "event",
            "attributes": {
                "type": "checkout_session.payment.paid",
                "livemode": False,
                "data": {
                    "id": session_id,
                    "type": "checkout_session",
                    "attributes": {
                        "metadata": {"reservation_id": str(reservation.id)},
                        "payments": [
                            {
                                "id": payment_id,
                                "type": "payment",
                                "attributes": {
                                    "status": "paid",
                                    "amount": to_centavos(reservation.total_amount),
                                    "currency": "PHP",
                                    "source": {"type": "gcash"},
                                },
                            }
                        ],
                    },
                },
            },
        }
    }


@override_settings(PAYMONGO_SECRET_KEY="sk_test_key", PAYMONGO_WEBHOOK_SECRET="whsec_test")
class PaymongoFlowTests(TestCase):

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.reservation = Reservation.objects.create(
            customer_name="Test Customer",
            customer_email="test@example.com",
            customer_phone="09171234567",
            reservation_date=timezone.localdate() + timedelta(days=7),
            start_time="10:00",
            end_time="11:00",
            hourly_rate="500.00",
            total_amount="500.00",
            status="P",
            expires_at=timezone.now() + timedelta(minutes=30),
            paymongo_checkout_session_id="cs_test",
        )

    def test_webhook_rejects_invalid_signature(self):
        body, _ = signed_event(paid_event(self.reservation))
        bad_header = "t=1,te=deadbeef,li="

        response = self.client.post(
            reverse("paymongo_webhook"),
            data=body,
            content_type="application/json",
            HTTP_PAYMONGO_SIGNATURE=bad_header,
        )

        self.assertEqual(response.status_code, 400)
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, "P")

    def test_webhook_marks_reservation_paid(self):
        body, header = signed_event(paid_event(self.reservation))

        response = self.client.post(
            reverse("paymongo_webhook"),
            data=body,
            content_type="application/json",
            HTTP_PAYMONGO_SIGNATURE=header,
        )

        self.assertEqual(response.status_code, 200)
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, "PA")
        self.assertEqual(self.reservation.paymongo_payment_id, "pay_test")
        self.assertTrue(PaymongoEvent.objects.filter(event_id="evt_test_1").exists())

        # A duplicate delivery must not change the reservation again
        duplicate = self.client.post(
            reverse("paymongo_webhook"),
            data=body,
            content_type="application/json",
            HTTP_PAYMONGO_SIGNATURE=header,
        )
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(PaymongoEvent.objects.filter(event_id="evt_test_1").count(), 1)

    @patch("reservations.views.paymongo.create_refund")
    def test_late_payment_is_refunded(self, create_refund):
        create_refund.return_value = {"id": "ref_test", "status": "processing"}
        self.reservation.expires_at = timezone.now() - timedelta(minutes=1)
        self.reservation.save(update_fields=["expires_at"])

        body, header = signed_event(paid_event(self.reservation))

        response = self.client.post(
            reverse("paymongo_webhook"),
            data=body,
            content_type="application/json",
            HTTP_PAYMONGO_SIGNATURE=header,
        )

        self.assertEqual(response.status_code, 200)
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, "E")
        self.assertEqual(self.reservation.paymongo_refund_id, "ref_test")
        self.assertEqual(self.reservation.paymongo_payment_id, "pay_test")
        create_refund.assert_called_once()

    @patch("reservations.views.paymongo.create_checkout_session")
    def test_checkout_uses_gcash_and_saves_session(self, create_checkout_session):
        create_checkout_session.return_value = {
            "id": "cs_created",
            "checkout_url": "https://checkout.paymongo.com/test",
        }
        self.reservation.paymongo_checkout_session_id = None
        self.reservation.save(update_fields=["paymongo_checkout_session_id"])

        response = self.client.post(
            reverse(
                "create_checkout_session",
                kwargs={"reservation_id": self.reservation.id},
            ),
            data=json.dumps(
                {"customer_email": "test@example.com", "customer_phone": "09171234567"}
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["session_id"], "cs_created")
        self.assertEqual(response.data["payment_method"], "gcash")
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.paymongo_checkout_session_id, "cs_created")
        create_checkout_session.assert_called_once_with(self.reservation)

    @patch("reservations.views.paymongo.create_refund")
    @patch("reservations.views.paymongo.retrieve_checkout_session")
    def test_cancel_refunds_paid_reservation(self, retrieve_session, create_refund):
        retrieve_session.return_value = {
            "data": {
                "attributes": {
                    "payments": [paid_event(self.reservation)["data"]["attributes"]["data"]["attributes"]["payments"][0]]
                }
            }
        }
        create_refund.return_value = {"id": "ref_cancel", "status": "processing"}

        response = self.client.post(
            reverse("cancel_reservation", kwargs={"reservation_id": self.reservation.id}),
            data=json.dumps(
                {"customer_email": "test@example.com", "customer_phone": "09171234567"}
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, "C")
        self.assertEqual(self.reservation.paymongo_payment_id, "pay_test")
        self.assertEqual(self.reservation.paymongo_refund_id, "ref_cancel")
        create_refund.assert_called_once()
