"""Strict Responses schemas for the existing fast-model output contracts.

Optional values are nullable because strict mode requires every object key.
The existing profile merge ignores nulls so unknown facts cannot erase memory.
"""


def obj(**properties):
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


def array(items):
    return {"type": "array", "items": items}


def enum(*values):
    return {"type": "string", "enum": list(values)}


def nullable(schema):
    return {"anyOf": [schema, {"type": "null"}]}


STRING = {"type": "string"}
BOOL = {"type": "boolean"}
OPTIONAL_STRING = nullable(STRING)
SCORE = {"type": "number", "minimum": 0, "maximum": 1}
CARD = obj(
    kind=enum("context", "answer", "fact", "contradiction", "follow_up"),
    title=STRING, body=STRING, usefulness_score=SCORE,
)

FAST_SCHEMAS = {
    "triage": obj(
        category=enum("vip", "action_required", "waiting_on", "automated", "newsletter", "fyi"),
        urgency=enum("low", "medium", "high", "critical"), topic=STRING,
        sentiment_of_sender=enum("neutral", "positive", "stressed", "frustrated", "urgent"),
        requires_response_by=OPTIONAL_STRING, key_entities=array(STRING),
    ),
    "voice_intent": obj(
        intent=enum("read_emails", "reply_to", "compose_new", "schedule_meeting",
                    "whats_today", "whos_waiting", "summarise_inbox", "start_meeting_notes",
                    "follow_up_with", "check_calendar", "general_question"),
        **{key: OPTIONAL_STRING for key in (
            "recipient", "topic", "reply_content", "timeframe", "date_iso",
            "weekday", "start_time", "end_time",
        )},
        duration_minutes=nullable({"type": "integer"}), raw_transcript=STRING,
    ),
    "follow_up_detect": obj(
        needs_follow_up=BOOL, topic=OPTIONAL_STRING,
        urgency=nullable(enum("low", "medium", "high")),
        suggested_follow_up_days=nullable({"type": "integer"}), reason=STRING,
    ),
    "commitment_detect": obj(commitments=array(obj(
        direction=enum("owed_by_user", "owed_to_user"), counterparty_email=STRING,
        text=STRING, source_quote=STRING, deadline_iso=OPTIONAL_STRING, confidence=SCORE,
    ))),
    "sentiment": obj(
        sentiment_of_sender=enum("neutral", "positive", "satisfied", "concerned",
                                 "frustrated", "stressed", "angry"),
        urgency_signals=array(STRING), pressure_level=enum("none", "mild", "moderate", "high"),
        notable_phrases=array(STRING),
    ),
    "session_summary": obj(summary=STRING, open_items=array(obj(item=STRING, owner=STRING))),
    "episode_distil": obj(summary=STRING, entities=array(STRING), importance=SCORE),
    "profile_extract": obj(
        profile=obj(
            **{key: OPTIONAL_STRING for key in (
                "name", "role", "company", "timezone", "communication_style",
            )},
            key_contacts=nullable(array(obj(name=STRING, relationship=STRING))),
        ),
        preferences=obj(**{key: OPTIONAL_STRING for key in (
            "email_tone", "briefing_detail_level", "scheduling_habits",
        )}),
    ),
    "live_assist_watch": obj(card=nullable(CARD)),
    # Root anyOf is unsupported. Keep both nullable fields; production already
    # checks interview_question first and then card. Null/null means silence.
    "live_assist_interview_watch": obj(
        card=nullable(CARD),
        interview_question=nullable(obj(
            question=STRING, answer_type=enum("coding", "system_design"),
            asked_by=enum("them", "me"), complete=BOOL,
        )),
    ),
}
