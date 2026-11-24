"""Small PayMongo client for GCash hosted checkout, refunds, and webhooks."""

import base64
import hashlib
import hmac
import json
import time
from decimal import Decimal

import requests
from django.conf import settings


class PayMongoError(Exception):
    """Base class for PayMongo integration errors."""


class PayMongoAPIError(PayMongoError):
    """PayMongo returned an error or an unusable response."""

    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class PayMongoSignatureError(PayMongoError):
    """The incoming webhook could not be verified."""


def to_centavos(amount):
    """Convert a peso amount to the integer centavos PayMongo expects."""
    return int((Decimal(str(amount)) * Decimal("100")).to_integral_value())


def _is_live_mode():
    return str(settings.PAYMONGO_SECRET_KEY or "").startswith("sk_live_")


def _headers(idempotency_key=None):
    secret_key = settings.PAYMONGO_SECRET_KEY or ""
    token = base64.b64encode(f"{secret_key}:".encode("utf-8")).decode("ascii")

    headers = {
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key

    return headers


def _request(method, path, payload=None, idempotency_key=None):
    base_url = (settings.PAYMONGO_API_BASE or "https://api.paymongo.com").rstrip("/")
    url = f"{base_url}{path}"

    try:
        response = requests.request(
            method,
            url,
            headers=_headers(idempotency_key),
            json=payload,
            timeout=15,
        )
    except requests.RequestException as exc:
        raise PayMongoAPIError("Unable to reach PayMongo.") from exc

    if not 200 <= response.status_code < 300:
        raise PayMongoAPIError(
            "PayMongo request failed.", status_code=response.status_code
        )

    try:
        return response.json()
    except ValueError as exc:
        raise PayMongoAPIError("PayMongo returned an invalid response.") from exc


def create_checkout_session(reservation):
    """Create a GCash-only hosted checkout session for a reservation."""
    amount = to_centavos(reservation.total_amount)
    description = (
        f"{reservation.reservation_date} "
        f"{reservation.start_time}-{reservation.end_time}"
    )

    payload = {
        "data": {
            "attributes": {
                "line_items": [
                    {
                        "name": "Basketball Court Reservation",
                        "description": description,
                        "amount": amount,
                        "currency": "PHP",
                        "quantity": 1,
                    }
                ],
                "payment_method_types": ["gcash"],
                "customer_email": reservation.customer_email,
                "description": "Online GCash payment for court reservation",
                "reference_number": str(reservation.id),
                "metadata": {
                    "reservation_id": str(reservation.id),
                },
                "success_url": settings.PAYMONGO_SUCCESS_URL,
                "cancel_url": settings.PAYMONGO_CANCEL_URL,
                "show_line_items": True,
                "send_email_receipt": False,
            }
        }
    }

    response = _request(
        "POST",
        "/v2/checkout_sessions",
        payload,
        idempotency_key=f"reservation-checkout-{reservation.id}",
    )

    data = response.get("data") or {}
    attributes = data.get("attributes") or {}
    session_id = data.get("id")
    checkout_url = attributes.get("checkout_url")

    if not session_id or not checkout_url:
        raise PayMongoAPIError("PayMongo did not return a checkout URL.")

    return {
        "id": session_id,
        "checkout_url": checkout_url,
    }


def retrieve_checkout_session(session_id):
    """Retrieve a hosted checkout session and its payments."""
    return _request("GET", f"/v1/checkout_sessions/{session_id}")


def expire_checkout_session(session_id):
    """Expire an unpaid hosted checkout session."""
    return _request("POST", f"/v1/checkout_sessions/{session_id}/expire")


def create_refund(reservation, payment_id):
    """Issue a full refund for a paid GCash payment."""
    payload = {
        "data": {
            "attributes": {
                "amount": to_centavos(reservation.total_amount),
                "payment_id": payment_id,
                "reason": "others",
                "notes": f"Reservation {reservation.id} was cancelled.",
            }
        }
    }

    response = _request(
        "POST",
        "/v1/refunds",
        payload,
        idempotency_key=f"reservation-refund-{reservation.id}",
    )

    data = response.get("data") or {}
    attributes = data.get("attributes") or {}
    refund_id = data.get("id")

    if not refund_id:
        raise PayMongoAPIError("PayMongo did not return a refund ID.")

    return {
        "id": refund_id,
        "status": attributes.get("status"),
    }


def verify_webhook_signature(raw_body, signature_header):
    """Verify Paymongo-Signature and return the decoded event payload."""
    parts = {}

    for part in (signature_header or "").split(","):
        key, separator, value = part.strip().partition("=")
        if separator:
            parts[key] = value

    timestamp = parts.get("t")
    secret = settings.PAYMONGO_WEBHOOK_SECRET or ""

    if not timestamp or not secret:
        raise PayMongoSignatureError("Missing webhook signature data.")

    try:
        timestamp_value = int(timestamp)
    except ValueError as exc:
        raise PayMongoSignatureError("Invalid webhook timestamp.") from exc

    tolerance = getattr(settings, "PAYMONGO_WEBHOOK_TOLERANCE_SECONDS", 300)
    if tolerance and abs(time.time() - timestamp_value) > tolerance:
        raise PayMongoSignatureError("Webhook timestamp is too old.")

    signed_payload = timestamp.encode("utf-8") + b"." + raw_body
    expected_signature = hmac.new(
        secret.encode("utf-8"),
        signed_payload,
        hashlib.sha256,
    ).hexdigest()

    # PayMongo sends both a test-mode (te) and live-mode (li) signature.
    candidate = parts.get("li") if _is_live_mode() else parts.get("te")

    if not candidate or not hmac.compare_digest(candidate, expected_signature):
        raise PayMongoSignatureError("Invalid webhook signature.")

    try:
        return json.loads(raw_body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise PayMongoSignatureError("Invalid webhook payload.") from exc
