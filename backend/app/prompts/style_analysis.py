STYLE_ANALYSIS_PROMPT = """Analyse these sent emails and extract a writing style profile for this person.

EMAILS (separated by ---):
{emails}

Return a JSON object with exactly these fields — no explanation, no markdown:
{{
  "avg_words_per_email": <float, average word count across the sample>,
  "formality_score": <float 0.0–1.0, where 0.0 = very casual, 1.0 = very formal>,
  "greeting_patterns": <list of strings ordered by frequency, e.g. ["Hi", "Hey", "Good morning"]>,
  "sign_off_patterns": <list of strings ordered by frequency, e.g. ["Cheers", "Thanks", "Best"]>,
  "bullet_point_tendency": <float 0.0–1.0, proportion of emails that use bullet points>,
  "emoji_frequency": <float 0.0–1.0, proportion of emails that contain at least one emoji>,
  "question_tendency": <float 0.0–1.0, how often they ask questions in their emails>,
  "hedging_language": <list of characteristic softening/hedging phrases they use, e.g. ["I think", "perhaps", "just wanted to"]>,
  "directness_score": <float 0.0–1.0, where 0.0 = very indirect/deferential, 1.0 = very direct/assertive>,
  "avg_response_time_hours": <float if detectable from timestamps, otherwise null>,
  "style_notes": "<1–2 sentence plain English summary capturing their overall voice and what makes their emails distinctive>"
}}"""
