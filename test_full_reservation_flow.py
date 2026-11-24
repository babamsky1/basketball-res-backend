import os
import sys
import json
import uuid
import webbrowser

from datetime import timedelta
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

# --------------------------------------------------
# DJANGO SETUP
# --------------------------------------------------

# Load Django settings so we can verify database records
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

from django.conf import settings
from django.utils import timezone

from reservations.models import Reservation

# --------------------------------------------------
# CONFIGURATION
# --------------------------------------------------

BASE_URL = "http://localhost:8000/api"

CUSTOMER_PHONE = "09171234567"

# This test requires PayMongo test mode
if not settings.PAYMONGO_SECRET_KEY.startswith("sk_test_"):
    print("ERROR: PayMongo test mode (sk_test_...) is required.")
    sys.exit(1)


# --------------------------------------------------
# HELPER: DISPLAY SECTION
# --------------------------------------------------


def show_section(title):
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


# --------------------------------------------------
# HELPER: SEND POST REQUEST
# --------------------------------------------------


def post_json(endpoint, payload):

    # Build API request
    request = Request(
        f"{BASE_URL}{endpoint}",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        # Send request
        with urlopen(request, timeout=60) as response:
            status_code = response.status
            body = response.read().decode("utf-8")

    except HTTPError as error:
        status_code = error.code
        body = error.read().decode("utf-8")

    except URLError as error:
        print("Connection error:", error)
        sys.exit(1)

    except Exception as error:
        print("Unexpected request error:", error)
        sys.exit(1)

    # Convert response into a dictionary when possible
    try:
        response_data = json.loads(body)
    except json.JSONDecodeError:
        response_data = {"raw_response": body}

    return status_code, response_data


# --------------------------------------------------
# HELPER: STOP IF AN API REQUEST FAILS
# --------------------------------------------------


def require_status(status_code, expected_status, response_data):

    if status_code != expected_status:
        print("\nTEST FAILED")
        print("Expected HTTP:", expected_status)
        print("Actual HTTP:", status_code)
        print("Response:", response_data)
        sys.exit(1)


# --------------------------------------------------
# STEP 1: CHECK AVAILABILITY
# --------------------------------------------------

show_section("STEP 1 - CHECK RESERVATION AVAILABILITY")

# Choose a date 14 days from today
reservation_date = (timezone.localdate() + timedelta(days=14)).isoformat()

start_time = "10:00"
end_time = "11:00"

print("Reservation date:", reservation_date)
print("Start time:", start_time)
print("End time:", end_time)

status_code, availability = post_json(
    "/check-availability/",
    {
        "reservation_date": reservation_date,
        "start_time": start_time,
        "end_time": end_time,
    },
)

require_status(status_code, 200, availability)

if not availability.get("available"):
    print("Selected schedule is unavailable.")
    print("Response:", availability)
    sys.exit(1)

print("Availability:", availability["message"])
print("STEP 1 PASSED")


# --------------------------------------------------
# STEP 2: CREATE RESERVATION
# --------------------------------------------------

show_section("STEP 2 - CREATE RESERVATION")

# Generate a unique email for this test run
customer_email = f"flowtest-{uuid.uuid4().hex[:8]}@example.com"

reservation_payload = {
    "customer_name": "Full Reservation Flow Test",
    "customer_email": customer_email,
    "customer_phone": CUSTOMER_PHONE,
    "reservation_date": reservation_date,
    "start_time": start_time,
    "end_time": end_time,
}

status_code, creation = post_json(
    "/create-reservations/",
    reservation_payload,
)

require_status(status_code, 201, creation)

reservation = creation.get("reservation", {})
reservation_id = reservation.get("id")

if not reservation_id:
    print("Reservation UUID missing from response.")
    sys.exit(1)

print("Reservation ID:", reservation_id)
print("Customer email:", customer_email)
print("Total amount:", reservation.get("total_amount"))
print("Status:", reservation.get("status"))

print("STEP 2 PASSED")


# --------------------------------------------------
# STEP 3: CREATE PAYMONGO CHECKOUT SESSION
# --------------------------------------------------

show_section("STEP 3 - CREATE PAYMONGO CHECKOUT")

checkout_payload = {
    "customer_email": customer_email,
    "customer_phone": CUSTOMER_PHONE,
}

status_code, checkout = post_json(
    f"/reservations/{reservation_id}/checkout/",
    checkout_payload,
)

require_status(status_code, 200, checkout)

checkout_url = checkout.get("checkout_url")
session_id = checkout.get("session_id")

if not checkout_url or not session_id:
    print("Checkout URL or Session ID is missing.")
    sys.exit(1)

print("PayMongo Session ID:", session_id)
print("Checkout URL:", checkout_url)

print("STEP 3 PASSED")


# --------------------------------------------------
# STEP 4: COMPLETE PAYMONGO GCASH TEST PAYMENT
# --------------------------------------------------

show_section("STEP 4 - COMPLETE PAYMONGO GCASH TEST PAYMENT")

print("Opening PayMongo Checkout...")

# Open the hosted Checkout page
webbrowser.open(checkout_url)

print("\nSelect GCash and complete the simulated test payment.")
print("Make sure your PayMongo test webhook endpoint is reachable so Django")
print("receives checkout_session.payment.paid.")

input(
    "\nPress ENTER after payment is complete "
    "and the PayMongo webhook has been delivered..."
)


# --------------------------------------------------
# STEP 5: VERIFY PAYMENT STATUS
# --------------------------------------------------

show_section("STEP 5 - VERIFY PAYMENT")

details_payload = {
    "customer_email": customer_email,
    "customer_phone": CUSTOMER_PHONE,
}

status_code, details = post_json(
    f"/reservations/{reservation_id}/details/",
    details_payload,
)

require_status(status_code, 200, details)

reservation_details = details.get("reservation", {})
payment_status = reservation_details.get("status")

print("Reservation status:", payment_status)
print("Readable status:", reservation_details.get("status_display"))

# Do not proceed to paid cancellation without confirmed payment
if payment_status != "PA":
    print("\nPayment is not yet confirmed by Django.")
    print("Do not proceed with cancellation.")
    sys.exit(1)

print("PAYMENT VERIFIED")
print("STEP 5 PASSED")


# --------------------------------------------------
# STEP 6: SEND TWO SIMULTANEOUS CANCELLATION REQUESTS
# --------------------------------------------------

show_section("STEP 6 - SIMULTANEOUS CANCELLATION")

print("Sending two cancellation requests concurrently...")

cancel_payload = {
    "customer_email": customer_email,
    "customer_phone": CUSTOMER_PHONE,
}

cancel_endpoint = f"/reservations/{reservation_id}/cancel/"

# Make both requests start at approximately the same time
barrier = Barrier(2)


def send_cancellation(request_number):

    barrier.wait()

    status_code, response_data = post_json(
        cancel_endpoint,
        cancel_payload,
    )

    print(f"\nCancellation Request {request_number}")
    print("HTTP Status:", status_code)
    print("Response:", response_data)

    return status_code, response_data


# Run both cancellation requests concurrently
with ThreadPoolExecutor(max_workers=2) as executor:

    futures = [
        executor.submit(send_cancellation, 1),
        executor.submit(send_cancellation, 2),
    ]

    cancellation_results = [future.result() for future in futures]


# Check expected results
successful_requests = [result for result in cancellation_results if result[0] == 200]

rejected_requests = [result for result in cancellation_results if result[0] == 400]

if len(successful_requests) != 1 or len(rejected_requests) != 1:
    print("\nUnexpected cancellation results.")
    print("Expected exactly one HTTP 200 and one HTTP 400.")
    sys.exit(1)

print("\nSTEP 6 PASSED")
print("One cancellation succeeded.")
print("The competing cancellation was rejected.")


# --------------------------------------------------
# STEP 7: VERIFY DATABASE AND REFUND
# --------------------------------------------------

show_section("STEP 7 - VERIFY REFUND")

# Retrieve the latest reservation directly from Django
reservation_record = Reservation.objects.get(id=reservation_id)

print("Reservation status:", reservation_record.status)
print("PayMongo Session ID:", reservation_record.paymongo_checkout_session_id)
print("PayMongo Payment ID:", reservation_record.paymongo_payment_id)
print("PayMongo Refund ID:", reservation_record.paymongo_refund_id)

# Verify reservation was cancelled
if reservation_record.status != "C":
    print("FAILED: Reservation is not cancelled.")
    sys.exit(1)

# Verify the webhook saved the PayMongo payment ID
if not reservation_record.paymongo_payment_id:
    print("FAILED: No PayMongo Payment ID was saved by the webhook.")
    sys.exit(1)

# Verify PayMongo refund ID exists
if not reservation_record.paymongo_refund_id:
    print("FAILED: No PayMongo Refund ID was saved.")
    sys.exit(1)

# Confirm only one reservation references this refund
refund_count = Reservation.objects.filter(
    paymongo_refund_id=reservation_record.paymongo_refund_id
).count()

print("Refund reference count:", refund_count)

if refund_count != 1:
    print("FAILED: Unexpected duplicate refund reference.")
    sys.exit(1)

print("STEP 7 PASSED")


# --------------------------------------------------
# FINAL SUMMARY
# --------------------------------------------------

show_section("FULL RESERVATION FLOW TEST SUMMARY")

print("Availability check: PASSED")
print("Reservation creation: PASSED")
print("PayMongo GCash Checkout creation: PASSED")
print("PayMongo GCash test payment: PASSED")
print("Payment webhook verification: PASSED")
print("Simultaneous cancellation: PASSED")
print("Duplicate refund protection: PASSED")
print("Database refund verification: PASSED")

print("\nRESERVATION ID:", reservation_id)
print("PAYMONGO SESSION ID:", reservation_record.paymongo_checkout_session_id)
print("PAYMONGO PAYMENT ID:", reservation_record.paymongo_payment_id)
print("PAYMONGO REFUND ID:", reservation_record.paymongo_refund_id)

print("\nALL END-TO-END TESTS PASSED!")
