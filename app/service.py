"""Service layer: the ONLY place that reads or writes the database.

Both the REST API (app/routes/patients.py) and the voice agent tools
(app/agent_tools.py) call these functions, so validation and persistence
rules can never diverge between the two entry points.
"""
import uuid
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import settings
from app.errors import ApiError, NotFoundError
from app.models import Appointment, CallLog, Patient, utcnow
from app.schemas import PatientCreate, PatientUpdate


def _commit(db: Session) -> None:
    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise


# --------------------------------------------------------------------------- #
# Patients
# --------------------------------------------------------------------------- #
def create_patient(db: Session, data: PatientCreate) -> Patient:
    patient = Patient(**data.model_dump())
    db.add(patient)
    _commit(db)
    db.refresh(patient)
    return patient


def get_patient(db: Session, patient_id: uuid.UUID) -> Patient:
    patient = db.scalar(
        select(Patient).where(Patient.patient_id == patient_id, Patient.deleted_at.is_(None))
    )
    if patient is None:
        raise NotFoundError()
    return patient


def list_patients(
    db: Session,
    last_name: str | None = None,
    date_of_birth: date | None = None,
    phone_number: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Patient]:
    query = select(Patient).where(Patient.deleted_at.is_(None))
    if last_name:
        query = query.where(func.lower(Patient.last_name) == last_name.lower())
    if date_of_birth:
        query = query.where(Patient.date_of_birth == date_of_birth)
    if phone_number:
        query = query.where(Patient.phone_number == phone_number)
    query = query.order_by(Patient.created_at.desc()).limit(limit).offset(offset)
    return list(db.scalars(query))


def update_patient(db: Session, patient_id: uuid.UUID, data: PatientUpdate) -> Patient:
    changes = data.model_dump(exclude_unset=True)
    if not changes:
        raise ApiError(400, "empty_update", "Provide at least one field to update")
    patient = get_patient(db, patient_id)
    for field, value in changes.items():
        setattr(patient, field, value)
    patient.updated_at = utcnow()
    _commit(db)
    db.refresh(patient)
    return patient


def soft_delete_patient(db: Session, patient_id: uuid.UUID) -> Patient:
    patient = get_patient(db, patient_id)
    patient.deleted_at = utcnow()
    patient.updated_at = patient.deleted_at
    _commit(db)
    return patient


def find_by_phone(db: Session, phone_number: str) -> list[Patient]:
    """Duplicate detection for returning callers. Several family members can
    share one phone number, so this returns every active match."""
    return list(
        db.scalars(
            select(Patient)
            .where(Patient.phone_number == phone_number, Patient.deleted_at.is_(None))
            .order_by(Patient.updated_at.desc())
        )
    )


# --------------------------------------------------------------------------- #
# Call logs (transcripts)
# --------------------------------------------------------------------------- #
def _get_or_create_call(db: Session, vapi_call_id: str) -> CallLog:
    call = db.scalar(select(CallLog).where(CallLog.vapi_call_id == vapi_call_id))
    if call is None:
        call = CallLog(vapi_call_id=vapi_call_id)
        db.add(call)
    return call


def link_call_to_patient(
    db: Session, vapi_call_id: str, patient_id: uuid.UUID, caller_number: str | None
) -> None:
    call = _get_or_create_call(db, vapi_call_id)
    call.patient_id = patient_id
    call.caller_number = call.caller_number or caller_number
    _commit(db)


def save_call_report(
    db: Session,
    vapi_call_id: str,
    caller_number: str | None,
    transcript: str | None,
    summary: str | None,
    ended_reason: str | None,
    duration_seconds: float | None,
) -> CallLog:
    call = _get_or_create_call(db, vapi_call_id)
    call.caller_number = call.caller_number or caller_number
    call.transcript = transcript
    call.summary = summary
    call.ended_reason = ended_reason
    call.duration_seconds = duration_seconds
    _commit(db)
    return call


def list_calls(db: Session, patient_id: uuid.UUID | None = None, limit: int = 50) -> list[CallLog]:
    query = select(CallLog)
    if patient_id:
        query = query.where(CallLog.patient_id == patient_id)
    return list(db.scalars(query.order_by(CallLog.created_at.desc()).limit(limit)))


# --------------------------------------------------------------------------- #
# Mock appointment scheduling (bonus)
# --------------------------------------------------------------------------- #
SLOT_TIMES = (time(9, 0), time(10, 30), time(13, 0), time(15, 30))
BUSINESS_DAYS_AHEAD = 3


def _utc_key(dt: datetime) -> datetime:
    """Comparable UTC key; SQLite returns naive datetimes (already UTC)."""
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def spoken_slot(dt: datetime) -> str:
    """'Monday, September 28 at 9:00 AM' (portable, no %-d which fails on Windows)."""
    hour = dt.hour % 12 or 12
    meridiem = "AM" if dt.hour < 12 else "PM"
    return f"{dt:%A, %B} {dt.day} at {hour}:{dt.minute:02d} {meridiem}"


def available_slots(db: Session) -> list[datetime]:
    tz = ZoneInfo(settings.clinic_timezone)
    day = datetime.now(tz).date()
    candidates: list[datetime] = []
    days_found = 0
    while days_found < BUSINESS_DAYS_AHEAD:
        day += timedelta(days=1)
        if day.weekday() >= 5:  # skip weekends
            continue
        days_found += 1
        candidates.extend(datetime.combine(day, t, tzinfo=tz) for t in SLOT_TIMES)

    window_start = candidates[0].astimezone(timezone.utc) - timedelta(days=1)
    booked = {
        _utc_key(dt)
        for dt in db.scalars(select(Appointment.scheduled_at))
        if _utc_key(dt) >= _utc_key(window_start)
    }
    return [c for c in candidates if _utc_key(c) not in booked]


def book_appointment(
    db: Session, patient_id: uuid.UUID, slot: datetime, reason: str | None
) -> Appointment:
    get_patient(db, patient_id)  # raises NotFoundError if missing
    if slot.tzinfo is None:
        slot = slot.replace(tzinfo=ZoneInfo(settings.clinic_timezone))
    if _utc_key(slot) not in {_utc_key(s) for s in available_slots(db)}:
        raise ApiError(409, "slot_unavailable", "That time slot is not available")
    appointment = Appointment(
        patient_id=patient_id,
        scheduled_at=slot.astimezone(timezone.utc),
        reason=(reason or "").strip()[:200] or None,
    )
    db.add(appointment)
    _commit(db)
    db.refresh(appointment)
    return appointment


def list_appointments(db: Session, patient_id: uuid.UUID) -> list[Appointment]:
    return list(
        db.scalars(
            select(Appointment)
            .where(Appointment.patient_id == patient_id)
            .order_by(Appointment.scheduled_at)
        )
    )
