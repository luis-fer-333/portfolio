"""Main agent service for the medical appointment chatbot.

Implements the conversational state machine that handles appointment
scheduling, cancellation, rescheduling, and querying. Manages session
persistence via DynamoDB and coordinates between the CalendarManager,
ConversationOrchestrator, and LLM intent parser.

Deployed as an AWS Lambda function via FastAPI + Mangum.

Note: User-facing messages are in Spanish as the chatbot is designed
for Spanish-speaking patients. Code comments and docstrings are in English.
"""

import os
import re
import unicodedata
import logging
import json
from datetime import datetime, timedelta
from typing import Dict, Any

import pytz
import boto3
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from mangum import Mangum

from calendar_manager import CalendarManager
from conversation_orchestrator import ConversationOrchestrator
from validators import validate_name, validate_dni_nie, validate_spanish_phone

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, LOG_LEVEL, 20))
log = logging.getLogger("agent_service")

TZ = pytz.timezone("Europe/Madrid")

# Sets for quick intent matching from user text
NEGATIVE_RESPONSES = {"no", "no gracias", "n", "nop", "nada", "eso es todo", "listo", "gracias, no", "no, gracias"}
AFFIRMATIVE_RESPONSES = {"s", "si", "sí", "y", "yes", "ok", "vale"}

ALLOW_WEAK_DNI = os.getenv("ALLOW_WEAK_DNI", "0") == "1"
_DNI_SIMPLE = re.compile(r'^[XYZ]?\d{7,8}[A-Za-z]$')
_DATE_DDMMYYYY = re.compile(r"^\s*(\d{2})/(\d{2})/(\d{4})\s*$")

APPOINTMENTS_TABLE = os.getenv("APPOINTMENTS_TABLE", "appointments_sessions")
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", str(7 * 24 * 3600)))

ddb = boto3.resource("dynamodb")
tbl = ddb.Table(APPOINTMENTS_TABLE)

app = FastAPI()
cm = CalendarManager()
orchestrator = ConversationOrchestrator(redirect_message_text=os.getenv("REDIRECT_MESSAGE"))


class Turn(BaseModel):
    """A single conversation turn: session ID + user text."""
    session_id: str
    text: str


# ── Session management ──────────────────────────────────────────

def _pk(sid: str) -> str:
    """Build the DynamoDB partition key for a session."""
    return f"session:{sid}"


def _new_state() -> Dict[str, Any]:
    """Return a fresh session state with all fields initialized."""
    return {
        "welcomed": False,
        "awaiting_intent": True,
        "history": [],
        "name": None,
        "dni": None,
        "phone": None,
        "pending_intent": None,
        "awaiting_field": None,
        "step": None,
        "date_options": None,
        "hours_by_date": None,
        "selected_date": None,
        "selected_time": None,
        "appointments": None,
        "event_mod": None,
        "notes": None,
        "last_user_input": None,
    }


def load_session(sid: str) -> Dict[str, Any]:
    """Load session state from DynamoDB, or return a fresh state if not found."""
    try:
        it = tbl.get_item(Key={"pk": _pk(sid), "sk": "meta"}).get("Item")
        if not it:
            return _new_state()
        state = it.get("state") or {}
        if not isinstance(state, dict):
            return _new_state()
        base = _new_state()
        base.update(state)
        return base
    except Exception:
        log.exception("load_session")
        return _new_state()


def save_session(sid: str, st: Dict[str, Any]) -> None:
    """Persist session state to DynamoDB with a TTL for auto-expiry."""
    try:
        now = int(datetime.utcnow().timestamp())
        ttl = now + SESSION_TTL_SECONDS
        tbl.put_item(Item={
            "pk": _pk(sid),
            "sk": "meta",
            "state": st,
            "updated_at": now,
            "expiresAt": ttl,
        })
    except Exception:
        log.exception("save_session")


def clear_session(sid: str) -> None:
    """Reset a session to its initial state."""
    st = _new_state()
    st["welcomed"] = False
    save_session(sid, st)


# ── Helpers ─────────────────────────────────────────────────────

def _fmt_es(iso_date: str) -> str:
    """Format an ISO date string as DD/MM/YYYY."""
    return datetime.strptime(iso_date, "%Y-%m-%d").strftime("%d/%m/%Y")


def _parse_date(options_iso: list, text: str) -> str | None:
    """Try to parse DD/MM/YYYY from text and match against available options."""
    text = (text or "").strip()
    try:
        dt = datetime.strptime(text, "%d/%m/%Y").date()
        iso = dt.strftime("%Y-%m-%d")
        if iso in options_iso:
            return iso
    except Exception:
        pass
    return None


def _parse_time(available: list, text: str) -> str | None:
    """Try to parse HH:MM from text and match against available slots."""
    m = re.match(r"^(\d{1,2}):(\d{2})$", (text or "").strip())
    if not m:
        return None
    hh = int(m.group(1))
    mm = int(m.group(2))
    if mm not in (0, 30):
        return None
    s = f"{hh:02d}:{mm:02d}"
    if s not in available:
        return None
    return s


def _parse_direct_date(text: str) -> str | None:
    """Try to parse a DD/MM/YYYY date from raw text (no option matching)."""
    m = _DATE_DDMMYYYY.match(text or "")
    if not m:
        return None
    dd, mm, yyyy = map(int, m.groups())
    try:
        dt = datetime(yyyy, mm, dd)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return None


def _create_event(user_input: str, date_iso: str, time_str: str, st: Dict) -> tuple[bool, str]:
    """Create a calendar event and return (success, message)."""
    try:
        dt_local = TZ.localize(datetime.strptime(f"{date_iso} {time_str}", "%Y-%m-%d %H:%M"))
        start = dt_local.isoformat()
        end = (dt_local + timedelta(hours=1)).isoformat()
        if not cm.check_availability(start, end):
            return False, "⚠️ There's already an appointment at that time."
        desc = (
            f"Original instruction: {user_input}\n"
            f"Name: {st.get('name')}\n"
            f"Phone: {st.get('phone')}\n"
            f"DNI: {st.get('dni')}"
        )
        notes = (st.get("notes") or "").strip()
        if notes:
            desc += f"\nPatient notes: {notes}"
        cm.create_event("Medical Appointment", desc, start, end, st.get("dni"))
        return True, f"✅ Appointment scheduled for {_fmt_es(date_iso)} at {time_str}."
    except Exception as e:
        log.exception("create_event")
        return False, f"❌ Error creating appointment: {e}"


def _format_appointments(appointments: list) -> str:
    """Format a list of calendar events into a readable string."""
    if not appointments:
        return "📭 You have no upcoming appointments."
    out = ["📋 Your upcoming appointments:"]
    for i, ev in enumerate(appointments, 1):
        start = ev.get("start", {}).get("dateTime")
        end = ev.get("end", {}).get("dateTime")
        dt_start = datetime.fromisoformat(start) if start else None
        dt_end = datetime.fromisoformat(end) if end else None
        date_str = dt_start.strftime("%d/%m/%Y") if dt_start else "?"
        time_range = f"{dt_start.strftime('%H:%M')}–{dt_end.strftime('%H:%M')}" if dt_start and dt_end else "?"
        out.append(f"{i}. {date_str} {time_range} — {ev.get('summary', 'Appointment')}")
    return "\n".join(out)


def _norm(s: str) -> str:
    """Normalize text: strip accents, lowercase, trim whitespace."""
    s = unicodedata.normalize("NFD", s or "").encode("ascii", "ignore").decode("ascii")
    return s.strip().lower()


def _menu_text() -> str:
    """Return the main menu message shown to the user."""
    return (
        "👋 Hi, I'm your **medical appointment assistant**.\n"
        "Options:\n"
        "• ➕ *schedule appointment*\n"
        "• ❌ *cancel appointment*\n"
        "• 📋 *view my appointments*\n"
        "• 🔁 *reschedule appointment*\n\n"
        "Type an option or a direct command (e.g., *schedule*)."
    )


# Phrases that trigger the help/menu
HELP_PHRASES = {"what can you do", "help", "options", "menu"}

# Keyword → intent routing (deterministic, no LLM needed)
ROUTING = {
    "schedule": "SCHEDULE",
    "schedule appointment": "SCHEDULE",
    "book": "SCHEDULE",
    "query": "QUERY",
    "check slots": "QUERY",
    "availability": "QUERY",
    "my appointments": "QUERY_MY_APPOINTMENTS",
    "view my appointments": "QUERY_MY_APPOINTMENTS",
    "cancel": "CANCEL",
    "cancel appointment": "CANCEL",
    "reschedule": "RESCHEDULE",
    "reschedule appointment": "RESCHEDULE",
}


def _needs_personal_data(intent: str) -> bool:
    """Return True if the intent requires patient identification."""
    return intent in {"SCHEDULE", "QUERY_MY_APPOINTMENTS", "CANCEL", "RESCHEDULE"}


def _request_data_in_order(st: dict) -> str | None:
    """Ask for the next missing personal data field, or return None if all collected."""
    if not st.get("name"):
        st["awaiting_field"] = "name"
        return "👤 Before we continue, I need your details. What is your **name**?"
    if not st.get("dni"):
        st["awaiting_field"] = "dni"
        return "🧾 What is your **DNI/NIE**?"
    if not st.get("phone"):
        st["awaiting_field"] = "phone"
        return "📱 What is your **phone number**? (9-digit Spanish number)"
    return None


def _after_data_collected(st: dict) -> str:
    """Execute the pending intent now that all personal data is collected."""
    intent = st.get("pending_intent")
    if not intent:
        return "✅ Data saved. How can I help you today?"

    if intent == "SCHEDULE":
        avail = cm.get_availability_next_days(days=5)
        st["step"] = "schedule_date"
        st["date_options"] = list(avail.keys())
        st["hours_by_date"] = avail
        lines = [f"📆 {_fmt_es(f)}: {', '.join(h) if h else 'No slots'}" for f, h in avail.items()]
        text = "🔎 Checking availability...\n" + "\n".join(lines)
        return text + "\n\n👉 Choose a date in **DD/MM/YYYY** format."

    if intent == "QUERY_MY_APPOINTMENTS":
        appointments = cm.list_patient_appointments(st["dni"], days_ahead=365)
        if not appointments:
            avail = cm.get_availability_next_days(days=5)
            st["step"] = "schedule_date"
            st["date_options"] = list(avail.keys())
            st["hours_by_date"] = avail
            lines = [f"📆 {_fmt_es(f)}: {', '.join(h) if h else 'No slots'}" for f, h in avail.items()]
            text = "📭 You have no upcoming appointments.\n\nWould you like to schedule one? Reply with a **date in DD/MM/YYYY**.\n\n"
            text += "🔎 Checking availability...\n" + "\n".join(lines)
            return text
        st["appointments"] = appointments
        st["step"] = "post_appointments"
        return _format_appointments(appointments) + "\n\nWould you like to **cancel (c)**, **reschedule (m)**, or **exit (s)**?"

    if intent in {"CANCEL", "RESCHEDULE"}:
        appointments = cm.list_patient_appointments(st["dni"], days_ahead=365)
        if not appointments:
            return "📭 You have no upcoming appointments."
        st["appointments"] = appointments
        if intent == "CANCEL":
            st["step"] = "cancel_select"
            return _format_appointments(appointments) + "\n\nSend the **number** of the appointment to cancel, or 'exit'."
        else:
            st["step"] = "reschedule_select"
            return _format_appointments(appointments) + "\n\nSend the **number** of the appointment to reschedule, or 'exit'."


# ── API routes ──────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"ok": True}


@app.post("/message")
async def handle_message(req: Request):
    """Main message handler: loads session, processes input through the state machine, saves session."""
    body = await req.json()
    t = Turn(**body)
    sid = t.session_id
    txt = (t.text or "").strip()
    st = load_session(sid)

    # 1) If user says "no" and no active step, say goodbye and clear session
    if (not st.get("step")) and (not st.get("awaiting_field")) and txt.lower() in NEGATIVE_RESPONSES:
        clear_session(sid)
        return JSONResponse({"reply": "👋 Thank you! If you need anything else, send me a greeting."})

    # 0) Welcome message for fresh sessions
    if not st.get("welcomed"):
        st["welcomed"] = True
        st["awaiting_intent"] = True
        save_session(sid, st)
        return JSONResponse({"reply": _menu_text()})

    # 2) Personal data capture flow
    awaiting = st.get("awaiting_field")
    if awaiting == "name":
        v = validate_name(txt)
        if v:
            st["name"] = v
            st["awaiting_field"] = None
            ask = _request_data_in_order(st)
            save_session(sid, st)
            return JSONResponse({"reply": ask or _after_data_collected(st)})
        save_session(sid, st)
        return JSONResponse({"reply": "❌ Invalid name. Try again (2–60 letters)."})

    if awaiting == "dni":
        v = validate_dni_nie(txt)
        if not v and ALLOW_WEAK_DNI and _DNI_SIMPLE.match(txt):
            v = txt.upper()
        if v:
            st["dni"] = v
            st["awaiting_field"] = None
            ask = _request_data_in_order(st)
            save_session(sid, st)
            return JSONResponse({"reply": ask or _after_data_collected(st)})
        save_session(sid, st)
        return JSONResponse({"reply": "❌ Invalid DNI/NIE. Example: 12345678Z or X1234567L"})

    if awaiting == "phone":
        v = validate_spanish_phone(txt)
        if v:
            st["phone"] = v
            st["awaiting_field"] = None
            ask = _request_data_in_order(st)
            save_session(sid, st)
            return JSONResponse({"reply": ask or _after_data_collected(st)})
        save_session(sid, st)
        return JSONResponse({"reply": "❌ Invalid phone number. Use 9 digits starting with 6/7/8/9."})

    # 3) Schedule flow
    if st.get("step") == "schedule_date":
        iso = _parse_date(st["date_options"], txt)
        if not iso:
            save_session(sid, st)
            return JSONResponse({"reply": "❌ Invalid date. Use **DD/MM/YYYY** from the list shown."})
        st["selected_date"] = iso
        st["step"] = "schedule_time"
        hours = st["hours_by_date"].get(iso, [])
        save_session(sid, st)
        return JSONResponse({"reply": f"🗓️ {_fmt_es(iso)}\n👉 Choose a time: {', '.join(hours)}"})

    if st.get("step") == "schedule_time":
        iso = st["selected_date"]
        h = _parse_time(st["hours_by_date"].get(iso, []), txt)
        if not h:
            save_session(sid, st)
            return JSONResponse({"reply": "❌ Invalid time. Use **HH:MM** from the listed options."})
        st["selected_time"] = h
        st["step"] = "schedule_notes"
        save_session(sid, st)
        return JSONResponse({"reply": "📝 Would you like to add a note for the doctor? (optional — type your note or 'no')"})

    if st.get("step") == "schedule_notes":
        if txt.lower() not in NEGATIVE_RESPONSES:
            st["notes"] = txt.strip()
        st["step"] = "schedule_confirm"
        save_session(sid, st)
        return JSONResponse({"reply": f"📅 Schedule appointment on {_fmt_es(st['selected_date'])} at {st['selected_time']}? (y/n)"})

    if st.get("step") == "schedule_confirm":
        if txt.strip().lower() in AFFIRMATIVE_RESPONSES:
            ok, msg = _create_event(st.get("last_user_input", ""), st["selected_date"], st["selected_time"], st)
            clear_session(sid)
            return JSONResponse({"reply": msg + "\n\n(Reply **yes** for another action or **no** to close.)"})
        else:
            clear_session(sid)
            return JSONResponse({"reply": "❌ Booking not confirmed.\n\n(Reply **yes** for another action or **no** to close.)"})

    # 4) List / cancel / reschedule flow
    if st.get("step") == "post_appointments":
        ttxt = txt.lower()
        if ttxt in ("s", "exit", "no"):
            clear_session(sid)
            return JSONResponse({"reply": "👋 Thank you! If you need anything else, send me a greeting."})
        if ttxt.startswith("c"):
            st["step"] = "cancel_select"
            save_session(sid, st)
            return JSONResponse({"reply": "✂️ Send the **number** of the appointment to cancel, or 'exit'."})
        if ttxt.startswith("m"):
            st["step"] = "reschedule_select"
            save_session(sid, st)
            return JSONResponse({"reply": "🕘 Send the **number** of the appointment to reschedule, or 'exit'."})
        save_session(sid, st)
        return JSONResponse({"reply": "❌ Unrecognized option. Use c / m / s."})

    if st.get("step") in ("cancel_select", "reschedule_select"):
        if txt.strip().lower() in ("exit", "s"):
            clear_session(sid)
            return JSONResponse({"reply": "👋 Thank you! If you need anything else, send me a greeting."})
        if not txt.isdigit():
            save_session(sid, st)
            return JSONResponse({"reply": "❌ Send a valid number or 'exit'."})
        idx = int(txt) - 1
        appointments = st.get("appointments", [])
        if idx < 0 or idx >= len(appointments):
            save_session(sid, st)
            return JSONResponse({"reply": "❌ Invalid selection. Try again."})
        ev = appointments[idx]
        if st["step"] == "cancel_select":
            date_txt = datetime.fromisoformat(ev["start"]["dateTime"]).strftime("%d/%m/%Y %H:%M")
            cm.cancel_event(ev["id"])
            clear_session(sid)
            return JSONResponse({"reply": f"✅ Appointment on {date_txt} cancelled.\n\n(Reply **yes** for another action or **no** to close.)"})
        else:
            # Prepare rescheduling
            avail = cm.get_availability_next_days(days=5)
            st["step"] = "reschedule_date"
            st["date_options"] = list(avail.keys())
            st["hours_by_date"] = avail
            st["event_mod"] = ev
            lines = [f"📆 {_fmt_es(f)}: {', '.join(h) if h else 'No slots'}" for f, h in avail.items()]
            text = "🔎 Checking availability...\n" + "\n".join(lines)
            save_session(sid, st)
            return JSONResponse({"reply": text + "\n\n👉 Choose a new date in **DD/MM/YYYY** format."})

    if st.get("step") == "reschedule_date":
        iso = _parse_date(st["date_options"], txt)
        if not iso:
            save_session(sid, st)
            return JSONResponse({"reply": "❌ Invalid date. Use **DD/MM/YYYY** from the list."})
        st["selected_date"] = iso
        st["step"] = "reschedule_time"
        hours = st["hours_by_date"].get(iso, [])
        save_session(sid, st)
        return JSONResponse({"reply": f"🗓️ {_fmt_es(iso)}\n👉 Choose a time: {', '.join(hours)}"})

    if st.get("step") == "reschedule_time":
        iso = st["selected_date"]
        h = _parse_time(st["hours_by_date"].get(iso, []), txt)
        if not h:
            save_session(sid, st)
            return JSONResponse({"reply": "❌ Invalid time. Use **HH:MM** from the listed options."})
        ev = st.get("event_mod")
        dt_local = TZ.localize(datetime.strptime(f"{iso} {h}", "%Y-%m-%d %H:%M"))
        start = dt_local.isoformat()
        end = (dt_local + timedelta(hours=1)).isoformat()
        if cm.check_availability_excluding(ev["id"], start, end):
            cm.update_event_time(ev["id"], start, end)
            clear_session(sid)
            return JSONResponse({"reply": f"✅ Appointment rescheduled to {_fmt_es(iso)} at {h}.\n\n(Reply **yes** for another action or **no** to close.)"})
        save_session(sid, st)
        return JSONResponse({"reply": "⚠️ That time slot is not available. Choose another time."})

    # 5) Robust shortcuts (recovery when step gets lost)
    # 5a) Direct date input → start scheduling
    iso_direct = _parse_direct_date(txt)
    if iso_direct and not st.get("event_mod"):
        hours_map = st.get("hours_by_date") or {}
        hours = hours_map.get(iso_direct)
        if hours is None:
            hours = cm.get_available_slots(iso_direct)
            st["date_options"] = [iso_direct]
            st["hours_by_date"] = {iso_direct: hours}
        st["selected_date"] = iso_direct
        st["step"] = "schedule_time"
        save_session(sid, st)
        return JSONResponse({"reply": f"🗓️ {_fmt_es(iso_direct)}\n👉 Choose a time: {', '.join(hours) if hours else 'No slots for this day'}"})

    # 5b) Direct date input with active reschedule
    if iso_direct and st.get("event_mod"):
        hours_map = st.get("hours_by_date") or {}
        hours = hours_map.get(iso_direct)
        if hours is None:
            hours = cm.get_available_slots(iso_direct)
            hours_map[iso_direct] = hours
            st["hours_by_date"] = hours_map
        st["selected_date"] = iso_direct
        st["step"] = "reschedule_time"
        save_session(sid, st)
        return JSONResponse({"reply": f"🗓️ {_fmt_es(iso_direct)}\n👉 Choose a time: {', '.join(hours) if hours else 'No slots for this day'}"})

    # 5c) Direct time input when date is already selected
    if st.get("selected_date") and st.get("hours_by_date"):
        h_direct = _parse_time(st["hours_by_date"].get(st["selected_date"], []), txt)
        if h_direct:
            st["selected_time"] = h_direct
            if st.get("event_mod"):
                ev = st.get("event_mod")
                iso = st["selected_date"]
                dt_local = TZ.localize(datetime.strptime(f"{iso} {h_direct}", "%Y-%m-%d %H:%M"))
                start = dt_local.isoformat()
                end = (dt_local + timedelta(hours=1)).isoformat()
                if cm.check_availability_excluding(ev["id"], start, end):
                    cm.update_event_time(ev["id"], start, end)
                    clear_session(sid)
                    return JSONResponse({"reply": f"✅ Appointment rescheduled to {_fmt_es(iso)} at {h_direct}.\n\n(Reply **yes** for another action or **no** to close.)"})
                save_session(sid, st)
                return JSONResponse({"reply": "⚠️ That time slot is not available. Choose another time."})
            else:
                st["step"] = "schedule_notes"
                save_session(sid, st)
                return JSONResponse({"reply": "📝 Would you like to add a note for the doctor? (optional — type your note or 'no')"})

    # 6) Fallback: help/menu + NLU via orchestrator
    reply = orchestrator.handle_user_input(st, txt)
    save_session(sid, st)
    return JSONResponse({"reply": reply})


handler = Mangum(app)
