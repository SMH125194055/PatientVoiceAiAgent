"""Database schema.

Validation is enforced twice on purpose (defense in depth):
  1. Pydantic in app/schemas.py gives friendly, field-specific errors.
  2. CHECK constraints below guarantee bad data can never land in the table,
     even if someone writes to the database without going through the API.

The CHECK constraints use Postgres regex syntax, so they are emitted only on
PostgreSQL (`ddl_if`). The SQLite database used by the test suite still gets
the same columns, types and NOT NULL rules.
"""
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

SEX_VALUES = ("Male", "Female", "Other", "Decline to Answer")
US_PHONE_REGEX = "^[2-9][0-9]{2}[2-9][0-9]{6}$"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def pg_check(sql: str, name: str) -> CheckConstraint:
    return CheckConstraint(sql, name=name).ddl_if(dialect="postgresql")


class Patient(Base):
    __tablename__ = "patients"

    patient_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    # Required demographics
    first_name: Mapped[str] = mapped_column(String(50), nullable=False)
    last_name: Mapped[str] = mapped_column(String(50), nullable=False)
    date_of_birth: Mapped[date] = mapped_column(Date, nullable=False)
    sex: Mapped[str] = mapped_column(String(20), nullable=False)
    phone_number: Mapped[str] = mapped_column(String(10), nullable=False)  # 10 digits, no formatting
    address_line_1: Mapped[str] = mapped_column(String(200), nullable=False)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str] = mapped_column(String(2), nullable=False)
    zip_code: Mapped[str] = mapped_column(String(10), nullable=False)  # 12345 or 12345-6789

    # Optional demographics
    email: Mapped[str | None] = mapped_column(String(254))
    address_line_2: Mapped[str | None] = mapped_column(String(200))
    insurance_provider: Mapped[str | None] = mapped_column(String(100))
    insurance_member_id: Mapped[str | None] = mapped_column(String(30))
    preferred_language: Mapped[str] = mapped_column(
        String(50), nullable=False, default="English", server_default="English"
    )
    emergency_contact_name: Mapped[str | None] = mapped_column(String(100))
    emergency_contact_phone: Mapped[str | None] = mapped_column(String(10))

    # Audit columns (all UTC)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow,
        server_default=func.now(),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # soft delete

    calls: Mapped[list["CallLog"]] = relationship(back_populates="patient")
    appointments: Mapped[list["Appointment"]] = relationship(back_populates="patient")

    __table_args__ = (
        pg_check("char_length(first_name) BETWEEN 1 AND 50", "ck_patients_first_name_len"),
        pg_check("char_length(last_name) BETWEEN 1 AND 50", "ck_patients_last_name_len"),
        # CURRENT_DATE in a CHECK is evaluated at insert/update time; the API
        # also rejects future dates, this is the backstop.
        pg_check(
            "date_of_birth >= DATE '1900-01-01' AND date_of_birth <= CURRENT_DATE",
            "ck_patients_dob_range",
        ),
        pg_check("sex IN ('Male', 'Female', 'Other', 'Decline to Answer')", "ck_patients_sex"),
        pg_check(f"phone_number ~ '{US_PHONE_REGEX}'", "ck_patients_phone"),
        pg_check(
            f"emergency_contact_phone IS NULL OR emergency_contact_phone ~ '{US_PHONE_REGEX}'",
            "ck_patients_emergency_phone",
        ),
        pg_check("state ~ '^[A-Z]{2}$'", "ck_patients_state"),
        pg_check("zip_code ~ '^[0-9]{5}(-[0-9]{4})?$'", "ck_patients_zip"),
        pg_check(
            "email IS NULL OR email ~* '^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$'", "ck_patients_email"
        ),
        pg_check(
            "insurance_member_id IS NULL OR insurance_member_id ~ '^[A-Z0-9]{3,30}$'",
            "ck_patients_member_id",
        ),
        pg_check("char_length(city) BETWEEN 1 AND 100", "ck_patients_city_len"),
        # Indexes for the three GET /patients filters and duplicate detection.
        Index("ix_patients_phone_number", "phone_number"),
        Index("ix_patients_last_name", "last_name"),
        Index("ix_patients_date_of_birth", "date_of_birth"),
    )


class CallLog(Base):
    """One row per phone call: links the Vapi call to the patient it created or
    updated, plus the transcript and summary from Vapi's end-of-call report."""

    __tablename__ = "call_logs"

    call_log_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    vapi_call_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    patient_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("patients.patient_id"), index=True
    )
    caller_number: Mapped[str | None] = mapped_column(String(20))
    summary: Mapped[str | None] = mapped_column(Text)
    transcript: Mapped[str | None] = mapped_column(Text)
    ended_reason: Mapped[str | None] = mapped_column(String(100))
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow,
        server_default=func.now(),
    )

    patient: Mapped[Patient | None] = relationship(back_populates="calls")


class Appointment(Base):
    """Mock scheduling: a single provider, so one booking per time slot."""

    __tablename__ = "appointments"

    appointment_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    patient_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("patients.patient_id"), nullable=False, index=True
    )
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now()
    )

    patient: Mapped[Patient] = relationship(back_populates="appointments")

    __table_args__ = (UniqueConstraint("scheduled_at", name="uq_appointments_slot"),)
