"""Request/response schemas and field validation.

Every validator both CHECKS and NORMALIZES, because voice input is messy:
  "(512) 555-0142", "+1 512 555 0142" and 5125550142  ->  "5125550142"
  "texas" / "Texas" / "tx"                              ->  "TX"
  "03/05/1990" or "1990-03-05"                          ->  date(1990, 3, 5)
  "prefer not to say"                                   ->  "Decline to Answer"

Error messages are written to be read by the voice agent, so they say exactly
what is wrong and let the agent re-prompt for that one field.
"""
import re
import unicodedata
import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal, Optional

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    EmailStr,
    ValidationError,
    field_serializer,
    model_validator,
)

# --------------------------------------------------------------------------- #
# Reference data
# --------------------------------------------------------------------------- #
US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming",
    # Territories also use 2-letter USPS codes
    "PR": "Puerto Rico", "GU": "Guam", "VI": "U.S. Virgin Islands",
    "AS": "American Samoa", "MP": "Northern Mariana Islands",
}
_STATE_BY_NAME = {name.lower(): code for code, name in US_STATES.items()}
_STATE_BY_NAME.update({"washington dc": "DC", "washington d.c.": "DC", "d.c.": "DC"})

SEX_ALIASES = {
    "male": "Male", "m": "Male", "man": "Male",
    "female": "Female", "f": "Female", "woman": "Female",
    "other": "Other", "non-binary": "Other", "nonbinary": "Other", "intersex": "Other",
    "decline to answer": "Decline to Answer", "decline": "Decline to Answer",
    "declined": "Decline to Answer", "prefer not to say": "Decline to Answer",
    "prefer not to answer": "Decline to Answer", "rather not say": "Decline to Answer",
}

# Letters (any language, so "José" works for Spanish callers), joined by single
# spaces, hyphens or apostrophes: "O'Brien", "Smith-Jones", "Mary Ann".
_NAME_RE = re.compile(r"^[^\W\d_]+(?:[ '\-][^\W\d_]+)*$")
_CITY_RE = re.compile(r"^[^\W\d_]+(?:[ .'\-]+[^\W\d_]+)*\.?$")


# --------------------------------------------------------------------------- #
# Normalizers (plain functions, reused by the API filters and the voice tools)
# --------------------------------------------------------------------------- #
def _clean_text(value: Any) -> str:
    """Basic sanitization: strip control characters and collapse whitespace."""
    if not isinstance(value, (str, int, float)):
        raise ValueError("must be text")
    text = "".join(ch for ch in str(value) if unicodedata.category(ch)[0] != "C")
    return " ".join(text.split())


def clean_name(value: Any, max_len: int = 50) -> str:
    name = _clean_text(value).replace("\u2019", "'")
    if not 1 <= len(name) <= max_len:
        raise ValueError(f"must be between 1 and {max_len} characters")
    if not _NAME_RE.match(name):
        raise ValueError("may only contain letters, spaces, hyphens and apostrophes")
    if name.islower():  # speech-to-text sometimes lowercases everything
        name = name.title()
    return name


def parse_date_of_birth(value: Any) -> date:
    if isinstance(value, datetime):
        parsed = value.date()
    elif isinstance(value, date):
        parsed = value
    else:
        text = _clean_text(value)
        for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
            try:
                parsed = datetime.strptime(text, fmt).date()
                break
            except ValueError:
                continue
        else:
            raise ValueError("must be a real calendar date in MM/DD/YYYY format")
    if parsed > date.today():
        raise ValueError("cannot be in the future")
    if parsed.year < 1900:
        raise ValueError("must be after the year 1900")
    return parsed


def normalize_sex(value: Any) -> str:
    key = _clean_text(value).lower()
    if key not in SEX_ALIASES:
        raise ValueError("must be Male, Female, Other, or Decline to Answer")
    return SEX_ALIASES[key]


def normalize_phone(value: Any) -> str:
    digits = re.sub(r"\D", "", str(value))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]  # drop the +1 country code
    if len(digits) != 10:
        raise ValueError(f"must be a 10-digit U.S. phone number (received {len(digits)} digits)")
    if digits[0] in "01" or digits[3] in "01":
        raise ValueError("is not a valid U.S. number (area code and exchange cannot start with 0 or 1)")
    return digits


def normalize_state(value: Any) -> str:
    text = _clean_text(value)
    if text.upper() in US_STATES:
        return text.upper()
    code = _STATE_BY_NAME.get(text.lower())
    if not code:
        raise ValueError("must be a valid 2-letter U.S. state abbreviation")
    return code


def normalize_zip(value: Any) -> str:
    text = _clean_text(value)
    if re.search(r"[^\d\s-]", text):
        raise ValueError("must be a 5-digit ZIP or ZIP+4 (12345 or 12345-6789)")
    digits = re.sub(r"\D", "", text)
    if len(digits) == 5:
        return digits
    if len(digits) == 9:
        return f"{digits[:5]}-{digits[5:]}"
    raise ValueError("must be a 5-digit ZIP or ZIP+4 (12345 or 12345-6789)")


def clean_city(value: Any) -> str:
    city = _clean_text(value)
    if not 1 <= len(city) <= 100:
        raise ValueError("must be between 1 and 100 characters")
    if not _CITY_RE.match(city):
        raise ValueError("may only contain letters, spaces, periods, hyphens and apostrophes")
    return city.title() if city.islower() else city


def clean_address(value: Any) -> str:
    address = _clean_text(value)
    if not 1 <= len(address) <= 200:
        raise ValueError("must be between 1 and 200 characters")
    return address


def clean_short_text(value: Any) -> str:
    text = _clean_text(value)
    if not 1 <= len(text) <= 100:
        raise ValueError("must be between 1 and 100 characters")
    return text


def normalize_member_id(value: Any) -> str:
    member_id = re.sub(r"[\s\-]", "", _clean_text(value)).upper()
    if not re.fullmatch(r"[A-Z0-9]{3,30}", member_id):
        raise ValueError("must be 3 to 30 letters or numbers")
    return member_id


def clean_language(value: Any) -> str:
    if value is None:
        return "English"
    language = _clean_text(value)
    if not 1 <= len(language) <= 50:
        raise ValueError("must be between 1 and 50 characters")
    return language.title()


def format_dob(value: date) -> str:
    return value.strftime("%m/%d/%Y")


# --------------------------------------------------------------------------- #
# Annotated field types
# --------------------------------------------------------------------------- #
Name50 = Annotated[str, BeforeValidator(lambda v: clean_name(v, 50))]
Name100 = Annotated[str, BeforeValidator(lambda v: clean_name(v, 100))]
DateOfBirth = Annotated[date, BeforeValidator(parse_date_of_birth)]
Sex = Annotated[Literal["Male", "Female", "Other", "Decline to Answer"], BeforeValidator(normalize_sex)]
Phone = Annotated[str, BeforeValidator(normalize_phone)]
Email = Annotated[EmailStr, AfterValidator(lambda v: v.lower())]
Address = Annotated[str, BeforeValidator(clean_address)]
City = Annotated[str, BeforeValidator(clean_city)]
State = Annotated[str, BeforeValidator(normalize_state)]
ZipCode = Annotated[str, BeforeValidator(normalize_zip)]
ShortText = Annotated[str, BeforeValidator(clean_short_text)]
MemberId = Annotated[str, BeforeValidator(normalize_member_id)]
Language = Annotated[str, BeforeValidator(clean_language)]

REQUIRED_FIELDS = (
    "first_name", "last_name", "date_of_birth", "sex", "phone_number",
    "address_line_1", "city", "state", "zip_code",
)


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _blank_strings_to_none(cls, data: Any) -> Any:
        """Treat "" and "   " as not provided, so optional fields stay NULL."""
        if isinstance(data, dict):
            return {k: (None if isinstance(v, str) and not v.strip() else v) for k, v in data.items()}
        return data


class PatientCreate(_Input):
    first_name: Name50
    last_name: Name50
    date_of_birth: DateOfBirth
    sex: Sex
    phone_number: Phone
    email: Optional[Email] = None
    address_line_1: Address
    address_line_2: Optional[Address] = None
    city: City
    state: State
    zip_code: ZipCode
    insurance_provider: Optional[ShortText] = None
    insurance_member_id: Optional[MemberId] = None
    preferred_language: Language = "English"
    emergency_contact_name: Optional[Name100] = None
    emergency_contact_phone: Optional[Phone] = None


class PatientUpdate(_Input):
    """Partial update: every field optional, but required fields can't be cleared."""

    first_name: Optional[Name50] = None
    last_name: Optional[Name50] = None
    date_of_birth: Optional[DateOfBirth] = None
    sex: Optional[Sex] = None
    phone_number: Optional[Phone] = None
    email: Optional[Email] = None
    address_line_1: Optional[Address] = None
    address_line_2: Optional[Address] = None
    city: Optional[City] = None
    state: Optional[State] = None
    zip_code: Optional[ZipCode] = None
    insurance_provider: Optional[ShortText] = None
    insurance_member_id: Optional[MemberId] = None
    preferred_language: Optional[Language] = None
    emergency_contact_name: Optional[Name100] = None
    emergency_contact_phone: Optional[Phone] = None

    @model_validator(mode="after")
    def _required_fields_not_cleared(self) -> "PatientUpdate":
        cleared = [f for f in REQUIRED_FIELDS if f in self.model_fields_set and getattr(self, f) is None]
        if cleared:
            raise ValueError(f"required fields cannot be cleared: {', '.join(cleared)}")
        return self


class PatientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    patient_id: uuid.UUID
    first_name: str
    last_name: str
    date_of_birth: date
    sex: str
    phone_number: str
    email: Optional[str]
    address_line_1: str
    address_line_2: Optional[str]
    city: str
    state: str
    zip_code: str
    insurance_provider: Optional[str]
    insurance_member_id: Optional[str]
    preferred_language: str
    emergency_contact_name: Optional[str]
    emergency_contact_phone: Optional[str]
    created_at: datetime
    updated_at: datetime

    @field_serializer("date_of_birth")
    def _dob_us_format(self, value: date) -> str:
        return format_dob(value)


class CallLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    call_log_id: uuid.UUID
    vapi_call_id: str
    patient_id: Optional[uuid.UUID]
    caller_number: Optional[str]
    summary: Optional[str]
    transcript: Optional[str]
    ended_reason: Optional[str]
    duration_seconds: Optional[float]
    created_at: datetime


class AppointmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    appointment_id: uuid.UUID
    patient_id: uuid.UUID
    scheduled_at: datetime
    reason: Optional[str]
    created_at: datetime


# --------------------------------------------------------------------------- #
# Error formatting shared by the REST API and the voice tools
# --------------------------------------------------------------------------- #
_SKIP_LOC = {"body", "query", "path"}


def format_validation_errors(errors: list[dict]) -> list[dict]:
    """Turn Pydantic's error list into [{"field": ..., "message": ...}]."""
    out = []
    for err in errors:
        loc = [str(p) for p in err.get("loc", ()) if str(p) not in _SKIP_LOC]
        # Drop internal type names Pydantic appends, e.g. "function-before[...]".
        loc = [p for p in loc if "[" not in p]
        field = ".".join(loc) or "body"
        message = str(err.get("msg", "is invalid")).removeprefix("Value error, ")
        if err.get("type") == "missing" or (err.get("input") is None and err.get("type", "").endswith("_type")):
            message = "is required"
        elif err.get("type") == "extra_forbidden":
            message = "is not an allowed field"
        out.append({"field": field, "message": message})
    return out


def validation_error_details(exc: ValidationError) -> list[dict]:
    return format_validation_errors(exc.errors())
