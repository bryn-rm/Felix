MEETING_NOTES_PROMPT = """You are generating structured meeting notes from a transcript.

Meeting: {meeting_title}
Attendees: {attendees}

Transcript:
{transcript}

Return a JSON object with exactly these fields:
{{
  "summary": <string, 2-4 sentence plain English summary of what was discussed>,
  "action_items": [
    {{"item": <string>, "owner": <string or null>, "deadline": <ISO date or null>}}
  ],
  "decisions_made": [<string>],
  "open_questions": [<string>],
  "follow_up_email_subject": <string, suggested subject line for follow-up email>,
  "follow_up_email_body": <string, complete draft follow-up email to send to attendees>
}}

Return JSON only — no explanation, no markdown.
"""
