COMMITMENT_DETECTION_PROMPT = """Analyse the email below and extract any commitments — concrete promises with a deliverable, made by either the SENDER or the RECIPIENT. Ignore vague pleasantries ("we should catch up", "let's chat soon") and statements of intent without a specific deliverable.

CONTEXT
- Source kind: {source_kind}     // "inbound" (sender wrote to user) or "sent" (user wrote to recipient)
- The user's name: {user_name}
- The user's email: {user_email}
- The other party (counterparty) email: {counterparty_email}
- The other party (counterparty) name: {counterparty_name}

EMAIL
From:    {from_email}
To:      {to_email}
Subject: {subject}
Date:    {sent_at}

Body:
{body}

EXTRACTION RULES
- A commitment is a sentence containing: "I'll …", "I will …", "we'll …", "we will …", "I can …", "I'm going to …", "you'll have …", "I'll send …", "I'll get back to you …", or equivalent. \
A specific deliverable + an implied or explicit deadline is required.
- For each commitment, fix the *direction*:
  - "owed_by_user" → the user has promised something to the counterparty.
  - "owed_to_user" → the counterparty has promised something to the user.
- Resolve "I" and "you" using the From/To headers above.
- For each commitment, also pick a `counterparty_email` — the specific person on the To/Cc line whom the promise concerns. If the body addresses a name ("Bob, I'll send the deck"), match that name to the matching address on the To/Cc line. If the body doesn't single anyone out, use the first non-user recipient. Always return an address that appears in From/To/Cc — never invent one.
- Capture the original sentence verbatim in `source_quote` (≤ 200 chars).
- Extract `deadline_iso` as ISO-8601 if a date or relative term is present \
("Friday", "EOD", "by next week", "tomorrow"). Use the email date for resolution. \
If no deadline is implied, set `deadline_iso` to null.
- `confidence` ∈ [0.0, 1.0]: 0.9+ for unambiguous commitments with clear deliverables; \
0.5–0.8 for likely-but-hedged ("I'll try to …"); below 0.5 should generally be excluded.

Return a JSON object with exactly this shape:
{{
  "commitments": [
    {{
      "direction":          "owed_by_user" | "owed_to_user",
      "counterparty_email": "<the specific recipient this commitment is to/from>",
      "text":               "<one short sentence describing the commitment>",
      "source_quote":       "<verbatim ≤200 chars>",
      "deadline_iso":       "<ISO-8601 string or null>",
      "confidence":         <float 0.0–1.0>
    }}
  ]
}}

If there are no commitments, return {{ "commitments": [] }}.
Return JSON only — no preamble, no markdown.
"""
