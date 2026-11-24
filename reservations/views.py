from decimal import Decimal

from django.db import transaction, IntegrityError
from django.utils import timezone
from datetime import timedelta
from django.utils.dateparse import parse_date, parse_time

from rest_framework.decorators import api_view, throttle_scope
from rest_framework.response import Response
from rest_framework import status

from .models import Reservation, PaymongoEvent
from . import paymongo

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.core.validators import validate_email
from django.core.exceptions import ValidationError

# Basketball court hourly rate
HOURLY_RATE = Decimal("500.00")


# Automatically expire unpaid reservations past their deadline
def expire_pending_reservations():

    Reservation.objects.filter(status="P", expires_at__lte=timezone.now()).update(
        status="E"
    )


# Check if a reservation schedule is available
@api_view(["POST"])
@throttle_scope("availability_check")
def check_availability(request):

    # Ensure the request body is a JSON object
    if not isinstance(request.data, dict):
        return Response(
            {"message": "Request body must be a JSON object."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Get reservation schedule as raw values
    reservation_date_value = request.data.get("reservation_date", "")
    start_time_value = request.data.get("start_time", "")
    end_time_value = request.data.get("end_time", "")

    # Validate that all input values are strings
    input_values = [
        reservation_date_value,
        start_time_value,
        end_time_value,
    ]

    if not all(isinstance(value, str) for value in input_values):
        return Response(
            {"message": "Reservation date and times must be provided as text."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Remove unnecessary spaces
    reservation_date_value = reservation_date_value.strip()
    start_time_value = start_time_value.strip()
    end_time_value = end_time_value.strip()

    # Validate required fields
    if not reservation_date_value or not start_time_value or not end_time_value:
        return Response(
            {"message": "Reservation date, start time, and end time are required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Parse reservation date and time
    reservation_date = parse_date(reservation_date_value)
    start_time = parse_time(start_time_value)
    end_time = parse_time(end_time_value)

    # Validate date and time formats
    if not reservation_date or not start_time or not end_time:
        return Response(
            {"message": "Invalid date or time format."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Prevent checking availability for past dates
    if reservation_date < timezone.localdate():
        return Response(
            {"message": "Reservation date cannot be in the past."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Prevent checking availability for times that have already passed today
    if reservation_date == timezone.localdate():
        current_time = timezone.localtime().time().replace(tzinfo=None)

        if start_time <= current_time:
            return Response(
                {"message": "Reservation start time must be in the future."},
                status=status.HTTP_400_BAD_REQUEST,
            )

    # Ensure the end time is later than the start time
    if end_time <= start_time:
        return Response(
            {"message": "End time must be later than start time."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Expire unpaid reservations before checking availability
    expire_pending_reservations()

    # Check for overlapping pending or paid reservations
    overlapping = Reservation.objects.filter(
        reservation_date=reservation_date,
        status__in=["P", "PA"],
        start_time__lt=end_time,
        end_time__gt=start_time,
    ).exists()

    # Return availability result
    return Response(
        {
            "available": not overlapping,
            "message": (
                "This schedule is already reserved."
                if overlapping
                else "This schedule is available."
            ),
        },
        status=status.HTTP_200_OK,
    )


# Create a new reservation
@api_view(["POST"])
@throttle_scope("reservation_creation")
@transaction.atomic
def create_reservation(request):

    # Ensure the request body is a JSON object
    if not isinstance(request.data, dict):
        return Response(
            {"message": "Request body must be a JSON object."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Get customer information
    customer_name = request.data.get("customer_name", "")
    customer_email = request.data.get("customer_email", "")
    customer_phone = request.data.get("customer_phone", "")

    # Get reservation schedule
    reservation_date_value = request.data.get("reservation_date", "")
    start_time_value = request.data.get("start_time", "")
    end_time_value = request.data.get("end_time", "")

    # Validate that all input values are strings
    input_values = [
        customer_name,
        customer_email,
        customer_phone,
        reservation_date_value,
        start_time_value,
        end_time_value,
    ]

    if not all(isinstance(value, str) for value in input_values):
        return Response(
            {
                "message": "All customer and reservation details must be provided as text."
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Remove unnecessary spaces
    customer_name = customer_name.strip()
    customer_email = customer_email.strip()
    customer_phone = customer_phone.strip()

    reservation_date_value = reservation_date_value.strip()
    start_time_value = start_time_value.strip()
    end_time_value = end_time_value.strip()

    # Parse reservation date and time
    reservation_date = parse_date(reservation_date_value)
    start_time = parse_time(start_time_value)
    end_time = parse_time(end_time_value)

    # Validate required customer information
    if not customer_name or not customer_email or not customer_phone:
        return Response(
            {"message": "Customer name, email, and phone are required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Validate customer information length
    if len(customer_name) > 150:
        return Response(
            {"message": "Customer name must not exceed 150 characters."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if len(customer_email) > 254:
        return Response(
            {"message": "Email address must not exceed 254 characters."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if len(customer_phone) > 20:
        return Response(
            {"message": "Phone number must not exceed 20 characters."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Validate email address format
    try:
        validate_email(customer_email)
    except ValidationError:
        return Response(
            {"message": "Please provide a valid email address."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Validate phone number format
    if not customer_phone.isdigit() or not (10 <= len(customer_phone) <= 15):
        return Response(
            {"message": "Phone number must contain 10 to 15 digits."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Validate date and time
    if not reservation_date or not start_time or not end_time:
        return Response(
            {"message": "Invalid reservation date or time."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Prevent reservations for past dates
    if reservation_date < timezone.localdate():
        return Response(
            {"message": "Reservation date cannot be in the past."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Prevent reservations for times that have already passed today
    if reservation_date == timezone.localdate():
        current_time = timezone.localtime().time().replace(tzinfo=None)

        if start_time <= current_time:
            return Response(
                {"message": "Reservation start time must be in the future."},
                status=status.HTTP_400_BAD_REQUEST,
            )

    # Ensure the end time is later than the start time
    if end_time <= start_time:
        return Response(
            {"message": "End time must be later than start time."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Calculate reservation duration
    from datetime import datetime

    start = datetime.combine(reservation_date, start_time)
    end = datetime.combine(reservation_date, end_time)

    duration = Decimal(str((end - start).total_seconds())) / Decimal("3600")

    # Calculate total payment
    total_amount = (duration * HOURLY_RATE).quantize(Decimal("0.01"))

    # Expire unpaid reservations before checking availability
    expire_pending_reservations()

    # Check availability again before saving
    overlapping = Reservation.objects.filter(
        reservation_date=reservation_date,
        status__in=["P", "PA"],
        start_time__lt=end_time,
        end_time__gt=start_time,
    ).exists()

    if overlapping:
        return Response(
            {"message": "This schedule is already reserved."},
            status=status.HTTP_409_CONFLICT,
        )

    # Set the reservation expiration to 30 minutes
    expiration_time = timezone.now() + timedelta(minutes=30)

    # Save reservation
    reservation = Reservation.objects.create(
        customer_name=customer_name,
        customer_email=customer_email,
        customer_phone=customer_phone,
        reservation_date=reservation_date,
        start_time=start_time,
        end_time=end_time,
        hourly_rate=HOURLY_RATE,
        total_amount=total_amount,
        status="P",
        expires_at=expiration_time,
    )

    # Return the newly created reservation
    return Response(
        {
            "message": "Reservation created successfully.",
            "reservation": {
                "id": reservation.id,
                "customer_name": reservation.customer_name,
                "reservation_date": reservation.reservation_date,
                "start_time": reservation.start_time,
                "end_time": reservation.end_time,
                "hourly_rate": str(reservation.hourly_rate),
                "total_amount": str(reservation.total_amount),
                "status": reservation.status,
            },
        },
        status=status.HTTP_201_CREATED,
    )


# Cancel a pending or paid reservation
@api_view(["POST"])
@throttle_scope("customer_verification")
@transaction.atomic
def cancel_reservation(request, reservation_id):

    # Find and lock the reservation to prevent simultaneous cancellations
    try:
        reservation = Reservation.objects.select_for_update().get(id=reservation_id)
    except Reservation.DoesNotExist:
        return Response(
            {"message": "Reservation not found."},
            status=status.HTTP_404_NOT_FOUND,
        )

    # Ensure the request body is a JSON object
    if not isinstance(request.data, dict):
        return Response(
            {"message": "Request body must be a JSON object."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Get customer details
    customer_email = request.data.get("customer_email", "")
    customer_phone = request.data.get("customer_phone", "")

    # Validate that customer information is provided as text
    if not isinstance(customer_email, str) or not isinstance(customer_phone, str):
        return Response(
            {"message": "Customer email and phone number must be provided as text."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Remove unnecessary spaces
    customer_email = customer_email.strip()
    customer_phone = customer_phone.strip()

    # Require both email and phone number
    if not customer_email or not customer_phone:
        return Response(
            {"message": "Customer email and phone number are required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Verify reservation ownership
    if (
        reservation.customer_email.lower() != customer_email.lower()
        or reservation.customer_phone.strip() != customer_phone
    ):
        return Response(
            {"message": "Invalid customer information."},
            status=status.HTTP_403_FORBIDDEN,
        )

    # Only pending and paid reservations can be cancelled
    if reservation.status not in ["P", "PA"]:
        return Response(
            {"message": "Only pending or paid reservations can be cancelled."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    def paid_payment_from(session_attributes):
        # Return the paid payment object from a PayMongo Checkout Session
        for payment in session_attributes.get("payments") or []:
            attributes = payment.get("attributes") or {}
            if attributes.get("status") == "paid":
                return payment
        return None

    try:
        session = None
        session_attributes = {}

        # Retrieve the PayMongo Checkout Session if one exists
        if reservation.paymongo_checkout_session_id:
            try:
                session = paymongo.retrieve_checkout_session(
                    reservation.paymongo_checkout_session_id
                )
            except paymongo.PayMongoAPIError as error:
                if error.status_code != 404:
                    raise
                session = None

            if session:
                session_attributes = (session.get("data") or {}).get("attributes") or {}

        payment_id = reservation.paymongo_payment_id
        paid_payment = paid_payment_from(session_attributes)

        if paid_payment:
            payment_id = paid_payment.get("id") or payment_id

        def refund_and_cancel():
            # Refund a paid reservation before cancelling it
            if not payment_id:
                return Response(
                    {"message": "Cannot refund: PayMongo payment ID not found."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Prevent duplicate refunds
            if reservation.paymongo_refund_id:
                return Response(
                    {"message": "A refund has already been initiated."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            refund = paymongo.create_refund(reservation, payment_id)

            reservation.paymongo_payment_id = payment_id
            reservation.paymongo_refund_id = refund["id"]
            reservation.status = "C"

            reservation.save(
                update_fields=[
                    "paymongo_payment_id",
                    "paymongo_refund_id",
                    "status",
                ]
            )

            return Response(
                {
                    "message": "Reservation cancelled and refund initiated.",
                    "reservation_id": str(reservation.id),
                    "status": reservation.status,
                    "refund_status": refund["status"],
                },
                status=status.HTTP_200_OK,
            )

        # Handle paid reservations
        if reservation.status == "PA" or paid_payment or payment_id:
            return refund_and_cancel()

        # Sessions already expired in PayMongo need no further action
        if session_attributes.get("status") == "expired":
            reservation.status = "E"
            reservation.save(update_fields=["status"])

            return Response(
                {
                    "message": "This reservation has already expired.",
                    "status": "E",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Expire an open hosted checkout session before cancelling
        if reservation.paymongo_checkout_session_id:
            try:
                paymongo.expire_checkout_session(
                    reservation.paymongo_checkout_session_id
                )
            except paymongo.PayMongoAPIError as error:
                if error.status_code not in (400, 404):
                    raise

                # The session may have been paid between lookup and expiry
                try:
                    refreshed = paymongo.retrieve_checkout_session(
                        reservation.paymongo_checkout_session_id
                    )
                except paymongo.PayMongoAPIError:
                    refreshed = None

                refreshed_attributes = (
                    (refreshed or {}).get("data") or {}
                ).get("attributes") or {}
                refreshed_payment = paid_payment_from(refreshed_attributes)

                if refreshed_payment:
                    payment_id = refreshed_payment.get("id") or payment_id
                    return refund_and_cancel()

                if refreshed_attributes.get("status") == "expired":
                    reservation.status = "E"
                    reservation.save(update_fields=["status"])

                    return Response(
                        {
                            "message": "This reservation has already expired.",
                            "status": "E",
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )

        # Cancel the pending reservation
        reservation.status = "C"

        reservation.save(update_fields=["status"])

        return Response(
            {
                "message": "Reservation cancelled successfully.",
                "reservation_id": str(reservation.id),
                "status": reservation.status,
            },
            status=status.HTTP_200_OK,
        )

    except paymongo.PayMongoError:
        # Avoid exposing internal PayMongo error details
        return Response(
            {
                "message": "Unable to process the cancellation or refund. Please try again."
            },
            status=status.HTTP_502_BAD_GATEWAY,
        )


# Create a PayMongo GCash Checkout Session
@api_view(["POST"])
@throttle_scope("checkout")
@transaction.atomic
def create_checkout_session(request, reservation_id):

    # Find and lock the reservation during Checkout creation
    try:
        reservation = Reservation.objects.select_for_update().get(id=reservation_id)
    except Reservation.DoesNotExist:
        return Response(
            {"message": "Reservation not found."},
            status=status.HTTP_404_NOT_FOUND,
        )

    # Ensure the request body is a JSON object
    if not isinstance(request.data, dict):
        return Response(
            {"message": "Request body must be a JSON object."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Get customer verification information
    customer_email = request.data.get("customer_email", "")
    customer_phone = request.data.get("customer_phone", "")

    # Validate customer information types
    if not isinstance(customer_email, str) or not isinstance(customer_phone, str):
        return Response(
            {"message": "Customer email and phone number must be provided as text."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Remove unnecessary spaces
    customer_email = customer_email.strip()
    customer_phone = customer_phone.strip()

    # Require both customer details
    if not customer_email or not customer_phone:
        return Response(
            {"message": "Customer email and phone number are required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Verify reservation ownership
    if (
        reservation.customer_email.lower() != customer_email.lower()
        or reservation.customer_phone.strip() != customer_phone
    ):
        return Response(
            {"message": "Invalid customer information."},
            status=status.HTTP_403_FORBIDDEN,
        )

    # Only pending reservations can be paid
    if reservation.status != "P":
        return Response(
            {"message": "This reservation cannot be paid."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Prevent checkout if the reservation has already expired
    if reservation.expires_at and reservation.expires_at <= timezone.now():
        expire_pending_reservations()

        return Response(
            {"message": "This reservation has expired. Please make a new reservation."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Prevent creating another Checkout Session
    if reservation.paymongo_checkout_session_id:
        return Response(
            {
                "message": "A checkout session has already been created.",
                "message_detail": "Please use your existing checkout session.",
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        # PayMongo sessions do not auto-expire; keep the local hold deadline
        checkout_expiration = reservation.created_at + timedelta(minutes=61)

        session = paymongo.create_checkout_session(reservation)

        reservation.expires_at = checkout_expiration
        reservation.paymongo_checkout_session_id = session["id"]

        reservation.save(
            update_fields=[
                "expires_at",
                "paymongo_checkout_session_id",
            ]
        )

        return Response(
            {
                "message": "Checkout session created.",
                "checkout_url": session["checkout_url"],
                "session_id": session["id"],
                "payment_method": "gcash",
            },
            status=status.HTTP_200_OK,
        )

    except paymongo.PayMongoError:
        # Avoid exposing internal PayMongo error details
        return Response(
            {
                "message": "Unable to create the PayMongo Checkout Session. Please try again."
            },
            status=status.HTTP_502_BAD_GATEWAY,
        )


# Customer is redirected here after successful checkout
@api_view(["GET"])
def payment_success(request):
    return Response(
        {
            "status": "success",
            "message": "Payment completed! Thank you for your reservation.",
        }
    )


# Customer is redirected here when checkout is cancelled
@api_view(["GET"])
def payment_cancelled(request):
    return Response(
        {
            "status": "cancelled",
            "message": "Payment was cancelled. Your reservation is still pending.",
        }
    )


# Receive payment notifications from PayMongo
@csrf_exempt
def paymongo_webhook(request):

    # Only accept POST requests
    if request.method != "POST":
        return JsonResponse({"message": "Method not allowed."}, status=405)

    # Verify the raw body before parsing it
    signature = request.headers.get("Paymongo-Signature")

    try:
        payload = paymongo.verify_webhook_signature(request.body, signature)
    except paymongo.PayMongoSignatureError:
        return JsonResponse({"message": "Invalid webhook signature."}, status=400)

    event = payload.get("data") or {}
    event_id = event.get("id")
    attributes = event.get("attributes") or {}
    event_type = attributes.get("type")

    if not event_id or not event_type:
        return JsonResponse({"message": "Invalid webhook event."}, status=400)

    # Ignore events belonging to the other PayMongo environment
    expected_livemode = str(settings.PAYMONGO_SECRET_KEY or "").startswith("sk_live_")
    if bool(attributes.get("livemode")) != expected_livemode:
        return JsonResponse(
            {"status": "ignored", "message": "Event from another environment."}
        )

    # Only hosted-checkout GCash payments confirm a reservation
    if event_type != "checkout_session.payment.paid":
        return JsonResponse({"status": "ignored", "message": "Unsupported event."})

    try:
        # Process and record the event in one transaction
        with transaction.atomic():

            # Use a nested transaction to handle duplicate deliveries
            try:
                with transaction.atomic():
                    PaymongoEvent.objects.create(event_id=event_id)
            except IntegrityError:
                return JsonResponse({"status": "duplicate"})

            session = attributes.get("data") or {}
            session_id = session.get("id")
            session_attributes = session.get("attributes") or {}
            metadata = session_attributes.get("metadata") or {}
            reservation_id = metadata.get("reservation_id")

            paid_payment = None

            for payment in session_attributes.get("payments") or []:
                payment_attributes = payment.get("attributes") or {}
                if payment_attributes.get("status") == "paid":
                    paid_payment = payment
                    break

            if not (session_id and reservation_id and paid_payment):
                return JsonResponse(
                    {"status": "ignored", "message": "No paid payment to process."}
                )

            # Find and lock the matching reservation
            reservation = (
                Reservation.objects.select_for_update()
                .filter(
                    id=reservation_id,
                    paymongo_checkout_session_id=session_id,
                )
                .first()
            )

            if reservation and reservation.status in ["P", "E"]:
                payment_id = paid_payment.get("id")
                payment_attributes = paid_payment.get("attributes") or {}
                source = payment_attributes.get("source") or {}
                expected_amount = paymongo.to_centavos(reservation.total_amount)

                # Only accept the exact GCash payment for this reservation
                if (
                    not payment_id
                    or payment_attributes.get("currency") != "PHP"
                    or payment_attributes.get("amount") != expected_amount
                    or source.get("type") != "gcash"
                ):
                    return JsonResponse(
                        {"status": "ignored", "message": "Payment details did not match."}
                    )

                # Check whether payment arrived after expiration
                is_late_payment = reservation.status == "E" or (
                    reservation.expires_at
                    and reservation.expires_at <= timezone.now()
                )

                if is_late_payment:

                    # Automatically refund late payments
                    if not reservation.paymongo_refund_id:
                        refund = paymongo.create_refund(reservation, payment_id)
                        reservation.paymongo_refund_id = refund["id"]

                    reservation.paymongo_payment_id = payment_id
                    reservation.status = "E"

                    reservation.save(
                        update_fields=[
                            "status",
                            "paymongo_payment_id",
                            "paymongo_refund_id",
                        ]
                    )
                else:
                    reservation.paymongo_payment_id = payment_id
                    reservation.status = "PA"

                    reservation.save(update_fields=["status", "paymongo_payment_id"])

        # Confirm successful event processing
        return JsonResponse({"status": "processed"})

    except Exception:
        # Return an error so PayMongo can retry the event
        return JsonResponse({"message": "Webhook processing failed."}, status=500)


# Retrieve a customer's reservation history
@api_view(["POST"])
@throttle_scope("customer_verification")
def customer_reservation_history(request):

    # Ensure the request body is a JSON object
    if not isinstance(request.data, dict):
        return Response(
            {"message": "Request body must be a JSON object."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Get customer details
    customer_email = request.data.get("customer_email", "")
    customer_phone = request.data.get("customer_phone", "")

    # Validate that customer information is provided as text
    if not isinstance(customer_email, str) or not isinstance(customer_phone, str):
        return Response(
            {"message": "Customer email and phone number must be provided as text."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Remove unnecessary spaces
    customer_email = customer_email.strip()
    customer_phone = customer_phone.strip()

    # Validate required fields
    if not customer_email or not customer_phone:
        return Response(
            {"message": "Customer email and phone number are required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Find reservations matching the customer's email and phone
    reservations = Reservation.objects.filter(
        customer_email__iexact=customer_email,
        customer_phone=customer_phone,
    ).order_by("-created_at")

    # Return a message if no reservations are found
    if not reservations.exists():
        return Response(
            {"message": "No reservations found."},
            status=status.HTTP_404_NOT_FOUND,
        )

    # Prepare reservation history
    reservation_data = []

    for reservation in reservations:

        # Convert reservation status into readable text
        status_display = {
            "P": "Pending",
            "PA": "Paid",
            "E": "Expired",
            "C": "Cancelled",
        }.get(reservation.status, "Unknown")

        reservation_data.append(
            {
                "reservation_id": reservation.id,
                "reservation_date": reservation.reservation_date,
                "start_time": reservation.start_time,
                "end_time": reservation.end_time,
                # Return monetary values as strings to preserve decimal precision
                "hourly_rate": str(reservation.hourly_rate),
                "total_amount": str(reservation.total_amount),
                "status": reservation.status,
                "status_display": status_display,
                "created_at": reservation.created_at,
            }
        )

    # Return all matching reservations
    return Response(
        {
            "message": "Reservation history retrieved successfully.",
            "total_reservations": len(reservation_data),
            "reservations": reservation_data,
        },
        status=status.HTTP_200_OK,
    )


# Retrieve details of a specific reservation
@api_view(["POST"])
@throttle_scope("customer_verification")
def reservation_details(request, reservation_id):

    # Ensure the request body is a JSON object
    if not isinstance(request.data, dict):
        return Response(
            {"message": "Request body must be a JSON object."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Get customer details
    customer_email = request.data.get("customer_email", "")
    customer_phone = request.data.get("customer_phone", "")

    # Validate that customer information is provided as text
    if not isinstance(customer_email, str) or not isinstance(customer_phone, str):
        return Response(
            {"message": "Customer email and phone number must be provided as text."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Remove unnecessary spaces
    customer_email = customer_email.strip()
    customer_phone = customer_phone.strip()

    # Validate required fields
    if not customer_email or not customer_phone:
        return Response(
            {"message": "Customer email and phone number are required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Find the reservation using its ID and customer details
    reservation = Reservation.objects.filter(
        id=reservation_id,
        customer_email__iexact=customer_email,
        customer_phone=customer_phone,
    ).first()

    # Check if the reservation exists
    if not reservation:
        return Response(
            {"message": "Reservation not found."},
            status=status.HTTP_404_NOT_FOUND,
        )

    # Convert status code into readable text
    status_display = {
        "P": "Pending",
        "PA": "Paid",
        "E": "Expired",
        "C": "Cancelled",
    }.get(reservation.status, "Unknown")

    # Return customer-facing reservation details
    return Response(
        {
            "message": "Reservation details retrieved successfully.",
            "reservation": {
                "reservation_id": reservation.id,
                "customer_name": reservation.customer_name,
                "customer_email": reservation.customer_email,
                "customer_phone": reservation.customer_phone,
                "reservation_date": reservation.reservation_date,
                "start_time": reservation.start_time,
                "end_time": reservation.end_time,
                # Return monetary values as strings to preserve decimal precision
                "hourly_rate": str(reservation.hourly_rate),
                "total_amount": str(reservation.total_amount),
                "status": reservation.status,
                "status_display": status_display,
                "created_at": reservation.created_at,
            },
        },
        status=status.HTTP_200_OK,
    )
