"""
Fusion Finance Voice Intelligence POC - PLIVO VERSION
Smaartbrand UI Theme - Orange/Blue
Real Plivo calls + Intelligence Dashboard

Run: python main.py
"""

import os
import json
import asyncio
import random
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict
from collections import defaultdict

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException
from fastapi.responses import HTMLResponse, Response, JSONResponse
from pydantic import BaseModel
import uvicorn
import plivo

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
        return {"success": True, "call_id": call_id}
        
    except Exception as e:
        print(f"Plivo error: {e}")
        del active_calls[call_id]
        return {"success": False, "error": str(e)}


async def add_transcript(call_id: str, speaker: str, text: str, dtmf: str = None, reason: str = None):
    if call_id not in active_calls:
        return
    
    entry = {
        "timestamp": datetime.now().strftime("%H:%M:%S"),
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

@app.on_event("startup")
async def startup():
    global APP_BASE_URL, AUDIO_BASE_URL
    APP_BASE_URL = get_base_url()
    AUDIO_BASE_URL = get_audio_base_url()
    print("=" * 60)
    print("Fusion Finance Voice Intelligence - PLIVO")
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
    
    print(f"=== PLIVO ANSWER: {call_id} ===")
    
    if call_id in active_calls:
        call = active_calls[call_id]
        lang = call.get("language", lang)
        await add_transcript(call_id, "Agent", f"Greeting - {RO_NAMES.get(lang, 'RO')} tomorrow")
        call["state"] = CallState.WAIT_AVAILABILITY
    
    audio_url = f"{AUDIO_BASE_URL}/audio/{lang}/01_greeting.wav"
    action_url = f"{APP_BASE_URL}/plivo/gather?call_id={call_id}&lang={lang}"
    
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <GetDigits action="{action_url}" method="POST" timeout="10" numDigits="1" retries="1">
        <Play>{audio_url}</Play>
    </GetDigits>
    <Play>{AUDIO_BASE_URL}/audio/{lang}/05_unclear.wav</Play>
    <Hangup/>
</Response>"""
    
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
        await add_transcript(call_id, "Borrower", f"Pressed {digits}", dtmf=digits)
    
    if digits == "1":
        # Confirmed
        if call_id in active_calls:
            await add_transcript(call_id, "Agent", "Confirmed - visit tomorrow")
            active_calls[call_id]["state"] = CallState.COMPLETED
            active_calls[call_id]["outcome"] = "AVAILABLE"
        
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Play>{AUDIO_BASE_URL}/audio/{lang}/02_confirmed.wav</Play>
    <Hangup/>
</Response>"""
        
    elif digits == "2":
        # Reschedule - ask reason
        if call_id in active_calls:
            await add_transcript(call_id, "Agent", "Asking decline reason")
            active_calls[call_id]["state"] = CallState.WAIT_REASON
        
        action_url = f"{APP_BASE_URL}/plivo/reason?call_id={call_id}&lang={lang}"
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <GetDigits action="{action_url}" method="POST" timeout="10" numDigits="1" retries="1">
        <Play>{AUDIO_BASE_URL}/audio/{lang}/03_ask_reason.wav</Play>
    </GetDigits>
    <Play>{AUDIO_BASE_URL}/audio/{lang}/05_unclear.wav</Play>
    <Hangup/>
</Response>"""
        
    else:
        # Unclear - repeat
        if call_id in active_calls:
            await add_transcript(call_id, "Agent", "Unclear - repeating")
        
        action_url = f"{APP_BASE_URL}/plivo/gather?call_id={call_id}&lang={lang}"
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <GetDigits action="{action_url}" method="POST" timeout="10" numDigits="1" retries="1">
        <Play>{AUDIO_BASE_URL}/audio/{lang}/05_unclear.wav</Play>
    </GetDigits>
    <Hangup/>
</Response>"""
    
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
        await add_transcript(call_id, "Borrower", f"Pressed {digits}", dtmf=digits, reason=reason_info[0])
        await add_transcript(call_id, "Agent", f"Rescheduling - reason: {reason_info[0]}")
        active_calls[call_id]["state"] = CallState.COMPLETED
        active_calls[call_id]["outcome"] = "DECLINED"
        active_calls[call_id]["decline_reason"] = reason_info[1]
    
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Play>{AUDIO_BASE_URL}/audio/{lang}/04_reschedule_confirm.wav</Play>
    <Hangup/>
</Response>"""
    
    return Response(content=xml, media_type="application/xml")


@app.post("/plivo/hangup")
async def plivo_hangup(request: Request):
    """Handle call hangup"""
    params = dict(request.query_params)
    call_id = params.get("call_id", "default")
    print(f"=== PLIVO HANGUP: {call_id} ===")
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
                            <div class="text-center text-gray-500 py-20">
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
    <div id="chatPanel" class="fixed right-0 top-0 h-full w-[420px] glass-dark z-50 flex flex-col transform translate-x-full transition-transform duration-300">
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
            if (box.querySelector('.text-gray-500')) box.innerHTML = '';
            const isAgent = entry.speaker === 'Agent';
            let tags = '';
            if (entry.dtmf) tags += `<span class="px-2 py-0.5 rounded text-xs bg-blue-500/20 text-blue-300 ml-2">DTMF: ${entry.dtmf}</span>`;
            if (entry.reason) tags += `<span class="px-2 py-0.5 rounded text-xs bg-orange-500/20 text-orange-300 ml-2">${entry.reason}</span>`;
            box.insertAdjacentHTML('beforeend', `
                <div class="message ${isAgent ? 'agent' : 'borrower'} rounded-xl p-4">
                    <div class="flex justify-between items-center mb-2">
                        <span class="font-semibold text-sm ${isAgent ? 'text-blue-400' : 'text-orange-400'}">${entry.speaker}</span>
                        <span class="text-xs text-gray-500">${entry.timestamp}</span>
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
            document.getElementById('transcript').innerHTML = '<div class="text-center text-gray-500 py-10">Connecting...</div>';
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
        
        function askChat(preset = null) {
            const input = document.getElementById('chatInput');
            const query = preset || input.value.trim();
            if (!query) return;
            input.value = '';
            const box = document.getElementById('chatMessages');
            box.innerHTML += `<div class="flex gap-3 justify-end"><div class="glass rounded-xl p-3 max-w-[85%]"><p class="text-sm">${query}</p></div></div>`;
            const ql = query.toLowerCase();
            let response;
            if (ql.includes('cluster') || ql.includes('risk') || ql.includes('village')) {
                const highRisk = Object.entries(intelData.clusters).filter(([,c]) => c.alert === 'HIGH');
                const medRisk = Object.entries(intelData.clusters).filter(([,c]) => c.alert === 'MEDIUM');
                response = `<strong>🔴 High Risk (${highRisk.length}):</strong><br>` + highRisk.map(([name, c]) => `• <strong>${name}</strong>: ${c.financial_stress} stress cases`).join('<br>') + `<br><br><strong>🟡 Medium Risk (${medRisk.length}):</strong><br>` + medRisk.map(([name, c]) => `• ${name}: ${c.frequent} decliners`).join('<br>');
            } else if (ql.includes('decline') || ql.includes('reason')) {
                const reasons = Object.entries(intelData.decline_reasons).sort((a,b) => b[1] - a[1]);
                response = `<strong>📊 Decline Reasons:</strong><br>` + reasons.map(([r, count]) => `• ${r.replace(/_/g, ' ')}: <strong>${count}</strong>`).join('<br>');
            } else if (ql.includes('frequent') || ql.includes('decliner')) {
                const top = intelData.frequent_decliners.slice(0, 5);
                response = `<strong>🚨 Top Decliners:</strong><br>` + top.map((b,i) => `${i+1}. <strong>${b.id}</strong> — ${b.decline_count}x, Risk: ${b.risk_score}`).join('<br>');
            } else if (ql.includes('npa') || ql.includes('predict')) {
                response = `<strong>📈 NPA Outlook:</strong><br>• Current: <strong>${intelData.npa.current}%</strong><br>• Predicted: <strong>${intelData.npa.predicted}%</strong><br>• At-Risk: <strong>₹${intelData.npa.at_risk}</strong><br>• Saveable: <strong>₹${intelData.npa.savings}</strong>`;
            } else if (ql.includes('action') || ql.includes('priority') || ql.includes('today')) {
                response = `<strong>🎯 Today's Priorities:</strong><br><br><strong>Collections:</strong><br>• Call 47 frequent decliners<br>• Prepare restructuring options<br><br><strong>Field Ops:</strong><br>• Deploy RO to Warangal Rural<br>• Evening visits for traders`;
            } else {
                response = `<strong>🤖 SmaartAnalyst</strong><br>Try: decline reasons, cluster risk, frequent decliners, NPA prediction, priority actions`;
            }
            setTimeout(() => {
                box.innerHTML += `<div class="flex gap-3"><div class="w-8 h-8 rounded-lg gradient-purple flex items-center justify-center flex-shrink-0 text-sm">🤖</div><div class="glass rounded-xl p-3 max-w-[85%]"><p class="text-sm">${response}</p></div></div>`;
                box.scrollTop = box.scrollHeight;
            }, 500);
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
