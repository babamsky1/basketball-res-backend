from django.contrib import admin
from .models import Reservation


# Display reservations in Django Admin
@admin.register(Reservation)
class ReservationAdmin(admin.ModelAdmin):

    # Columns displayed in the reservation list
    list_display = (
        "id",
        "customer_name",
        "customer_email",
        "reservation_date",
        "start_time",
        "end_time",
        "total_amount",
        "status",
        "created_at",
    )

    # Filters on the right side
    list_filter = (
        "status",
        "reservation_date",
    )

    # Search reservations by customer details
    search_fields = (
        "customer_name",
        "customer_email",
        "customer_phone",
    )

    # Default sorting: newest reservations first
    ordering = ("-created_at",)
