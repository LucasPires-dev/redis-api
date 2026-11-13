"""What each feature tells people, and where the notification leads.

Kept in one place so the wording stays consistent. Every function here is
called right where the fact happens and only calls `notify()`, which never
raises.
"""

from django.utils import timezone

from .service import notify


def _money(cents) -> str:
    return f"R$ {cents / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _when(dt) -> str:
    return timezone.localtime(dt).strftime("%d/%m às %H:%M")


def _class_label(course_class) -> str:
    return f"{course_class.course.name} — {course_class.name}"


# ── Courses ──────────────────────────────────────────────────────────────────


def waitlist_offer(entry):
    """A seat opened and is now held for this person in line."""
    notify(
        entry.person_id,
        "waitlist_offer",
        f"Abriu uma vaga: {_class_label(entry.course_class)}",
        body=f"A vaga fica reservada para você até {_when(entry.offer_expires_at)}. "
        "Faça a inscrição para garantir.",
        link="/app/cursos",
        dedupe_key=f"waitlist-offer:{entry.pk}",
    )


def enrollment_payment(payment):
    """Money received for a course enrollment (online or in person)."""
    enrollment = payment.enrollment
    balance = enrollment.amount_cents - enrollment.paid_cents
    body = f"Recebemos {_money(payment.amount_cents)}."
    body += f" Falta {_money(balance)}." if balance > 0 else " Sua inscrição está quitada."
    notify(
        enrollment.person_id,
        "enrollment_payment",
        f"Pagamento recebido: {_class_label(enrollment.course_class)}",
        body=body,
        link="/app/cursos/matriculas",
        dedupe_key=f"enrollment-payment:{payment.pk}",
    )


def course_completed(enrollment):
    """The person finished a class: congratulations and the certificate."""
    notify(
        enrollment.person_id,
        "course_completed",
        f"Você concluiu {enrollment.course_class.course.name}!",
        body="Seu certificado já está disponível em Minhas matrículas.",
        link="/app/cursos/matriculas",
        dedupe_key=f"course-completed:{enrollment.pk}",
    )


# ── Events ───────────────────────────────────────────────────────────────────


def event_registration_confirmed(registration):
    notify(
        registration.person_id,
        "event_registration",
        f"Inscrição confirmada: {registration.event.title}",
        body="Seu pagamento foi confirmado." if registration.asaas_payment_id else "",
        link="/app/eventos/inscricoes",
        dedupe_key=f"event-registration:{registration.pk}",
    )


# ── Joining a campus ─────────────────────────────────────────────────────────


def join_request_created(join_request):
    """Tells the campus leadership (or, with none, the managers) someone asked to join."""
    from django.contrib.auth import get_user_model

    campus = join_request.campus
    recipients = [lead.person_id for lead in campus.leaderships.all()]
    if not recipients:
        recipients = list(
            get_user_model().objects.filter(is_manager=True, is_active=True).values_list("pk", flat=True)
        )
    notify(
        recipients,
        "join_request",
        f"Novo pedido de vínculo: {join_request.person.name}",
        body=f"Quer fazer parte do campus {campus.name}.",
        link=f"/app/campus/{campus.pk}#pedidos",
        dedupe_key=f"join-request:{join_request.pk}",
    )


def join_request_decided(join_request, approved: bool):
    campus = join_request.campus
    if approved:
        title = f"Bem-vindo(a) ao campus {campus.name}!"
        body = "Seu pedido de vínculo foi aprovado."
        link = "/app"
    else:
        title = f"Pedido de vínculo ao campus {campus.name} recusado"
        body = "Fale com a liderança do campus ou peça vínculo a outro campus."
        link = ""
    notify(
        join_request.person_id,
        "join_request_decided",
        title,
        body=body,
        link=link,
        dedupe_key=f"join-request-decided:{join_request.pk}",
    )


# ── Appointments ─────────────────────────────────────────────────────────────


def appointed(person_ids, role: str, of: str, link: str, key: str = ""):
    """The person got a responsibility: "Você agora é <role> <of>"."""
    notify(
        person_ids,
        "appointment",
        f"Você agora é {role} {of}",
        link=link,
        dedupe_key=f"appointment:{key}" if key else "",
    )
