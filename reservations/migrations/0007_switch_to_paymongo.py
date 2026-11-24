from django.db import migrations, models


def expire_legacy_stripe_checkouts(apps, schema_editor):
    """Pending rows tied to a Stripe session can no longer be paid."""
    Reservation = apps.get_model("reservations", "Reservation")

    Reservation.objects.filter(
        status="P",
        paymongo_checkout_session_id__isnull=False,
    ).exclude(paymongo_checkout_session_id="").update(status="E")


class Migration(migrations.Migration):

    dependencies = [
        ("reservations", "0006_alter_reservation_stripe_session_id_and_more"),
    ]

    operations = [
        migrations.RenameField(
            model_name="reservation",
            old_name="stripe_session_id",
            new_name="paymongo_checkout_session_id",
        ),
        migrations.RenameField(
            model_name="reservation",
            old_name="stripe_refund_id",
            new_name="paymongo_refund_id",
        ),
        migrations.AddField(
            model_name="reservation",
            name="paymongo_payment_id",
            field=models.CharField(blank=True, max_length=255, null=True, unique=True),
        ),
        migrations.RenameModel(
            old_name="StripeEvent",
            new_name="PaymongoEvent",
        ),
        migrations.RunPython(expire_legacy_stripe_checkouts, migrations.RunPython.noop),
    ]
