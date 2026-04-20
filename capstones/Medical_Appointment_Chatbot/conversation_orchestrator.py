"""Conversation orchestrator for the medical appointment chatbot.

Routes user input through keyword matching or LLM-based intent parsing,
manages the conversational state machine, and handles redirect/reset logic
when the user's intent cannot be understood after multiple attempts.
"""

import os
from llm_intent_parser import parse_conversation


class ConversationOrchestrator:
    """Orchestrates the conversation flow between the user and the appointment system."""

    # Intents recognized by the assistant
    ALLOWED_ACTIONS = {
        "SCHEDULE",
        "QUERY",
        "QUERY_MY_APPOINTMENTS",
        "CANCEL",
        "RESCHEDULE",
        "GREETING",
    }

    MAX_REDIRECT_ATTEMPTS = int(os.getenv("MAX_REDIRECT_ATTEMPTS", "3"))

    def __init__(self, redirect_message_text: str | None = None):
        """Initialize with an optional custom redirect message.

        The message can also be set via the REDIRECT_MESSAGE env var.
        """
        self.redirect_message_text = (
            redirect_message_text
            or os.getenv(
                "REDIRECT_MESSAGE",
                "❌ I didn't understand your request. Could you rephrase it?",
            )
        )

    def redirect_message(self, session_state: dict, user_text: str, intent: str) -> str:
        """Return a redirect message or reset the conversation after too many failures."""
        import agent_service as svc

        count = session_state.get("redirect_attempts", 0) + 1
        session_state["redirect_attempts"] = count
        if count < self.MAX_REDIRECT_ATTEMPTS:
            return self.redirect_message_text

        # Reset the conversation with stricter rules
        session_state.clear()
        session_state.update(svc._new_state())
        return (
            "⚠️ I couldn't understand your intent after several attempts. "
            "Let's start over — stricter rules will be applied."
        )

    def handle_user_input(self, session_state: dict, user_text: str) -> str:
        """Process user input, update session state, and return the reply."""
        # Lazy import to avoid circular dependency at import time
        import agent_service as svc

        st = session_state
        txt = (user_text or "").strip()

        # Append to conversation history for LLM context
        st["history"] = (st.get("history") or [])[-20:] + [f"Patient: {txt}"]
        norm = svc._norm(txt)

        # Help commands
        if norm in svc.HELP_PHRASES:
            st["redirect_attempts"] = 0
            return svc._menu_text()

        # Deterministic keyword routing or LLM-based intent parsing
        forced_intent = svc.ROUTING.get(norm)
        intent, date_str, time_str = parse_conversation(
            st.get("dni"), st["history"], txt
        )
        if forced_intent:
            intent, date_str, time_str = forced_intent, "NONE", "NONE"

        st["last_user_input"] = txt

        if intent not in self.ALLOWED_ACTIONS:
            return self.redirect_message(st, txt, intent)

        # Valid intent recognized — reset redirect counter
        st["redirect_attempts"] = 0

        # Check if personal data is needed before proceeding
        if svc._needs_personal_data(intent) and (
            not st.get("name") or not st.get("dni") or not st.get("phone")
        ):
            st["pending_intent"] = intent
            return svc._request_data_in_order(st)

        if intent == "QUERY":
            if date_str == "NONE":
                avail = svc.cm.get_availability_next_days(days=5)
                lines = [
                    f"📆 {svc._fmt_es(f)}: {', '.join(h) if h else 'No slots'}"
                    for f, h in avail.items()
                ]
                text = "🔎 Checking availability...\n" + "\n".join(lines)
                return text + "\n\n(Reply **yes** for another action or **no** to close.)"
            svc.cm.get_available_slots(date_str)
            return (
                f"📋 Availability for {svc._fmt_es(date_str)} shown."
                "\n\n(Reply **yes** for another action or **no** to close.)"
            )

        if intent == "SCHEDULE":
            if st.get("date_options"):
                iso_direct = svc._parse_date(st["date_options"], txt)
                if iso_direct:
                    st["selected_date"] = iso_direct
                    st["step"] = "schedule_time"
                    hours = st["hours_by_date"].get(iso_direct, [])
                    return f"🗓️ {svc._fmt_es(iso_direct)}\n👉 Choose a time: {', '.join(hours)}"
            if date_str == "NONE" or time_str == "NONE":
                avail = svc.cm.get_availability_next_days(days=5)
                st["step"] = "schedule_date"
                st["date_options"] = list(avail.keys())
                st["hours_by_date"] = avail
                lines = [
                    f"📆 {svc._fmt_es(f)}: {', '.join(h) if h else 'No slots'}"
                    for f, h in avail.items()
                ]
                text = "🔎 Checking availability...\n" + "\n".join(lines)
                return text + "\n\n👉 Choose a date in **DD/MM/YYYY** format."
            st["selected_date"] = date_str
            st["selected_time"] = time_str
            st["step"] = "schedule_notes"
            return "📝 Would you like to add a note for the doctor? (optional — type your note or 'no')"

        if intent == "QUERY_MY_APPOINTMENTS":
            appointments = svc.cm.list_patient_appointments(st["dni"], days_ahead=365)
            if not appointments:
                avail = svc.cm.get_availability_next_days(days=5)
                st["step"] = "schedule_date"
                st["date_options"] = list(avail.keys())
                st["hours_by_date"] = avail
                lines = [
                    f"📆 {svc._fmt_es(f)}: {', '.join(h) if h else 'No slots'}"
                    for f, h in avail.items()
                ]
                text = "📭 You have no upcoming appointments.\n\nWould you like to schedule one? Reply with a **date in DD/MM/YYYY**.\n\n"
                text += "🔎 Checking availability...\n" + "\n".join(lines)
                return text
            st["appointments"] = appointments
            st["step"] = "post_appointments"
            return (
                svc._format_appointments(appointments)
                + "\n\nWould you like to **cancel (c)**, **reschedule (m)**, or **exit (s)**?"
            )

        if intent == "CANCEL":
            appointments = svc.cm.list_patient_appointments(st["dni"], days_ahead=365)
            if not appointments:
                return "📭 You have no upcoming appointments."
            st["appointments"] = appointments
            st["step"] = "cancel_select"
            return (
                svc._format_appointments(appointments)
                + "\n\nSend the **number** of the appointment to cancel, or 'exit'."
            )

        if intent == "RESCHEDULE":
            appointments = svc.cm.list_patient_appointments(st["dni"], days_ahead=365)
            if not appointments:
                return "📭 You have no upcoming appointments."
            st["appointments"] = appointments
            st["step"] = "reschedule_select"
            return (
                svc._format_appointments(appointments)
                + "\n\nSend the **number** of the appointment to reschedule, or 'exit'."
            )

        if intent == "GREETING":
            st["awaiting_intent"] = True
            return svc._menu_text()

        # Quick yes confirmation when no active step
        if norm in {"yes", "y", "ok", "sure"} and not st.get("step") and not st.get("awaiting_field"):
            st["awaiting_intent"] = True
            return svc._menu_text()

        # Unrecognized message — redirect
        return self.redirect_message(st, txt, intent)
