"""Create tables and insert demo seed data (fake people only).

Run once:  python -m app.init_db
Safe to re-run: create_all skips existing tables and seeding only happens
when the patients table is empty.
"""
import logging

from sqlalchemy import func, select

from app import models  # noqa: F401  (registers the tables on Base)
from app.db import Base, SessionLocal, engine
from app.models import Patient
from app.schemas import PatientCreate
from app.service import create_patient

SEED_PATIENTS = [
    {
        "first_name": "Jane", "last_name": "Doe", "date_of_birth": "03/15/1985",
        "sex": "Female", "phone_number": "512-555-0142", "email": "jane.doe@example.com",
        "address_line_1": "742 Evergreen Terrace", "city": "Austin", "state": "TX",
        "zip_code": "78701", "insurance_provider": "Blue Cross Blue Shield",
        "insurance_member_id": "BCB123456789", "preferred_language": "English",
        "emergency_contact_name": "John Doe", "emergency_contact_phone": "512-555-0199",
    },
    {
        "first_name": "Carlos", "last_name": "Rivera", "date_of_birth": "11/02/1972",
        "sex": "Male", "phone_number": "305-555-0117", "address_line_1": "1200 Brickell Ave",
        "address_line_2": "Apt 5B", "city": "Miami", "state": "FL", "zip_code": "33131",
        "preferred_language": "Spanish",
    },
]


def init_db(seed: bool = True) -> None:
    Base.metadata.create_all(engine)
    if not seed:
        return
    with SessionLocal() as db:
        if db.scalar(select(func.count()).select_from(Patient)):
            return
        for record in SEED_PATIENTS:
            create_patient(db, PatientCreate(**record))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_db()
    print("Tables are ready and seed data is in place.")
