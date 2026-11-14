from django.db import models


class EnrollmentStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    COMPLETED = "completed", "Completed"
    DROPPED = "dropped", "Dropped"


class TeacherRole(models.TextChoices):
    TEACHER = "teacher", "Teacher"
    ASSISTANT = "assistant", "Assistant"


class EnrollmentPaymentStatus(models.TextChoices):
    """Where an enrollment stands financially. Independent of EnrollmentStatus:
    finishing the course never depends on having paid."""

    FREE = "free", "Gratuita"
    PENDING = "pending", "Aguardando pagamento"
    PARTIAL = "partial", "Pagando"  # some installments/amount received
    PAID = "paid", "Pago"
    WAIVED = "waived", "Bolsa integral"
    EXPIRED = "expired", "Reserva expirada"
    CANCELLED = "cancelled", "Cancelada"
    REFUNDED = "refunded", "Reembolsada"


# Statuses that hold a seat in the class.
SEAT_HOLDING_PAYMENT_STATUSES = [
    EnrollmentPaymentStatus.FREE,
    EnrollmentPaymentStatus.PENDING,
    EnrollmentPaymentStatus.PARTIAL,
    EnrollmentPaymentStatus.PAID,
    EnrollmentPaymentStatus.WAIVED,
]

# Statuses after which the enrollment is closed financially (a new enrollment
# reuses the row and starts over).
CLOSED_PAYMENT_STATUSES = [
    EnrollmentPaymentStatus.EXPIRED,
    EnrollmentPaymentStatus.CANCELLED,
    EnrollmentPaymentStatus.REFUNDED,
]


class PaymentMethod(models.TextChoices):
    ASAAS = "asaas", "Online (Asaas)"
    MANUAL = "manual", "Presencial"
