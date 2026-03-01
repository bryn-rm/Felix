# FELIX — AI Email & Calendar Chief of Staff
## A Private Fyxer Clone + Voice Layer + Deep Personalisation
### Stack: Next.js · FastAPI · Claude API · Google APIs · ElevenLabs · Supabase · Cloud Run
### Multi-user: invite-only, Google sign-in, fully siloed per user

---

## 1. WHAT FYXER DOES (AND WHAT WE'LL DO BETTER)

Fyxer's core loop is:
1. Watch your inbox → categorise emails → draft replies in your voice
2. Join meetings → transcribe → generate summaries + follow-ups
3. Suggest calendar slots when scheduling

**What Fyxer doesn't do that we will:**
- Voice-first interface (speak to reply, compose, schedule)
- Morning spoken briefing (your day + inbox priorities, read aloud)
- Relationship intelligence (deep context on every contact)
- Proactive follow-up tracking with voice alerts
- VIP contact system with custom rules per person
- Sentiment analysis on incoming email (detect stress/urgency in sender tone)
- Smart email digest (choose when you want to be interrupted)
- Personal context cards (full history with a person: emails + meetings + notes)
- Auto-agenda builder for meetings based on email threads
- Writing style evolution tracking (how your tone changes over time, per contact type)

This is a **private, invite-only** app — you control who gets access. You and your partner (or any other trusted person) each sign in with your own Google account. Felix connects to each person's Gmail and Calendar independently, keeps all data completely siloed, and builds a separate style profile and relationship graph per user. There's no shared inbox, no shared data — just a shared deployment. Invite someone by adding their Google account as a Test User in the GCP OAuth consent screen. You never need to publish to the Google app marketplace.

---

## 2. ARCHITECTURE OVERVIEW

**Multi-user model:** Supabase Auth is the source of truth for user identity. Every request to the backend carries a Supabase JWT. The backend validates the JWT, extracts `user_id`, loads that user's Google credentials from the database, and scopes all queries to that user. Users are completely isolated at the database level via Row Level Security (RLS). The background job scheduler iterates over all active users rather than being hardcoded to one.

```
┌─────────────────────────────────────────────────────────────────┐
│                         FRONTEND                                 │
│              Next.js 14 (App Router, TypeScript)                 │
│   Dashboard │ Inbox │ Calendar │ Contacts │ Voice Interface      │
└──────────────────────────┬──────────────────────────────────────┘
                           │ HTTPS / WebSocket
┌──────────────────────────▼──────────────────────────────────────┐
│                      BACKEND API                                  │
│               FastAPI (Python) — Cloud Run                        │
│                                                                   │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────────────┐   │
│  │  Email      │  │  Calendar    │  │  Voice Gateway        │   │
│  │  Service    │  │  Service     │  │  (STT + TTS pipeline) │   │
│  └──────┬──────┘  └──────┬───────┘  └──────────┬────────────┘   │
│         │                │                      │                │
│  ┌──────▼──────────────────▼──────────────────────▼────────────┐ │
│  │                    AI LAYER (Claude API)                     │ │
│  │  Triage │ Draft │ Summarise │ Style │ Sentiment │ Briefing  │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                                                                   │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │              BACKGROUND JOBS (APScheduler)                  │ │
│  │  Inbox Sync │ Follow-up Checker │ Nightly Digest │ Briefing │ │
│  └─────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
              │                        │
┌─────────────▼──────┐    ┌────────────▼─────────────┐
│   Google APIs      │    │      Supabase             │
│  Gmail + Calendar  │    │  PostgreSQL + Auth        │
│  OAuth 2.0         │    │  Realtime + Storage       │
└────────────────────┘    └──────────────────────────┘
              │
┌─────────────▼──────────────────┐
│      External AI Services      │
│  Anthropic Claude API          │
│  ElevenLabs TTS                │
│  Google Cloud Speech-to-Text   │
└────────────────────────────────┘
```

---

## 3. CORE FEATURES — PARITY WITH FYXER

### 3A. Smart Inbox Triage
Every email that arrives gets automatically categorised into:

| Category | Description | Action |
|---|---|---|
| `action_required` | Needs a reply or decision | Badge count, auto-draft queued |
| `fyi` | Informational, no reply needed | Marked read after 24h |
| `waiting_on` | You sent this, awaiting reply | Follow-up tracker |
| `newsletter` | Subscriptions, digests | Weekly digest only |
| `automated` | Order confirmations, receipts | Archived silently |
| `vip` | From your VIP contacts list | Instant voice alert |

Claude prompt for triage:
```python
TRIAGE_PROMPT = """
You are triaging email for {user_name}. Given this email, classify it into exactly one category:
action_required | fyi | waiting_on | newsletter | automated | vip

Also extract:
- urgency: low | medium | high | critical
- topic: single phrase (e.g. "Q3 budget proposal", "meeting rescheduled")
- sentiment_of_sender: neutral | positive | stressed | frustrated | urgent
- requires_response_by: ISO date if deadline implied, null otherwise
- key_entities: people, companies, dates, amounts mentioned

Return JSON only.

VIP contacts: {vip_list}
Email: 
From: {sender}
Subject: {subject}
Body: {body}
"""
```

### 3B. AI Draft Replies — In Your Voice
The first time you use Felix, it analyses your last 200 sent emails to build a style profile:

```python
@dataclass
class StyleProfile:
    avg_words_per_email: float
    formality_score: float        # 0.0 (very casual) → 1.0 (very formal)
    greeting_patterns: list[str]  # ["Hey", "Hi", "Good morning"]
    sign_off_patterns: list[str]  # ["Cheers", "Thanks", "Best"]
    bullet_point_tendency: float  # 0.0 → 1.0
    emoji_frequency: float
    question_tendency: float      # do you ask questions? how many?
    hedging_language: list[str]   # ["I think", "perhaps", "might"]
    directness_score: float
    avg_response_time_hours: float
    per_contact_adjustments: dict  # {contact_email: style_override}
```

Draft prompt:
```python
DRAFT_PROMPT = """
You are ghostwriting an email reply for {user_name}.

THEIR WRITING STYLE:
- Formality: {formality} (0=casual, 1=formal)
- Average length: {avg_words} words
- Typical greeting: {greeting}
- Typical sign-off: {sign_off}
- Uses bullet points: {bullet_tendency}
- Characteristic phrases: {phrases}

CONTEXT:
- Relationship with sender: {relationship_context}
- Previous emails with this person (last 3): {thread_history}
- Any relevant meetings with this person: {meeting_context}
- User's calendar: {calendar_context}

EMAIL TO REPLY TO:
{email_content}

INSTRUCTION: {user_intent}

Write a complete draft reply. Match their voice exactly. Do not add any meta-commentary.
"""
```

### 3C. Meeting Notes + Follow-ups
Since this is personal (not enterprise), meeting recording works differently from Fyxer:
- **Google Meet**: Use the Meet API's closed captions + recording if available
- **Fallback**: Browser extension that captures tab audio
- **In-person**: Voice Gateway — user says "Felix, start meeting notes" → records via microphone

After each meeting:
1. Transcription → Google Cloud Speech-to-Text
2. Claude generates: summary, action items (owner + deadline), decisions made, open questions
3. Auto-draft follow-up email to attendees (requires user approval before send)
4. Auto-create calendar follow-up events for action items

### 3D. Calendar Intelligence
- Suggest available times when someone asks to meet ("What times work?")
- Detect scheduling conflicts before they become problems
- Flag when you have no buffer between meetings
- Identify "dead zone" time (tiny gaps too short to be useful)

---

## 4. BEYOND FYXER — EXCLUSIVE PERSONALISED FEATURES

### 4A. 🎙️ VOICE-FIRST INTERFACE
The biggest differentiator. Everything Fyxer makes you click, Felix lets you speak.

**Voice commands:**
```
"Felix, read me my priority emails"
"Felix, reply to John saying I'll be there at 3pm"
"Felix, schedule a 30-minute call with Sarah next week"
"Felix, what do I have tomorrow?"
"Felix, who's waiting on me?"
"Felix, draft an email to the team about the Q3 deadline"
"Felix, start meeting notes"
"Felix, follow up with Mike about the contract"
"Felix, summarise my inbox from today"
```

Voice is always-available via:
- Floating mic button in web app
- Keyboard shortcut (Cmd+Shift+F)
- Wake word detection (optional, browser-based): "Hey Felix"

**Morning Briefing** (auto-triggers at your configured wake time):
```
"Good morning. Here's your day. You have 3 priority emails waiting —
the most important is from your accountant about the tax deadline,
and it's marked urgent. You have 4 meetings today, back to back from
2 to 5pm — I've protected your morning for focus work. One thing to
know: you sent a proposal to DataCorp 5 days ago and haven't heard
back — want me to draft a follow-up? Finally, your HRV last night
was strong, so this looks like a good day for creative work.
What would you like to tackle first?"
```

ElevenLabs voice config:
```python
FELIX_VOICE_CONFIG = {
    "voice_id": os.getenv("FELIX_VOICE_ID"),  # custom clone
    "model_id": "eleven_turbo_v2_5",
    "voice_settings": {
        "stability": 0.60,
        "similarity_boost": 0.80,
        "style": 0.15,
        "use_speaker_boost": True
    }
}
```

---

### 4B. 🤝 RELATIONSHIP INTELLIGENCE ENGINE
Every contact gets a living profile that Felix maintains automatically.

```python
@dataclass
class ContactProfile:
    email: str
    name: str
    company: str
    role: str
    
    # Relationship signals
    relationship_strength: float    # 0.0 → 1.0 (computed weekly)
    total_emails_exchanged: int
    avg_response_time_yours: float  # how fast you reply to them
    avg_response_time_theirs: float # how fast they reply to you
    last_contacted: datetime
    contact_frequency: str          # 'daily' | 'weekly' | 'monthly' | 'sporadic'
    
    # Meeting history
    meetings_together: int
    last_meeting: datetime
    meeting_notes_summary: str      # AI summary of all meetings with them
    
    # Context
    topics_discussed: list[str]     # ["project alpha", "budget", "Q3 review"]
    their_communication_style: str  # "formal", "casual", "terse", "verbose"
    your_tone_with_them: str        # extracted from sent emails
    
    # Personal details (extracted from emails)
    known_facts: dict               # {"their_assistant": "Jane", "timezone": "EST"}
    open_commitments: list[str]     # things you've promised them
    their_open_commitments: list[str]  # things they've promised you
    
    # Custom
    vip: bool
    vip_rules: str                  # "always alert immediately, respond within 2h"
    personal_notes: str             # your own notes
    tags: list[str]                 # ["client", "investor", "advisor"]
```

**Contact card in UI:** When you open any email, a side panel shows the full relationship card for that sender — last meeting notes, open commitments, conversation history, their typical tone, your typical tone with them.

**Relationship health alerts:**
- "You haven't contacted Sarah in 3 weeks — you usually email monthly. Want to reach out?"
- "You have 3 open commitments to DataCorp that haven't been addressed"
- "Tom's last 4 emails have had an increasingly stressed tone"

---

### 4C. 📬 PROACTIVE FOLLOW-UP ENGINE
Fyxer tracks follow-ups but only tells you. Felix manages them.

**How it works:**
1. Every email you send is scanned: does it contain a request, proposal, or question?
2. If yes → creates a follow-up record with auto-calculated deadline (based on context)
3. Monitors for reply. If no reply by deadline → voice alert + auto-drafted follow-up
4. You approve/edit → sends

```python
@dataclass
class FollowUp:
    id: str
    email_id: str                   # the email you sent
    recipient: str
    topic: str                      # "DataCorp proposal", "Invoice payment"
    sent_at: datetime
    follow_up_by: datetime          # AI-calculated deadline
    status: str                     # 'waiting' | 'replied' | 'followed_up' | 'closed'
    urgency: str
    auto_draft: str                 # pre-written follow-up ready to go
    reminder_count: int
```

**Voice interaction:**
```
Felix: "Quick heads up — DataCorp hasn't replied to your proposal from Tuesday.
        That's 5 days with no response. Want me to send the follow-up I drafted?"
You:   "Yes, send it"
Felix: "Done. I've sent the follow-up and will alert you if there's still no reply by Friday."
```

---

### 4D. 📊 EMAIL SENTIMENT MONITOR
Felix tracks the emotional temperature of your inbox and important relationships.

For every email received, Claude extracts:
- Sender sentiment: neutral / positive / satisfied / concerned / frustrated / stressed / angry
- Email urgency: implied deadline or pressure signals
- Relationship trajectory: is this relationship getting warmer or cooler over time?

**UI:** A subtle "temperature" indicator on contact cards and email threads. No dashboard noise — just signals when something needs attention.

**Alert example:**
```
"Heads up — your last 3 emails from the DataCorp team have had an increasingly
frustrated tone. Their last email used the phrase 'still waiting' twice.
Want me to draft a priority response?"
```

---

### 4E. 🗓️ SMART CALENDAR AUTOPILOT
Beyond simple scheduling suggestions.

**Auto-agenda builder:**
When you have a meeting with a person or group, Felix scans:
- Your last 3 email threads with them
- Your last meeting notes with them
- Any open action items from previous meetings
→ Generates a meeting agenda and optionally sends it to attendees

**Energy-aware scheduling:**
You define your energy profile (morning = deep work, afternoons = meetings, etc.). Felix refuses to book meetings in protected deep work time and groups meetings together to preserve focus blocks.

**Travel buffer intelligence:**
If you have an in-person meeting location, Felix adds realistic travel time as a buffer event.

**Recurring review:**
Every Friday afternoon, Felix sends a spoken/text summary:
- "This week: 12 meetings, 47 emails, 3 items still waiting on replies. Your busiest contact was..."
- Next week preview with suggestions for what to reschedule

---

### 4F. 📝 PERSONAL CONTEXT INJECTION
When drafting any email or reply, Felix automatically injects relevant context from your memory:

- Last time you spoke to this person (and what about)
- Any promises you made them in previous emails
- Relevant meetings you had with them
- Facts you know about them (extracted from past emails)
- Their current timezone and usual working hours

This means draft replies that say things like:
*"Following up from our call on the 14th where you mentioned the budget approval was coming..."*
...automatically, without you having to remind Felix.

---

### 4G. 📱 DIGEST MODE
Instead of being interrupted by emails all day, configure Felix to batch your inbox into digests:

- **Morning digest** (8am): Priority emails only, spoken briefing or visual card
- **Midday digest** (12pm): Action items and time-sensitive replies
- **Evening digest** (6pm): What came in, what's waiting, tomorrow preview

Between digests, only VIP emails trigger alerts. Everything else waits in the queue.

---

### 4H. ✍️ WRITING STYLE EVOLUTION
Felix tracks how your writing evolves over time:
- Monthly style report: "Your emails are getting 15% shorter. You're using more direct language."
- Per-relationship style: "You tend to be more formal with investors than clients"
- Flags style drift: "Your recent emails to the DataCorp team are much more terse than usual — intentional?"

---

### 4I. 🏷️ SMART TEMPLATE LIBRARY
Felix learns your most-common email types and auto-creates reusable templates:
- Detected: you send a very similar "meeting follow-up" email after every client call → saved as template
- Detected: you frequently write "just checking in" emails → template + schedule for auto-send

You can also manually create templates with voice:
```
"Felix, save this as a template called 'proposal follow-up'"
```

---

### 4J. 🔔 VIP CONTACT SYSTEM
Define up to 20 VIP contacts with custom rules per person:
```json
{
  "contact": "sarah@importantclient.com",
  "vip": true,
  "rules": {
    "alert_immediately": true,
    "target_response_time_hours": 2,
    "custom_voice_alert": "Sarah has emailed — this is high priority",
    "always_suggest_meeting": true,
    "draft_style": "extra_professional"
  }
}
```

---

## 5. TECHNICAL SPECIFICATION

### 5A. Project Structure

```
felix/
├── README.md
├── docker-compose.yml
├── .env.example
│
├── frontend/                         # Next.js 14, TypeScript
│   ├── src/
│   │   ├── app/
│   │   │   ├── page.tsx              # Landing / onboarding
│   │   │   ├── dashboard/page.tsx    # Main dashboard
│   │   │   ├── inbox/
│   │   │   │   ├── page.tsx          # Inbox triage view
│   │   │   │   └── [emailId]/page.tsx# Email detail + contact card
│   │   │   ├── calendar/page.tsx     # Calendar view
│   │   │   ├── contacts/
│   │   │   │   ├── page.tsx          # Contact directory
│   │   │   │   └── [email]/page.tsx  # Individual contact profile
│   │   │   ├── follow-ups/page.tsx   # Follow-up tracker
│   │   │   ├── briefing/page.tsx     # Daily briefing replay
│   │   │   └── settings/page.tsx     # Config + integrations
│   │   ├── components/
│   │   │   ├── felix/
│   │   │   │   ├── VoiceOrb.tsx      # Animated voice input button
│   │   │   │   ├── VoiceModal.tsx    # Full-screen voice interaction
│   │   │   │   └── TranscriptDisplay.tsx
│   │   │   ├── inbox/
│   │   │   │   ├── EmailList.tsx
│   │   │   │   ├── EmailCard.tsx     # With category badge + sentiment dot
│   │   │   │   ├── DraftPanel.tsx    # Draft review + edit + send
│   │   │   │   └── ContactSidebar.tsx# Relationship card
│   │   │   ├── calendar/
│   │   │   │   ├── WeekView.tsx
│   │   │   │   ├── MeetingCard.tsx   # With agenda + notes link
│   │   │   │   └── AgendaBuilder.tsx
│   │   │   ├── dashboard/
│   │   │   │   ├── DailyBriefingCard.tsx
│   │   │   │   ├── FollowUpWidget.tsx
│   │   │   │   ├── PriorityInbox.tsx
│   │   │   │   └── RelationshipAlerts.tsx
│   │   │   └── ui/                  # shadcn/ui components
│   │   ├── hooks/
│   │   │   ├── useVoice.ts           # WebSocket voice pipeline
│   │   │   ├── useFelix.ts           # Main API client hook
│   │   │   ├── useEmailStream.ts     # SSE for streaming drafts
│   │   │   └── useRealtime.ts        # Supabase realtime
│   │   └── lib/
│   │       ├── api.ts                # Typed API client
│   │       ├── supabase.ts
│   │       └── utils.ts
│   └── package.json
│
├── backend/                          # FastAPI, Python
│   ├── app/
│   │   ├── main.py                   # FastAPI app entry
│   │   ├── config.py                 # Settings + env vars
│   │   │
│   │   ├── api/                      # Route handlers
│   │   │   ├── auth.py               # Google OAuth flow
│   │   │   ├── email.py              # Inbox, drafts, send
│   │   │   ├── calendar.py           # Events, scheduling
│   │   │   ├── voice.py              # WebSocket voice endpoint
│   │   │   ├── contacts.py           # Contact profiles
│   │   │   ├── follow_ups.py         # Follow-up management
│   │   │   ├── briefing.py           # Daily briefing generation
│   │   │   └── settings.py           # User preferences
│   │   │
│   │   ├── services/
│   │   │   ├── gmail_service.py      # Gmail API wrapper
│   │   │   ├── calendar_service.py   # Google Calendar API wrapper
│   │   │   ├── ai_service.py         # Claude API calls (all prompts live here)
│   │   │   ├── voice_service.py      # STT (Google) + TTS (ElevenLabs)
│   │   │   ├── style_profiler.py     # Builds + updates your style profile
│   │   │   ├── relationship_engine.py# Contact intelligence
│   │   │   ├── follow_up_engine.py   # Follow-up detection + tracking
│   │   │   ├── sentiment_analyser.py # Email sentiment analysis
│   │   │   └── briefing_service.py   # Morning briefing generator
│   │   │
│   │   ├── jobs/                     # Background scheduled tasks
│   │   │   ├── scheduler.py          # APScheduler setup
│   │   │   ├── inbox_sync.py         # Poll Gmail every 2 min
│   │   │   ├── triage_worker.py      # Process new emails through Claude
│   │   │   ├── follow_up_checker.py  # Check for overdue follow-ups
│   │   │   ├── relationship_updater.py# Refresh contact profiles nightly
│   │   │   └── briefing_generator.py # Generate morning briefing
│   │   │
│   │   ├── models/                   # Pydantic models + SQLAlchemy
│   │   │   ├── email.py
│   │   │   ├── contact.py
│   │   │   ├── follow_up.py
│   │   │   ├── briefing.py
│   │   │   └── user.py
│   │   │
│   │   └── prompts/                  # All Claude system prompts
│   │       ├── triage.py
│   │       ├── draft.py
│   │       ├── style_analysis.py
│   │       ├── meeting_notes.py
│   │       ├── follow_up_detection.py
│   │       ├── sentiment.py
│   │       ├── briefing.py
│   │       └── voice_intent.py
│   │
│   ├── requirements.txt
│   └── Dockerfile
│
└── infra/
    ├── terraform/
    │   ├── main.tf
    │   ├── cloud_run.tf
    │   └── secrets.tf
    └── cloudbuild.yaml
```

---

### 5B. Google OAuth Setup — Multi-User Flow

This is the most important architectural piece. There are **two separate OAuth steps**:
1. **Supabase Auth** — signs the user into Felix (creates their Felix account, issues a JWT)
2. **Google OAuth** — grants Felix access to their Gmail + Calendar (stored per user)

Both happen during onboarding in sequence.

**Step 1: Adding allowed users (you control this)**

Felix is not a public app. In your GCP project, the OAuth consent screen stays in "Testing" mode permanently. You add permitted Google accounts manually:
```
GCP Console → APIs & Services → OAuth consent screen
→ Test Users → Add Users → paste Google email addresses
```
Anyone whose email isn't on this list will see "This app is blocked" when they try to connect. This is your access control — no invite codes, no email allowlisting in code needed.

**Step 2: Supabase Auth (sign in to Felix)**

Use Supabase's built-in Google provider for the Felix login. This is separate from the Google API access — it just authenticates who you are.

```typescript
// frontend/src/lib/supabase.ts
import { createClient } from '@supabase/supabase-js'
export const supabase = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!
)

// Sign in button — uses Supabase Auth Google provider
export async function signInWithGoogle() {
  const { error } = await supabase.auth.signInWithOAuth({
    provider: 'google',
    options: {
      redirectTo: `${window.location.origin}/auth/callback`,
      // Note: different scopes from Gmail — this is just profile/email
    }
  })
}
```

**Step 3: Google API OAuth (connect Gmail + Calendar)**

After signing into Felix, the user connects their Google account for API access. This is a separate OAuth flow with the Gmail/Calendar scopes.

```python
# backend/app/api/auth.py
from google_auth_oauthlib.flow import Flow
from fastapi import Depends
from app.middleware.auth import get_current_user  # validates Supabase JWT

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
    "openid", "email", "profile"
]

@router.get("/auth/google/connect")
async def connect_google(current_user: User = Depends(get_current_user)):
    """Initiate Google API OAuth for the logged-in Felix user."""
    flow = Flow.from_client_config(
        client_config={
            "web": {
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=GOOGLE_SCOPES,
        redirect_uri=settings.GOOGLE_REDIRECT_URI
    )
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        # Embed user_id in state so we know who this is on callback
        state=f"{current_user.id}:{state}"
    )
    return {"auth_url": auth_url}

@router.get("/auth/google/callback")
async def google_callback(code: str, state: str):
    """Handle Google OAuth callback — store tokens against the user."""
    user_id, original_state = state.split(":", 1)
    
    flow = Flow.from_client_config(..., state=original_state)
    flow.fetch_token(code=code)
    credentials = flow.credentials
    
    # Encrypt and store tokens — scoped to this specific user
    await db.upsert("google_connections", {
        "user_id": user_id,
        "access_token": encrypt(credentials.token),
        "refresh_token": encrypt(credentials.refresh_token),
        "token_expiry": credentials.expiry.isoformat(),
        "google_email": get_google_email(credentials),
        "connected_at": datetime.utcnow().isoformat()
    })
    return RedirectResponse("/dashboard")
```

**Step 4: Auth middleware — every request**

```python
# backend/app/middleware/auth.py
from fastapi import HTTPException, Header
from supabase import create_client

supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)

async def get_current_user(authorization: str = Header(...)) -> User:
    """Validate Supabase JWT and return the authenticated user."""
    token = authorization.replace("Bearer ", "")
    try:
        result = supabase.auth.get_user(token)
        if not result.user:
            raise HTTPException(status_code=401, detail="Invalid token")
        return result.user
    except Exception:
        raise HTTPException(status_code=401, detail="Unauthorized")

async def get_google_credentials(user_id: str) -> Credentials:
    """Load and auto-refresh this user's Google credentials."""
    row = await db.query_one(
        "SELECT * FROM google_connections WHERE user_id = $1", user_id
    )
    if not row:
        raise HTTPException(status_code=403, detail="Google account not connected")
    
    creds = Credentials(
        token=decrypt(row["access_token"]),
        refresh_token=decrypt(row["refresh_token"]),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET
    )
    if creds.expired:
        creds.refresh(Request())
        await db.update("google_connections", {
            "user_id": user_id,
            "access_token": encrypt(creds.token),
            "token_expiry": creds.expiry.isoformat()
        })
    return creds

# Every service call pattern:
@router.get("/emails")
async def get_emails(current_user: User = Depends(get_current_user)):
    creds = await get_google_credentials(current_user.id)
    gmail = GmailService(creds)
    emails = await gmail.get_recent_emails()
    # All DB queries automatically scoped to current_user.id via RLS
    return emails
```

**Scopes needed:**
```python
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
    "openid", "email", "profile"
]
```

---

### 5C. Gmail Service

```python
# app/services/gmail_service.py
from googleapiclient.discovery import build
from email.mime.text import MIMEText
import base64

class GmailService:
    def __init__(self, credentials):
        self.service = build("gmail", "v1", credentials=credentials)
    
    async def get_recent_emails(self, max_results=50, query="") -> list[dict]:
        """Fetch recent emails not yet processed."""
        results = self.service.users().messages().list(
            userId="me",
            maxResults=max_results,
            q=query or "in:inbox -label:felix-processed"
        ).execute()
        
        messages = []
        for msg in results.get("messages", []):
            full = self.service.users().messages().get(
                userId="me", id=msg["id"], format="full"
            ).execute()
            messages.append(self._parse_message(full))
        
        return messages
    
    def _parse_message(self, raw: dict) -> dict:
        headers = {h["name"]: h["value"] for h in raw["payload"]["headers"]}
        body = self._extract_body(raw["payload"])
        return {
            "id": raw["id"],
            "thread_id": raw["threadId"],
            "from": headers.get("From", ""),
            "to": headers.get("To", ""),
            "subject": headers.get("Subject", ""),
            "date": headers.get("Date", ""),
            "body": body,
            "snippet": raw.get("snippet", ""),
            "labels": raw.get("labelIds", [])
        }
    
    async def send_email(self, to: str, subject: str, body: str, 
                          thread_id: str | None = None) -> dict:
        """Send email, optionally as a reply in a thread."""
        message = MIMEText(body)
        message["to"] = to
        message["subject"] = subject
        if thread_id:
            message["References"] = thread_id
        
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        result = self.service.users().messages().send(
            userId="me",
            body={"raw": raw, "threadId": thread_id}
        ).execute()
        return result
    
    async def create_label(self, name: str) -> str:
        """Create a Gmail label (e.g., 'felix/action-required')."""
        label = self.service.users().labels().create(
            userId="me",
            body={"name": name}
        ).execute()
        return label["id"]
    
    async def apply_label(self, message_id: str, label_id: str):
        self.service.users().messages().modify(
            userId="me",
            id=message_id,
            body={"addLabelIds": [label_id]}
        ).execute()
    
    async def get_sent_emails(self, max_results=200) -> list[dict]:
        """For style profiling — fetch sent emails."""
        results = self.service.users().messages().list(
            userId="me", maxResults=max_results, q="in:sent"
        ).execute()
        # ... parse and return
    
    async def get_thread(self, thread_id: str) -> list[dict]:
        """Get full thread for context."""
        thread = self.service.users().threads().get(
            userId="me", id=thread_id, format="full"
        ).execute()
        return [self._parse_message(m) for m in thread["messages"]]
```

---

### 5D. Voice Gateway (WebSocket)

```python
# app/api/voice.py
from fastapi import WebSocket
from google.cloud import speech_v2
import asyncio

@router.websocket("/voice/stream")
async def voice_stream(websocket: WebSocket):
    await websocket.accept()
    
    speech_client = speech_v2.SpeechAsyncClient()
    
    async def stream_audio():
        """Receive audio chunks from browser, stream to Google STT."""
        config = speech_v2.StreamingRecognitionConfig(
            config=speech_v2.RecognitionConfig(
                auto_decoding_config=speech_v2.AutoDetectDecodingConfig(),
                language_codes=["en-US"],
                model="long",
                features=speech_v2.RecognitionFeatures(
                    enable_automatic_punctuation=True,
                    enable_spoken_punctuation=True,
                )
            ),
            streaming_features=speech_v2.StreamingRecognitionFeatures(
                interim_results=True
            )
        )
        
        async def audio_generator():
            yield speech_v2.StreamingRecognizeRequest(
                recognizer=f"projects/{PROJECT_ID}/locations/global/recognizers/_",
                streaming_config=config
            )
            while True:
                try:
                    audio_data = await asyncio.wait_for(
                        websocket.receive_bytes(), timeout=30.0
                    )
                    yield speech_v2.StreamingRecognizeRequest(audio=audio_data)
                except asyncio.TimeoutError:
                    break
        
        async for response in await speech_client.streaming_recognize(audio_generator()):
            for result in response.results:
                transcript = result.alternatives[0].transcript
                is_final = result.is_final
                
                # Send interim transcript to UI
                await websocket.send_json({
                    "type": "transcript",
                    "text": transcript,
                    "final": is_final
                })
                
                if is_final:
                    # Process intent and generate Felix response
                    await handle_voice_command(websocket, transcript)
    
    await stream_audio()


async def handle_voice_command(websocket: WebSocket, transcript: str):
    """Route voice command to appropriate action."""
    
    # 1. Parse intent with Claude
    intent = await ai_service.parse_voice_intent(transcript)
    
    # 2. Execute action
    response_text = await route_intent(intent)
    
    # 3. Stream text response back to UI
    await websocket.send_json({"type": "response_text", "text": response_text})
    
    # 4. Convert to speech with ElevenLabs (stream audio)
    async for audio_chunk in tts_service.stream(response_text):
        await websocket.send_bytes(audio_chunk)
    
    await websocket.send_json({"type": "audio_complete"})
```

---

### 5E. AI Service (All Claude Calls)

```python
# app/services/ai_service.py
from anthropic import AsyncAnthropic

client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

class AIService:
    
    async def triage_email(self, email: dict, vip_list: list, style_profile: dict) -> dict:
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=500,
            messages=[{
                "role": "user",
                "content": TRIAGE_PROMPT.format(
                    email_from=email["from"],
                    email_subject=email["subject"],
                    email_body=email["body"][:3000],  # truncate
                    vip_list=", ".join(vip_list)
                )
            }]
        )
        return json.loads(response.content[0].text)
    
    async def draft_reply(self, email: dict, thread_history: list, 
                           contact: dict, style_profile: dict,
                           user_intent: str = "") -> AsyncIterator[str]:
        """Stream a draft reply."""
        async with client.messages.stream(
            model="claude-sonnet-4-6",
            max_tokens=1000,
            messages=[{
                "role": "user",
                "content": DRAFT_PROMPT.format(
                    style_profile=json.dumps(style_profile),
                    thread_history=format_thread(thread_history),
                    contact_context=json.dumps(contact),
                    email=format_email(email),
                    user_intent=user_intent or "Reply appropriately"
                )
            }]
        ) as stream:
            async for text in stream.text_stream:
                yield text
    
    async def analyse_writing_style(self, sent_emails: list[dict]) -> dict:
        """Build style profile from sent email history."""
        # Sample 50 representative emails to save tokens
        sample = sent_emails[:50]
        email_text = "\n\n---\n\n".join([
            f"To: {e['to']}\nSubject: {e['subject']}\n{e['body'][:500]}"
            for e in sample
        ])
        
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1000,
            messages=[{
                "role": "user",
                "content": STYLE_ANALYSIS_PROMPT.format(emails=email_text)
            }]
        )
        return json.loads(response.content[0].text)
    
    async def generate_meeting_notes(self, transcript: str, 
                                      attendees: list, title: str) -> dict:
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2000,
            messages=[{
                "role": "user",
                "content": MEETING_NOTES_PROMPT.format(
                    meeting_title=title,
                    attendees=", ".join(attendees),
                    transcript=transcript
                )
            }]
        )
        return json.loads(response.content[0].text)
    
    async def generate_daily_briefing(self, context: dict) -> str:
        """Generate the spoken morning briefing text."""
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=400,
            messages=[{
                "role": "user",
                "content": BRIEFING_PROMPT.format(**context)
            }]
        )
        return response.content[0].text
    
    async def parse_voice_intent(self, transcript: str) -> dict:
        """Classify voice command into structured intent."""
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",  # fast + cheap for routing
            max_tokens=200,
            messages=[{
                "role": "user", 
                "content": VOICE_INTENT_PROMPT.format(transcript=transcript)
            }]
        )
        return json.loads(response.content[0].text)
    
    async def detect_follow_ups(self, sent_email: dict) -> dict | None:
        """Detect if a sent email requires a follow-up."""
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{
                "role": "user",
                "content": FOLLOW_UP_DETECTION_PROMPT.format(
                    to=sent_email["to"],
                    subject=sent_email["subject"],
                    body=sent_email["body"]
                )
            }]
        )
        result = json.loads(response.content[0].text)
        return result if result.get("needs_follow_up") else None
```

---

### 5F. Background Jobs — Multi-User Aware

Every scheduled job now iterates over all active users rather than one hardcoded account.

```python
# app/jobs/scheduler.py
from apscheduler.schedulers.asyncio import AsyncIOScheduler

scheduler = AsyncIOScheduler()

async def get_active_users() -> list[dict]:
    """Return all users who have a connected Google account."""
    return await db.query(
        "SELECT user_id, timezone, briefing_time FROM settings s "
        "JOIN google_connections g USING (user_id)"
    )

# Poll inbox every 2 minutes — for ALL users
@scheduler.scheduled_job("interval", minutes=2)
async def sync_all_inboxes():
    users = await get_active_users()
    # Run all users in parallel — they're completely independent
    await asyncio.gather(*[sync_user_inbox(u["user_id"]) for u in users])

async def sync_user_inbox(user_id: str):
    """Process new emails for one specific user."""
    creds = await get_google_credentials(user_id)
    gmail = GmailService(creds)
    settings = await db.query_one("SELECT * FROM settings WHERE user_id = $1", user_id)
    
    new_emails = await gmail.get_recent_emails(q="is:unread -label:felix-processed")
    for email in new_emails:
        triage = await ai_service.triage_email(
            email,
            vip_list=settings["vip_contacts"],
            style_profile=settings["style_profile"]
        )
        
        # All DB inserts include user_id — RLS enforces isolation
        await db.upsert("emails", {**email, "user_id": user_id, "triage": triage})
        
        label_id = await get_or_create_felix_label(gmail, triage["category"], user_id)
        await gmail.apply_label(email["id"], label_id)
        
        if triage["category"] == "action_required":
            draft = ""
            async for chunk in ai_service.draft_reply(email, user_id=user_id):
                draft += chunk
            await db.insert("drafts", {
                "email_id": email["id"],
                "user_id": user_id,
                "draft": draft
            })
        
        if triage["category"] == "vip":
            await notify_user(user_id, f"VIP email from {email['from_name']}")

# Check follow-ups hourly — all users
@scheduler.scheduled_job("interval", hours=1)
async def check_all_follow_ups():
    users = await get_active_users()
    await asyncio.gather(*[check_user_follow_ups(u["user_id"]) for u in users])

async def check_user_follow_ups(user_id: str):
    overdue = await db.query(
        "SELECT * FROM follow_ups WHERE user_id = $1 "
        "AND follow_up_by < NOW() AND status = 'waiting'",
        user_id
    )
    for fu in overdue:
        await notify_user(user_id, f"Follow-up overdue: {fu['topic']}")

# Morning briefing — per user, respects their timezone + configured time
@scheduler.scheduled_job("interval", minutes=5)
async def check_morning_briefings():
    """Check every 5 min if any user's briefing time has arrived."""
    users = await get_active_users()
    for user in users:
        user_now = datetime.now(pytz.timezone(user["timezone"]))
        briefing_time = user["briefing_time"]  # e.g. "07:30"
        
        if (user_now.strftime("%H:%M") == briefing_time
                and not await briefing_generated_today(user["user_id"])):
            asyncio.create_task(generate_briefing_for_user(user["user_id"]))

async def generate_briefing_for_user(user_id: str):
    context = await gather_briefing_context(user_id)
    text = await ai_service.generate_daily_briefing(context)
    audio_url = await tts_service.generate_and_store(text, user_id)
    await db.insert("briefings", {
        "user_id": user_id,
        "text": text,
        "audio_url": audio_url,
        "date": date.today().isoformat()
    })
    await notify_user(user_id, "Your morning briefing is ready")

# Nightly relationship refresh — all users
@scheduler.scheduled_job("cron", hour=23, minute=0)
async def refresh_all_relationships():
    users = await get_active_users()
    await asyncio.gather(*[
        relationship_engine.refresh_user(u["user_id"]) for u in users
    ])

# Weekly style re-analysis — Sunday nights
@scheduler.scheduled_job("cron", day_of_week="sun", hour=22)
async def refresh_all_style_profiles():
    users = await get_active_users()
    for user in users:
        creds = await get_google_credentials(user["user_id"])
        gmail = GmailService(creds)
        sent = await gmail.get_sent_emails(max_results=100)
        profile = await ai_service.analyse_writing_style(sent)
        await db.update("settings", {
            "user_id": user["user_id"],
            "style_profile": profile
        })
```

---

### 5G. Database Schema (Supabase PostgreSQL) — Multi-User with RLS

Every table has a `user_id` column that references `auth.users`. Row Level Security policies ensure users can only ever read and write their own rows — even if the backend accidentally passes the wrong `user_id`, Supabase blocks it at the database level.

```sql
-- ============================================
-- ENABLE ROW LEVEL SECURITY ON ALL TABLES
-- ============================================

-- Helper: who is the current user?
-- (Supabase sets this from the JWT automatically)

-- ============================================
-- GOOGLE CONNECTIONS (one row per user)
-- ============================================
CREATE TABLE google_connections (
    user_id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    google_email TEXT NOT NULL,
    access_token TEXT NOT NULL,          -- encrypted with pgcrypto
    refresh_token TEXT NOT NULL,         -- encrypted with pgcrypto
    token_expiry TIMESTAMPTZ,
    connected_at TIMESTAMPTZ DEFAULT NOW(),
    last_sync TIMESTAMPTZ
);
ALTER TABLE google_connections ENABLE ROW LEVEL SECURITY;
CREATE POLICY "users manage own google connection"
    ON google_connections FOR ALL
    USING (user_id = auth.uid());

-- ============================================
-- USER SETTINGS (one row per user)
-- ============================================
CREATE TABLE settings (
    user_id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    display_name TEXT,
    timezone TEXT DEFAULT 'Europe/London',
    briefing_time TIME DEFAULT '07:30',
    style_profile JSONB,
    vip_contacts TEXT[],
    digest_mode BOOLEAN DEFAULT FALSE,
    digest_times TEXT[],
    energy_profile JSONB,
    felix_voice_id TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE settings ENABLE ROW LEVEL SECURITY;
CREATE POLICY "users manage own settings"
    ON settings FOR ALL
    USING (user_id = auth.uid());

-- ============================================
-- EMAILS
-- ============================================
CREATE TABLE emails (
    id TEXT NOT NULL,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    thread_id TEXT,
    from_email TEXT,
    from_name TEXT,
    to_email TEXT,
    subject TEXT,
    body TEXT,
    snippet TEXT,
    received_at TIMESTAMPTZ,
    category TEXT,
    urgency TEXT,
    sentiment TEXT,
    topic TEXT,
    triage_json JSONB,
    processed_at TIMESTAMPTZ,
    draft_generated BOOLEAN DEFAULT FALSE,
    PRIMARY KEY (id, user_id)
);
ALTER TABLE emails ENABLE ROW LEVEL SECURITY;
CREATE POLICY "users see own emails"
    ON emails FOR ALL
    USING (user_id = auth.uid());
CREATE INDEX idx_emails_user_received ON emails(user_id, received_at DESC);
CREATE INDEX idx_emails_user_category ON emails(user_id, category);

-- ============================================
-- DRAFTS
-- ============================================
CREATE TABLE drafts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    email_id TEXT,
    draft_text TEXT,
    status TEXT DEFAULT 'pending',       -- 'pending' | 'approved' | 'sent' | 'discarded'
    edited_text TEXT,
    generated_at TIMESTAMPTZ DEFAULT NOW(),
    sent_at TIMESTAMPTZ
);
ALTER TABLE drafts ENABLE ROW LEVEL SECURITY;
CREATE POLICY "users manage own drafts"
    ON drafts FOR ALL
    USING (user_id = auth.uid());

-- ============================================
-- FOLLOW-UPS
-- ============================================
CREATE TABLE follow_ups (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    email_id TEXT,
    to_email TEXT,
    subject TEXT,
    topic TEXT,
    sent_at TIMESTAMPTZ,
    follow_up_by TIMESTAMPTZ,
    status TEXT DEFAULT 'waiting',       -- 'waiting' | 'replied' | 'followed_up' | 'closed'
    urgency TEXT,
    auto_draft TEXT,
    reminder_count INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE follow_ups ENABLE ROW LEVEL SECURITY;
CREATE POLICY "users manage own follow ups"
    ON follow_ups FOR ALL
    USING (user_id = auth.uid());

-- ============================================
-- CONTACTS (relationship intelligence)
-- ============================================
CREATE TABLE contacts (
    email TEXT NOT NULL,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    name TEXT,
    company TEXT,
    role TEXT,
    vip BOOLEAN DEFAULT FALSE,
    vip_rules JSONB,
    relationship_strength FLOAT,
    total_emails INT DEFAULT 0,
    last_contacted TIMESTAMPTZ,
    meeting_count INT DEFAULT 0,
    last_meeting TIMESTAMPTZ,
    topics_discussed TEXT[],
    open_commitments TEXT[],
    their_open_commitments TEXT[],
    sentiment_trend TEXT,
    known_facts JSONB,
    personal_notes TEXT,
    tags TEXT[],
    style_profile JSONB,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (email, user_id)         -- same contact email, different users = different rows
);
ALTER TABLE contacts ENABLE ROW LEVEL SECURITY;
CREATE POLICY "users manage own contacts"
    ON contacts FOR ALL
    USING (user_id = auth.uid());

-- ============================================
-- MEETINGS
-- ============================================
CREATE TABLE meetings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    calendar_event_id TEXT,
    title TEXT,
    date TIMESTAMPTZ,
    duration_minutes INT,
    attendees TEXT[],
    transcript TEXT,
    summary TEXT,
    action_items JSONB,
    decisions JSONB,
    open_questions JSONB,
    follow_up_email_id TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE meetings ENABLE ROW LEVEL SECURITY;
CREATE POLICY "users manage own meetings"
    ON meetings FOR ALL
    USING (user_id = auth.uid());

-- ============================================
-- DAILY BRIEFINGS
-- ============================================
CREATE TABLE briefings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    date DATE NOT NULL,
    text TEXT,
    audio_url TEXT,
    priority_emails JSONB,
    calendar_summary JSONB,
    follow_ups_summary JSONB,
    generated_at TIMESTAMPTZ DEFAULT NOW(),
    listened_at TIMESTAMPTZ,
    UNIQUE(user_id, date)
);
ALTER TABLE briefings ENABLE ROW LEVEL SECURITY;
CREATE POLICY "users manage own briefings"
    ON briefings FOR ALL
    USING (user_id = auth.uid());

-- ============================================
-- VOICE SESSION LOG
-- ============================================
CREATE TABLE voice_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    transcript TEXT,
    intent JSONB,
    response TEXT,
    action_taken TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE voice_sessions ENABLE ROW LEVEL SECURITY;
CREATE POLICY "users manage own voice sessions"
    ON voice_sessions FOR ALL
    USING (user_id = auth.uid());
```

---

## 6. FRONTEND KEY COMPONENTS

### Main Dashboard
```
┌─────────────────────────────────────────────────────────────┐
│  🌅 Good morning, [Name].                      [🎙 Felix]   │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  📬 PRIORITY INBOX (4)          📅 TODAY                    │
│  ┌────────────────────────────┐  ┌────────────────────────┐ │
│  │ 🔴 Sarah — contract draft  │  │ 09:00 Team standup     │ │
│  │ 🟡 Tom — invoice query     │  │ 11:00 [FOCUS BLOCK]    │ │
│  │ 🟡 DataCorp — follow up ↩  │  │ 14:00 Client call      │ │
│  │ 🟢 Newsletter: Product     │  │ 15:30 1:1 with Mike    │ │
│  └────────────────────────────┘  └────────────────────────┘ │
│                                                             │
│  ⏰ WAITING ON YOU (3)          🔔 RELATIONSHIP ALERTS      │
│  ┌────────────────────────────┐  ┌────────────────────────┐ │
│  │ DataCorp proposal — 5 days │  │ ⚠️ Tom: increasingly   │ │
│  │ Invoice to Client A — 8d   │  │    stressed tone       │ │
│  │ Meeting notes to James     │  │ 💬 Haven't emailed     │ │
│  └────────────────────────────┘  │    Sarah in 3 weeks    │ │
│                                  └────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

### Email Detail View with Contact Sidebar
```
┌────────────────────────────────┬───────────────────────────┐
│ From: sarah@client.com          │  SARAH JOHNSON            │
│ Re: Contract revision needed    │  VP Sales, ClientCo       │
│                                 │  ⭐ VIP Contact           │
│ Hi there,                       │                           │
│ Following up on the contract... │  📊 Relationship: Strong  │
│                                 │  📧 47 emails (3 months)  │
│                                 │  📅 Last meeting: Tue     │
│                                 │                           │
│                                 │  OPEN COMMITMENTS         │
│                                 │  • Send revised terms     │
│ ─────────────────────────────── │  • Intro to legal team    │
│ ✨ AI DRAFT REPLY               │                           │
│ ┌──────────────────────────┐    │  LAST MEETING NOTES       │
│ │ Hi Sarah,                │    │  "Discussed Q2 renewal,   │
│ │ Thanks for following up. │    │   Sarah flagged pricing   │
│ │ I'll send the revised... │    │   sensitivity..."         │
│ │                          │    │                           │
│ │ [Edit] [Send] [Discard]  │    │  [Full profile →]         │
│ └──────────────────────────┘    │                           │
└────────────────────────────────┴───────────────────────────┘
```

---

## 7. DEPLOYMENT — GCP + PERSONAL SETUP

### Option A: Cloud Run (Recommended)
Simple, serverless, scales to zero when you're not using it.

```yaml
# infra/cloudbuild.yaml
steps:
  # Build and deploy backend
  - name: 'gcr.io/cloud-builders/docker'
    args: ['build', '-t', 'gcr.io/$PROJECT_ID/felix-backend', './backend']
  - name: 'gcr.io/cloud-builders/docker'
    args: ['push', 'gcr.io/$PROJECT_ID/felix-backend']
  - name: 'gcr.io/cloud-builders/gcloud'
    args:
      - 'run'
      - 'deploy'
      - 'felix-backend'
      - '--image=gcr.io/$PROJECT_ID/felix-backend'
      - '--region=europe-west2'
      - '--allow-unauthenticated'
      - '--set-secrets=ANTHROPIC_API_KEY=anthropic-key:latest,ELEVENLABS_API_KEY=elevenlabs-key:latest'
      - '--set-env-vars=SUPABASE_URL=$$SUPABASE_URL'
      - '--min-instances=1'
      - '--max-instances=3'
      - '--memory=1Gi'
```

### Google Cloud Console Setup

```bash
# 1. Create project
gcloud projects create felix-personal-$(date +%s) --name="Felix"
gcloud config set project [YOUR_PROJECT_ID]

# 2. Enable APIs
gcloud services enable \
  run.googleapis.com \
  gmail.googleapis.com \
  calendar-json.googleapis.com \
  speech.googleapis.com \
  secretmanager.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com

# 3. Create OAuth 2.0 credentials
# → console.cloud.google.com
# → APIs & Services → OAuth consent screen
# → User Type: External (required, even for personal use)
# → Fill in app name ("Felix"), support email, developer email
# → Scopes: add Gmail + Calendar scopes listed above
# → IMPORTANT — Test Users: add YOUR Google email + your partner's Google email
#   (Anyone not on this list will be blocked. Keep app in "Testing" forever —
#    you do NOT need to publish or go through Google verification.)
# → Credentials → Create OAuth Client ID → Web application
# → Authorized redirect URIs:
#     http://localhost:8000/auth/google/callback  (local dev)
#     https://felix-backend-xxxx.run.app/auth/google/callback  (production)
# → Download JSON → note client_id and client_secret

# 4. Supabase Auth — enable Google provider
# → supabase.com → your project → Authentication → Providers → Google
# → Paste your Google OAuth client_id and client_secret
# → This enables "Sign in with Google" for the Felix login step

# 5. Store secrets in GCP Secret Manager
echo -n "sk-ant-..." | gcloud secrets create anthropic-key --data-file=-
echo -n "your-elevenlabs-key" | gcloud secrets create elevenlabs-key --data-file=-
echo -n "GOCSPX-xxxx" | gcloud secrets create google-client-secret --data-file=-

# 6. Deploy backend
gcloud run deploy felix-backend \
  --source ./backend \
  --region europe-west2 \
  --allow-unauthenticated \
  --min-instances=1

# 7. Deploy frontend to Vercel
cd frontend
npx vercel --prod
# Set in Vercel dashboard:
#   NEXT_PUBLIC_API_URL=https://felix-backend-xxxx.run.app
#   NEXT_PUBLIC_SUPABASE_URL=https://xxxx.supabase.co
#   NEXT_PUBLIC_SUPABASE_ANON_KEY=xxxx
```

### Adding a New User (your partner or anyone else)

```
1. Go to GCP Console → APIs & Services → OAuth consent screen → Test Users
2. Click "Add Users" → enter their Google email address → Save
3. Send them the Felix URL
4. They click "Sign in with Google" → create their Felix account
5. They click "Connect Gmail & Calendar" → approve Google permissions
6. Felix immediately starts syncing their inbox — completely separate from yours
```

That's the entire "invite" process. To revoke access: remove their email from Test Users and delete their row from `auth.users` in Supabase.

---

## 8. ENVIRONMENT VARIABLES

```env
# === BACKEND (.env) ===

# Google OAuth (from GCP Console)
GOOGLE_CLIENT_ID=xxxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-xxxx
GOOGLE_REDIRECT_URI=https://felix-backend.run.app/auth/google/callback

# GCP
GCP_PROJECT_ID=felix-personal-xxxxx
GCP_REGION=europe-west2

# AI
ANTHROPIC_API_KEY=sk-ant-xxxx
ANTHROPIC_MODEL_SMART=claude-sonnet-4-6          # drafts, analysis
ANTHROPIC_MODEL_FAST=claude-haiku-4-5-20251001   # triage, intent routing

# ElevenLabs
ELEVENLABS_API_KEY=xxxx
FELIX_VOICE_ID=xxxx                              # shared voice for all users

# Supabase (service key — full access, backend only)
SUPABASE_URL=https://xxxx.supabase.co
SUPABASE_SERVICE_KEY=xxxx

# Encryption key for storing Google tokens
TOKEN_ENCRYPTION_KEY=xxxx                        # 32-byte key, generate with: openssl rand -hex 32

# App
BACKEND_SECRET_KEY=xxxx
FRONTEND_URL=https://felix.vercel.app            # for CORS

# === FRONTEND (.env.local) ===
NEXT_PUBLIC_API_URL=https://felix-backend.run.app
NEXT_PUBLIC_SUPABASE_URL=https://xxxx.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=xxxx              # anon key only — RLS handles the rest
```

> **Note:** There are no longer any `USER_NAME`, `USER_EMAIL`, or `BRIEFING_TIME` hardcoded env vars. All user-specific config (name, timezone, briefing time, voice preference) lives in the `settings` table, set by each user in the Felix settings page.

---

## 9. IMPLEMENTATION PHASES

### Phase 1 — Auth + Google Connection (Week 1)
- [ ] GCP project setup + APIs enabled
- [ ] OAuth consent screen configured (External, Testing mode, test users added)
- [ ] Supabase project created + Google Auth provider enabled
- [ ] FastAPI backend skeleton + Cloud Run deployment
- [ ] Supabase schema deployed with RLS policies on all tables
- [ ] Auth middleware: JWT validation + `get_current_user` dependency
- [ ] Two-step auth flow: Supabase sign-in → then Google API connect
- [ ] `google_connections` table: store encrypted tokens per user
- [ ] Token refresh logic in `get_google_credentials(user_id)`
- [ ] Basic frontend: sign-in page → connect Google page → dashboard shell
- [ ] Onboarding flow: after Google connect, collect name/timezone/briefing time

### Phase 2 — Inbox Triage + Draft Replies (Week 2)
- [ ] Gmail service: fetch, parse, label emails (credentials loaded per user)
- [ ] Basic triage with Claude — all DB writes include `user_id`
- [ ] Inbox view in frontend (reads via Supabase anon key — RLS scopes automatically)
- [ ] Analyse sent email history → per-user style profile stored in `settings`
- [ ] Draft reply generation (streaming) — scoped to requesting user
- [ ] Draft edit + send from UI
- [ ] Thread history context injection
- [ ] Auto-label system in Gmail (per-user Felix labels)
- [ ] Background `sync_all_inboxes()` job iterating over all connected users

### Phase 3 — Voice Layer (Week 3)
- [ ] WebSocket voice endpoint (FastAPI)
- [ ] Google Cloud Speech-to-Text V2 streaming
- [ ] ElevenLabs TTS integration
- [ ] Voice intent parser (Claude Haiku — fast)
- [ ] Frontend: VoiceOrb component + audio capture
- [ ] Core voice commands: read emails, reply, what's on today

### Phase 4 — Calendar Integration (Week 4)
- [ ] Google Calendar service (read events, create events)
- [ ] Calendar view in frontend
- [ ] Scheduling suggestions in email replies ("How about Tuesday at 3pm?")
- [ ] Focus block protection
- [ ] Morning briefing text generation
- [ ] Briefing audio via ElevenLabs

### Phase 5 — Follow-up Engine (Week 5)
- [ ] Follow-up detection on sent emails
- [ ] Follow-up tracker UI
- [ ] Auto-draft follow-up generation
- [ ] Voice alerts for overdue follow-ups
- [ ] Follow-up approval + send flow

### Phase 6 — Relationship Intelligence (Week 6)
- [ ] Contact profile builder (auto-built from email history)
- [ ] Contact sidebar in email view
- [ ] Relationship health alerts
- [ ] VIP contact system + custom rules
- [ ] Sentiment trend tracking

### Phase 7 — Extra Features + Polish (Week 7-8)
- [ ] Meeting notes (manual transcript paste + voice recording mode)
- [ ] Digest mode (batch notifications)
- [ ] Weekly review email
- [ ] Smart template library
- [ ] Writing style evolution report
- [ ] Settings page (all preferences)
- [ ] Mobile PWA optimisation

---

## 10. ESTIMATED RUNNING COSTS (Monthly, 2 Users)

| Service | Estimate | Notes |
|---|---|---|
| Cloud Run (backend) | $5-10 | min-instances=1, shared across all users |
| Supabase | $0-25 | Free tier covers ~2 users easily |
| Anthropic Claude | $20-50 | ~2000 emails/month total across both users |
| ElevenLabs | $5-11 | Starter plan, ~60 min audio/month across users |
| Google APIs | $0 | Gmail + Calendar APIs free for any volume at personal scale |
| Google Cloud Speech | $2-5 | ~2h voice/month total |
| **Total** | **~$32-100/mo** | Still vs Fyxer Pro at $900+/year for 2 users |

Costs scale very gradually — adding a third or fourth user barely moves the needle given the free tier headroom on most services.

---

## 11. FIRST COMMANDS — START HERE

```bash
# 1. Create project structure
mkdir felix && cd felix
mkdir -p backend/app/{api,services,jobs,models,prompts,middleware} frontend

# 2. Python backend setup
cd backend
python -m venv venv && source venv/bin/activate
pip install fastapi uvicorn anthropic google-api-python-client \
  google-auth-oauthlib google-cloud-speech elevenlabs \
  supabase apscheduler python-dotenv pydantic websockets \
  cryptography pytz python-jose

# 3. Frontend setup
cd ../
npx create-next-app@latest frontend --typescript --tailwind --app --src-dir
cd frontend
npm install @supabase/supabase-js @supabase/auth-helpers-nextjs lucide-react recharts
npx shadcn@latest init

# 4. GCP setup
gcloud auth login
gcloud projects create felix-[YOUR-SUFFIX]
gcloud config set project felix-[YOUR-SUFFIX]
gcloud services enable run.googleapis.com gmail.googleapis.com \
  calendar-json.googleapis.com speech.googleapis.com \
  secretmanager.googleapis.com

# 5. OAuth consent screen + test users
# → console.cloud.google.com → APIs & Services → OAuth consent screen
# → External → fill in → add Gmail/Calendar scopes
# → Test Users → add your email + partner's email
# → Credentials → OAuth Client ID → Web → add redirect URIs → copy client_id + secret

# 6. Supabase project
# → supabase.com → New project
# → Authentication → Providers → Google → paste client_id + client_secret
# → SQL editor → paste and run schema from Section 5G
# → Settings → API → copy URL + anon key + service key

# 7. Store secrets
echo -n "sk-ant-..." | gcloud secrets create anthropic-key --data-file=-
echo -n "your-elevenlabs-key" | gcloud secrets create elevenlabs-key --data-file=-
echo -n "GOCSPX-xxxx" | gcloud secrets create google-client-secret --data-file=-
openssl rand -hex 32 | gcloud secrets create token-encryption-key --data-file=-

# 8. Deploy backend
gcloud run deploy felix-backend \
  --source ./backend --region europe-west2 \
  --allow-unauthenticated --min-instances=1

# 9. Deploy frontend
cd frontend && npx vercel --prod

# Build order within backend:
# 1. app/config.py
# 2. app/middleware/auth.py         ← JWT validation, get_current_user
# 3. app/services/gmail_service.py  ← takes credentials as param, not global
# 4. app/api/auth.py                ← both Supabase callback + Google API connect
# 5. app/services/ai_service.py
# 6. app/api/email.py               ← every route uses Depends(get_current_user)
# 7. app/jobs/scheduler.py          ← get_active_users() + iterate
# 8. app/api/voice.py               ← WebSocket, JWT in query param
```
