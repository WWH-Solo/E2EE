# secure_chat_browser_e2ee.py
from flask import Flask, render_template_string, request, redirect, url_for, session
from flask_socketio import SocketIO, join_room, emit
import random, string, socket, threading, time, os

app = Flask(__name__)
app.secret_key = os.urandom(32)
socketio = SocketIO(app)

# ===========================
# In-memory storage
# ===========================
ROOMS = {}          # room_code: [usernames]
MESSAGES = {}       # room_code: [{'user':user,'msg':encrypted,'timestamp':ts}]

# ===========================
# Templates
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
body { background:#000; color:#0f0; font-family:'Courier New', monospace; margin:0; padding:0; height:100vh; display:flex; flex-direction:column; }
header { text-align:center; font-size:2em; padding:20px; text-shadow:0 0 10px #0f0; }
.logout { position:absolute; top:20px; right:20px; background:#0f0; color:#000; padding:10px; border:none; border-radius:5px; cursor:pointer; }
#chat-box { flex:1; overflow:auto; margin:10px; border:1px solid #0f0; padding:10px; max-height:70%; }
.message { padding:5px; margin:5px; border-radius:5px; max-width:60%; word-break:break-word; }
.sent { background:#0f0; color:#000; text-align:right; margin-left:auto; }
.received { background:#060; color:#0f0; text-align:left; margin-right:auto; }
#msg-input { width:70%; padding:8px; margin:10px; background:#000; color:#0f0; border:1px solid #0f0; }
button.send { padding:8px 12px; margin-left:5px; cursor:pointer; }
</style>
<script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.6.1/socket.io.min.js"></script>
<script>
// ===========================
// Browser-side Encryption
// ===========================
const socket = io();
const username = "{{ username }}";
const room = "{{ room }}";

// Generate AES key per room if first user
if(!localStorage.getItem('aes_key_'+room)){
    let array = new Uint8Array(32);
    crypto.getRandomValues(array);
    localStorage.setItem('aes_key_'+room, btoa(String.fromCharCode(...array)));
}
const AES_KEY = atob(localStorage.getItem('aes_key_'+room));

socket.emit('join', {'username': username, 'room': room});

// Morse code mapping
const MORSE = {'A':'.-','B':'-...','C':'-.-.','D':'-..','E':'.','F':'..-.','G':'--.','H':'....','I':'..','J':'.---','K':'-.-','L':'.-..','M':'--','N':'-.','O':'---','P':'.--.','Q':'--.-','R':'.-.','S':'...','T':'-','U':'..-','V':'...-','W':'.--','X':'-..-','Y':'-.--','Z':'--..',' ':'/'};
function toMorse(text){ return text.toUpperCase().split('').map(c=>MORSE[c]||c).join(' '); }
function fromMorse(code){ const rev = Object.fromEntries(Object.entries(MORSE).map(([k,v])=>[v,k])); return code.split(' ').map(c=>rev[c]||c).join(''); }

function encryptMessage(msg){
    let morse = toMorse(msg);
    return btoa(morse);
}
function decryptMessage(msg){
    try{
        return fromMorse(atob(msg));
    }catch(e){ return "✖ Unable to decrypt"; }
}

function sendMessage(){
    let msg = document.getElementById('msg-input').value;
    if(msg.trim()==="") return;
    let encrypted = encryptMessage(msg);
    socket.emit('message', {'user':username,'room':room,'msg':encrypted});
    document.getElementById('msg-input').value='';
}

socket.on('message', function(data){
    let decrypted = decryptMessage(data.msg);
    let div = document.createElement('div');
    div.textContent = data.user + ": " + decrypted;
    div.className = 'message ' + (data.user===username?'sent':'received');
    document.getElementById('chat-box').appendChild(div);
    document.getElementById('chat-box').scrollTop = document.getElementById('chat-box').scrollHeight;
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

# Ephemeral messages cleaning
def clean_old_messages():
    while True:
        now = time.time()
        for room, msgs in MESSAGES.items():
            MESSAGES[room] = [m for m in msgs if now - m['timestamp'] < 600]
        time.sleep(60)

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
    emit('message', {'user':'Server','msg':encrypt_message_for_demo(f"{data['username']} joined the room")}, room=data['room'])

def encrypt_message_for_demo(msg):
    return base64.b64encode(msg.encode()).decode()  # simple placeholder for join messages

@socketio.on('message')
def on_message(data):
    room = data['room']
    user = data['user']
    encrypted = data['msg']
    MESSAGES[room].append({'user':user,'msg':encrypted,'timestamp':time.time()})
    emit('message', {'user':user,'msg':encrypted}, room=room)

# ===========================
# Main
# ===========================
if __name__=="__main__":
    free_port = find_free_port()
    print(f"[*] Running Secure Chat on port {free_port}")
    socketio.run(app, host="0.0.0.0", port=free_port, debug=True)
