"""REST API for patient records. Thin layer: parse input, call the service,
wrap the result in the {"data", "error"} envelope."""
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app import service
from app.db import get_db
from app.errors import ApiError, envelope
from app.schemas import (
    AppointmentOut,
    CallLogOut,
    PatientCreate,
    PatientOut,
    PatientUpdate,
    normalize_phone,
    parse_date_of_birth,
)

router = APIRouter(prefix="/patients", tags=["patients"])


def _patient_json(patient) -> dict:
    return PatientOut.model_validate(patient).model_dump(mode="json")


def _parse_id(patient_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(patient_id)
    except ValueError:
        raise ApiError(400, "invalid_id", "patient_id must be a valid UUID")


def _filter(field: str, value: str | None, parser):
    if value is None or not value.strip():
        return None
    try:
        return parser(value)
    except ValueError as exc:
        raise ApiError(422, "validation_error", "Invalid query parameter",
                       [{"field": field, "message": str(exc)}])


@router.get("")
def list_patients(
    last_name: str | None = None,
    date_of_birth: str | None = None,
    phone_number: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    patients = service.list_patients(
        db,
        last_name=(last_name or "").strip() or None,
        date_of_birth=_filter("date_of_birth", date_of_birth, parse_date_of_birth),
        phone_number=_filter("phone_number", phone_number, normalize_phone),
        limit=limit,
        offset=offset,
    )
    return envelope([_patient_json(p) for p in patients])


@router.get("/{patient_id}")
def get_patient(patient_id: str, db: Session = Depends(get_db)):
    return envelope(_patient_json(service.get_patient(db, _parse_id(patient_id))))


@router.post("", status_code=201)
def create_patient(payload: PatientCreate, db: Session = Depends(get_db)):
    return envelope(_patient_json(service.create_patient(db, payload)))


@router.put("/{patient_id}")
def update_patient(patient_id: str, payload: PatientUpdate, db: Session = Depends(get_db)):
    return envelope(_patient_json(service.update_patient(db, _parse_id(patient_id), payload)))


@router.delete("/{patient_id}")
def delete_patient(patient_id: str, db: Session = Depends(get_db)):
    patient = service.soft_delete_patient(db, _parse_id(patient_id))
    return envelope({"patient_id": str(patient.patient_id), "deleted_at": patient.deleted_at.isoformat()})


# Bonus endpoints used by the dashboard
@router.get("/{patient_id}/calls")
def patient_calls(patient_id: str, db: Session = Depends(get_db)):
    pid = _parse_id(patient_id)
    service.get_patient(db, pid)
    calls = service.list_calls(db, patient_id=pid)
    return envelope([CallLogOut.model_validate(c).model_dump(mode="json") for c in calls])


@router.get("/{patient_id}/appointments")
def patient_appointments(patient_id: str, db: Session = Depends(get_db)):
    pid = _parse_id(patient_id)
    service.get_patient(db, pid)
    appts = service.list_appointments(db, pid)
    return envelope([AppointmentOut.model_validate(a).model_dump(mode="json") for a in appts])
