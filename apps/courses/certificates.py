"""Completion certificates.

Every completed enrollment gets a verification code (12 characters without
look-alikes such as O/0 and I/1, shown in groups of four). The PDF carries the
code and the address of the public page that confirms it, so anyone receiving
a certificate can check it's genuine.

The certificate is valid while the enrollment stays completed: reopening it
(back to active) makes the code answer "not found" until it's completed again.
"""

import io
import secrets
import unicodedata

from django.conf import settings
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas

from .choices import EnrollmentStatus
from .models import Enrollment

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 12

MONTHS = [
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
]


def new_code():
    while True:
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        if not Enrollment.objects.filter(certificate_code=code).exists():
            return code


def normalize_code(raw):
    """What a person types or a link carries → the stored form (no dashes/spaces, upper case)."""
    return "".join(ch for ch in (raw or "").upper() if ch.isalnum())


def display_code(code):
    return "-".join(code[i:i + 4] for i in range(0, len(code), 4))


def ensure_code(enrollment):
    """Gives a completed enrollment its code (once; kept if reopened and completed again)."""
    if not enrollment.certificate_code:
        enrollment.certificate_code = new_code()
        enrollment.save(update_fields=["certificate_code", "updated_at"])
    return enrollment.certificate_code


def find_valid(code):
    """The completed enrollment behind a code, or None."""
    code = normalize_code(code)
    if len(code) != CODE_LENGTH:
        return None
    return (
        Enrollment.objects.select_related(
            "person", "course_class__course__church", "course_class__campus"
        )
        .filter(certificate_code=code, status=EnrollmentStatus.COMPLETED)
        .first()
    )


def long_date(value):
    return f"{value.day} de {MONTHS[value.month - 1]} de {value.year}"


def completion_date(enrollment):
    completed_at = enrollment.completed_at or timezone.now()
    return timezone.localtime(completed_at).date()


def public_data(enrollment):
    """What the public verification page shows — no contact data."""
    from . import completion

    course_class = enrollment.course_class
    attendance = completion.attendance_by_person(course_class).get(
        enrollment.person_id, completion.EMPTY_SUMMARY
    )
    return {
        "code": display_code(enrollment.certificate_code),
        "person_name": enrollment.person.name,
        "course_name": course_class.course.name,
        "class_name": course_class.name,
        "campus_name": course_class.campus.name,
        "church_name": course_class.course.church.name,
        "start_date": course_class.start_date,
        "end_date": course_class.end_date,
        "completed_on": completion_date(enrollment),
        "lesson_count": course_class.lessons.count(),
        "attendance_percent": attendance["percent"],
        "teachers": [t.person.name for t in course_class.teachers.select_related("person").order_by("role")],
    }


def verification_url(code, origin=""):
    base = (getattr(settings, "FRONTEND_URL", "") or origin or "").rstrip("/")
    return f"{base}/certificado/{display_code(code)}" if base else ""


# ── PDF ──────────────────────────────────────────────────────────────────────

INK = colors.HexColor("#1f2933")
MUTED = colors.HexColor("#616e7c")
ACCENT = colors.HexColor("#3e4c59")


def _centered(pdf, text, y, font, size, color=INK, width=None):
    """Draws text centred on the page, wrapping when wider than `width`; returns the next y."""
    page_width = pdf._pagesize[0]
    width = width or page_width - 200
    pdf.setFont(font, size)
    pdf.setFillColor(color)
    for line in simpleSplit(text, font, size, width):
        pdf.drawCentredString(page_width / 2, y, line)
        y -= size * 1.35
    return y


def build_pdf(enrollment, origin=""):
    """The certificate as PDF bytes (A4 landscape)."""
    data = public_data(enrollment)
    buffer = io.BytesIO()
    page_width, page_height = landscape(A4)
    pdf = canvas.Canvas(buffer, pagesize=(page_width, page_height))
    pdf.setTitle(f"Certificado — {data['course_name']} — {data['person_name']}")
    pdf.setAuthor(data["church_name"])

    # Frame.
    pdf.setStrokeColor(ACCENT)
    pdf.setLineWidth(2)
    pdf.rect(28, 28, page_width - 56, page_height - 56)
    pdf.setLineWidth(0.6)
    pdf.rect(38, 38, page_width - 76, page_height - 76)

    y = page_height - 110
    y = _centered(pdf, data["church_name"].upper(), y, "Helvetica-Bold", 13, MUTED)
    y = _centered(pdf, f"Campus {data['campus_name']}", y + 2, "Helvetica", 11, MUTED)

    y -= 42
    y = _centered(pdf, "CERTIFICADO DE CONCLUSÃO", y, "Helvetica-Bold", 30, INK)

    y -= 40
    y = _centered(pdf, "Certificamos que", y, "Helvetica", 14, MUTED)
    y -= 8
    y = _centered(pdf, data["person_name"], y, "Helvetica-Bold", 26, INK)

    y -= 20
    lessons = data["lesson_count"]
    period = ""
    if data["start_date"] and data["end_date"]:
        period = (
            f", de {data['start_date'].strftime('%d/%m/%Y')} a {data['end_date'].strftime('%d/%m/%Y')}"
        )
    class_name = data["class_name"]
    if not class_name.lower().startswith("turma"):
        class_name = f"turma {class_name}"
    sentence = (
        f"concluiu o curso {data['course_name']} ({class_name}), "
        f"com {lessons} {'aula' if lessons == 1 else 'aulas'}{period}"
    )
    if data["attendance_percent"] is not None:
        sentence += f" e frequência de {data['attendance_percent']}%"
    y = _centered(pdf, sentence + ".", y, "Helvetica", 14, INK, width=page_width - 220)

    if data["teachers"]:
        y -= 12
        label = "Professor" if len(data["teachers"]) == 1 else "Professores"
        y = _centered(pdf, f"{label}: {', '.join(data['teachers'])}", y, "Helvetica", 12, MUTED)

    y -= 30
    _centered(pdf, f"Concluído em {long_date(data['completed_on'])}", y, "Helvetica-Oblique", 12, MUTED)

    # Verification footer.
    footer = f"Código de verificação: {data['code']}"
    url = verification_url(enrollment.certificate_code, origin)
    _centered(pdf, footer, 78, "Helvetica-Bold", 10, MUTED)
    if url:
        _centered(pdf, f"Confira a autenticidade em {url}", 63, "Helvetica", 9, MUTED)

    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def filename(enrollment):
    def slug(text):
        ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
        return "-".join("".join(ch if ch.isalnum() else " " for ch in ascii_text).split()).lower()

    return f"certificado-{slug(enrollment.course_class.course.name)}-{slug(enrollment.person.name)}.pdf"
