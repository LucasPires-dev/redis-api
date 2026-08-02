from django.db.models import F, Q
from rest_framework.permissions import SAFE_METHODS, BasePermission

from apps.churches.choices import PRODUCTION_MINISTRY_KINDS
from apps.churches.models import Ministry
from apps.churches.permissions import can_manage_campus, can_manage_ministry, general_led_kinds


def _local_ministry_leader(person, kind, campus) -> bool:
    if not kind or not campus:
        return False
    return Ministry.objects.filter(campus=campus, kind=kind, leaderships__person=person).exists()


def _ministry_participant(person, kind, campus) -> bool:
    """Manager-style check for "does this person represent this (kind, campus)":
    general leader of the kind, local leader at that campus, or plain member
    (read/report access, e.g. reporting a rundown item status)."""
    if not kind:
        return False
    if kind in general_led_kinds(person):
        return True
    if not campus:
        return False
    if _local_ministry_leader(person, kind, campus):
        return True
    return Ministry.objects.filter(campus=campus, kind=kind, members=person).exists()


def can_manage_event(person, event) -> bool:
    """Quem pode editar o Evento em si (título, horário, itens do rundown, status
    geral): manager, líder do campus (evento local), líder geral do ministério
    dono (qualquer campus), ou líder local do ministério dono naquele campus."""
    if not person.is_authenticated:
        return False
    if person.is_manager:
        return True
    if event.campus_id and can_manage_campus(person, event.campus):
        return True
    if event.ministry_kind and event.ministry_kind in general_led_kinds(person):
        return True
    return _local_ministry_leader(person, event.ministry_kind, event.campus)


def can_create_event(person, ministry_kind, campus) -> bool:
    """Quem pode criar um Evento: Culto regular (sem ministry_kind) só
    manager/líder do campus informado; Evento de ministério exige liderança
    (geral ou local) daquele ministry_kind."""
    if not person.is_authenticated:
        return False
    if person.is_manager:
        return True
    if not ministry_kind:
        return bool(campus) and can_manage_campus(person, campus)
    return ministry_kind in general_led_kinds(person) or _local_ministry_leader(person, ministry_kind, campus)


def _can_act_for_ministry_on_event(person, event, ministry_kind) -> bool:
    """Regra compartilhada: manager, quem gerencia o evento, ou qualquer
    participante (líder ou membro, geral ou local) do ministry_kind informado.
    Usada tanto pro status 🟢🟡🔴 de item do rundown quanto pro checklist."""
    if not person.is_authenticated:
        return False
    if person.is_manager or can_manage_event(person, event):
        return True
    return _ministry_participant(person, ministry_kind, event.campus)


def can_report_item_status(person, item, ministry_kind) -> bool:
    return _can_act_for_ministry_on_event(person, item.event, ministry_kind)


def can_manage_checklist_item(person, item, ministry_kind=None) -> bool:
    return _can_act_for_ministry_on_event(person, item.event, ministry_kind or item.ministry_kind)


def can_respond_roster_entry(person, entry) -> bool:
    """Confirmar/recusar: a própria pessoa convidada, ou quem gerencia o
    ministério (líder local/geral ou líder de campus, via can_manage_ministry)."""
    if not person.is_authenticated:
        return False
    return person.is_manager or entry.person_id == person.pk or can_manage_ministry(person, entry.ministry)


def can_manage_demand_source(person, demand) -> bool:
    """Lado que abriu a demanda — pode editar/cancelar enquanto ainda aberta."""
    if not person.is_authenticated:
        return False
    if person.is_manager:
        return True
    return _ministry_participant(person, demand.from_ministry_kind, demand.from_campus)


def can_manage_demand_target(person, demand) -> bool:
    """Lado que recebeu a demanda — pode aceitar/concluir/recusar."""
    if not person.is_authenticated:
        return False
    if person.is_manager:
        return True
    return _ministry_participant(person, demand.to_ministry_kind, demand.to_campus)


def scope_events(qs, person):
    """Eventos visíveis: tudo pro manager; Culto regular geral (sem campus/kind) é
    visível a qualquer autenticado; demais eventos, a quem criou, lidera o campus,
    lidera (geral ou local) o ministério dono, ou é membro do ministério dono."""
    if person.is_manager:
        return qs
    kinds = general_led_kinds(person)
    condition = (
        Q(created_by=person)
        | Q(campus__isnull=True, ministry_kind__isnull=True)
        | Q(campus__leaderships__person=person)
        | Q(campus__ministries__kind=F("ministry_kind"), campus__ministries__leaderships__person=person)
        | Q(campus__ministries__kind=F("ministry_kind"), campus__ministries__members=person)
        # Ministérios técnicos/produção apoiam TODOS os eventos do campus deles
        # (não só os do próprio kind) — precisam ver o rundown inteiro para
        # reportar status, mesmo sem ser o ministério dono do evento.
        | Q(
            campus__ministries__kind__in=PRODUCTION_MINISTRY_KINDS,
            campus__ministries__leaderships__person=person,
        )
        | Q(campus__ministries__kind__in=PRODUCTION_MINISTRY_KINDS, campus__ministries__members=person)
    )
    if kinds:
        condition |= Q(ministry_kind__in=kinds)
    return qs.filter(condition).distinct()


def _demand_side_condition(prefix: str, person) -> Q:
    """Constrói o Q de "esta pessoa representa o lado `prefix` ('from'/'to') desta
    Demand": líder de campus daquele lado, líder (geral ou local) ou membro do
    ministério daquele lado. F() compara com o campo *_ministry_kind da própria
    linha, já que o kind varia por Demand (diferente do reconciliation, que
    compara com uma constante fixa)."""
    kind_field, campus_field = f"{prefix}_ministry_kind", f"{prefix}_campus"
    condition = Q(**{f"{campus_field}__leaderships__person": person})
    condition |= Q(**{f"{campus_field}__ministries__kind": F(kind_field), f"{campus_field}__ministries__leaderships__person": person})
    condition |= Q(**{f"{campus_field}__ministries__kind": F(kind_field), f"{campus_field}__ministries__members": person})
    kinds = general_led_kinds(person)
    if kinds:
        condition |= Q(**{f"{kind_field}__in": kinds})
    return condition


def scope_demands(qs, person, direction=None):
    """Demandas visíveis: tudo pro manager. Sem `direction`: quem criou ou
    representa qualquer um dos dois lados. `direction="sent"`: só o lado de
    origem (+ quem criou). `direction="received"`: só o lado de destino."""
    if person.is_manager:
        return qs
    if direction == "sent":
        condition = _demand_side_condition("from", person) | Q(created_by=person)
    elif direction == "received":
        condition = _demand_side_condition("to", person)
    else:
        condition = _demand_side_condition("from", person) | _demand_side_condition("to", person) | Q(created_by=person)
    return qs.filter(condition).distinct()


class EventPermission(BasePermission):
    """Leitura: quem enxerga (queryset já filtrado por scope_events). Criação:
    valida ministry_kind/campus_id do corpo via can_create_event. Edição do
    Evento e dos itens/status do rundown: gated na view (RundownItemPermission)."""

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if view.action == "create":
            ministry_kind = request.data.get("ministry_kind") or None
            campus_id = request.data.get("campus_id") or None
            from apps.churches.models import Campus

            campus = Campus.objects.filter(pk=campus_id).first() if campus_id else None
            return can_create_event(request.user, ministry_kind, campus)
        return True

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        return can_manage_event(request.user, obj)


class RundownItemPermission(BasePermission):
    """Criar/editar/remover item: quem gerencia o evento. Marcar status
    (`set_status`): qualquer participante do ministério informado no corpo."""

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if view.action == "create":
            from .models import Event

            event_id = request.data.get("event_id")
            event = Event.objects.filter(pk=event_id).first() if event_id else None
            if event is None:
                return True  # deixa a validação do serializer gerar um 400 limpo
            return can_manage_event(request.user, event)
        return True

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        if view.action == "set_status":
            ministry_kind = request.data.get("ministry_kind")
            return can_report_item_status(request.user, obj, ministry_kind)
        return can_manage_event(request.user, obj.event)


class DemandPermission(BasePermission):
    """Criar: quem representa o ministério de origem informado no corpo. Editar
    (lado de origem, enquanto aberta): can_manage_demand_source. Aceitar/
    concluir/recusar (lado de destino): can_manage_demand_target, checado na
    própria action da view (não aqui, já que o método HTTP é o mesmo POST para
    ações distintas)."""

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if view.action == "create":
            from_kind = request.data.get("from_ministry_kind")
            from apps.churches.models import Campus

            from_campus_id = request.data.get("from_campus_id")
            from_campus = Campus.objects.filter(pk=from_campus_id).first() if from_campus_id else None
            if not from_kind:
                return True  # deixa a validação do serializer gerar um 400 limpo
            return _ministry_participant(request.user, from_kind, from_campus) or request.user.is_manager
        return True

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        if view.action in ("accept", "complete", "reject"):
            return can_manage_demand_target(request.user, obj)
        return can_manage_demand_source(request.user, obj)


class EventRosterEntryPermission(BasePermission):
    """Convidar (criar): quem gerencia o ministério informado no corpo.
    Confirmar/recusar: can_respond_roster_entry (o próprio convidado ou quem
    gerencia o ministério). Remover: quem gerencia o ministério."""

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if view.action == "create":
            ministry_id = request.data.get("ministry_id")
            ministry = Ministry.objects.filter(pk=ministry_id).first() if ministry_id else None
            if ministry is None:
                return True  # deixa a validação do serializer gerar um 400 limpo
            return can_manage_ministry(request.user, ministry)
        return True

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        if view.action in ("confirm", "decline"):
            return can_respond_roster_entry(request.user, obj)
        return can_manage_ministry(request.user, obj.ministry)


class EventChecklistItemPermission(BasePermission):
    """Criar/marcar feito: quem pode agir pelo ministry_kind informado no corpo
    naquele evento (_can_act_for_ministry_on_event)."""

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if view.action == "create":
            from .models import Event

            event_id = request.data.get("event_id")
            ministry_kind = request.data.get("ministry_kind")
            event = Event.objects.filter(pk=event_id).first() if event_id else None
            if event is None or not ministry_kind:
                return True  # deixa a validação do serializer gerar um 400 limpo
            return _can_act_for_ministry_on_event(request.user, event, ministry_kind)
        return True

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        return can_manage_checklist_item(request.user, obj)
