# Fusion Voice POC - Plivo

Pre-collection Voice AI for Microfinance. Outbound IVR with dynamic multilingual audio.

## Features

- Outbound calls via Plivo Voice API
- 6 Indian languages: Hindi, Tamil, Telugu, Kannada, Marathi, English
- DTMF input handling
- Dynamic audio playback from URLs
- Intelligence dashboard (mock data)

## Setup

1. Create Plivo account at https://console.plivo.com
2. Buy an Indian phone number
3. Deploy to Railway

## Environment Variables

```
PLIVO_AUTH_ID=your_auth_id
PLIVO_AUTH_TOKEN=your_auth_token
PLIVO_PHONE_NUMBER=+918031320387
BASE_URL=https://your-railway-app.up.railway.app
```

## Call Flow

1. POST `/api/call` with phone number and language
2. Plivo initiates outbound call
3. Customer answers → Play greeting → Gather DTMF
4. Press 1 → Confirmed → Hangup
5. Press 2 → Ask reason → Gather DTMF → Confirm → Hangup

## Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Demo UI |
| `/api/call` | POST | Trigger outbound call |
| `/plivo/answer` | POST | Plivo callback - play greeting |
| `/plivo/gather` | POST | Plivo callback - handle DTMF |
| `/plivo/reason` | POST | Plivo callback - handle reason |
| `/plivo/hangup` | POST | Plivo callback - call ended |
| `/api/intelligence` | GET | Mock analytics data |
| `/health` | GET | Health check |

## Audio Files

Place WAV files (8kHz mono) in `/audio/{lang}/`:
- `01_greeting.wav`
- `02_confirmed.wav`
- `03_ask_reason.wav`
- `04_reschedule_confirm.wav`
- `05_unclear.wav`

## License

Proprietary - Acquink Technologies
