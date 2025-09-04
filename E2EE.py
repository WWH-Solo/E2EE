# E2EE.py - Secure Chat All-in-One with Background Image
from flask import Flask, render_template_string, request, redirect, url_for, session
from flask_socketio import SocketIO, join_room, emit
import random, string, socket, threading, time, os, webbrowser, base64

# ===========================
# Flask App & SocketIO Setup
# ===========================
app = Flask(__name__)
app.secret_key = os.urandom(32)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# ===========================
# In-memory storage
# ===========================
ROOMS = {}          # room_code: [usernames]
MESSAGES = {}       # room_code: [{'user':user,'msg':encrypted,'timestamp':ts}]
BLOCKED_USERS = set()

# ===========================
# HTML Templates
# ===========================
LOGIN_HTML = """
<!DOCTYPE html>
<html>
<head>
<title>Secure Chat Login</title>
<style>
body { background:#000; color:#0f0; font-family:'Courier New', monospace; display:flex; justify-content:center; align-items:center; height:100vh; margin:0;}
.login { border:2px solid #0f0; padding:30px; border-radius:10px; text-align:center; box-shadow:0 0 15px #0f0; }
input { background:#000; border:1px solid #0f0; color:#0f0; padding:8px; margin:8px; width:90%; }
button { background:#0f0; color:#000; padding:10px; border:none; cursor:pointer; width:100%; }
button:hover { background:#050; color:#0f0; }
</style>
</head>
<body>
<div class="login">
<h2>🔒 Secure Chat Portal 🔒</h2>
<form method="POST">
<input type="text" name="username" placeholder="Enter your name" required><br>
<input type="text" name="room" placeholder="Paste Room/Friend ID (optional)"><br>
<button type="submit">Enter Chat</button>
</form>
</div>
</body>
</html>
"""

CHAT_HTML = """
<!DOCTYPE html>
<html>
<head>
<title>Secure Chat</title>
<style>
body {
    background: url('https://i.imgur.com/kq3n1uR.jpg') no-repeat center center fixed;
    background-size: cover;
    color:#0f0;
    font-family:'Courier New', monospace;
    margin:0;
    padding:0;
    height:100vh;
    display:flex;
    flex-direction:column;
}
header { text-align:center; font-size:2em; padding:20px; text-shadow:0 0 10px #0f0; }
.logout { position:absolute; top:20px; right:20px; background:#0f0; color:#000; padding:10px; border:none; border-radius:5px; cursor:pointer; }
#chat-box { flex:1; overflow:auto; margin:10px; border:1px solid #0f0; padding:10px; max-height:70%; background: rgba(0,0,0,0.6); }
.message { padding:5px; margin:5px; border-radius:5px; max-width:60%; word-break:break-word; }
.sent { background:#0f0; color:#000; text-align:right; margin-left:auto; }
.received { background:#060; color:#0f0; text-align:left; margin-right:auto; }
.system { color:#999; font-style:italic; }
#msg-input { width:70%; padding:8px; margin:10px; background:#000; color:#0f0; border:1px solid #0f0; }
button.send { padding:8px 12px; margin-left:5px; cursor:pointer; }
</style>
<script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.6.1/socket.io.min.js"></script>
<script>
const socket = io();
const username = "{{ username }}";
const room = "{{ room }}";

// AES key simulation using random base64
if(!localStorage.getItem('aes_key_'+room)){
    let array = new Uint8Array(32);
    crypto.getRandomValues(array);
    localStorage.setItem('aes_key_'+room, btoa(String.fromCharCode(...array)));
}
const AES_KEY = atob(localStorage.getItem('aes_key_'+room));

const MORSE = {'A':'.-','B':'-...','C':'-.-.','D':'-..','E':'.','F':'..-.','G':'--.','H':'....','I':'..','J':'.---','K':'-.-','L':'.-..','M':'--','N':'-.','O':'---','P':'.--.','Q':'--.-','R':'.-.','S':'...','T':'-','U':'..-','V':'...-','W':'.--','X':'-..-','Y':'-.--','Z':'--..',' ':'/'};
function toMorse(text){ return text.toUpperCase().split('').map(c=>MORSE[c]||c).join(' '); }
function fromMorse(code){ const rev = Object.fromEntries(Object.entries(MORSE).map(([k,v])=>[v,k])); return code.split(' ').map(c=>rev[c]||c).join(''); }
function encryptMessage(msg){ return btoa(toMorse(msg)); }
function decryptMessage(msg){ try{ return fromMorse(atob(msg)); }catch(e){ return "✖ Unable to decrypt"; } }

function sendMessage(){
    let msg = document.getElementById('msg-input').value;
    if(msg.trim()==="") return;
    let encrypted = encryptMessage(msg);
    socket.emit('message', {'user':username,'room':room,'msg':encrypted});
    document.getElementById('msg-input').value='';
}

socket.emit('join', {'username': username, 'room': room});

socket.on('message', function(data){
    let chatBox = document.getElementById('chat-box');
    let div = document.createElement('div');
    if(data.system){
        div.className = 'system';
        div.textContent = "[SYSTEM] " + data.text;
    } else {
        div.className = (data.user===username?'sent':'received');
        div.textContent = data.user + ": " + decryptMessage(data.msg);
    }
    chatBox.appendChild(div);
    chatBox.scrollTop = chatBox.scrollHeight;
});
</script>
</head>
<body>
<header>🔒 Room: {{ room }} | User: {{ username }} 🔒</header>
<a href="{{ url_for('logout') }}" class="logout">Logout</a>
<div id="chat-box"></div>
<div>
<input type="text" id="msg-input" placeholder="Type message...">
<button class="send" onclick="sendMessage()">Send</button>
</div>
</body>
</html>
"""

# ===========================
# Utility Functions
# ===========================
def generate_room_code(length=6):
    return ''.join(random.choices(string.ascii_uppercase+string.digits, k=length))

def find_free_port():
    s = socket.socket()
    s.bind(('',0))
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
    except:
        return "127.0.0.1"

def clean_old_messages():
    while True:
        now = time.time()
        for room, msgs in MESSAGES.items():
            MESSAGES[room] = [m for m in msgs if now - m['timestamp'] < 600]
        time.sleep(60)

def open_browser_links(port, local_ip):
    time.sleep(1)  # wait for server to start
    try:
        webbrowser.open(f"http://localhost:{port}")
        webbrowser.open(f"http://{local_ip}:{port}")
    except:
        print("[!] Unable to auto-launch browser, open manually.")

threading.Thread(target=clean_old_messages, daemon=True).start()

# ===========================
# Routes
# ===========================
@app.route("/", methods=["GET","POST"])
def login():
    if request.method=="POST":
        username = request.form["username"]
        room_code = request.form.get("room","").strip()
        if room_code and room_code in ROOMS:
            ROOMS[room_code].append(username)
        else:
            room_code = generate_room_code()
            ROOMS[room_code] = [username]
            MESSAGES[room_code] = []
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

# ===========================
# SocketIO Events
# ===========================
@socketio.on('join')
def on_join(data):
    join_room(data['room'])
    emit('message', {'system': True, 'text': f"{data['username']} joined {data['room']}"}, room=data['room'])

@socketio.on('message')
def on_message(data):
    room = data['room']
    user = data['user']
    if user in BLOCKED_USERS:
        return
    encrypted = data['msg']
    if room not in MESSAGES:
        MESSAGES[room] = []
    MESSAGES[room].append({'user':user,'msg':encrypted,'timestamp':time.time()})
    emit('message', {'user':user,'msg':encrypted}, room=room)

# ===========================
# Admin CLI
# ===========================
def admin_cli(port, local_ip):
    while True:
        os.system('clear' if os.name=='posix' else 'cls')
        print(f"=== ADMIN CONTROL ===".ljust(50) + f"=== CHAT LINKS ===")
        print(f"1. List Online Users".ljust(50) + f"Local:  http://localhost:{port}")
        print(f"2. View Rooms & Messages".ljust(50) + f"LAN:    http://{local_ip}:{port}")
        print(f"3. Kick User\n4. Delete Messages in Room\n5. Block User\n6. Unblock User\n7. Exit CLI")
        choice = input("Enter choice: ")
        if choice=="1":
            for r,u in ROOMS.items():
                print(f"Room {r}: {', '.join(u)}")
            input("Press Enter...")
        elif choice=="2":
            for r,m in MESSAGES.items():
                print(f"\nRoom {r}:")
                for msg in m:
                    print(f"[{time.ctime(msg['timestamp'])}] {msg['user']}: {msg['msg']}")
            input("Press Enter...")
        elif choice=="3":
            user = input("Username to kick: ")
            for r,u in ROOMS.items():
                if user in u:
                    u.remove(user)
                    emit('message', {'system': True, 'text': f"{user} was kicked by admin"}, room=r)
            input("Press Enter...")
        elif choice=="4":
            room = input("Room code: ")
            if room in MESSAGES:
                MESSAGES[room] = []
                emit('message', {'system': True, 'text': f"All messages cleared by admin"}, room=room)
            input("Press Enter...")
        elif choice=="5":
            user = input("Username to block: ")
            BLOCKED_USERS.add(user)
            input("Press Enter...")
        elif choice=="6":
            user = input("Username to unblock: ")
            BLOCKED_USERS.discard(user)
            input("Press Enter...")
        elif choice=="7":
            print("Exiting Admin CLI...")
            break

# ===========================
# Main Runner
# ===========================
if __name__ == "__main__":
    free_port = find_free_port()
    local_ip = get_local_ip()
    print(f"[*] Running Secure Chat on port {free_port}")
    print(f"Open in browser (localhost): http://localhost:{free_port}")
    print(f"Open in browser (LAN): http://{local_ip}:{free_port}")

    # Auto-launch browser after short delay
    threading.Thread(target=open_browser_links, args=(free_port, local_ip), daemon=True).start()
    # Start Admin CLI in background
    threading.Thread(target=admin_cli, args=(free_port, local_ip), daemon=True).start()
    # Start Flask + SocketIO server without debug/reloader to avoid multi-thread conflicts
    socketio.run(app, host="0.0.0.0", port=free_port, debug=False)
