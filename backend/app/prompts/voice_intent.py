VOICE_INTENT_PROMPT = """Classify this voice command from a Felix user into a structured intent.

Voice command: "{transcript}"

Possible intents:
- read_emails: user wants to hear their emails
- reply_email: user wants to reply to an email (extract recipient and reply content if given)
- compose_email: user wants to write a new email (extract recipient and topic)
- schedule_meeting: user wants to schedule a meeting (extract who, duration, timeframe)
- check_calendar: user wants to know their schedule
- check_follow_ups: user wants to see what they're waiting on
- summarise_inbox: user wants a summary of their inbox
- start_meeting_notes: user wants to start recording meeting notes
- general_question: general question for Felix

Return a JSON object:
{{
  "intent": <string from list above>,
  "recipient": <string email or name, null if not applicable>,
  "topic": <string, what the email/meeting is about, null if not applicable>,
  "reply_content": <string, what the user wants to say, null if not applicable>,
  "timeframe": <string, e.g. "next week", "tomorrow afternoon", null if not applicable>,
  "duration_minutes": <int, null if not applicable>,
  "raw_transcript": "{transcript}"
}}

Return JSON only — no explanation, no markdown.
"""
