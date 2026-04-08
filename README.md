# Fusion Finance Voice Intelligence POC (Plivo)

Pre-collection call system using Plivo for outbound calls.

## Setup

### Environment Variables
```
PLIVO_AUTH_ID=MAOWI5YJMWNZQTYJQ0OS
PLIVO_AUTH_TOKEN=<from Plivo dashboard>
PLIVO_PHONE_NUMBER=+918031320387
AUDIO_BASE_URL=https://voice-poc-production.up.railway.app
```

### Deploy to Railway
1. Create new Railway app
2. Connect this GitHub repo
3. Add environment variables above
4. Deploy

### Call Flow
```
POST /api/call (phone, language)
  → Plivo outbound call with answer_url
  → Customer answers → POST /plivo/answer
  → Returns XML: <GetDigits><Play>01_greeting.wav</Play></GetDigits>
  → Press 1 → POST /plivo/gather → <Play>02_confirmed.wav</Play> → Hangup
  → Press 2 → POST /plivo/gather → <Play>03_ask_reason.wav</Play> + GetDigits
  → Press 1-6 → POST /plivo/reason → <Play>04_reschedule_confirm.wav</Play> → Hangup
```

### Languages Supported
- Hindi (hi-IN)
- Telugu (te-IN)
- Tamil (ta-IN)
- Kannada (kn-IN)
- Marathi (mr-IN)
- English (en-IN)

### Audio Files
Audio files are served from the existing Exotel app via `AUDIO_BASE_URL`.
