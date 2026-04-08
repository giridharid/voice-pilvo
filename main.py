"""
Fusion Finance Voice Intelligence POC - PLIVO VERSION
Smaartbrand UI Theme - Orange/Blue
Real Plivo calls + Intelligence Dashboard

Run: python main.py
"""

VERSION = "1.1.1"  # 2026-04-08 22:50 IST - All audio via Play WAV

import os
import json
import asyncio
import random
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict
from collections import defaultdict

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException
from fastapi.responses import HTMLResponse, Response, JSONResponse
from pydantic import BaseModel
import uvicorn
import plivo
from plivo import plivoxml

# IST timezone (UTC+5:30)
IST = timezone(timedelta(hours=5, minutes=30))

# ============================================================================
# Configuration
# ============================================================================

def load_config():
    return {
        "PLIVO_AUTH_ID": os.getenv("PLIVO_AUTH_ID", ""),
        "PLIVO_AUTH_TOKEN": os.getenv("PLIVO_AUTH_TOKEN", ""),
        "PLIVO_PHONE_NUMBER": os.getenv("PLIVO_PHONE_NUMBER", ""),
    }

CONFIG = load_config()

def get_base_url():
    railway_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN")
    if railway_domain:
        return f"https://{railway_domain}"
    return os.getenv("BASE_URL", "http://localhost:8000")

def get_audio_base_url():
    # Default to same as app URL - we serve audio files ourselves
    return os.getenv("AUDIO_BASE_URL", get_base_url())

APP_BASE_URL = get_base_url()
AUDIO_BASE_URL = get_audio_base_url()

# ============================================================================
# Call State & Data
# ============================================================================

class CallState:
    GREETING = "greeting"
    WAIT_AVAILABILITY = "wait_availability"
    ASK_REASON = "ask_reason"
    WAIT_REASON = "wait_reason"
    COMPLETED = "completed"

active_calls: Dict[str, dict] = {}
ui_connections = []

DTMF_REASONS = {
    "1": ("Travel / Market", "TRAVEL_MARKET"),
    "2": ("Health Issue", "HEALTH"),
    "3": ("Financial Stress", "FINANCIAL_STRESS"),
    "4": ("Work / Office", "WORK_CONFLICT"),
    "5": ("Family Event", "FAMILY_EVENT"),
    "6": ("Crop / Agriculture", "CROP_AGRICULTURE"),
}

LANGUAGE_NAMES = {
    "hi-IN": ("Hindi", "हिंदी"),
    "te-IN": ("Telugu", "తెలుగు"),
    "ta-IN": ("Tamil", "தமிழ்"),
    "kn-IN": ("Kannada", "ಕನ್ನಡ"),
    "mr-IN": ("Marathi", "मराठी"),
    "en-IN": ("English", "English"),
}

RO_NAMES = {
    "hi-IN": "Amit ji",
    "te-IN": "Srinivas garu",
    "ta-IN": "Suresh sir",
    "kn-IN": "Shetty sir",
    "mr-IN": "Patil saheb",
    "en-IN": "Amit sir",
}

# ============================================================================
# Mock Intelligence Data
# ============================================================================

def generate_mock_intelligence():
    clusters = [
        {"name": "Warangal Rural", "state": "Telangana"},
        {"name": "Shad Nagar", "state": "Telangana"},
        {"name": "Karimnagar", "state": "Telangana"},
        {"name": "Nizamabad", "state": "Telangana"},
        {"name": "Medak", "state": "Telangana"},
    ]
    
    borrowers = []
    for i in range(500):
        cluster = random.choice(clusters)
        is_frequent = random.random() < 0.12
        
        decline_reasons = []
        if is_frequent:
            primary = random.choices(
                ["FINANCIAL_STRESS", "TRAVEL_MARKET", "HEALTH", "CROP_AGRICULTURE"],
                weights=[40, 20, 15, 25]
            )[0]
            for _ in range(random.randint(3, 6)):
                decline_reasons.append(primary)
        
        borrowers.append({
            "id": f"BRW{10000+i}",
            "cluster": cluster["name"],
            "state": cluster["state"],
            "persona": random.choice(["FARMER", "TRADER", "SALARIED", "SELF_EMPLOYED", "DAILY_WAGE"]),
            "decline_count": len(decline_reasons),
            "decline_reasons": decline_reasons,
            "is_frequent": is_frequent,
            "risk_score": min(100, len(decline_reasons) * 15 + (40 if "FINANCIAL_STRESS" in decline_reasons else 0)),
            "loan_amount": random.randint(20000, 80000),
        })
    
    reason_counts = defaultdict(int)
    for b in borrowers:
        for r in b["decline_reasons"]:
            reason_counts[r] += 1
    
    cluster_stats = {}
    for cluster in clusters:
        cb = [b for b in borrowers if b["cluster"] == cluster["name"]]
        avg_risk = sum(b["risk_score"] for b in cb) / len(cb) if cb else 0
        freq = len([b for b in cb if b["is_frequent"]])
        fin_stress = len([b for b in cb if "FINANCIAL_STRESS" in b["decline_reasons"]])
        
        cluster_stats[cluster["name"]] = {
            "state": cluster["state"],
            "total": len(cb),
            "avg_risk": round(avg_risk, 1),
            "frequent": freq,
            "financial_stress": fin_stress,
            "alert": "HIGH" if avg_risk > 40 or fin_stress > 10 else ("MEDIUM" if avg_risk > 25 else "LOW"),
        }
    
    persona_counts = defaultdict(int)
    for b in borrowers:
        persona_counts[b["persona"]] += 1
    
    frequent_decliners = sorted([b for b in borrowers if b["is_frequent"]], key=lambda x: -x["risk_score"])[:10]
    
    return {
        "summary": {
            "total_calls": 1247,
            "connected": 1089,
            "connection_rate": 87.3,
            "confirmed": 734,
            "confirmation_rate": 67.4,
            "declined": 355,
            "borrowers_profiled": 500,
        },
        "decline_reasons": dict(reason_counts),
        "clusters": cluster_stats,
        "personas": dict(persona_counts),
        "frequent_decliners": frequent_decliners,
        "early_warnings": [
            {"cluster": "Warangal Rural", "type": "CLUSTER_STRESS", "message": "23% increase in financial stress declines", "level": "HIGH"},
            {"cluster": "Nashik Rural", "type": "CROP_SIGNAL", "message": "Spike in crop/agriculture reasons", "level": "MEDIUM"},
            {"cluster": "Guntur District", "type": "FREQUENT_DECLINER", "message": "8 new frequent decliners this month", "level": "MEDIUM"},
        ],
        "npa": {
            "current": 2.8,
            "predicted": 3.4,
            "at_risk": "4.2 Cr",
            "savings": "1.8 Cr",
        },
    }

INTELLIGENCE_DATA = generate_mock_intelligence()

# ============================================================================
# Plivo Integration
# ============================================================================

async def make_plivo_call(to_number: str, language: str = "hi-IN") -> dict:
    if not all([CONFIG["PLIVO_AUTH_ID"], CONFIG["PLIVO_AUTH_TOKEN"], CONFIG["PLIVO_PHONE_NUMBER"]]):
        return {"success": False, "error": "Plivo credentials not configured"}
    
    # Clean phone number - add +91 for Indian numbers
    to_number = to_number.replace(" ", "").replace("-", "")
    if not to_number.startswith("+"):
        if to_number.startswith("91"):
            to_number = "+" + to_number
        elif to_number.startswith("0"):
            to_number = "+91" + to_number[1:]
        else:
            to_number = "+91" + to_number
    
    call_id = f"call_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{to_number[-4:]}"
    
    active_calls[call_id] = {
        "to_number": to_number,
        "language": language,
        "state": CallState.GREETING,
        "decline_reason": None,
        "transcript": [],
        "started_at": datetime.now().isoformat(),
    }
    
    answer_url = f"{APP_BASE_URL}/plivo/answer?call_id={call_id}&lang={language}"
    hangup_url = f"{APP_BASE_URL}/plivo/hangup?call_id={call_id}"
    
    print(f"=== INITIATING PLIVO CALL ===")
    print(f"To: {to_number}")
    print(f"From: {CONFIG['PLIVO_PHONE_NUMBER']}")
    print(f"Answer URL: {answer_url}")
    
    try:
        client = plivo.RestClient(CONFIG["PLIVO_AUTH_ID"], CONFIG["PLIVO_AUTH_TOKEN"])
        response = client.calls.create(
            from_=CONFIG["PLIVO_PHONE_NUMBER"],
            to_=to_number,
            answer_url=answer_url,
            answer_method="POST",
            hangup_url=hangup_url,
            hangup_method="POST",
        )
        
        active_calls[call_id]["plivo_uuid"] = response.request_uuid
        print(f"Call initiated: {call_id}, Plivo UUID: {response.request_uuid}")
        
        # Add call initiation to transcript
        lang_name = LANGUAGE_NAMES.get(language, ('Unknown', 'Unknown'))[0]
        ist_now = datetime.now(IST)
        active_calls[call_id]["transcript"].append({
            "timestamp": ist_now.strftime("%H:%M:%S"),
            "speaker": "System",
            "text": f"📞 CALL INITIATED: Dialing {to_number} in {lang_name}...",
            "dtmf": None,
            "reason": None,
        })
        
        # Broadcast to UI
        for ws in ui_connections[:]:
            try:
                asyncio.create_task(ws.send_json({"type": "transcript", "call_id": call_id, "entry": active_calls[call_id]["transcript"][-1]}))
            except:
                pass
        
        return {"success": True, "call_id": call_id}
        
    except Exception as e:
        print(f"Plivo error: {e}")
        del active_calls[call_id]
        return {"success": False, "error": str(e)}


async def add_transcript(call_id: str, speaker: str, text: str, dtmf: str = None, reason: str = None):
    if call_id not in active_calls:
        return
    
    # Use IST timezone for timestamp
    ist_now = datetime.now(IST)
    entry = {
        "timestamp": ist_now.strftime("%H:%M:%S"),
        "speaker": speaker,
        "text": text,
        "dtmf": dtmf,
        "reason": reason,
    }
    active_calls[call_id]["transcript"].append(entry)
    
    for ws in ui_connections[:]:
        try:
            await ws.send_json({"type": "transcript", "call_id": call_id, "entry": entry})
        except:
            ui_connections.remove(ws)

# ============================================================================
# FastAPI App
# ============================================================================

app = FastAPI(title="Fusion Finance Voice Intelligence - Plivo")

@app.get("/version")
async def get_version():
    """Quick version check"""
    return {"version": VERSION}

@app.on_event("startup")
async def startup():
    global APP_BASE_URL, AUDIO_BASE_URL
    APP_BASE_URL = get_base_url()
    AUDIO_BASE_URL = get_audio_base_url()
    print("=" * 60)
    print(f"Fusion Finance Voice Intelligence - PLIVO v{VERSION}")
    print(f"Callback URL: {APP_BASE_URL}")
    print(f"Audio URL: {AUDIO_BASE_URL}")
    print("=" * 60)


# ============================================================================
# Plivo Webhooks
# ============================================================================

@app.post("/plivo/answer")
async def plivo_answer(request: Request):
    """Handle Plivo answer callback - play greeting and gather DTMF"""
    params = dict(request.query_params)
    call_id = params.get("call_id", "default")
    lang = params.get("lang", "hi-IN")
    
    print(f"=== PLIVO ANSWER v{VERSION}: {call_id} ===")
    
    if call_id in active_calls:
        call = active_calls[call_id]
        lang = call.get("language", lang)
        ro_name = RO_NAMES.get(lang, 'RO')
        lang_name = LANGUAGE_NAMES.get(lang, ('Unknown', 'Unknown'))[0]
        await add_transcript(call_id, "Agent", f"🔊 Playing greeting in {lang_name}: '{ro_name} will visit you tomorrow. Press 1 to confirm, 2 to reschedule.'")
        call["state"] = CallState.WAIT_AVAILABILITY
    
    action_url = f"{APP_BASE_URL}/plivo/gather?call_id={call_id}&amp;lang={lang}"
    
    # Use pre-recorded WAV files for best quality
    audio_url = f"{AUDIO_BASE_URL}/audio/{lang}/01_greeting.wav"
    
    xml = f'<Response><GetDigits action="{action_url}" numDigits="1" timeout="10"><Play>{audio_url}</Play></GetDigits><Speak>We did not receive any input. Goodbye.</Speak></Response>'
    
    print(f"=== PLIVO XML ===\n{xml}")
    
    return Response(content=xml, media_type="application/xml")


@app.post("/plivo/gather")
async def plivo_gather(request: Request):
    """Handle DTMF input for availability"""
    params = dict(request.query_params)
    form = await request.form()
    
    call_id = params.get("call_id", "default")
    lang = params.get("lang", "hi-IN")
    digits = form.get("Digits", "")
    
    print(f"=== PLIVO GATHER: {call_id}, Digits: {digits} ===")
    
    if call_id in active_calls:
        await add_transcript(call_id, "Borrower", f"📱 DTMF Input: Pressed [{digits}]", dtmf=digits)
    
    if digits == "1":
        # Confirmed - play confirmation audio
        if call_id in active_calls:
            await add_transcript(call_id, "Agent", "✅ CONFIRMED: Borrower confirmed availability for tomorrow's visit")
            active_calls[call_id]["state"] = CallState.COMPLETED
            active_calls[call_id]["outcome"] = "AVAILABLE"
        
        audio_url = f"{AUDIO_BASE_URL}/audio/{lang}/02_confirmed.wav"
        xml = f'<Response><Play>{audio_url}</Play><Hangup/></Response>'
        
    elif digits == "2":
        # Reschedule - play reason menu audio
        if call_id in active_calls:
            await add_transcript(call_id, "Agent", "🔄 RESCHEDULE REQUESTED: Playing decline reason menu (1=Travel, 2=Health, 3=Financial, 4=Work, 5=Family, 6=Agriculture)")
            active_calls[call_id]["state"] = CallState.WAIT_REASON
        
        action_url = f"{APP_BASE_URL}/plivo/reason?call_id={call_id}&amp;lang={lang}"
        audio_url = f"{AUDIO_BASE_URL}/audio/{lang}/03_ask_reason.wav"
        xml = f'<Response><GetDigits action="{action_url}" numDigits="1" timeout="10"><Play>{audio_url}</Play></GetDigits><Speak>We did not receive your input. Goodbye.</Speak><Hangup/></Response>'
        
    else:
        # Unclear - play unclear audio and repeat
        if call_id in active_calls:
            await add_transcript(call_id, "Agent", "⚠️ UNCLEAR INPUT: No valid DTMF received, repeating prompt")
        
        action_url = f"{APP_BASE_URL}/plivo/gather?call_id={call_id}&amp;lang={lang}"
        audio_url = f"{AUDIO_BASE_URL}/audio/{lang}/05_unclear.wav"
        xml = f'<Response><GetDigits action="{action_url}" numDigits="1" timeout="10"><Play>{audio_url}</Play></GetDigits><Hangup/></Response>'
    
    print(f"=== PLIVO GATHER XML ===\n{xml}")
    return Response(content=xml, media_type="application/xml")


@app.post("/plivo/reason")
async def plivo_reason(request: Request):
    """Handle DTMF input for decline reason"""
    params = dict(request.query_params)
    form = await request.form()
    
    call_id = params.get("call_id", "default")
    lang = params.get("lang", "hi-IN")
    digits = form.get("Digits", "")
    
    print(f"=== PLIVO REASON: {call_id}, Digits: {digits} ===")
    
    reason_info = DTMF_REASONS.get(digits, ("Other", "OTHER"))
    
    if call_id in active_calls:
        await add_transcript(call_id, "Borrower", f"📱 DTMF Input: Pressed [{digits}] → Reason: {reason_info[0]}", dtmf=digits, reason=reason_info[0])
        await add_transcript(call_id, "Agent", f"❌ DECLINED: Reason captured as '{reason_info[0]}'. RO will reschedule visit.")
        active_calls[call_id]["state"] = CallState.COMPLETED
        active_calls[call_id]["outcome"] = "DECLINED"
        active_calls[call_id]["decline_reason"] = reason_info[1]
    
    # Play reschedule confirmation audio
    audio_url = f"{AUDIO_BASE_URL}/audio/{lang}/04_reschedule_confirm.wav"
    xml = f'<Response><Play>{audio_url}</Play><Hangup/></Response>'
    
    print(f"=== PLIVO REASON XML ===\n{xml}")
    return Response(content=xml, media_type="application/xml")


@app.post("/plivo/hangup")
async def plivo_hangup(request: Request):
    """Handle call hangup"""
    params = dict(request.query_params)
    call_id = params.get("call_id", "default")
    print(f"=== PLIVO HANGUP: {call_id} ===")
    
    # Add hangup to transcript
    if call_id in active_calls:
        call = active_calls[call_id]
        outcome = call.get("outcome", "NO_RESPONSE")
        reason = call.get("decline_reason", "")
        
        if outcome == "AVAILABLE":
            msg = "📴 CALL ENDED: Borrower confirmed ✓"
        elif outcome == "DECLINED":
            msg = f"📴 CALL ENDED: Declined - {reason.replace('_', ' ').title()}"
        else:
            msg = "📴 CALL ENDED: No response received"
        
        await add_transcript(call_id, "System", msg)
    
    return Response(content="OK", media_type="text/plain")


# ============================================================================
# API Endpoints
# ============================================================================

class CallRequest(BaseModel):
    phone: str
    language: str = "hi-IN"

@app.post("/api/call")
async def api_make_call(req: CallRequest):
    return await make_plivo_call(req.phone, req.language)

@app.get("/api/config")
async def api_config():
    return {
        "version": VERSION,
        "provider": "Plivo",
        "auth_id": CONFIG["PLIVO_AUTH_ID"][:8] + "..." if CONFIG["PLIVO_AUTH_ID"] else None,
        "phone_number": CONFIG["PLIVO_PHONE_NUMBER"],
        "api_configured": bool(CONFIG["PLIVO_AUTH_ID"] and CONFIG["PLIVO_AUTH_TOKEN"]),
        "audio_base_url": AUDIO_BASE_URL,
    }

@app.get("/api/intelligence")
async def api_intelligence():
    return INTELLIGENCE_DATA

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    ui_connections.append(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ui_connections.remove(websocket)

@app.get("/health")
async def health():
    return {"status": "ok", "provider": "plivo"}

@app.get("/acquink_logo.png")
async def serve_logo():
    logo_path = Path(__file__).parent / "acquink_logo.png"
    if not logo_path.exists():
        raise HTTPException(status_code=404, detail="Logo not found")
    return Response(content=logo_path.read_bytes(), media_type="image/png")

@app.get("/favicon.ico")
async def serve_favicon():
    # Return a simple orange favicon (1x1 pixel)
    # Or serve the logo as favicon
    logo_path = Path(__file__).parent / "acquink_logo.png"
    if logo_path.exists():
        return Response(content=logo_path.read_bytes(), media_type="image/png")
    raise HTTPException(status_code=404, detail="Favicon not found")

@app.get("/audio/{lang}/{filename}")
async def serve_audio(lang: str, filename: str):
    """Serve audio files for Plivo playback"""
    audio_path = Path(__file__).parent / "audio" / lang / filename
    if not audio_path.exists():
        print(f"Audio file not found: {audio_path}")
        raise HTTPException(status_code=404, detail=f"Audio file not found: {lang}/{filename}")
    return Response(content=audio_path.read_bytes(), media_type="audio/wav")


# ============================================================================
# Main UI - Exact same as Exotel version
# ============================================================================

HTML_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Smaartbrand Voice | Pre-Collection Intelligence</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        * { font-family: 'Inter', sans-serif; }
        body { background: linear-gradient(135deg, #0f172a 0%, #1e1b4b 50%, #0f172a 100%); }
        
        .glass { background: rgba(255,255,255,0.05); backdrop-filter: blur(10px); border: 1px solid rgba(255,255,255,0.1); }
        .glass-dark { background: rgba(0,0,0,0.4); border: 1px solid rgba(255,255,255,0.05); }
        .chat-panel-bg { background: rgba(17,17,17,0.98); backdrop-filter: blur(20px); border-left: 1px solid rgba(255,255,255,0.1); }
        
        .gradient-text { background: linear-gradient(135deg, #f97316 0%, #8b5cf6 100%); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
        .gradient-orange { background: linear-gradient(135deg, #f97316 0%, #ea580c 100%); }
        .gradient-purple { background: linear-gradient(135deg, #8b5cf6 0%, #6d28d9 100%); }
        
        .tab-btn { transition: all 0.3s; border-radius: 8px; }
        .tab-btn.active { background: linear-gradient(135deg, #f97316 0%, #ea580c 100%); color: white; }
        .tab-btn:not(.active):hover { background: rgba(255,255,255,0.05); }
        
        .stat-card { transition: all 0.3s; }
        .stat-card:hover { transform: translateY(-4px); box-shadow: 0 10px 40px rgba(249, 115, 22, 0.15); }
        
        .alert-high { border-left: 3px solid #ef4444; background: rgba(239, 68, 68, 0.1); }
        .alert-medium { border-left: 3px solid #f59e0b; background: rgba(245, 158, 11, 0.1); }
        .alert-low { border-left: 3px solid #22c55e; background: rgba(34, 197, 94, 0.1); }
        
        .message.agent { background: rgba(139, 92, 246, 0.15); border-left: 3px solid #8b5cf6; }
        .message.borrower { background: rgba(249, 115, 22, 0.15); border-left: 3px solid #f97316; }
        
        .lang-btn.active { background: linear-gradient(135deg, #8b5cf6 0%, #6d28d9 100%); border-color: #8b5cf6; color: white; }
        
        ::-webkit-scrollbar { width: 6px; }
        ::-webkit-scrollbar-track { background: rgba(0,0,0,0.2); }
        ::-webkit-scrollbar-thumb { background: #8b5cf6; border-radius: 3px; }
    </style>
</head>
<body class="text-white flex flex-col min-h-screen">
    <!-- Header -->
    <header class="px-6 py-4 border-b border-white/10">
        <div class="max-w-7xl mx-auto flex items-center justify-between">
            <div class="flex items-center gap-3">
                <!-- Acquink Logo -->
                <img src="/acquink_logo.png" alt="Acquink" style="height: 40px; width: auto;">
                <div>
                    <h1 class="text-xl font-semibold gradient-text">Smaartbrand Voice</h1>
                    <p class="text-xs text-gray-500">Pre-Collection Intelligence</p>
                </div>
            </div>
            
            <div class="flex items-center gap-6">
                <div class="flex items-center gap-1 glass rounded-lg p-1">
                    <button class="tab-btn active px-4 py-2 text-sm font-medium" onclick="showTab('calls')">Live Calls</button>
                    <button class="tab-btn px-4 py-2 text-sm font-medium" onclick="showTab('intel')">Intelligence</button>
                </div>
                
                <button onclick="toggleChat()" class="flex items-center gap-2 glass px-4 py-2 rounded-lg text-sm hover:bg-white/5">
                    <span>💬</span>
                    <span>SmaartAnalyst</span>
                </button>
                
                <div id="wsStatus" class="px-3 py-1 rounded-full text-xs font-medium bg-yellow-500/20 text-yellow-400">Connecting...</div>
            </div>
        </div>
    </header>

    <main class="max-w-7xl mx-auto p-6 flex-1">
        <!-- Live Calls Tab -->
        <div id="callsTab" class="tab-panel">
            <div class="grid grid-cols-3 gap-6">
                <!-- Call Controls -->
                <div class="col-span-1 space-y-4">
                    <div class="glass rounded-2xl p-6">
                        <h3 class="text-sm font-semibold text-gray-400 uppercase tracking-wide mb-4">Make a Call</h3>
                        
                        <div class="space-y-4">
                            <div>
                                <label class="text-xs text-gray-500 mb-1 block">Phone Number</label>
                                <input type="tel" id="phone" placeholder="9876543210" class="w-full glass-dark rounded-lg px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-orange-500">
                            </div>
                            
                            <div>
                                <label class="text-xs text-gray-500 mb-2 block">Language</label>
                                <div class="grid grid-cols-3 gap-2">
                                    <button class="lang-btn active glass rounded-lg py-2 text-xs border border-transparent" data-lang="hi-IN">हिंदी</button>
                                    <button class="lang-btn glass rounded-lg py-2 text-xs border border-transparent" data-lang="te-IN">తెలుగు</button>
                                    <button class="lang-btn glass rounded-lg py-2 text-xs border border-transparent" data-lang="ta-IN">தமிழ்</button>
                                    <button class="lang-btn glass rounded-lg py-2 text-xs border border-transparent" data-lang="kn-IN">ಕನ್ನಡ</button>
                                    <button class="lang-btn glass rounded-lg py-2 text-xs border border-transparent" data-lang="mr-IN">मराठी</button>
                                    <button class="lang-btn glass rounded-lg py-2 text-xs border border-transparent" data-lang="en-IN">English</button>
                                </div>
                            </div>
                            
                            <button id="callBtn" onclick="makeCall()" class="w-full gradient-orange text-white font-semibold py-3 rounded-xl hover:opacity-90 transition">
                                📞 Call Now
                            </button>
                            
                            <div id="callStatus" class="text-center text-sm text-gray-400"></div>
                        </div>
                    </div>
                    
                    <div class="glass rounded-2xl p-6">
                        <h3 class="text-sm font-semibold text-gray-400 uppercase tracking-wide mb-4">Configuration</h3>
                        <div id="configInfo" class="space-y-2 text-xs"></div>
                    </div>
                </div>
                
                <!-- Live Transcript -->
                <div class="col-span-2">
                    <div class="glass rounded-2xl p-6 h-[600px] flex flex-col">
                        <h3 class="text-sm font-semibold text-gray-400 uppercase tracking-wide mb-4">Live Transcript</h3>
                        <div id="transcript" class="flex-1 overflow-y-auto space-y-3">
                            <div class="transcript-placeholder text-center text-gray-500 py-20">
                                <div class="text-4xl mb-4">🎙️</div>
                                <p>Make a call to see the live transcript</p>
                                <p class="text-xs mt-2">DTMF responses and decline reasons will appear here</p>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        
        <!-- Intelligence Dashboard Tab -->
        <div id="intelTab" class="tab-panel hidden">
            <!-- Stats Row -->
            <div class="grid grid-cols-5 gap-4 mb-6">
                <div class="stat-card glass rounded-xl p-5 text-center">
                    <div class="text-3xl font-bold text-orange-500" id="statCalls">1,247</div>
                    <div class="text-xs text-gray-400 mt-1">Total Calls</div>
                </div>
                <div class="stat-card glass rounded-xl p-5 text-center">
                    <div class="text-3xl font-bold text-blue-400" id="statConnected">87.3%</div>
                    <div class="text-xs text-gray-400 mt-1">Connection Rate</div>
                </div>
                <div class="stat-card glass rounded-xl p-5 text-center">
                    <div class="text-3xl font-bold text-green-400" id="statConfirmed">67.4%</div>
                    <div class="text-xs text-gray-400 mt-1">Confirmed</div>
                </div>
                <div class="stat-card glass rounded-xl p-5 text-center">
                    <div class="text-3xl font-bold text-red-400" id="statDecliners">12.4%</div>
                    <div class="text-xs text-gray-400 mt-1">Frequent Decliners</div>
                </div>
                <div class="stat-card glass rounded-xl p-5 text-center">
                    <div class="text-3xl font-bold text-purple-400" id="statBorrowers">500</div>
                    <div class="text-xs text-gray-400 mt-1">Profiled</div>
                </div>
            </div>
            
            <!-- Main Grid -->
            <div class="grid grid-cols-2 gap-6 mb-6">
                <!-- Decline Reasons -->
                <div class="glass rounded-2xl p-6">
                    <h3 class="text-sm font-semibold text-gray-400 uppercase tracking-wide mb-4">📊 Decline Reason Distribution</h3>
                    <div id="reasonsList" class="space-y-3"></div>
                </div>
                
                <!-- Cluster Risk -->
                <div class="glass rounded-2xl p-6">
                    <h3 class="text-sm font-semibold text-gray-400 uppercase tracking-wide mb-4">📍 Cluster Risk Monitor</h3>
                    <div id="clustersList" class="space-y-3"></div>
                </div>
            </div>
            
            <!-- Early Warnings -->
            <div class="glass rounded-2xl p-6 mb-6">
                <h3 class="text-sm font-semibold text-gray-400 uppercase tracking-wide mb-4">⚠️ Early Warning Signals</h3>
                <div id="warningsList" class="grid grid-cols-3 gap-4"></div>
            </div>
            
            <!-- Bottom Row -->
            <div class="grid grid-cols-2 gap-6 mb-6">
                <!-- Frequent Decliners -->
                <div class="glass rounded-2xl p-6">
                    <h3 class="text-sm font-semibold text-gray-400 uppercase tracking-wide mb-4">🚨 Frequent Decliners (Top 10)</h3>
                    <table class="w-full text-sm">
                        <thead>
                            <tr class="text-gray-500 text-xs">
                                <th class="text-left py-2">ID</th>
                                <th class="text-left py-2">Cluster</th>
                                <th class="text-left py-2">Declines</th>
                                <th class="text-left py-2">Reason</th>
                                <th class="text-left py-2">Risk</th>
                            </tr>
                        </thead>
                        <tbody id="declinersTbody"></tbody>
                    </table>
                </div>
                
                <!-- Persona Distribution -->
                <div class="glass rounded-2xl p-6">
                    <h3 class="text-sm font-semibold text-gray-400 uppercase tracking-wide mb-4">👥 Borrower Personas</h3>
                    <div id="personaGrid" class="grid grid-cols-5 gap-3"></div>
                </div>
            </div>
            
            <!-- NPA Prediction -->
            <div class="glass rounded-2xl p-6 border border-orange-500/30">
                <h3 class="text-sm font-semibold text-gray-400 uppercase tracking-wide mb-4">🎯 NPA Prediction & Early Intervention Value</h3>
                <div class="grid grid-cols-4 gap-6">
                    <div class="text-center">
                        <div class="text-xs text-gray-500 mb-2">Current NPA</div>
                        <div class="text-3xl font-bold text-green-400">2.8%</div>
                    </div>
                    <div class="text-center">
                        <div class="text-xs text-gray-500 mb-2">Predicted (60 days)</div>
                        <div class="text-3xl font-bold text-yellow-400">3.4%</div>
                    </div>
                    <div class="text-center">
                        <div class="text-xs text-gray-500 mb-2">At-Risk Portfolio</div>
                        <div class="text-3xl font-bold text-red-400">₹4.2 Cr</div>
                    </div>
                    <div class="text-center">
                        <div class="text-xs text-gray-500 mb-2">Early Intervention Saves</div>
                        <div class="text-3xl font-bold text-orange-500">₹1.8 Cr</div>
                    </div>
                </div>
            </div>
        </div>
    </main>
    
    <!-- SmaartAnalyst Chat Panel -->
    <div id="chatPanel" class="fixed right-0 top-0 h-full w-[420px] chat-panel-bg z-50 flex flex-col transform translate-x-full transition-transform duration-300">
        <div class="flex items-center justify-between p-4 border-b border-white/10">
            <div class="flex items-center gap-3">
                <div class="w-8 h-8 rounded-lg gradient-purple flex items-center justify-center">🤖</div>
                <div>
                    <h3 class="font-semibold text-sm">SmaartAnalyst</h3>
                    <p class="text-xs text-gray-400">Voice Intelligence AI</p>
                </div>
            </div>
            <button onclick="toggleChat()" class="p-2 hover:bg-white/10 rounded-lg">✕</button>
        </div>
        
        <div id="chatMessages" class="flex-1 overflow-y-auto p-4 space-y-4">
            <div class="flex gap-3">
                <div class="w-8 h-8 rounded-lg gradient-purple flex items-center justify-center flex-shrink-0 text-sm">🤖</div>
                <div class="glass rounded-xl p-3 max-w-[85%]">
                    <p class="text-sm">Hello! I'm SmaartAnalyst. Ask me about call patterns, decline reasons, or cluster risks. Try:</p>
                    <ul class="text-sm text-gray-400 mt-2 space-y-1">
                        <li>• Which cluster has highest risk?</li>
                        <li>• Top decline reasons this week?</li>
                        <li>• Who are frequent decliners?</li>
                    </ul>
                </div>
            </div>
        </div>
        
        <div class="p-3 border-t border-white/10">
            <div class="flex gap-2 flex-wrap mb-3">
                <button class="glass text-xs px-3 py-1.5 rounded-full hover:bg-white/10" onclick="askChat('What are the priority actions for today?')">🎯 Today's priorities</button>
                <button class="glass text-xs px-3 py-1.5 rounded-full hover:bg-white/10" onclick="askChat('Which cluster has highest risk?')">🔴 Risky clusters</button>
                <button class="glass text-xs px-3 py-1.5 rounded-full hover:bg-white/10" onclick="askChat('NPA prediction')">📈 NPA outlook</button>
            </div>
            <div class="flex gap-2">
                <input type="text" id="chatInput" placeholder="Ask about call intelligence..." class="flex-1 glass-dark rounded-lg px-4 py-2 text-sm focus:outline-none" onkeypress="if(event.key==='Enter')askChat()">
                <button onclick="askChat()" class="gradient-orange px-4 py-2 rounded-lg text-sm font-medium">Send</button>
            </div>
        </div>
    </div>
    
    <!-- Footer -->
    <footer class="px-6 py-4 border-t border-white/10">
        <div class="max-w-7xl mx-auto flex items-center justify-between">
            <div class="flex items-center gap-3">
                <img src="/acquink_logo.png" alt="Acquink" style="height: 24px; width: auto;">
                <span class="text-sm text-gray-500">© 2026 Acquink</span>
            </div>
            <div class="text-sm text-gray-500">
                Powered by <span class="text-purple-400 font-medium">MASI</span> Technology | <span class="text-green-400">Plivo</span>
            </div>
        </div>
    </footer>

    <script>
        let ws;
        let selectedLang = 'hi-IN';
        let intelData = null;
        
        function showTab(tab) {
            document.querySelectorAll('.tab-panel').forEach(p => p.classList.add('hidden'));
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.getElementById(tab + 'Tab').classList.remove('hidden');
            event.target.classList.add('active');
            if (tab === 'intel' && !intelData) loadIntelligence();
        }
        
        document.querySelectorAll('.lang-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('.lang-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                selectedLang = btn.dataset.lang;
            });
        });
        
        function toggleChat() {
            document.getElementById('chatPanel').classList.toggle('translate-x-full');
        }
        
        function connectWS() {
            const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
            ws = new WebSocket(`${protocol}//${location.host}/ws`);
            ws.onopen = () => {
                document.getElementById('wsStatus').className = 'px-3 py-1 rounded-full text-xs font-medium bg-green-500/20 text-green-400';
                document.getElementById('wsStatus').textContent = 'Connected';
            };
            ws.onclose = () => {
                document.getElementById('wsStatus').className = 'px-3 py-1 rounded-full text-xs font-medium bg-red-500/20 text-red-400';
                document.getElementById('wsStatus').textContent = 'Disconnected';
                setTimeout(connectWS, 3000);
            };
            ws.onmessage = (e) => {
                const msg = JSON.parse(e.data);
                if (msg.type === 'transcript') addTranscriptEntry(msg.entry);
            };
        }
        
        function addTranscriptEntry(entry) {
            const box = document.getElementById('transcript');
            if (box.querySelector('.transcript-placeholder')) box.innerHTML = '';
            const isAgent = entry.speaker === 'Agent';
            const isSystem = entry.speaker === 'System';
            let msgClass = isAgent ? 'agent' : (isSystem ? 'system' : 'borrower');
            let colorClass = isAgent ? 'text-blue-400' : (isSystem ? 'text-gray-400' : 'text-orange-400');
            let tags = '';
            if (entry.dtmf) tags += `<span class="px-2 py-0.5 rounded text-xs bg-blue-500/20 text-blue-300 ml-2">DTMF: ${entry.dtmf}</span>`;
            if (entry.reason) tags += `<span class="px-2 py-0.5 rounded text-xs bg-orange-500/20 text-orange-300 ml-2">${entry.reason}</span>`;
            box.insertAdjacentHTML('beforeend', `
                <div class="message ${msgClass} rounded-xl p-4">
                    <div class="flex justify-between items-center mb-2">
                        <span class="font-semibold text-sm ${colorClass}">${entry.speaker}</span>
                        <span class="text-xs text-gray-400">${entry.timestamp}</span>
                    </div>
                    <div class="text-sm">${entry.text}${tags}</div>
                </div>
            `);
            box.scrollTop = box.scrollHeight;
        }
        
        let isCallInProgress = false;
        async function makeCall() {
            if (isCallInProgress) return;
            const phone = document.getElementById('phone').value;
            if (!phone) { alert('Enter phone number'); return; }
            isCallInProgress = true;
            const btn = document.getElementById('callBtn');
            btn.disabled = true;
            btn.textContent = '📞 Calling...';
            btn.style.opacity = '0.5';
            document.getElementById('callStatus').textContent = 'Initiating...';
            document.getElementById('transcript').innerHTML = '<div class="transcript-placeholder text-center text-gray-500 py-10">Connecting...</div>';
            try {
                const resp = await fetch('/api/call', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ phone, language: selectedLang })
                });
                const result = await resp.json();
                if (result.success) {
                    document.getElementById('callStatus').textContent = `Call ID: ${result.call_id}`;
                } else {
                    document.getElementById('callStatus').textContent = `Error: ${result.error}`;
                }
            } catch (err) {
                document.getElementById('callStatus').textContent = `Error: ${err.message}`;
            }
            setTimeout(() => {
                isCallInProgress = false;
                btn.disabled = false;
                btn.textContent = '📞 Call Now';
                btn.style.opacity = '1';
            }, 3000);
        }
        
        async function loadConfig() {
            const resp = await fetch('/api/config');
            const cfg = await resp.json();
            document.getElementById('configInfo').innerHTML = `
                <div class="flex justify-between py-1 border-b border-white/5">
                    <span class="text-gray-500">Version</span>
                    <span class="text-purple-400 font-mono">${cfg.version}</span>
                </div>
                <div class="flex justify-between py-1 border-b border-white/5">
                    <span class="text-gray-500">Provider</span>
                    <span class="text-green-400">${cfg.provider}</span>
                </div>
                <div class="flex justify-between py-1 border-b border-white/5">
                    <span class="text-gray-500">Phone</span>
                    <span class="text-gray-300">${cfg.phone_number || 'Not set'}</span>
                </div>
                <div class="flex justify-between py-1 border-b border-white/5">
                    <span class="text-gray-500">API</span>
                    <span class="${cfg.api_configured ? 'text-green-400' : 'text-red-400'}">${cfg.api_configured ? '✓ Configured' : '✗ Missing'}</span>
                </div>
                <div class="flex justify-between py-1">
                    <span class="text-gray-500">Audio</span>
                    <span class="text-gray-300 text-xs truncate max-w-[150px]" title="${cfg.audio_base_url}">${cfg.audio_base_url.replace('https://', '').substring(0, 20)}...</span>
                </div>
            `;
        }
        
        async function loadIntelligence() {
            const resp = await fetch('/api/intelligence');
            intelData = await resp.json();
            const reasons = Object.entries(intelData.decline_reasons).sort((a,b) => b[1] - a[1]);
            const maxReason = Math.max(...Object.values(intelData.decline_reasons));
            document.getElementById('reasonsList').innerHTML = reasons.map(([r, count]) => `
                <div class="flex items-center gap-3">
                    <div class="w-32 text-xs text-gray-400">${r.replace(/_/g, ' ')}</div>
                    <div class="flex-1 h-2 glass rounded-full overflow-hidden">
                        <div class="h-full gradient-orange" style="width: ${count/maxReason*100}%"></div>
                    </div>
                    <div class="w-8 text-right text-xs text-orange-400">${count}</div>
                </div>
            `).join('');
            const clusters = Object.entries(intelData.clusters).sort((a,b) => b[1].avg_risk - a[1].avg_risk);
            document.getElementById('clustersList').innerHTML = clusters.map(([name, c]) => `
                <div class="flex items-center justify-between p-3 rounded-lg alert-${c.alert.toLowerCase()}">
                    <div>
                        <div class="font-medium text-sm">${name}</div>
                        <div class="text-xs text-gray-400">${c.state} • ${c.total} borrowers</div>
                    </div>
                    <div class="text-right">
                        <div class="text-sm font-bold ${c.alert === 'HIGH' ? 'text-red-400' : c.alert === 'MEDIUM' ? 'text-yellow-400' : 'text-green-400'}">${c.alert}</div>
                        <div class="text-xs text-gray-400">Risk: ${c.avg_risk}</div>
                    </div>
                </div>
            `).join('');
            document.getElementById('warningsList').innerHTML = intelData.early_warnings.map(w => `
                <div class="p-4 rounded-xl alert-${w.level.toLowerCase()}">
                    <div class="font-medium text-sm mb-1">${w.cluster}</div>
                    <div class="text-xs text-gray-400">${w.message}</div>
                </div>
            `).join('');
            document.getElementById('declinersTbody').innerHTML = intelData.frequent_decliners.map(d => `
                <tr class="border-b border-white/5">
                    <td class="py-2 text-orange-400">${d.id}</td>
                    <td class="py-2">${d.cluster}</td>
                    <td class="py-2">${d.decline_count}x</td>
                    <td class="py-2 text-xs">${d.decline_reasons[0] || '-'}</td>
                    <td class="py-2 ${d.risk_score >= 60 ? 'text-red-400' : 'text-yellow-400'}">${d.risk_score}</td>
                </tr>
            `).join('');
            const personas = Object.entries(intelData.personas);
            const icons = { FARMER: '👨‍🌾', TRADER: '🏪', SALARIED: '💼', SELF_EMPLOYED: '🔧', DAILY_WAGE: '🏗️' };
            document.getElementById('personaGrid').innerHTML = personas.map(([p, count]) => `
                <div class="glass rounded-xl p-4 text-center">
                    <div class="text-2xl mb-2">${icons[p] || '👤'}</div>
                    <div class="text-lg font-bold text-purple-400">${count}</div>
                    <div class="text-xs text-gray-400">${p.replace(/_/g, ' ')}</div>
                </div>
            `).join('');
        }
        
        function askChat(question) {
            const input = document.getElementById('chatInput');
            const q = question || input.value.trim();
            if (!q) return;
            input.value = '';
            
            const box = document.getElementById('chatMessages');
            
            // Add user message
            box.innerHTML += `
                <div class="flex gap-3 justify-end">
                    <div class="glass rounded-xl p-3 max-w-[85%] bg-orange-500/20">
                        <p class="text-sm">${q}</p>
                    </div>
                </div>
            `;
            
            // Generate response based on question - HARDCODED DEMO DATA
            let response = '';
            const ql = q.toLowerCase();
            
            if (ql.includes('cluster') || ql.includes('risk') || ql.includes('village')) {
                response = `<strong>🔴 High Risk Clusters (2):</strong><br>` +
                    `• <strong>Warangal Rural</strong>: 23 financial stress cases, Avg Risk 52<br>` +
                    `• <strong>Karimnagar</strong>: 18 financial stress cases, Avg Risk 47<br><br>` +
                    `<strong>🟡 Medium Risk (2):</strong><br>` +
                    `• <strong>Shad Nagar</strong>: 12 frequent decliners, Avg Risk 34<br>` +
                    `• <strong>Nizamabad</strong>: 9 frequent decliners, Avg Risk 28<br><br>` +
                    `<strong>🟢 Low Risk (1):</strong><br>` +
                    `• <strong>Medak</strong>: 4 frequent decliners, Avg Risk 18<br><br>` +
                    `<strong>📋 Department Actions:</strong><br>` +
                    `• <em>Field Ops:</em> Deploy senior ROs to Warangal Rural immediately<br>` +
                    `• <em>Collections:</em> Prioritize 41 financial stress cases for restructuring<br>` +
                    `• <em>Risk:</em> Flag HIGH clusters for daily monitoring this week`;
            } else if (ql.includes('decline') || ql.includes('reason')) {
                response = `<strong>📊 Decline Reason Analysis (355 declines):</strong><br><br>` +
                    `• <strong>Financial Stress</strong>: 127 (36%) <span class="text-red-400">↑ 8% vs last month</span><br>` +
                    `• <strong>Travel / Market</strong>: 89 (25%)<br>` +
                    `• <strong>Crop / Agriculture</strong>: 71 (20%) <span class="text-yellow-400">↑ seasonal</span><br>` +
                    `• <strong>Health Issues</strong>: 39 (11%)<br>` +
                    `• <strong>Work / Office</strong>: 18 (5%)<br>` +
                    `• <strong>Family Event</strong>: 11 (3%)<br><br>` +
                    `<strong>📋 Department Actions:</strong><br>` +
                    `• <em>Collections:</em> Financial stress cases need early restructuring — 127 borrowers at risk of NPA<br>` +
                    `• <em>Field Ops:</em> Adjust visit timing to evenings for Travel/Market conflicts<br>` +
                    `• <em>Product:</em> Consider harvest-aligned EMI dates for agricultural borrowers`;
            } else if (ql.includes('frequent') || ql.includes('decliner')) {
                response = `<strong>🚨 Frequent Decliners (47 borrowers with 3+ declines):</strong><br><br>` +
                    `1. <strong>BRW10234</strong> — 6x declined, Risk: <span class="text-red-400">92</span><br>` +
                    `&nbsp;&nbsp;&nbsp;Cluster: Warangal Rural | Reason: Financial Stress<br>` +
                    `2. <strong>BRW10456</strong> — 5x declined, Risk: <span class="text-red-400">78</span><br>` +
                    `&nbsp;&nbsp;&nbsp;Cluster: Karimnagar | Reason: Financial Stress<br>` +
                    `3. <strong>BRW10789</strong> — 5x declined, Risk: <span class="text-yellow-400">65</span><br>` +
                    `&nbsp;&nbsp;&nbsp;Cluster: Shad Nagar | Reason: Crop/Agriculture<br>` +
                    `4. <strong>BRW10123</strong> — 4x declined, Risk: <span class="text-yellow-400">58</span><br>` +
                    `&nbsp;&nbsp;&nbsp;Cluster: Warangal Rural | Reason: Travel/Market<br>` +
                    `5. <strong>BRW10567</strong> — 4x declined, Risk: <span class="text-yellow-400">52</span><br>` +
                    `&nbsp;&nbsp;&nbsp;Cluster: Nizamabad | Reason: Health<br><br>` +
                    `<strong>📋 Department Actions:</strong><br>` +
                    `• <em>Relationship Manager:</em> Personal call to top 5 today — understand root cause<br>` +
                    `• <em>Collections:</em> Offer EMI restructuring for BRW10234, BRW10456<br>` +
                    `• <em>Risk:</em> All 47 added to 60-day NPA watchlist`;
            } else if (ql.includes('npa') || ql.includes('predict') || ql.includes('portfolio')) {
                response = `<strong>📈 NPA Prediction (60-Day Outlook):</strong><br><br>` +
                    `<div style="display:flex;gap:20px;margin-bottom:12px;">` +
                    `<div><span class="text-gray-400">Current NPA</span><br><strong class="text-2xl">2.8%</strong></div>` +
                    `<div><span class="text-gray-400">Predicted</span><br><strong class="text-2xl text-red-400">3.4%</strong> <span class="text-red-400">↑0.6%</span></div>` +
                    `</div>` +
                    `• At-Risk Amount: <strong>₹4.2 Cr</strong> (154 borrowers)<br>` +
                    `• Saveable with intervention: <strong class="text-green-400">₹1.8 Cr</strong> (67 borrowers)<br><br>` +
                    `<strong>📋 Department Priorities:</strong><br>` +
                    `• <em>CEO/CCO:</em> Authorize early intervention budget for 154 at-risk borrowers<br>` +
                    `• <em>Collections Head:</em> Focus on 47 frequent decliners with financial stress<br>` +
                    `• <em>Field Ops:</em> Warangal + Karimnagar need 2 additional ROs this month<br>` +
                    `• <em>Risk:</em> Weekly tracking of predicted vs actual NPA conversion`;
            } else if (ql.includes('warangal')) {
                response = `<strong>🏢 Warangal Rural Centre Insights:</strong><br><br>` +
                    `<strong>Status:</strong> <span class="text-red-400">🔴 HIGH ALERT</span><br>` +
                    `<strong>State:</strong> Telangana<br>` +
                    `<strong>Total Borrowers:</strong> 127<br>` +
                    `<strong>Average Risk Score:</strong> 52<br>` +
                    `<strong>Frequent Decliners:</strong> 18<br>` +
                    `<strong>Financial Stress Cases:</strong> 23<br><br>` +
                    `<strong>📊 Key Patterns:</strong><br>` +
                    `• Peak decline reason: Financial Stress (18%)<br>` +
                    `• 18 borrowers declined 3+ times<br>` +
                    `• Collection efficiency dropped to 48% this month<br><br>` +
                    `<strong>📋 Actions for Warangal Rural:</strong><br>` +
                    `• <em>Branch Manager:</em> Personal review of top 5 decliners today<br>` +
                    `• <em>Collections:</em> Restructuring offers for 23 financial stress cases<br>` +
                    `• <em>Field Ops:</em> Deploy senior RO Ravi for support this week<br>` +
                    `• <em>Risk:</em> Daily monitoring until alert level drops`;
            } else if (ql.includes('karimnagar')) {
                response = `<strong>🏢 Karimnagar Centre Insights:</strong><br><br>` +
                    `<strong>Status:</strong> <span class="text-red-400">🔴 HIGH ALERT</span><br>` +
                    `<strong>State:</strong> Telangana<br>` +
                    `<strong>Total Borrowers:</strong> 98<br>` +
                    `<strong>Average Risk Score:</strong> 47<br>` +
                    `<strong>Frequent Decliners:</strong> 14<br>` +
                    `<strong>Financial Stress Cases:</strong> 18<br><br>` +
                    `<strong>📊 Key Patterns:</strong><br>` +
                    `• 8 consecutive decliners (same borrowers declining every call)<br>` +
                    `• Crop/Agriculture reasons spiking — harvest delay<br><br>` +
                    `<strong>📋 Actions for Karimnagar:</strong><br>` +
                    `• <em>Branch Manager:</em> Meet with 8 consecutive decliners in person<br>` +
                    `• <em>Collections:</em> Offer harvest-aligned EMI deferment<br>` +
                    `• <em>Field Ops:</em> Coordinate with agriculture extension officer`;
            } else if (ql.includes('centre') || ql.includes('center') || ql.includes('branch') || ql.includes('ro')) {
                response = `<strong>🏢 Centre Performance Analysis:</strong><br><br>` +
                    `<strong class="text-red-400">⬇️ Needs Attention:</strong><br>` +
                    `• <strong>Warangal Rural</strong> (Telangana)<br>` +
                    `&nbsp;&nbsp;Risk: 52 | Decliners: 18 | Financial Stress: 23<br>` +
                    `• <strong>Karimnagar</strong> (Telangana)<br>` +
                    `&nbsp;&nbsp;Risk: 47 | Decliners: 14 | Financial Stress: 18<br><br>` +
                    `<strong class="text-green-400">⬆️ Top Performers:</strong><br>` +
                    `• <strong>Medak</strong> (Telangana)<br>` +
                    `&nbsp;&nbsp;Risk: 18 | Decliners: 4 | 94% confirmation rate<br><br>` +
                    `<strong>💡 Ask about specific centre:</strong><br>` +
                    `• "Warangal Rural insights"<br>` +
                    `• "Karimnagar analysis"`;
            } else if (ql.includes('action') || ql.includes('what should') || ql.includes('recommend') || ql.includes('priority') || ql.includes('today')) {
                response = `<strong>🎯 Today's Priority Actions by Department:</strong><br><br>` +
                    `<strong>👔 CCO / Management:</strong><br>` +
                    `• Review ₹4.2 Cr at-risk portfolio with Collections Head<br>` +
                    `• Approve emergency RO deployment to Warangal<br><br>` +
                    `<strong>💰 Collections Team:</strong><br>` +
                    `• Call 47 frequent decliners — prioritize financial stress cases<br>` +
                    `• Prepare restructuring options for 23 Warangal cases<br>` +
                    `• Follow up on yesterday's 12 "will call back" promises<br><br>` +
                    `<strong>🚗 Field Operations:</strong><br>` +
                    `• Deploy senior RO to Warangal Rural (HIGH alert)<br>` +
                    `• Shift Karimnagar visits to evenings (market trader conflicts)<br>` +
                    `• Coordinate Nizamabad visits with harvest schedule<br><br>` +
                    `<strong>📊 Risk Management:</strong><br>` +
                    `• Update watchlist with 154 early warning borrowers<br>` +
                    `• Generate weekly NPA projection for Friday CCO meeting<br>` +
                    `• Flag 8 Karimnagar consecutive decliners for escalation`;
            } else if (ql.includes('persona') || ql.includes('borrower') || ql.includes('type') || ql.includes('segment')) {
                response = `<strong>👥 Borrower Persona Analysis (500 profiled):</strong><br><br>` +
                    `• 👨‍🌾 <strong>Farmers (38%)</strong> — 190 borrowers<br>` +
                    `&nbsp;&nbsp;Crop cycle dependent, seasonal income, harvest-aligned EMIs work best<br>` +
                    `• 🏪 <strong>Traders (24%)</strong> — 120 borrowers<br>` +
                    `&nbsp;&nbsp;Market day conflicts (Mon/Thu), cash flow gaps mid-week<br>` +
                    `• 💼 <strong>Salaried (18%)</strong> — 90 borrowers<br>` +
                    `&nbsp;&nbsp;Most reliable segment, 89% confirmation rate<br>` +
                    `• 🔧 <strong>Self-Employed (12%)</strong> — 60 borrowers<br>` +
                    `&nbsp;&nbsp;Variable income, financial stress most common decline reason<br>` +
                    `• 🏗️ <strong>Daily Wage (8%)</strong> — 40 borrowers<br>` +
                    `&nbsp;&nbsp;Highest risk segment, 34% are frequent decliners<br><br>` +
                    `<strong>📋 Segment-Specific Actions:</strong><br>` +
                    `• <em>Farmers:</em> Align collection calls with harvest cycles (Oct-Nov, Mar-Apr)<br>` +
                    `• <em>Traders:</em> Call on Wed/Fri, avoid market days<br>` +
                    `• <em>Daily Wage:</em> Morning calls before 8 AM, before they leave for work`;
            } else if (ql.includes('how many') || ql.includes('calls today') || ql.includes('summary') || ql.includes('overview')) {
                response = `<strong>📞 Today's Call Summary:</strong><br><br>` +
                    `<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:12px;">` +
                    `<div class="glass rounded-lg p-3 text-center"><span class="text-2xl font-bold text-orange-400">1,247</span><br><span class="text-xs text-gray-400">Total Calls</span></div>` +
                    `<div class="glass rounded-lg p-3 text-center"><span class="text-2xl font-bold text-green-400">87.3%</span><br><span class="text-xs text-gray-400">Connected</span></div>` +
                    `<div class="glass rounded-lg p-3 text-center"><span class="text-2xl font-bold text-purple-400">67.4%</span><br><span class="text-xs text-gray-400">Confirmed</span></div>` +
                    `</div>` +
                    `<strong>Breakdown:</strong><br>` +
                    `• ✅ Confirmed visits: <strong>734</strong> borrowers ready for tomorrow<br>` +
                    `• ❌ Declined/Reschedule: <strong>355</strong> borrowers with reasons captured<br>` +
                    `• 📵 Not connected: <strong>158</strong> (retry scheduled for evening)<br><br>` +
                    `<strong>💡 Insight:</strong> Confirmation rate is 4% below target. Financial stress declines up 8% — recommend proactive restructuring outreach.`;
            } else if (ql.includes('why') && ql.includes('warangal')) {
                response = `<strong>🔍 Root Cause Analysis: Warangal Rural HIGH Alert</strong><br><br>` +
                    `Warangal Rural triggered HIGH alert due to <strong>3 converging factors</strong>:<br><br>` +
                    `<strong>1. Financial Stress Spike (+23% MoM)</strong><br>` +
                    `• 23 borrowers citing financial stress — highest in portfolio<br>` +
                    `• Correlates with local factory layoffs in September<br>` +
                    `• 8 of these are daily wage workers from affected factory<br><br>` +
                    `<strong>2. Consecutive Decliners</strong><br>` +
                    `• 6 borrowers have declined 4+ consecutive calls<br>` +
                    `• Pattern suggests avoidance, not scheduling conflict<br>` +
                    `• BRW10234 has declined 6 times — likely pre-NPA<br><br>` +
                    `<strong>3. RO Coverage Gap</strong><br>` +
                    `• Current RO Suresh handling 127 borrowers (above 100 threshold)<br>` +
                    `• Visit completion rate dropped to 78% vs 92% target<br><br>` +
                    `<strong>📋 Recommended Intervention:</strong><br>` +
                    `Deploy senior RO + restructuring offers for financial stress cases. Estimated ₹45L saveable.`;
            } else if (ql.includes('compare') || ql.includes('vs') || ql.includes('versus') || ql.includes('difference')) {
                response = `<strong>📊 Cluster Comparison Analysis:</strong><br><br>` +
                    `<table style="width:100%;font-size:12px;border-collapse:collapse;">` +
                    `<tr style="border-bottom:1px solid rgba(255,255,255,0.1);"><th style="text-align:left;padding:4px;">Centre</th><th>Risk</th><th>Decliners</th><th>Stress</th><th>Status</th></tr>` +
                    `<tr style="border-bottom:1px solid rgba(255,255,255,0.1);"><td style="padding:4px;">Warangal Rural</td><td class="text-red-400">52</td><td>18</td><td>23</td><td>🔴 HIGH</td></tr>` +
                    `<tr style="border-bottom:1px solid rgba(255,255,255,0.1);"><td style="padding:4px;">Karimnagar</td><td class="text-red-400">47</td><td>14</td><td>18</td><td>🔴 HIGH</td></tr>` +
                    `<tr style="border-bottom:1px solid rgba(255,255,255,0.1);"><td style="padding:4px;">Shad Nagar</td><td class="text-yellow-400">34</td><td>12</td><td>8</td><td>🟡 MED</td></tr>` +
                    `<tr style="border-bottom:1px solid rgba(255,255,255,0.1);"><td style="padding:4px;">Nizamabad</td><td class="text-yellow-400">28</td><td>9</td><td>6</td><td>🟡 MED</td></tr>` +
                    `<tr><td style="padding:4px;">Medak</td><td class="text-green-400">18</td><td>4</td><td>2</td><td>🟢 LOW</td></tr>` +
                    `</table><br>` +
                    `<strong>💡 Key Insight:</strong> Warangal & Karimnagar together account for <strong>68%</strong> of all financial stress cases. Medak's success pattern: smaller portfolio (67 borrowers) + dedicated senior RO.`;
            } else if (ql.includes('trend') || ql.includes('week') || ql.includes('month') || ql.includes('pattern')) {
                response = `<strong>📈 Weekly Trend Analysis:</strong><br><br>` +
                    `<strong>Confirmation Rate Trend:</strong><br>` +
                    `• Week 1: 72.1% ✓<br>` +
                    `• Week 2: 69.8% ↓<br>` +
                    `• Week 3: 68.2% ↓<br>` +
                    `• Week 4 (current): <span class="text-yellow-400">67.4%</span> ↓<br><br>` +
                    `<strong>🚨 Concerning Patterns:</strong><br>` +
                    `• Financial stress mentions up <span class="text-red-400">+31%</span> over 4 weeks<br>` +
                    `• Agricultural declines spiking — harvest season impact<br>` +
                    `• Warangal degraded from MEDIUM → HIGH in 2 weeks<br><br>` +
                    `<strong>✅ Positive Signals:</strong><br>` +
                    `• Connection rate stable at 87%+ (IVR + voice working)<br>` +
                    `• Medak improved from MEDIUM → LOW after RO change<br>` +
                    `• Health-related declines down 12% (seasonal)<br><br>` +
                    `<strong>📋 Forecast:</strong> Without intervention, expect NPA to hit 3.4% (+0.6%) in 60 days.`;
            } else if (ql.includes('save') || ql.includes('prevent') || ql.includes('intervention') || ql.includes('restructur')) {
                response = `<strong>💰 Intervention & Savings Analysis:</strong><br><br>` +
                    `<strong>At-Risk Portfolio:</strong> ₹4.2 Cr across 154 borrowers<br><br>` +
                    `<strong>Saveable with Intervention:</strong><br>` +
                    `<div class="glass rounded-lg p-3 my-2 text-center">` +
                    `<span class="text-3xl font-bold text-green-400">₹1.8 Cr</span><br>` +
                    `<span class="text-xs text-gray-400">67 borrowers responsive to restructuring</span>` +
                    `</div><br>` +
                    `<strong>Intervention Strategies by Segment:</strong><br>` +
                    `• <strong>EMI Restructuring</strong> (41 borrowers): Extend tenure, reduce EMI by 20-30%<br>` +
                    `• <strong>Harvest Alignment</strong> (18 farmers): Defer 2 EMIs to post-harvest<br>` +
                    `• <strong>Personal Outreach</strong> (8 high-value): Branch manager home visits<br><br>` +
                    `<strong>ROI Calculation:</strong><br>` +
                    `• Intervention cost: ~₹2.5L (RO time + restructuring ops)<br>` +
                    `• Potential save: ₹1.8 Cr<br>` +
                    `• <strong>ROI: 72x</strong> — every ₹1 spent saves ₹72 in potential NPA`;
            } else if (ql.includes('hello') || ql.includes('hi') || ql.includes('hey')) {
                response = `<strong>👋 Hello!</strong><br><br>` +
                    `I'm SmaartAnalyst, your Voice Intelligence copilot. I've analyzed today's <strong>1,247 pre-collection calls</strong> and I'm ready to help.<br><br>` +
                    `<strong>Quick highlights:</strong><br>` +
                    `• 🔴 2 clusters need immediate attention (Warangal, Karimnagar)<br>` +
                    `• 💰 ₹1.8 Cr saveable with early intervention<br>` +
                    `• 📊 Financial stress up 8% — recommend proactive outreach<br><br>` +
                    `What would you like to explore?`;
            } else if (ql.includes('thank')) {
                response = `You're welcome! 🙏<br><br>Remember, every insight I provide is derived from actual borrower voice data — their stated reasons, patterns, and behaviors. This is the <strong>decision intelligence layer</strong> that turns calls into actionable strategy.<br><br>Anything else you'd like to analyze?`;
            } else {
                response = `<strong>🤖 SmaartAnalyst — Voice Intelligence</strong><br><br>` +
                    `I analyze your Day Minus 1 call data to surface patterns humans miss. Try asking:<br><br>` +
                    `• 📊 <em>"Why is Warangal high risk?"</em> — Root cause analysis<br>` +
                    `• 🔴 <em>"Compare all clusters"</em> — Side-by-side performance<br>` +
                    `• 📈 <em>"Show me the trend"</em> — Weekly patterns<br>` +
                    `• 💰 <em>"How much can we save?"</em> — Intervention ROI<br>` +
                    `• 📞 <em>"How many calls today?"</em> — Quick summary<br>` +
                    `• 🎯 <em>"Priority actions"</em> — Department-wise tasks<br><br>` +
                    `<strong>Or try:</strong> <em>"${['What are the priority actions for today?', 'Why is Warangal high risk?', 'Compare all clusters', 'How much can we save with intervention?'][Math.floor(Math.random()*4)]}"</em>`;
            }
            
            // Add thinking indicator then response
            const thinkingId = 'thinking-' + Date.now();
            box.innerHTML += `
                <div class="flex gap-3" id="${thinkingId}">
                    <div class="w-8 h-8 rounded-lg gradient-purple flex items-center justify-center flex-shrink-0 text-sm">🤖</div>
                    <div class="glass rounded-xl p-3">
                        <p class="text-sm text-gray-400"><span class="animate-pulse">Analyzing call data...</span></p>
                    </div>
                </div>
            `;
            box.scrollTop = box.scrollHeight;
            
            // Replace thinking with actual response
            setTimeout(() => {
                document.getElementById(thinkingId).innerHTML = `
                    <div class="w-8 h-8 rounded-lg gradient-purple flex items-center justify-center flex-shrink-0 text-sm">🤖</div>
                    <div class="glass rounded-xl p-3 max-w-[85%]">
                        <p class="text-sm">${response}</p>
                    </div>
                `;
                box.scrollTop = box.scrollHeight;
            }, 800 + Math.random() * 400);
            
            box.scrollTop = box.scrollHeight;
        }
        
        connectWS();
        loadConfig();
        loadIntelligence();
    </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def root():
    return HTML_PAGE


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
