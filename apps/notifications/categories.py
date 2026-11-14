"""Notification kinds grouped into categories, which is what a person chooses
to receive by email. The notification center always shows everything."""

CATEGORIES = {
    "courses": {
        "label": "Cursos",
        "description": "Vagas, pagamentos, certificados e lembretes das suas turmas.",
        "kinds": {
            "waitlist_offer",
            "offer_expiring",
            "reservation_expiring",
            "enrollment_payment",
            "course_completed",
            "class_care",
        },
    },
    "cells": {
        "label": "Células",
        "description": "Encontros para registrar e pessoas para cuidar.",
        "kinds": {"meeting_not_registered", "cell_care"},
    },
    "events": {
        "label": "Eventos",
        "description": "Inscrições confirmadas e lembretes na véspera.",
        "kinds": {"event_registration", "event_tomorrow"},
    },
    "service": {
        "label": "Escalas e produção",
        "description": "Convites de escala, trocas, lembretes de quando você serve e demandas entre ministérios.",
        "kinds": {
            "roster_invite",
            "roster_declined",
            "roster_swap",
            "roster_swap_answered",
            "roster_tomorrow",
            "roster_unanswered",
            "demand_received",
            "demand_answered",
        },
    },
    "church": {
        "label": "Vínculo e responsabilidades",
        "description": "Pedidos de vínculo ao campus e novas responsabilidades.",
        "kinds": {"join_request", "join_request_decided", "appointment"},
    },
}

_BY_KIND = {kind: key for key, cat in CATEGORIES.items() for kind in cat["kinds"]}


def category_of(kind: str) -> str | None:
    return _BY_KIND.get(kind)
