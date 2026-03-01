TRIAGE_PROMPT = """You are triaging email for {user_name}. Given this email, classify it into exactly one category:
action_required | fyi | waiting_on | newsletter | automated | vip

Category definitions:
- action_required: Needs a reply or decision from {user_name}
- fyi: Informational only, no reply needed
- waiting_on: {user_name} sent something and is awaiting a reply (use for emails where {user_name} is the sender in a thread context — typically not applicable for inbound emails unless clearly a reply to something they sent)
- newsletter: Subscription content, digests, marketing
- automated: Order confirmations, receipts, system notifications, no-reply senders
- vip: From a contact on the VIP list (takes priority over other categories)

Also extract:
- urgency: low | medium | high | critical
- topic: single descriptive phrase (e.g. "Q3 budget proposal", "meeting rescheduled", "invoice payment")
- sentiment_of_sender: neutral | positive | stressed | frustrated | urgent
- requires_response_by: ISO 8601 date string if an explicit or implied deadline exists, null otherwise
- key_entities: list of people, companies, dates, and amounts mentioned

VIP contacts: {vip_list}

Email:
From: {sender}
Subject: {subject}
Body: {body}

Return JSON only — no explanation, no markdown. Example format:
{{
  "category": "action_required",
  "urgency": "high",
  "topic": "contract revision request",
  "sentiment_of_sender": "stressed",
  "requires_response_by": "2024-03-15",
  "key_entities": ["Sarah Johnson", "DataCorp", "£45,000"]
}}"""
