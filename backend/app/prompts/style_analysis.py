STYLE_ANALYSIS_PROMPT = """Analyse the following sent emails and extract a writing style profile for this person.

EMAILS (separated by ---):
{emails}

Return a JSON object with exactly these fields:
{{
  "avg_words_per_email": <float>,
  "formality_score": <float 0.0-1.0, where 0=very casual, 1=very formal>,
  "greeting_patterns": <list of strings, e.g. ["Hi", "Hey", "Good morning"]>,
  "sign_off_patterns": <list of strings, e.g. ["Cheers", "Thanks", "Best"]>,
  "bullet_point_tendency": <float 0.0-1.0>,
  "emoji_frequency": <float 0.0-1.0>,
  "question_tendency": <float 0.0-1.0, how often they ask questions>,
  "hedging_language": <list of characteristic phrases, e.g. ["I think", "perhaps", "might"]>,
  "directness_score": <float 0.0-1.0, where 0=very indirect, 1=very direct>,
  "avg_response_time_hours": <float, estimated from patterns if detectable, else null>,
  "style_notes": <string, 1-2 sentence plain English summary of their voice>
}}

Return JSON only — no explanation, no markdown.
"""
