SENTIMENT_PROMPT = """Analyse the emotional tone and urgency of this email.

From: {sender}
Subject: {subject}
Body: {body}

Return a JSON object:
{{
  "sentiment": <"neutral"|"positive"|"satisfied"|"concerned"|"frustrated"|"stressed"|"angry">,
  "urgency_signals": <list of strings — specific phrases or signals that indicate urgency, empty list if none>,
  "pressure_level": <"none"|"mild"|"moderate"|"high">,
  "notable_phrases": <list of strings — up to 3 phrases that best capture the tone>
}}

Return JSON only — no explanation, no markdown.
"""
