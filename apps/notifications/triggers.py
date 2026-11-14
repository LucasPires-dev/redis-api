"""What each feature tells people, and where the notification leads.

Kept in one place so the wording stays consistent. Every function here is
called right where the fact happens and only calls `notify()`, which never
raises.
"""

from django.utils import timezone

from .service import notify


def money_text(cents) -> str:
    return f"R$ {cents / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def when_text(dt) -> str:
    return timezone.localtime(dt).strftime("%d/%m às %H:%M")


def class_label(course_class) -> str:
    return f"{course_class.course.name} — {course_class.name}"


# ── Courses ──────────────────────────────────────────────────────────────────


def waitlist_offer(entry):
    """A seat opened and is now held for this person in line."""
    notify(
        entry.person_id,
        "waitlist_offer",
        f"Abriu uma vaga: {class_label(entry.course_class)}",
        body=f"A vaga fica reservada para você até {when_text(entry.offer_expires_at)}. "
        "Faça a inscrição para garantir.",
        link="/app/cursos",
        dedupe_key=f"waitlist-offer:{entry.pk}",
    )


def enrollment_payment(payment):
    """Money received for a course enrollment (online or in person)."""
    enrollment = payment.enrollment
    balance = enrollment.amount_cents - enrollment.paid_cents
    body = f"Recebemos {money_text(payment.amount_cents)}."
    body += f" Falta {money_text(balance)}." if balance > 0 else " Sua inscrição está quitada."
    notify(
        enrollment.person_id,
        "enrollment_payment",
        f"Pagamento recebido: {class_label(enrollment.course_class)}",
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


# ── Serving roster ───────────────────────────────────────────────────────────

MY_ROSTER = "/app/eventos/escalas"


def roster_place(entry) -> str:
    """"Mídia · Câmera 2 — 14/11 às 19:00"."""
    place = entry.ministry.name + (f" · {entry.function}" if entry.function else "")
    return f"{place} — {when_text(entry.event.starts_at)}"


def _production(event_id) -> str:
    return f"/app/eventos/{event_id}/producao"


def roster_invited(entry):
    if entry.invited_by_id == entry.person_id:
        return
    notify(
        entry.person_id,
        "roster_invite",
        f"Você foi escalado(a): {entry.event.title}",
        body=f"{roster_place(entry)}. Confirme ou recuse em Minhas escalas.",
        link=MY_ROSTER,
        dedupe_key=f"roster-invite:{entry.pk}:{entry.person_id}",
    )


def roster_declined(entry, by):
    """Whoever invited learns the person can't serve, to find someone else."""
    if not entry.invited_by_id or entry.invited_by_id == by.pk:
        return
    notify(
        entry.invited_by_id,
        "roster_declined",
        f"{entry.person.name} não poderá servir: {entry.event.title}",
        body=f"Recusou a escala de {roster_place(entry)}. Convide outra pessoa.",
        link=_production(entry.event_id),
        dedupe_key=f"roster-declined:{entry.pk}:{entry.person_id}",
    )


def roster_swap_requested(swap):
    entry = swap.entry
    body = f"{roster_place(entry)}."
    if swap.message:
        body += f" “{swap.message}”"
    notify(
        swap.substitute_id,
        "roster_swap",
        f"{swap.requested_by.name} pediu para você servir no lugar: {entry.event.title}",
        body=body,
        link=MY_ROSTER,
        dedupe_key=f"roster-swap:{swap.pk}",
    )


def roster_swap_answered(swap, accepted: bool):
    entry, sub = swap.entry, swap.substitute
    if accepted:
        title = f"{sub.name} assumiu seu lugar: {entry.event.title}"
        body = f"Você saiu da escala de {roster_place(entry)}."
    else:
        title = f"{sub.name} não pode trocar: {entry.event.title}"
        body = "Você continua na escala. Peça a outra pessoa ou fale com a liderança."
    notify(
        swap.requested_by_id,
        "roster_swap_answered",
        title,
        body=body,
        link=MY_ROSTER,
        dedupe_key=f"roster-swap-answered:{swap.pk}",
    )
    if accepted and entry.invited_by_id not in (None, swap.requested_by_id, swap.substitute_id):
        notify(
            entry.invited_by_id,
            "roster_swap_answered",
            f"Troca na escala: {entry.event.title}",
            body=f"{sub.name} entra no lugar de {swap.requested_by.name} ({roster_place(entry)}).",
            link=_production(entry.event_id),
            dedupe_key=f"roster-swap-leader:{swap.pk}",
        )


# ── Demands between ministries ───────────────────────────────────────────────

MY_DEMANDS = "/app/eventos/demandas"


def _demand_target_people(demand):
    """The leaders of the ministry that received it (at its campus); with none,
    the general leaders of that kind of ministry."""
    from apps.churches.models import MinistryGeneralLeadership, MinistryLeadership

    local = MinistryLeadership.objects.filter(ministry__kind=demand.to_ministry_kind)
    if demand.to_campus_id:
        local = local.filter(ministry__campus_id=demand.to_campus_id)
    ids = set(local.values_list("person_id", flat=True))
    if not ids:
        ids = set(
            MinistryGeneralLeadership.objects.filter(kind=demand.to_ministry_kind).values_list("leaders", flat=True)
        )
    ids.discard(None)
    ids.discard(demand.created_by_id)
    return ids


def _demand_label(demand) -> str:
    text = demand.get_demand_type_display()
    if demand.event_id:
        text += f" — {demand.event.title}"
    return text


def demand_received(demand):
    title = f"Nova demanda para {demand.get_to_ministry_kind_display()}: {_demand_label(demand)}"
    if demand.priority == "urgent":
        title = f"Urgente — {title}"
    body = demand.description[:200]
    if demand.due_date:
        body = f"Para {demand.due_date:%d/%m}. {body}".strip()
    notify(
        _demand_target_people(demand),
        "demand_received",
        title,
        body=body,
        link=MY_DEMANDS,
        dedupe_key=f"demand:{demand.pk}",
    )


DEMAND_ANSWER = {
    "in_progress": "Demanda aceita",
    "done": "Demanda concluída",
    "rejected": "Demanda recusada",
}


def demand_answered(demand, by):
    if not demand.created_by_id or demand.created_by_id == by.pk:
        return
    body = f"Por {demand.get_to_ministry_kind_display()}."
    if demand.status == "rejected" and demand.rejection_reason:
        body += f" Motivo: {demand.rejection_reason}"
    notify(
        demand.created_by_id,
        "demand_answered",
        f"{DEMAND_ANSWER.get(demand.status, 'Demanda atualizada')}: {_demand_label(demand)}",
        body=body,
        link=MY_DEMANDS,
        dedupe_key=f"demand-{demand.status}:{demand.pk}",
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
