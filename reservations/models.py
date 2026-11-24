import uuid

from django.db import models


class Reservation(models.Model):

    # Available reservation statuses
    STATUS_CHOICES = [
        ("P", "Pending"),
        ("PA", "Paid"),
        ("E", "Expired"),
        ("C", "Cancelled"),
    ]

    # Use UUID instead of sequential reservation IDs
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    # Customer information
    customer_name = models.CharField(max_length=150)
    customer_email = models.EmailField()
    customer_phone = models.CharField(max_length=20)

    # Reservation schedule
    reservation_date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()

    # Payment information
    hourly_rate = models.DecimalField(max_digits=10, decimal_places=2)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)

    # Restrict reservation status to the available choices
    status = models.CharField(
        max_length=2,
        choices=STATUS_CHOICES,
        default="P",
    )

    # Each PayMongo Checkout Session can belong to only one reservation
    paymongo_checkout_session_id = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        unique=True,
    )

    # PayMongo Payment ID is required when refunding a paid checkout
    paymongo_payment_id = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        unique=True,
    )

    # Each PayMongo Refund ID can belong to only one reservation
    paymongo_refund_id = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        unique=True,
    )

    # Record when the reservation was created
    created_at = models.DateTimeField(auto_now_add=True)

    # Automatically expire unpaid reservations after a time limit
    expires_at = models.DateTimeField(null=True, blank=True)

    # Database-level rules and performance indexes
    class Meta:

        # Improve searches by reservation date and status
        indexes = [
            models.Index(
                fields=["reservation_date", "status"],
                name="reservation_date_status_idx",
            ),
        ]

        # Protect reservation records from invalid values
        constraints = [
            # End time must be later than start time
            models.CheckConstraint(
                condition=models.Q(start_time__lt=models.F("end_time")),
                name="reservation_end_after_start",
            ),
            # Hourly rate must be greater than zero
            models.CheckConstraint(
                condition=models.Q(hourly_rate__gt=0),
                name="reservation_hourly_rate_positive",
            ),
            # Total payment must be greater than zero
            models.CheckConstraint(
                condition=models.Q(total_amount__gt=0),
                name="reservation_total_amount_positive",
            ),
            # Only supported reservation statuses are allowed
            models.CheckConstraint(
                condition=models.Q(status__in=["P", "PA", "E", "C"]),
                name="reservation_valid_status",
            ),
        ]

    def __str__(self):
        return f"{self.customer_name} - {self.reservation_date}"


class PaymongoEvent(models.Model):

    # Unique PayMongo event ID prevents duplicate event records
    event_id = models.CharField(max_length=255, unique=True)

    # Record when this event was received
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.event_id
