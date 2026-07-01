"""Meeting-capture summarizer prompt — Phase 4 of the meeting-capture feature.

Distinct from ``meeting_notes.py`` (the legacy voice path): this produces the
capture output schema (tldr / decisions / action_items / enhanced_notes /
confidence) and is consumed by ``meeting_service.summarize_meeting`` (Phase 5),
which fans owner=='me' action items out to commitments.

Two hard rules the model must not break:
  • ``enhanced_notes`` preserves the user's own note text **verbatim** as
    ``origin:'user'`` blocks. The model only *adds* ``origin:'ai'`` blocks; it
    never rewrites, paraphrases, merges, or drops a user note.
  • ``action_item.owner`` is whoever committed in the transcript. The mic
    channel is the user, so a commitment the user made is ``owner:'me'``; a
    commitment the other party made is ``owner:'them'`` (or their name).
"""

from app.prompts._helpers import wrap_untrusted

# Per-template guidance, interpolated into the prompt as {template_guidance}.
# Default ("general") is used for any unknown template name.
TEMPLATE_GUIDANCE: dict[str, str] = {
    "general": (
        "This is a general meeting. Capture the substance of the discussion: "
        "what was discussed, what was decided, and who owes what next."
    ),
    "interview": (
        "This is an interview (the user may be the interviewer or the candidate). "
        "Focus the TL;DR and notes on the candidate's background, signal on "
        "strengths/concerns, and any next steps in the process. Treat scheduling "
        "or 'we'll get back to you' statements as action items with the right owner."
    ),
    "one_on_one": (
        "This is a 1:1. Emphasise commitments each side made, blockers raised, "
        "and follow-ups. Personal/feedback context belongs in enhanced_notes, not "
        "the TL;DR."
    ),
    "standup": (
        "This is a standup. Keep the TL;DR to status across participants; surface "
        "blockers as open items and owner-tagged action items."
    ),
}


def guidance_for(template: str | None) -> str:
    """Return the guidance block for a template, falling back to 'general'."""
    return TEMPLATE_GUIDANCE.get((template or "general"), TEMPLATE_GUIDANCE["general"])


MEETING_SUMMARY_PROMPT = """You are summarising a meeting that was captured live with two audio channels.

Speaker tags in the transcript:
- "me"   = the user whose mic captured this side of the conversation.
- "them" = the other party on the call.

{template_guidance}

This meeting took place on: {meeting_date}. Resolve any relative deadlines
("Friday", "next week", "tomorrow") against that date.

Transcript (speaker-tagged, ordered by time):
""" + wrap_untrusted("{transcript}", "transcript") + """

The user's own notes, typed live during the meeting:
""" + wrap_untrusted("{user_notes}", "user_notes") + """

Produce a JSON object with exactly these fields:
{{
  "tldr": <string, 2-4 sentence plain-English summary of the meeting>,
  "decisions": [
    {{"text": <string, one decision that was actually reached>}}
  ],
  "action_items": [
    {{
      "text": <string, the task to be done>,
      "owner": <"me" if the user committed to it, "them" if the other party did, or the person's name if known>,
      "due_hint": <string natural-language deadline from the transcript (e.g. "by Friday", "next week"), or null>,
      "due_iso": <the due_hint resolved to an absolute date "YYYY-MM-DD" (or full ISO-8601 datetime) relative to the meeting date above, or null if there is no deadline>
    }}
  ],
  "enhanced_notes": [
    {{"origin": "user", "text": <one of the user's notes, copied VERBATIM>}},
    {{"origin": "ai", "text": <string, detail or context drawn from the transcript that enriches the note above>}}
  ],
  "confidence": <float 0.0-1.0, your confidence that this summary is accurate and complete>
}}

Rules for enhanced_notes — follow exactly:
- Every one of the user's notes MUST appear as an {{"origin": "user", ...}} block with its text copied CHARACTER-FOR-CHARACTER. Do not fix typos, reword, translate, merge, reorder away from, or omit any user note.
- You may insert {{"origin": "ai", ...}} blocks before or after a user block to add transcript-derived detail. Never put AI-generated text inside an "origin": "user" block.
- If the user wrote no notes, enhanced_notes may contain only "origin": "ai" blocks (or be an empty array if there is nothing to add).

Rules for action_items:
- owner is whoever made the commitment in the transcript, not whoever benefits. The mic channel ("me") is the user.
- Only include items that were genuinely committed to or requested. Do not invent follow-ups.
- due_iso must be a real calendar date derived from due_hint and the meeting date; if the deadline is vague or absent, set both due_hint and due_iso to null. Never guess a date that wasn't implied.

Return JSON only — no explanation, no markdown.
"""
