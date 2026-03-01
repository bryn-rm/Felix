FOLLOW_UP_DETECTION_PROMPT = """Analyse this sent email and determine whether it requires a follow-up if no reply is received.

To: {to}
Subject: {subject}
Body: {body}

A follow-up is needed if the email:
- Contains a proposal, quote, or offer awaiting acceptance
- Asks a specific question that requires an answer
- Requests action from the recipient
- Involves a deadline or time-sensitive matter
- Is part of an ongoing negotiation

Return a JSON object:
{{
  "needs_follow_up": <true|false>,
  "topic": <string, brief description e.g. "DataCorp proposal" — null if no follow-up needed>,
  "urgency": <"low"|"medium"|"high" — null if no follow-up needed>,
  "suggested_follow_up_days": <int, how many days before following up — null if no follow-up needed>,
  "reason": <string, why a follow-up is or isn't needed>
}}

Return JSON only — no explanation, no markdown.
"""
