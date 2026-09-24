"""Tools the voice agent can call during a phone conversation.

This module knows nothing about Vapi's payload format (that lives in
app/routes/vapi.py). Each tool takes plain arguments, calls the shared service
layer, and returns a small dict that the LLM reads. The `status` field tells
the LLM what happened; `instruction` tells it what to say or do next, so a
failure is always spoken to the caller instead of turning into silence.
"""
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import service
from app.errors import ApiError, NotFoundError
from app.schemas import (
    PatientCreate,
    PatientOut,
    PatientUpdate,
    format_dob,
    normalize_phone,
    validation_error_details,
)

log = logging.getLogger("app.agent_tools")

PATIENT_FIELDS = set(PatientCreate.model_fields)

SAVE_FAILED = {
    "status": "system_error",
    "instruction": (
        "The record could NOT be saved because of a system problem. Apologize, tell the "
        "caller their information was not saved, and ask them to call back a little later."
    ),
}


@dataclass
class CallContext:
    call_id: str | None
    caller_number: str | None


def _patient_fields(args: dict) -> dict:
    """Keep only known patient fields and drop empty values the LLM may send."""
    return {k: v for k, v in args.items() if k in PATIENT_FIELDS and v not in (None, "")}


def _validation_result(exc: ValidationError) -> dict:
    return {
        "status": "validation_error",
        "errors": validation_error_details(exc),
        "instruction": (
            "Tell the caller which specific field is invalid and why, ask for that field "
            "again, then retry. Do not re-ask fields that were fine."
        ),
    }


def _parse_uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


def _link_call(db: Session, ctx: CallContext, patient_id: uuid.UUID) -> None:
    """Best effort: a failure to link the transcript must never fail the call."""
    if not ctx.call_id:
        return
    try:
        service.link_call_to_patient(db, ctx.call_id, patient_id, ctx.caller_number)
    except SQLAlchemyError:
        log.exception("link_call_failed call_id=%s", ctx.call_id)


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #
def lookup_patient(db: Session, args: dict, ctx: CallContext) -> dict:
    raw_phone = args.get("phone_number") or ctx.caller_number
    if not raw_phone:
        return {"status": "no_phone", "instruction": "Ask the caller for their phone number."}
    try:
        phone = normalize_phone(raw_phone)
    except ValueError as exc:
        return {"status": "invalid_phone", "message": f"phone_number {exc}"}

    matches = service.find_by_phone(db, phone)
    if not matches:
        return {"status": "not_found", "phone_number": phone}
    return {
        "status": "found",
        "matches": [
            {
                "patient_id": str(p.patient_id),
                "first_name": p.first_name,
                "last_name": p.last_name,
                "date_of_birth": format_dob(p.date_of_birth),
            }
            for p in matches
        ],
        "instruction": (
            "Ask whether they want to update their existing record. Before reading back or "
            "changing any stored details, ask the caller for their date of birth and make "
            "sure it matches date_of_birth."
        ),
    }


def register_patient(db: Session, args: dict, ctx: CallContext) -> dict:
    try:
        data = PatientCreate(**_patient_fields(args))
    except ValidationError as exc:
        return _validation_result(exc)
    try:
        patient = service.create_patient(db, data)
    except SQLAlchemyError:
        log.exception("register_patient_failed call_id=%s", ctx.call_id)
        return SAVE_FAILED

    _link_call(db, ctx, patient.patient_id)
    # Observability requirement: log the final collected payload.
    log.info(
        "patient_registered call_id=%s payload=%s",
        ctx.call_id,
        json.dumps(PatientOut.model_validate(patient).model_dump(mode="json")),
    )
    return {
        "status": "saved",
        "patient_id": str(patient.patient_id),
        "first_name": patient.first_name,
        "instruction": "Tell the caller they are all set, using their first name.",
    }


def update_patient(db: Session, args: dict, ctx: CallContext) -> dict:
    patient_id = _parse_uuid(args.get("patient_id"))
    if patient_id is None:
        return {"status": "invalid_patient_id", "instruction": "Call lookup_patient first to get the patient_id."}
    fields = _patient_fields(args)
    if not fields:
        return {"status": "no_changes", "instruction": "Ask the caller what they would like to change."}
    try:
        data = PatientUpdate(**fields)
    except ValidationError as exc:
        return _validation_result(exc)
    try:
        patient = service.update_patient(db, patient_id, data)
    except NotFoundError:
        return {"status": "not_found", "instruction": "That record no longer exists. Offer to register them as a new patient."}
    except SQLAlchemyError:
        log.exception("update_patient_failed call_id=%s", ctx.call_id)
        return SAVE_FAILED

    _link_call(db, ctx, patient.patient_id)
    log.info("patient_updated call_id=%s patient_id=%s fields=%s", ctx.call_id, patient_id, sorted(fields))
    return {
        "status": "updated",
        "patient_id": str(patient.patient_id),
        "first_name": patient.first_name,
        "updated_fields": sorted(fields),
    }


def get_available_slots(db: Session, args: dict, ctx: CallContext) -> dict:
    slots = service.available_slots(db)[:6]
    if not slots:
        return {"status": "none_available", "instruction": "Apologize and say the front desk will call them to schedule."}
    return {
        "status": "ok",
        "slots": [{"slot_id": s.isoformat(), "spoken": service.spoken_slot(s)} for s in slots],
        "instruction": "Offer two or three of these times in natural speech. Never read the slot_id aloud.",
    }


def book_appointment(db: Session, args: dict, ctx: CallContext) -> dict:
    patient_id = _parse_uuid(args.get("patient_id"))
    if patient_id is None:
        return {"status": "invalid_patient_id", "instruction": "Register or look up the patient first."}
    try:
        slot = datetime.fromisoformat(str(args.get("slot_id", "")))
    except ValueError:
        return {"status": "invalid_slot", "instruction": "Call get_available_slots and use one of its slot_id values."}
    try:
        appointment = service.book_appointment(db, patient_id, slot, args.get("reason"))
    except NotFoundError:
        return {"status": "not_found", "instruction": "The patient record was not found."}
    except ApiError as exc:
        if exc.code == "slot_unavailable":
            return {"status": "slot_unavailable", "instruction": "That time was just taken. Call get_available_slots and offer other times."}
        raise
    except SQLAlchemyError:
        log.exception("book_appointment_failed call_id=%s", ctx.call_id)
        return SAVE_FAILED

    log.info("appointment_booked call_id=%s patient_id=%s at=%s", ctx.call_id, patient_id, slot.isoformat())
    return {
        "status": "booked",
        "appointment_id": str(appointment.appointment_id),
        "spoken": service.spoken_slot(slot),
    }


TOOLS: dict[str, Callable[[Session, dict, CallContext], dict]] = {
    "lookup_patient": lookup_patient,
    "register_patient": register_patient,
    "update_patient": update_patient,
    "get_available_slots": get_available_slots,
    "book_appointment": book_appointment,
}


def run_tool(db: Session, name: str | None, args: dict, ctx: CallContext) -> dict:
    tool = TOOLS.get(name or "")
    if tool is None:
        return {"status": "error", "message": f"Unknown tool: {name}"}
    try:
        return tool(db, args, ctx)
    except Exception:  # last line of defense: the caller must always hear something
        log.exception("tool_crashed name=%s call_id=%s", name, ctx.call_id)
        db.rollback()
        return SAVE_FAILED
