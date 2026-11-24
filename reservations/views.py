from decimal import Decimal

from django.db import transaction, IntegrityError
from django.utils import timezone
from datetime import timedelta
from django.utils.dateparse import parse_date, parse_time

from rest_framework.decorators import api_view, throttle_scope
from rest_framework.response import Response
from rest_framework import status

from .models import Reservation, StripeEvent

import stripe

from django.conf import settings
from django.http import HttpResponse
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

    try:
        # Initialize Stripe using our secret key
        client = stripe.StripeClient(settings.STRIPE_SECRET_KEY)

        # Retrieve the Stripe Checkout Session if one exists
        session = None

        if reservation.stripe_session_id:
            session = client.v1.checkout.sessions.retrieve(
                reservation.stripe_session_id
            )

        # Check whether Stripe already received payment
        is_paid = session is not None and session.payment_status == "paid"

        # Handle paid reservations
        if reservation.status == "PA" or is_paid:

            # Make sure a Stripe Session exists
            if not session:
                return Response(
                    {"message": "Cannot refund: Stripe Checkout Session not found."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Prevent duplicate refunds
            if reservation.stripe_refund_id:
                return Response(
                    {"message": "A refund has already been initiated."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Get the PaymentIntent associated with Checkout
            payment_intent = session.payment_intent

            if hasattr(payment_intent, "id"):
                payment_intent = payment_intent.id

            if not payment_intent:
                return Response(
                    {"message": "Stripe PaymentIntent not found."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Create a stable idempotency key for this reservation's refund
            idempotency_key = f"reservation-refund-{reservation.id}"

            # Create a full refund through Stripe
            refund = client.v1.refunds.create(
                params={
                    "payment_intent": payment_intent,
                },
                options={
                    "idempotency_key": idempotency_key,
                },
            )

            # Save the refund ID and cancel the reservation
            reservation.stripe_refund_id = refund.id
            reservation.status = "C"

            reservation.save(
                update_fields=[
                    "stripe_refund_id",
                    "status",
                ]
            )

            return Response(
                {
                    "message": "Reservation cancelled and refund initiated.",
                    "reservation_id": str(reservation.id),
                    "status": reservation.status,
                    "refund_status": refund.status,
                },
                status=status.HTTP_200_OK,
            )

        # Handle pending reservations with an open Checkout Session
        if session and session.status == "open":

            # Expire the existing Stripe Checkout Session
            client.v1.checkout.sessions.expire(reservation.stripe_session_id)

        # Do not cancel a completed but unpaid Checkout Session
        elif session and session.status == "complete":
            return Response(
                {"message": "Checkout is complete, but payment is not yet confirmed."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Handle sessions already expired in Stripe
        elif session and session.status == "expired":

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

    except stripe.StripeError:
        # Avoid exposing internal Stripe error details
        return Response(
            {
                "message": "Unable to process the cancellation or refund. Please try again."
            },
            status=status.HTTP_502_BAD_GATEWAY,
        )


# Create a Stripe Checkout Session
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
    if reservation.stripe_session_id:
        return Response(
            {
                "message": "A checkout session has already been created.",
                "message_detail": "Please use your existing checkout session.",
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        # Initialize Stripe using our secret key
        client = stripe.StripeClient(settings.STRIPE_SECRET_KEY)

        # Use a fixed expiration based on the original reservation creation time
        stripe_expiration = reservation.created_at + timedelta(minutes=61)

        # Create a stable idempotency key for this reservation
        idempotency_key = f"reservation-checkout-{reservation.id}"

        # Create Stripe Checkout Session
        session = client.v1.checkout.sessions.create(
            params={
                "mode": "payment",
                "customer_email": reservation.customer_email,
                "line_items": [
                    {
                        "price_data": {
                            "currency": "php",
                            "product_data": {
                                "name": "Basketball Court Reservation",
                            },
                            # Stripe uses centavos
                            "unit_amount": int(reservation.total_amount * 100),
                        },
                        "quantity": 1,
                    }
                ],
                # Stripe Checkout expiration
                "expires_at": int(stripe_expiration.timestamp()),
                # Redirect URLs after checkout
                "success_url": "http://localhost:8000/api/payment-success/",
                "cancel_url": "http://localhost:8000/api/payment-cancelled/",
                # Link payment to our reservation
                "metadata": {
                    "reservation_id": str(reservation.id),
                },
            },
            # Reuse the same key if the same operation is retried
            options={
                "idempotency_key": idempotency_key,
            },
        )

        # Save Stripe Session ID and synchronize expiration
        reservation.expires_at = stripe_expiration
        reservation.stripe_session_id = session.id

        reservation.save(
            update_fields=[
                "expires_at",
                "stripe_session_id",
            ]
        )

        return Response(
            {
                "message": "Checkout session created.",
                "checkout_url": session.url,
                "session_id": session.id,
            },
            status=status.HTTP_200_OK,
        )

    except stripe.StripeError:
        # Avoid exposing internal Stripe error details
        return Response(
            {
                "message": "Unable to create the Stripe Checkout Session. Please try again."
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


# Receive payment notifications from Stripe
@csrf_exempt
def stripe_webhook(request):

    # Only accept POST requests
    if request.method != "POST":
        return HttpResponse(status=405)

    # Get Stripe signature
    signature = request.headers.get("stripe-signature")

    try:
        # Verify that the request came from Stripe
        event = stripe.Webhook.construct_event(
            request.body,
            signature,
            settings.STRIPE_WEBHOOK_SECRET,
        )

    except ValueError:
        return HttpResponse(status=400)

    except stripe.SignatureVerificationError:
        return HttpResponse(status=400)

    # Convert Stripe event into a regular Python dictionary
    event_data = event.to_dict()

    # Get event ID and type
    event_id = event_data.get("id")
    event_type = event_data.get("type")

    # Validate required event information
    if not event_id or not event_type:
        return HttpResponse(status=400)

    try:
        # Process and record the event in one transaction
        with transaction.atomic():

            # Attempt to register the event ID
            try:
                # Use a nested transaction to handle duplicate events
                with transaction.atomic():
                    StripeEvent.objects.create(event_id=event_id)

            except IntegrityError:
                # This event was already recorded
                return HttpResponse(status=200)

            # Process supported Checkout Session events
            if event_type in [
                "checkout.session.expired",
                "checkout.session.completed",
                "checkout.session.async_payment_succeeded",
            ]:

                # Get Checkout Session data
                session = event_data["data"]["object"]

                # Get reservation information from Stripe metadata
                metadata = session.get("metadata") or {}
                reservation_id = metadata.get("reservation_id")
                session_id = session.get("id")

                # Find and lock the matching reservation
                if reservation_id and session_id:

                    reservation = (
                        Reservation.objects.select_for_update()
                        .filter(
                            id=reservation_id,
                            stripe_session_id=session_id,
                        )
                        .first()
                    )

                    if reservation:

                        # Handle expired Checkout Sessions
                        if event_type == "checkout.session.expired":

                            if reservation.status == "P":
                                reservation.status = "E"

                                reservation.save(update_fields=["status"])

                        # Handle successful payments
                        elif session.get("payment_status") == "paid":

                            # Process unpaid or already-expired reservations
                            if reservation.status in ["P", "E"]:

                                # Check whether payment arrived after expiration
                                is_late_payment = reservation.status == "E" or (
                                    reservation.expires_at
                                    and reservation.expires_at <= timezone.now()
                                )

                                # Automatically refund late payments
                                if is_late_payment:

                                    # Avoid creating another refund
                                    if not reservation.stripe_refund_id:

                                        # Get PaymentIntent from Checkout Session
                                        payment_intent = session.get("payment_intent")

                                        # Handle expanded PaymentIntent objects
                                        if isinstance(payment_intent, dict):
                                            payment_intent = payment_intent.get("id")

                                        if not payment_intent:
                                            raise ValueError(
                                                "PaymentIntent missing from paid Checkout Session."
                                            )

                                        # Initialize Stripe
                                        client = stripe.StripeClient(
                                            settings.STRIPE_SECRET_KEY
                                        )

                                        # Use a stable refund idempotency key
                                        idempotency_key = (
                                            f"reservation-refund-{reservation.id}"
                                        )

                                        # Create full refund through Stripe
                                        refund = client.v1.refunds.create(
                                            params={
                                                "payment_intent": payment_intent,
                                            },
                                            options={
                                                "idempotency_key": idempotency_key,
                                            },
                                        )

                                        # Save Stripe Refund ID
                                        reservation.stripe_refund_id = refund.id

                                    # Keep the reservation expired
                                    reservation.status = "E"

                                    reservation.save(
                                        update_fields=[
                                            "status",
                                            "stripe_refund_id",
                                        ]
                                    )

                                # Accept payment before expiration
                                elif reservation.status == "P":

                                    reservation.status = "PA"

                                    reservation.save(update_fields=["status"])

            # Save event only after processing succeeds
            StripeEvent.objects.create(event_id=event_id)

        # Confirm successful event processing
        return HttpResponse(status=200)

    except Exception:
        # Return an error so Stripe can retry the event
        return HttpResponse(status=500)


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
