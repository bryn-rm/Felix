from app.prompts._helpers import wrap_untrusted

SENTIMENT_PROMPT = """Analyse the emotional tone and urgency of this email.

From: {sender}
Subject: {subject}
Body:
""" + wrap_untrusted("{body}", "email") + """

Return a JSON object:
{{
  "sentiment_of_sender": <"neutral"|"positive"|"satisfied"|"concerned"|"frustrated"|"stressed"|"angry">,
  "urgency_signals": <list of strings — specific phrases or signals that indicate urgency, empty list if none>,
  "pressure_level": <"none"|"mild"|"moderate"|"high">,
  "notable_phrases": <list of strings — up to 3 phrases that best capture the tone>
}}

Return JSON only — no explanation, no markdown.
"""
