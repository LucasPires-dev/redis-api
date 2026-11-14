"""Birthdays of the month for the people a leader looks after: the members and
leaders of the cells they manage. Only the day and month are shown, never the
year (the age stays private). Filling in the birth date is always optional."""

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.churches.models import Cell
from apps.churches.permissions import can_manage_cell, scope_managed_cells

from .models import Person


def cell_people(cells):
    """{person_id: [cell names]} for the members and leaders of `cells`."""
    found = {}
    for cell in cells.prefetch_related("members", "leaderships"):
        ids = {m.pk for m in cell.members.all()} | {lead.person_id for lead in cell.leaderships.all()}
        for person_id in ids:
            found.setdefault(person_id, []).append(cell.name)
    return found


class BirthdaysView(APIView):
    """GET /api/birthdays/?month=1..12&cell=<id>: day, name and cells."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            month = int(request.query_params.get("month") or timezone.localdate().month)
        except ValueError:
            month = 0
        if not 1 <= month <= 12:
            raise ValidationError({"month": "Mês inválido."})
        cell_id = request.query_params.get("cell")
        if cell_id:
            cell = get_object_or_404(Cell, pk=cell_id)
            if not can_manage_cell(request.user, cell):
                raise PermissionDenied("Só a liderança da célula vê os aniversariantes.")
            cells = Cell.objects.filter(pk=cell.pk)
        else:
            cells = scope_managed_cells(Cell.objects.active(), request.user)
        where = cell_people(cells)
        people = Person.objects.filter(pk__in=where, birth_date__month=month, is_active=True)
        rows = [
            {"id": str(p.pk), "name": p.name, "day": p.birth_date.day, "cells": sorted(where[p.pk])}
            for p in people
        ]
        return Response(sorted(rows, key=lambda r: (r["day"], r["name"])))
