"""CPF handling. Asaas only creates a charge for a customer with a CPF (or
CNPJ), so it is asked for the first time a person pays online and kept on
the person. It is personal data: never returned by the API, only `has_cpf`."""

import re

from rest_framework import serializers

CPF_REQUIRED_MESSAGE = "Informe o CPF para gerar a cobrança online."


def normalize_cpf(value) -> str:
    return re.sub(r"\D", "", str(value or ""))


def is_valid_cpf(digits: str) -> bool:
    if len(digits) != 11 or not digits.isdigit() or digits == digits[0] * 11:
        return False
    numbers = [int(d) for d in digits]
    for size in (9, 10):
        total = sum(n * w for n, w in zip(numbers[:size], range(size + 1, 1, -1)))
        check = (total * 10) % 11 % 10
        if check != numbers[size]:
            return False
    return True


class CpfField(serializers.CharField):
    """Optional CPF input: accepts "123.456.789-09" or digits; returns digits ("" when blank)."""

    def __init__(self, **kwargs):
        kwargs.setdefault("required", False)
        kwargs.setdefault("allow_blank", True)
        kwargs.setdefault("max_length", 20)
        super().__init__(**kwargs)

    def to_internal_value(self, data):
        digits = normalize_cpf(super().to_internal_value(data))
        if digits and not is_valid_cpf(digits):
            raise serializers.ValidationError("CPF inválido.")
        return digits


def apply_cpf(person, cpf: str) -> None:
    """Keeps a CPF typed at payment time on the person."""
    if cpf and cpf != person.cpf:
        person.cpf = cpf
        person.save(update_fields=["cpf"])


def require_cpf(person) -> None:
    if not person.cpf:
        raise serializers.ValidationError({"cpf": CPF_REQUIRED_MESSAGE})
