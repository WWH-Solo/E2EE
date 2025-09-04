# E2EE.py - Secure Chat All-in-One with Background Image

# ===========================
# Standard Library Imports
# ===========================
import os
import sys
import time
import base64
import random
import string
import socket
import threading
import webbrowser

# ===========================
# Prefer Eventlet if available (better websockets)
# ===========================
ASYNC_MODE = "threading"
try:
    import eventlet  # type: ignore
    eventlet.monkey_patch()
    ASYNC_MODE = "eventlet"
except Exception:
    # Fall back to threading if eventlet isn't present
    ASYNC_MODE = "threading"

# ===========================
# Flask / SocketIO Imports
# ===========================
from flask import Flask, render_template_string, request, redirect, url_for, session
from flask_socketio import SocketIO, join_room

# ===========================
# Flask App & SocketIO Setup
# ===========================
app = Flask(__name__)
app.secret_key = os.urandom(32)

socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode=ASYNC_MODE,
    ping_interval=25,
    ping_timeout=60,
)

# ===========================
# In-memory storage
# ===========================
ROOMS = {}          # room_code: [usernames]
MESSAGES = {}       # room_code: [{'user':user,'msg':encrypted,'timestamp':ts}]
BLOCKED_USERS = set()
STORE_LOCK = threading.Lock()

# ===========================
# HTML Templates
# ===========================
LOGIN_HTML = """
<!DOCTYPE html>
<html>
<head>
<title>Secure Chat Login</title>
<meta name="viewport" content="width=device-width, initial-scale=1" />
<style>
body { background:#000; color:#0f0; font-family:'Courier New', monospace; display:flex; justify-content:center; align-items:center; height:100vh; margin:0;}
.login { border:2px solid #0f0; padding:30px; border-radius:10px; text-align:center; box-shadow:0 0 15px #0f0; width:320px; max-width:92vw;}
input { background:#000; border:1px solid #0f0; color:#0f0; padding:10px; margin:10px 0; width:100%; border-radius:6px;}
button { background:#0f0; color:#000; padding:12px; border:none; cursor:pointer; width:100%; border-radius:8px; font-weight:bold;}
button:hover { background:#050; color:#0f0; }
.small { color:#8f8; font-size:12px; margin-top:8px; }
</style>
</head>
<body>
<div class="login">
  <h2>🔒 Secure Chat Portal 🔒</h2>
  <form method="POST">
    <input type="text" name="username" placeholder="Enter your name" autocomplete="username" required>
    <input type="text" name="room" placeholder="Paste Room/Friend ID (optional)">
    <button type="submit">Enter Chat</button>
  </form>
  <div class="small">Leave Room ID blank to create a new room.</div>
</div>
</body>
</html>
"""

CHAT_HTML = """
<!DOCTYPE html>
<html>
<head>
<title>Secure Chat</title>
<meta name="viewport" content="width=device-width, initial-scale=1" />
<style>
:root { --g:#0f0; --bg: rgba(0,0,0,0.6); }
html, body { height:100%; }
body {
    background: url('https://i.imgur.com/kq3n1uR.jpg') no-repeat center center fixed;
    background-size: cover;
    color:var(--g);
    font-family:'Courier New', monospace;
    margin:0; padding:0;
    min-height:100vh;
    display:flex; flex-direction:column;
}
header { position:relative; text-align:center; font-size:1.5em; padding:16px 48px; text-shadow:0 0 10px var(--g); }
.logout { position:absolute; top:12px; right:12px; background:var(--g); color:#000; padding:10px 14px; border:none; border-radius:8px; cursor:pointer; font-weight:bold; }
.container { display:flex; flex-direction:column; gap:8px; padding:10px; }
#chat-box { flex:1; overflow:auto; margin:10px; border:1px solid var(--g); padding:10px; max-height:70vh; background: var(--bg); border-radius:10px; }
.message { padding:8px 10px; margin:6px 0; border-radius:10px; max-width:80%; word-break:break-word; display:inline-block; }
.sent { background:var(--g); color:#000; align-self:flex-end; text-align:right; }
.received { background:#063; color:var(--g); align-self:flex-start; text-align:left; }
.system { color:#9c9; font-style:italic; }
.controls { display:flex; gap:8px; align-items:center; padding:10px; }
#msg-input { flex:1; padding:10px; background:#000; color:var(--g); border:1px solid var(--g); border-radius:8px; }
button.send { padding:10px 14px; border-radius:8px; border:1px solid var(--g); background:#001900; color:var(--g); cursor:pointer; font-weight:bold; }
button.send:hover { background:#052; }
</style>
<script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.6.1/socket.io.min.js" integrity="sha512-R0L9f7rYpPoa6b5xPL7aHiM2YjP1YGrwWQITn08b6+mLllm0k3nSP0Q8nM7Hk8U8B9z7bE+0g1tZ2zUqhSItZw==" crossorigin="anonymous" referrerpolicy="no-referrer"></script>
<script>
const socket = io();
const username = "{{ username }}";
const room = "{{ room }}";

// "Toy" encryption: Base64(Morse). Do not use in real-world.
const MORSE = {'A':'.-','B':'-...','C':'-.-.','D':'-..','E':'.','F':'..-.','G':'--.','H':'....','I':'..','J':'.---','K':'-.-','L':'.-..','M':'--','N':'-.','O':'---','P':'.--.','Q':'--.-','R':'.-.','S':'...','T':'-','U':'..-','V':'...-','W':'.--','X':'-..-','Y':'-.--','Z':'--..',' ':'/','0':'-----','1':'.----','2':'..---','3':'...--','4':'....-','5':'.....','6':'-....','7':'--...','8':'---..','9':'----.'};
function toMorse(text){ return text.toUpperCase().split('').map(c=>MORSE[c]||c).join(' '); }
function fromMorse(code){ const rev = Object.fromEntries(Object.entries(MORSE).map(([k,v])=>[v,k])); return code.split(' ').map(c=>rev[c]||c).join(''); }
function encryptMessage(msg){ return btoa(unescape(encodeURIComponent(toMorse(msg)))); }
function decryptMessage(msg){ try{ return fromMorse(decodeURIComponent(escape(atob(msg)))); }catch(e){ return "✖ Unable to decrypt"; } }

function sendMessage(){
    const input = document.getElementById('msg-input');
    let msg = input.value;
    if(msg.trim()==="") return;
    let encrypted = encryptMessage(msg);
    socket.emit('message', {'user':username,'room':room,'msg':encrypted});
    input.value = '';
}

socket.emit('join', {'username': username, 'room': room});

socket.on('message', function(data){
    let chatBox = document.getElementById('chat-box');
    let wrapper = document.createElement('div');
    if(data.system){
        wrapper.className = 'system';
        wrapper.textContent = "[SYSTEM] " + data.text;
    } else {
        wrapper.className = 'message ' + (data.user===username ? 'sent' : 'received');
        wrapper.textContent = data.user + ": " + decryptMessage(data.msg);
    }
    chatBox.appendChild(wrapper);
    chatBox.scrollTop = chatBox.scrollHeight;
});

document.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') { sendMessage(); }
});
</script>
</head>
<body>
  <header>🔒 Room: {{ room }} | User: {{ username }} 🔒
    <a href="{{ url_for('logout') }}" class="logout">Logout</a>
  </header>
  <div class="container">
    <div id="chat-box"></div>
    <div class="controls">
      <input type="text" id="msg-input" placeholder="Type message...">
      <button class="send" onclick="sendMessage()">Send</button>
    </div>
  </div>
</body>
</html>
"""

# ===========================
# Utility Functions
# ===========================
def generate_room_code(length=6):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

def find_free_port():
    s = socket.socket()
    s.bind(('', 0))
    port = s.getsockname()[1]
    s.close()
    return port

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def clean_old_messages():
    while True:
        now = time.time()
        with STORE_LOCK:
            for room, msgs in list(MESSAGES.items()):
                MESSAGES[room] = [m for m in msgs if now - m['timestamp'] < 600]
        time.sleep(60)

def open_browser_links(port, local_ip):
    time.sleep(1)  # wait for server to start
    try:
        webbrowser.open(f"http://localhost:{port}")
        webbrowser.open(f"http://{local_ip}:{port}")
    except Exception:
        print("[!] Unable to auto-launch browser, open manually.")

threading.Thread(target=clean_old_messages, daemon=True).start()

# ===========================
# Routes
# ===========================
@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        room_code = request.form.get("room", "").strip()

        with STORE_LOCK:
            if room_code and room_code in ROOMS:
                ROOMS[room_code].append(username)
            else:
                room_code = generate_room_code()
                ROOMS[room_code] = [username]
                MESSAGES.setdefault(room_code, [])

        session["username"] = username
        session["room"] = room_code
        return redirect(url_for("chat"))
    return render_template_string(LOGIN_HTML)

@app.route("/chat")
def chat():
    if "username" not in session or "room" not in session:
        return redirect(url_for("login"))
    return render_template_string(CHAT_HTML, username=session["username"], room=session["room"])

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/healthz")
def healthz():
    return "ok", 200

# ===========================
# SocketIO Events
# ===========================
@socketio.on('join')
def on_join(data):
    room = data.get('room')
    username = data.get('username', 'user')
    join_room(room)
    socketio.emit('message', {'system': True, 'text': f"{username} joined {room}"}, room=room)

@socketio.on('message')
def on_message(data):
    room = data.get('room')
    user = data.get('user')
    encrypted = data.get('msg', '')

    if not room or not user:
        return

    with STORE_LOCK:
        if user in BLOCKED_USERS:
            return
        MESSAGES.setdefault(room, []).append({'user': user, 'msg': encrypted, 'timestamp': time.time()})

    socketio.emit('message', {'user': user, 'msg': encrypted}, room=room)

# ===========================
# Admin CLI (local only)
# ===========================
def admin_cli(port, local_ip):
    while True:
        os.system('clear' if os.name == 'posix' else 'cls')
        print(f"=== ADMIN CONTROL ===".ljust(50) + f"=== CHAT LINKS ===")
        print(f"1. List Online Users".ljust(50) + f"Local:  http://localhost:{port}")
        print(f"2. View Rooms & Messages".ljust(50) + f"LAN:    http://{local_ip}:{port}")
        print(f"3. Kick User\n4. Delete Messages in Room\n5. Block User\n6. Unblock User\n7. Exit CLI")
        choice = input("Enter choice: ").strip()
        if choice == "1":
            with STORE_LOCK:
                for r, u in ROOMS.items():
                    print(f"Room {r}: {', '.join(u) if u else '(empty)'}")
            input("Press Enter...")
        elif choice == "2":
            with STORE_LOCK:
                for r, m in MESSAGES.items():
                    print(f"\nRoom {r}:")
                    for msg in m:
                        print(f"[{time.ctime(msg['timestamp'])}] {msg['user']}: {msg['msg']}")
            input("Press Enter...")
        elif choice == "3":
            user = input("Username to kick: ").strip()
            kicked_rooms = []
            with STORE_LOCK:
                for r, u in ROOMS.items():
                    if user in u:
                        u.remove(user)
                        kicked_rooms.append(r)
            for r in kicked_rooms:
                socketio.emit('message', {'system': True, 'text': f"{user} was kicked by admin"}, room=r)
            input("Press Enter...")
        elif choice == "4":
            room = input("Room code: ").strip()
            with STORE_LOCK:
                if room in MESSAGES:
                    MESSAGES[room] = []
            socketio.emit('message', {'system': True, 'text': f"All messages cleared by admin"}, room=room)
            input("Press Enter...")
        elif choice == "5":
            user = input("Username to block: ").strip()
            with STORE_LOCK:
                BLOCKED_USERS.add(user)
            input("Press Enter...")
        elif choice == "6":
            user = input("Username to unblock: ").strip()
            with STORE_LOCK:
                BLOCKED_USERS.discard(user)
            input("Press Enter...")
        elif choice == "7":
            print("Exiting Admin CLI...")
            break
        else:
            print("Invalid option.")
            time.sleep(1)

# ===========================
# Main Runner
# ===========================
if __name__ == "__main__":
    # Bind to Railway's provided port if present
    port = int(os.environ.get("PORT", 5000))
    local_ip = get_local_ip()

    print(f"[*] Async mode: {ASYNC_MODE}")
    print(f"[*] Running Secure Chat on port {port}")
    print(f"Open in browser (localhost): http://localhost:{port}")
    print(f"Open in browser (LAN): http://{local_ip}:{port}")

    # Detect production environment (Railway)
    IS_PROD = bool(os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RAILWAY"))
    # If not attached to a real TTY, treat as prod (prevents CLI in containers)
    if not sys.stdout.isatty():
        IS_PROD = True

    if IS_PROD:
        # Production on Railway: no auto-browser, no interactive CLI
        socketio.run(app, host="0.0.0.0", port=port, debug=False)
    else:
        # Local dev: auto-browser + admin CLI
        threading.Thread(target=open_browser_links, args=(port, local_ip), daemon=True).start()
        threading.Thread(target=admin_cli, args=(port, local_ip), daemon=True).start()
        socketio.run(app, host="0.0.0.0", port=port, debug=False)
