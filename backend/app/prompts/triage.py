from app.prompts._helpers import wrap_untrusted

TRIAGE_PROMPT = """You are triaging email for {user_name}. Classify this email into exactly one category and extract metadata.

CATEGORIES (pick the FIRST that applies, in this order):
1. vip: Sender is on the VIP list below — this overrides everything else
2. action_required: The email asks {user_name} to DO something — reply, decide, review, approve, sign, confirm, provide information, attend, schedule, or complete any task. If the sender is asking a question, requesting input, proposing something that needs agreement, or expecting {user_name} to take any next step, this is action_required.
3. waiting_on: {user_name} is the one waiting for someone else to respond (rare for inbound; use only when the email shows {user_name} chased and is still waiting)
4. automated: System notifications, order confirmations, receipts, shipping updates, security alerts, no-reply senders, automated reports
5. newsletter: Subscription content, marketing emails, digests, promotional offers
6. fyi: Purely informational with NO action expected from {user_name} — status updates that need no reply, CC'd threads where someone else is handling it, announcements that don't require acknowledgement

IMPORTANT: When in doubt between action_required and fyi, choose action_required. It is better to surface an email that needs attention than to bury it. An email is action_required if a reasonable person would feel they should respond or do something after reading it.

VIP CONTACTS:
""" + wrap_untrusted("{vip_list}", "vip_list") + """

EMAIL:
From: {sender}
Subject: {subject}
Body:
""" + wrap_untrusted("{body}", "email") + """

Return a JSON object with exactly these fields — no explanation, no markdown:
{{
  "category": "<one of the categories above>",
  "urgency": "<low|medium|high|critical>",
  "topic": "<single descriptive phrase, e.g. 'Q3 budget proposal' or 'invoice payment request'>",
  "sentiment_of_sender": "<neutral|positive|stressed|frustrated|urgent>",
  "requires_response_by": "<ISO 8601 date string if a deadline is stated or clearly implied, otherwise null>",
  "key_entities": ["<person, company, date, or amount mentioned>"]
}}

Return raw JSON only. Do not wrap in markdown code blocks. Do not include ```json or ``` in your response. Return only the JSON object."""
