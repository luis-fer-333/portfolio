"""LLM-based intent parser using Cerebras (LLaMA 3.3-70B).

Classifies patient messages into appointment-management intents
(SCHEDULE, QUERY, CANCEL, RESCHEDULE, GREETING, UNKNOWN) and extracts
date/time entities from relative natural-language expressions.
"""

import os
import json
import requests
from datetime import datetime, timedelta

import pytz
import boto3

TZ = pytz.timezone("Europe/Madrid")
API_URL = "https://api.cerebras.ai/v1/chat/completions"
MODEL_ID = "llama-3.3-70b"

sm = boto3.client("secretsmanager")


def _resolve_secret(name: str, default: str = "") -> str:
    """Read an env var; if its value starts with 'arn:', resolve it via Secrets Manager."""
    v = os.getenv(name, default)
    if v and v.startswith("arn:"):
        try:
            s = sm.get_secret_value(SecretId=v).get("SecretString", "")
            return s or v
        except Exception:
            return v
    return v


API_KEY = _resolve_secret("CEREBRAS_API_KEY", "")


def parse_conversation(patient_id: str, history: list[str], user_input: str):
    """Send the conversation context to the LLM and return (intent, date, time).

    The LLM is prompted to return structured JSON with:
      - intent: SCHEDULE | QUERY | CANCEL | RESCHEDULE | GREETING | UNKNOWN
      - date: YYYY-MM-DD or NONE
      - time: HH:MM or NONE

    Relative date expressions ("tomorrow", "next Monday") are resolved by the
    LLM using the current date/time injected into the prompt.
    """
    now = datetime.now(TZ)
    context = "\n".join(history[-3:])

    prompt = f"""
You are a virtual assistant for a medical clinic.
Interpret the patient's intent and, if it involves an appointment, convert
relative date/time expressions into concrete values.
Respond in JSON with:
{{
  "intent": "SCHEDULE" | "QUERY" | "CANCEL" | "RESCHEDULE" | "GREETING" | "UNKNOWN",
  "date": "YYYY-MM-DD" | "NONE",
  "time": "HH:MM" | "NONE"
}}
Today is {now.strftime('%A %Y-%m-%d')} and the time is {now.strftime('%H:%M')} in Europe/Madrid.
The patient is identified as: {patient_id}

Examples:
User: I want an appointment tomorrow at 10
→ {{"intent": "SCHEDULE", "date": "{(now + timedelta(days=1)).strftime('%Y-%m-%d')}", "time": "10:00"}}
User: reschedule for two days from now at 14
→ {{"intent": "RESCHEDULE", "date": "{(now + timedelta(days=2)).strftime('%Y-%m-%d')}", "time": "14:00"}}
User: next Monday at 15
→ {{"intent": "SCHEDULE", "date": "{(now + timedelta(days=(7 - now.weekday()) % 7 or 7)).strftime('%Y-%m-%d')}", "time": "15:00"}}
User: hello
→ {{"intent": "GREETING", "date": "NONE", "time": "NONE"}}
User: I like football
→ {{"intent": "UNKNOWN", "date": "NONE", "time": "NONE"}}

Recent history:
{context}

New message:
{user_input}
""".strip()

    response = requests.post(
        API_URL,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": MODEL_ID,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Respond only with valid JSON. Do not explain. If the message "
                        "is unrelated to appointment management, respond with "
                        '{"intent": "UNKNOWN", "date": "NONE", "time": "NONE"}.'
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
        },
        timeout=30,
    )

    try:
        content = response.json()["choices"][0]["message"]["content"]
        json_part = content[content.index("{") : content.rindex("}") + 1]
        data = json.loads(json_part)
        intent = data.get("intent", "UNKNOWN")
        valid_intents = {"SCHEDULE", "QUERY", "CANCEL", "RESCHEDULE", "GREETING", "UNKNOWN"}
        if intent not in valid_intents:
            intent = "UNKNOWN"
        return intent, data.get("date", "NONE"), data.get("time", "NONE")
    except Exception:
        return "UNKNOWN", "NONE", "NONE"
