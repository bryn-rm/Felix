TRIAGE_PROMPT = """You are triaging email for {user_name}. Classify this email into exactly one category and extract metadata.

CATEGORIES:
- action_required: Needs a reply or decision from {user_name}
- fyi: Informational only, no reply needed
- waiting_on: {user_name} is awaiting a reply they're chasing (rare for pure inbound; use when it's clear from context)
- newsletter: Subscription content, marketing, digests
- automated: System notifications, order confirmations, receipts, no-reply senders
- vip: Sender is on the VIP list — overrides all other categories

VIP CONTACTS: {vip_list}

EMAIL:
From: {sender}
Subject: {subject}
Body:
{body}

Return a JSON object with exactly these fields — no explanation, no markdown:
{{
  "category": "<one of the categories above>",
  "urgency": "<low|medium|high|critical>",
  "topic": "<single descriptive phrase, e.g. 'Q3 budget proposal' or 'invoice payment request'>",
  "sentiment_of_sender": "<neutral|positive|stressed|frustrated|urgent>",
  "requires_response_by": "<ISO 8601 date string if a deadline is stated or clearly implied, otherwise null>",
  "key_entities": ["<person, company, date, or amount mentioned>"]
}}"""
