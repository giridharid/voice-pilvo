"""
Fusion Finance Voice POC - Plivo Version
Pre-collection outbound IVR with dynamic multilingual audio
"""

from fastapi import FastAPI, Request, Form, Query
from fastapi.responses import Response, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import plivo
from plivo import plivoxml
import os
import logging
from datetime import datetime
import json

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Fusion Voice POC - Plivo")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files for audio
app.mount("/audio", StaticFiles(directory="audio"), name="audio")

# Plivo credentials (set these in Railway environment variables)
PLIVO_AUTH_ID = os.getenv("PLIVO_AUTH_ID", "MAOWI5YJMWNZQTYJQ0OS")
PLIVO_AUTH_TOKEN = os.getenv("PLIVO_AUTH_TOKEN", "your_auth_token_here")
PLIVO_PHONE_NUMBER = os.getenv("PLIVO_PHONE_NUMBER", "+918031320387")  # Your Plivo number

# Base URL for callbacks (Railway deployment)
BASE_URL = os.getenv("BASE_URL", "https://voice-poc-plivo-production.up.railway.app")

# Audio base URL (can point to existing server with audio files)
AUDIO_BASE_URL = os.getenv("AUDIO_BASE_URL", BASE_URL)

# Supported languages
LANGUAGES = {
    "hi-IN": "Hindi",
    "ta-IN": "Tamil", 
    "te-IN": "Telugu",
    "kn-IN": "Kannada",
    "mr-IN": "Marathi",
    "en-IN": "English"
}

# Store call data
call_data = {}


# ============== Demo UI ==============

@app.get("/", response_class=HTMLResponse)
async def demo_ui():
    """Demo UI for triggering calls"""
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Fusion Voice POC - Smaartanalyst</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
        <style>
            * { box-sizing: border-box; margin: 0; padding: 0; }
            body { 
                font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
                background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
                min-height: 100vh; color: #fff;
            }
            .header {
                display: flex; justify-content: space-between; align-items: center;
                padding: 16px 24px; border-bottom: 1px solid rgba(255,255,255,0.1);
            }
            .logo { display: flex; align-items: center; gap: 10px; }
            .logo-icon { 
                width: 36px; height: 36px; background: linear-gradient(135deg, #f97316, #ea580c);
                border-radius: 8px; display: flex; align-items: center; justify-content: center;
                font-weight: 700; font-size: 18px;
            }
            .logo-text { font-size: 20px; font-weight: 600; }
            .logo-text span { color: #f97316; }
            .badge { 
                background: rgba(34, 197, 94, 0.2); color: #22c55e; 
                padding: 6px 12px; border-radius: 20px; font-size: 12px; font-weight: 500;
            }
            .container { max-width: 480px; margin: 0 auto; padding: 40px 20px; }
            .hero { text-align: center; margin-bottom: 32px; }
            .hero h1 { font-size: 28px; margin-bottom: 8px; }
            .hero h1 span { color: #f97316; }
            .hero p { color: #94a3b8; font-size: 14px; }
            .card {
                background: rgba(255,255,255,0.05); border-radius: 16px;
                padding: 24px; margin-bottom: 20px; border: 1px solid rgba(255,255,255,0.1);
            }
            .card-title { font-size: 12px; color: #94a3b8; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 16px; }
            label { display: block; margin-bottom: 6px; color: #e2e8f0; font-size: 14px; font-weight: 500; }
            input, select {
                width: 100%; padding: 14px 16px; border-radius: 10px; border: 1px solid rgba(255,255,255,0.15);
                background: rgba(255,255,255,0.05); color: #fff; font-size: 15px; margin-bottom: 16px;
                transition: border-color 0.2s;
            }
            input:focus, select:focus { outline: none; border-color: #f97316; background: rgba(255,255,255,0.08); }
            input::placeholder { color: #64748b; }
            select option { background: #1a1a2e; color: #fff; }
            button {
                width: 100%; padding: 16px; border: none; border-radius: 10px;
                background: linear-gradient(135deg, #f97316 0%, #ea580c 100%);
                color: white; font-size: 16px; font-weight: 600; cursor: pointer;
                transition: transform 0.2s, box-shadow 0.2s;
                display: flex; align-items: center; justify-content: center; gap: 8px;
            }
            button:hover { transform: translateY(-2px); box-shadow: 0 10px 30px rgba(249,115,22,0.3); }
            button:disabled { opacity: 0.6; cursor: not-allowed; transform: none; box-shadow: none; }
            .status {
                margin-top: 20px; padding: 16px; border-radius: 12px;
                background: rgba(255,255,255,0.05); display: none;
            }
            .status.show { display: block; }
            .status.success { border-left: 4px solid #22c55e; }
            .status.error { border-left: 4px solid #ef4444; }
            .status strong { display: block; margin-bottom: 8px; }
            .log { 
                font-family: 'SF Mono', Monaco, monospace; font-size: 12px; 
                color: #94a3b8; background: rgba(0,0,0,0.2); padding: 12px;
                border-radius: 8px; margin-top: 10px;
            }
            .log div { margin-bottom: 4px; }
            .log span { color: #64748b; }
            .footer {
                text-align: center; padding: 24px; color: #64748b; font-size: 12px;
                border-top: 1px solid rgba(255,255,255,0.05); margin-top: 40px;
            }
            .footer a { color: #f97316; text-decoration: none; }
            .provider-badge {
                display: inline-flex; align-items: center; gap: 6px;
                background: rgba(59, 130, 246, 0.15); color: #60a5fa;
                padding: 4px 10px; border-radius: 6px; font-size: 11px; font-weight: 500;
                margin-left: 8px;
            }
        </style>
    </head>
    <body>
        <div class="header">
            <div class="logo">
                <div class="logo-icon">S</div>
                <div class="logo-text">Smaart<span>analyst</span></div>
            </div>
            <div class="badge">● Live POC</div>
        </div>
        
        <div class="container">
            <div class="hero">
                <h1>Pre-Collection <span>Voice AI</span></h1>
                <p>Day Minus 1 Borrower Confirmation <span class="provider-badge">⚡ Plivo</span></p>
            </div>
            
            <div class="card">
                <div class="card-title">Call Configuration</div>
                
                <label>Phone Number</label>
                <input type="tel" id="phone" placeholder="+91 98492 70361" value="+919849270361">
                
                <label>Language</label>
                <select id="language">
                    <option value="hi-IN">🇮🇳 Hindi</option>
                    <option value="ta-IN">🇮🇳 Tamil</option>
                    <option value="te-IN" selected>🇮🇳 Telugu</option>
                    <option value="kn-IN">🇮🇳 Kannada</option>
                    <option value="mr-IN">🇮🇳 Marathi</option>
                    <option value="en-IN">🇮🇳 English</option>
                </select>
                
                <label>Loan ID</label>
                <input type="text" id="loan_id" placeholder="LN123456" value="LN789012">
                
                <button onclick="makeCall()" id="callBtn">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/>
                    </svg>
                    Make Call
                </button>
            </div>
            
            <div class="status" id="status"></div>
        </div>
        
        <div class="footer">
            Powered by <a href="https://acquink.com" target="_blank">Acquink Technologies</a><br>
            Decision Intelligence for Enterprise
        </div>
        
        <script>
            let isCallInProgress = false;
            
            async function makeCall() {
                if (isCallInProgress) return;
                
                const btn = document.getElementById('callBtn');
                const status = document.getElementById('status');
                const phone = document.getElementById('phone').value;
                const language = document.getElementById('language').value;
                const loanId = document.getElementById('loan_id').value;
                
                isCallInProgress = true;
                btn.disabled = true;
                btn.innerHTML = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="animation: spin 1s linear infinite;"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg> Calling...';
                status.className = 'status show';
                status.innerHTML = '<strong>⏳ Initiating call...</strong>';
                
                try {
                    const response = await fetch('/api/call', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ phone, language, loan_id: loanId })
                    });
                    
                    const data = await response.json();
                    
                    if (data.success) {
                        status.className = 'status show success';
                        status.innerHTML = `
                            <strong>✅ Call initiated successfully!</strong>
                            <div class="log">
                                <div><span>Call UUID:</span> ${data.call_uuid}</div>
                                <div><span>To:</span> ${phone}</div>
                                <div><span>Language:</span> ${language}</div>
                                <div><span>Loan ID:</span> ${loanId}</div>
                            </div>
                        `;
                    } else {
                        throw new Error(data.error || 'Call failed');
                    }
                } catch (err) {
                    status.className = 'status show error';
                    status.innerHTML = `<strong>❌ Error</strong><div class="log">${err.message}</div>`;
                }
                
                setTimeout(() => {
                    isCallInProgress = false;
                    btn.disabled = false;
                    btn.innerHTML = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/></svg> Make Call';
                }, 3000);
            }
        </script>
        <style>@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }</style>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


# ============== API Endpoints ==============

@app.post("/api/call")
async def make_call(request: Request):
    """Trigger outbound call via Plivo"""
    try:
        body = await request.json()
        phone = body.get("phone", "").strip()
        language = body.get("language", "en-IN")
        loan_id = body.get("loan_id", "unknown")
        
        # Format phone number
        phone = phone.replace(" ", "").replace("-", "")
        if not phone.startswith("+"):
            if phone.startswith("91"):
                phone = "+" + phone
            else:
                phone = "+91" + phone
        
        logger.info(f"Making call to {phone} in {language}")
        
        # Create Plivo client
        client = plivo.RestClient(PLIVO_AUTH_ID, PLIVO_AUTH_TOKEN)
        
        # Answer URL with language parameter
        answer_url = f"{BASE_URL}/plivo/answer?lang={language}&loan_id={loan_id}"
        hangup_url = f"{BASE_URL}/plivo/hangup"
        
        # Make the call
        response = client.calls.create(
            from_=PLIVO_PHONE_NUMBER,
            to_=phone,
            answer_url=answer_url,
            answer_method="POST",
            hangup_url=hangup_url,
            hangup_method="POST"
        )
        
        call_uuid = response.request_uuid
        
        # Store call data
        call_data[call_uuid] = {
            "phone": phone,
            "language": language,
            "loan_id": loan_id,
            "status": "initiated",
            "started_at": datetime.now().isoformat()
        }
        
        logger.info(f"Call initiated: {call_uuid}")
        
        return JSONResponse({
            "success": True,
            "call_uuid": call_uuid,
            "phone": phone,
            "language": language
        })
        
    except Exception as e:
        logger.error(f"Call failed: {str(e)}")
        return JSONResponse({
            "success": False,
            "error": str(e)
        }, status_code=500)


# ============== Plivo Callbacks ==============

@app.post("/plivo/answer")
async def plivo_answer(
    request: Request,
    lang: str = Query("en-IN"),
    loan_id: str = Query("unknown")
):
    """
    Called when customer answers. 
    Play greeting audio and gather DTMF input.
    """
    form_data = await request.form()
    call_uuid = form_data.get("CallUUID", "unknown")
    from_number = form_data.get("From", "unknown")
    to_number = form_data.get("To", "unknown")
    
    logger.info(f"Call answered: {call_uuid}, lang={lang}")
    
    # Build Plivo XML response
    response = plivoxml.ResponseElement()
    
    # GetDigits with greeting audio
    greeting_url = f"{AUDIO_BASE_URL}/audio/{lang}/01_greeting.wav"
    action_url = f"{BASE_URL}/plivo/gather?lang={lang}&loan_id={loan_id}"
    
    get_digits = plivoxml.GetDigitsElement(
        action=action_url,
        method="POST",
        timeout=10,
        num_digits=1,
        retries=2
    )
    get_digits.add(plivoxml.PlayElement(greeting_url))
    response.add(get_digits)
    
    # If no input, play unclear message
    unclear_url = f"{AUDIO_BASE_URL}/audio/{lang}/05_unclear.wav"
    response.add(plivoxml.PlayElement(unclear_url))
    response.add(plivoxml.HangupElement())
    
    xml_response = response.to_string()
    logger.info(f"Answer XML: {xml_response}")
    
    return Response(content=xml_response, media_type="application/xml")


@app.post("/plivo/gather")
async def plivo_gather(
    request: Request,
    lang: str = Query("en-IN"),
    loan_id: str = Query("unknown")
):
    """
    Handle DTMF input from customer.
    """
    form_data = await request.form()
    call_uuid = form_data.get("CallUUID", "unknown")
    digits = form_data.get("Digits", "")
    
    logger.info(f"DTMF received: {digits} for call {call_uuid}")
    
    response = plivoxml.ResponseElement()
    
    if digits == "1":
        # Confirmed payment
        logger.info(f"Call {call_uuid}: Payment CONFIRMED")
        audio_url = f"{AUDIO_BASE_URL}/audio/{lang}/02_confirmed.wav"
        response.add(plivoxml.PlayElement(audio_url))
        response.add(plivoxml.HangupElement())
        
        # Update call data
        if call_uuid in call_data:
            call_data[call_uuid]["response"] = "confirmed"
            call_data[call_uuid]["digit"] = "1"
        
    elif digits == "2":
        # Wants to reschedule - ask reason
        logger.info(f"Call {call_uuid}: Asking for REASON")
        audio_url = f"{AUDIO_BASE_URL}/audio/{lang}/03_ask_reason.wav"
        action_url = f"{BASE_URL}/plivo/reason?lang={lang}&loan_id={loan_id}"
        
        get_digits = plivoxml.GetDigitsElement(
            action=action_url,
            method="POST",
            timeout=10,
            num_digits=1
        )
        get_digits.add(plivoxml.PlayElement(audio_url))
        response.add(get_digits)
        
        # Fallback
        unclear_url = f"{AUDIO_BASE_URL}/audio/{lang}/05_unclear.wav"
        response.add(plivoxml.PlayElement(unclear_url))
        response.add(plivoxml.HangupElement())
        
    else:
        # Invalid input
        logger.info(f"Call {call_uuid}: INVALID input '{digits}'")
        unclear_url = f"{AUDIO_BASE_URL}/audio/{lang}/05_unclear.wav"
        response.add(plivoxml.PlayElement(unclear_url))
        response.add(plivoxml.HangupElement())
    
    xml_response = response.to_string()
    logger.info(f"Gather XML: {xml_response}")
    
    return Response(content=xml_response, media_type="application/xml")


@app.post("/plivo/reason")
async def plivo_reason(
    request: Request,
    lang: str = Query("en-IN"),
    loan_id: str = Query("unknown")
):
    """
    Handle reschedule reason DTMF.
    """
    form_data = await request.form()
    call_uuid = form_data.get("CallUUID", "unknown")
    digits = form_data.get("Digits", "")
    
    reasons = {
        "1": "salary_delay",
        "2": "medical_emergency", 
        "3": "family_issue",
        "4": "other"
    }
    
    reason = reasons.get(digits, "unknown")
    logger.info(f"Call {call_uuid}: Reschedule reason = {reason}")
    
    response = plivoxml.ResponseElement()
    
    # Play confirmation
    audio_url = f"{AUDIO_BASE_URL}/audio/{lang}/04_reschedule_confirm.wav"
    response.add(plivoxml.PlayElement(audio_url))
    response.add(plivoxml.HangupElement())
    
    # Update call data
    if call_uuid in call_data:
        call_data[call_uuid]["response"] = "reschedule"
        call_data[call_uuid]["reason"] = reason
        call_data[call_uuid]["digit"] = digits
    
    xml_response = response.to_string()
    logger.info(f"Reason XML: {xml_response}")
    
    return Response(content=xml_response, media_type="application/xml")


@app.post("/plivo/hangup")
async def plivo_hangup(request: Request):
    """Handle call hangup callback"""
    form_data = await request.form()
    call_uuid = form_data.get("CallUUID", "unknown")
    duration = form_data.get("Duration", "0")
    status = form_data.get("CallStatus", "unknown")
    
    logger.info(f"Call ended: {call_uuid}, duration={duration}s, status={status}")
    
    if call_uuid in call_data:
        call_data[call_uuid]["ended_at"] = datetime.now().isoformat()
        call_data[call_uuid]["duration"] = duration
        call_data[call_uuid]["final_status"] = status
    
    return JSONResponse({"status": "ok"})


# ============== Intelligence Dashboard ==============

@app.get("/api/intelligence")
async def get_intelligence():
    """Mock intelligence data for dashboard"""
    return JSONResponse({
        "summary": {
            "total_calls": 1247,
            "confirmed": 847,
            "reschedule": 312,
            "no_response": 88,
            "confirmation_rate": 67.9
        },
        "clusters": [
            {"name": "Warangal Rural", "calls": 234, "confirmed": 178, "rate": 76.1, "risk": "low"},
            {"name": "Shad Nagar", "calls": 189, "confirmed": 134, "rate": 70.9, "risk": "low"},
            {"name": "Karimnagar", "calls": 312, "confirmed": 198, "rate": 63.5, "risk": "medium"},
            {"name": "Nizamabad", "calls": 267, "confirmed": 156, "rate": 58.4, "risk": "medium"},
            {"name": "Medak", "calls": 245, "confirmed": 181, "rate": 73.9, "risk": "low"}
        ],
        "reschedule_reasons": {
            "salary_delay": 156,
            "medical_emergency": 67,
            "family_issue": 54,
            "other": 35
        }
    })


@app.get("/api/calls")
async def get_calls():
    """Get recent call data"""
    return JSONResponse({"calls": list(call_data.values())})


# ============== Health Check ==============

@app.get("/health")
async def health():
    return {"status": "ok", "provider": "plivo", "timestamp": datetime.now().isoformat()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
