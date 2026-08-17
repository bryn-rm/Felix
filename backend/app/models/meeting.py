"""Constrained meeting configuration shared by API and service layers."""

from typing import Literal


MeetingType = Literal["general", "interview"]
MeetingUserRole = Literal["candidate", "interviewer"]

# How the session was opened. 'browser_capture' records tab + mic audio over the
# capture WebSocket; 'manual_notes' is the assistant-only session that has no
# socket, microphone, or tab share at all. Enough behaviour branches on this —
# WebSocket admission, the auto-end sweep's idle rule, the REST ask route, the
# standalone ask prompt — that the literals belong here rather than being
# hand-repeated at each site.
MeetingSource = Literal["browser_capture", "manual_notes"]

MEETING_SOURCE_CAPTURE: MeetingSource = "browser_capture"
MEETING_SOURCE_MANUAL: MeetingSource = "manual_notes"
MEETING_SOURCES = frozenset({MEETING_SOURCE_CAPTURE, MEETING_SOURCE_MANUAL})

# How a meeting's stored configuration resolves into behaviour. "legacy_" marks
# rows written before migration 020, which have no explicit meeting_type.
AssistMeetingMode = Literal[
    "general",
    "interview_candidate",
    "interview_interviewer",
    "legacy_interview",
]

# The modes where the USER is the one being interviewed. Two consequences hang
# off this and must agree: Live Assist solves the other participant's technical
# questions, and the summary fans out to the user's own job tracker. An
# interviewer's meeting is an interview too — but the candidate in it is someone
# else, so neither applies.
CANDIDATE_ASSIST_MODES = frozenset({"interview_candidate", "legacy_interview"})


def validate_meeting_source(source: str) -> MeetingSource:
    """Reject a source the rest of the stack has no branch for.

    The column has no CHECK constraint, so an unrecognised value would be
    written happily and then behave as neither kind: the WebSocket rejects it,
    the auto-end sweep skips it, and the ask route 404s — with nothing failing
    at write time. Fail at the boundary instead.
    """
    if source not in MEETING_SOURCES:
        raise ValueError(f"Unknown meeting source: {source!r}")
    return source  # type: ignore[return-value]


def validate_meeting_mode(
    meeting_type: MeetingType | None,
    user_role: MeetingUserRole | None,
) -> None:
    """Reject combinations that cannot have coherent Live Assist behaviour.

    ``None`` is accepted only for a fully unclassified legacy meeting. New
    clients always send an explicit type, but the start API still needs to
    preserve the previous ``template='interview'`` contract for cached clients.
    """
    if meeting_type is None:
        if user_role is not None:
            raise ValueError("Legacy meetings cannot have an interview role")
        return
    if meeting_type == "interview" and user_role is None:
        raise ValueError("Interview meetings require a Candidate or Interviewer role")
    if meeting_type == "general" and user_role is not None:
        raise ValueError("General meetings cannot have an interview role")


def resolve_assist_meeting_mode(
    meeting_type: str | None,
    user_role: str | None,
    template: str | None,
) -> AssistMeetingMode:
    """Apply explicit configuration before the legacy template fallback.

    ``meeting_type IS NULL`` is the migration's legacy marker. Only those rows
    may retain the old ``template='interview'`` behaviour; an explicit General
    selection is never promoted to Interview by a template or model response.
    Invalid explicit combinations fail safely into the general card flow.

    The frontend mirrors this in ``components/meetings/constants.ts`` — change
    both together.
    """
    if meeting_type == "interview":
        if user_role == "candidate":
            return "interview_candidate"
        if user_role == "interviewer":
            return "interview_interviewer"
        return "general"
    # Any other explicit value (including "general", and anything the CHECK
    # constraint would reject) is general. Only a NULL — a genuinely legacy row —
    # may fall back to the template.
    if meeting_type is None and template == "interview":
        return "legacy_interview"
    return "general"


def uses_candidate_assist(
    meeting_type: str | None,
    user_role: str | None,
    template: str | None,
) -> bool:
    """Is the Felix user the one being interviewed in this meeting?"""
    return resolve_assist_meeting_mode(
        meeting_type, user_role, template
    ) in CANDIDATE_ASSIST_MODES
