from app.prompts._helpers import wrap_untrusted

JOB_DETECTION_PROMPT = """You are analysing one email from a user who is job hunting. Decide whether it relates to a specific job application, and if so extract structured fields.

From: {from_email}
To: {to_email}
Subject: {subject}
Date: {sent_at}
Body:
""" + wrap_untrusted("{body}", "email") + """

An email IS job-related if it concerns a specific role the user applied to or is being recruited for: application confirmations, recruiter outreach about a concrete role, interview scheduling, assessments/take-homes, offers, or rejections. It is NOT job-related if it is a generic job-board digest/newsletter, a marketing blast, a LinkedIn notification, or unrelated personal/work mail.

Classify the pipeline stage using these exact definitions — be precise, this drives a board:
- "saved":        the user is interested but has not yet applied (e.g. saved posting, intro chat with no application).
- "applied":      an application was submitted or acknowledged ("we received your application"); no human screen yet.
- "phone_screen": an initial recruiter / screening call is proposed, scheduled, or completed (recruiter chat, HR screen).
- "interview":    a substantive interview round — technical, onsite, panel, hiring-manager, or take-home assessment.
- "offer":        an offer is extended or offer terms are being discussed.
- "rejected":     the application was declined / "not moving forward" / position filled.
- "withdrawn":    the user withdrew or declined to proceed.
- "accepted":     the user accepted an offer.

Pick the SINGLE stage this email most directly signals. Prefer the furthest stage the email actually evidences (an interview-scheduling email is "interview"/"phone_screen", not "applied"). A rejection is "rejected" regardless of which round it follows.

Return a JSON object:
{{
  "is_job_related": <true|false>,
  "company":        <string company name, or null>,
  "role_title":     <string role/title, or null>,
  "stage":          <one of the stages above, or null if not job-related>,
  "contact_name":   <recruiter/interviewer name if present, else null>,
  "contact_email":  <their email if present, else null>,
  "confidence":     <float 0.0-1.0 — your certainty this is job-related AND the stage is correct>,
  "summary":        <one short sentence describing what happened, or null>
}}

Confidence guidance: 0.9+ only when company, role, and stage are all unambiguous. Lower it when the company/role is implied, the stage is uncertain, or the email could plausibly be non-job-related.

Return JSON only — no explanation, no markdown.
"""
