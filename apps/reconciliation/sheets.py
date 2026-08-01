import base64
import hashlib
import json
from datetime import datetime

from django.conf import settings
from django.utils import timezone
from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

# Header-name (lowercased, whitespace-collapsed) -> model field. Column-NAME-based,
# not positional — the Forms owner can reorder/rename columns as long as one of
# these aliases still appears in the header row.
COLUMN_ALIASES = {
    "carimbo de data/hora": "submitted_at",
    "nome": "name",
    "nome completo": "name",
    "telefone": "phone",
    "whatsapp": "phone",
    "número de whatsapp": "phone",
    "email": "email",
    "e-mail": "email",
    "campus": "raw_campus_label",
    "qual campus você frequenta": "raw_campus_label",
}


def _normalize_header(h: str) -> str:
    return " ".join(h.strip().lower().split())


def build_sheets_client():
    info = json.loads(base64.b64decode(settings.GOOGLE_SHEETS_CREDENTIALS_JSON))
    credentials = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    return build("sheets", "v4", credentials=credentials)


def fetch_rows() -> list[dict]:
    """Returns a list of dicts keyed by model field name, built from the sheet's own
    header row (row 1) — NOT by column position."""
    client = build_sheets_client()
    result = (
        client.spreadsheets()
        .values()
        .get(spreadsheetId=settings.GOOGLE_SHEETS_SPREADSHEET_ID, range=settings.GOOGLE_SHEETS_RANGE)
        .execute()
    )
    values = result.get("values", [])
    if not values:
        return []
    header, *data_rows = values
    field_by_col = {i: COLUMN_ALIASES.get(_normalize_header(h)) for i, h in enumerate(header)}

    rows = []
    for raw in data_rows:
        row = {}
        raw_row_dict = {}
        for i, cell in enumerate(raw):
            raw_row_dict[header[i] if i < len(header) else f"col_{i}"] = cell
            field = field_by_col.get(i)
            if field:
                row[field] = cell
        row["raw_row"] = raw_row_dict
        rows.append(row)
    return rows


def parse_submitted_at(raw):
    """Google Forms timestamps arrive with no timezone info — they're entered in
    the form owner's local time (config'd as TIME_ZONE), so localize rather than
    leaving a naive datetime for a tz-aware DateTimeField."""
    if not raw:
        return None
    for fmt in ("%d/%m/%Y %H:%M:%S", "%m/%d/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        return timezone.make_aware(parsed) if timezone.is_naive(parsed) else parsed
    return None


def compute_dedup_key(email: str, phone: str, submitted_at) -> str:
    identity = (email or "").strip().lower() or (phone or "").strip()
    ts = submitted_at.isoformat() if submitted_at else ""
    return hashlib.sha256(f"{identity}|{ts}".encode()).hexdigest()
