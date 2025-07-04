from flask import Flask, render_template, send_from_directory, request, redirect, session
from flask_socketio import SocketIO, emit, join_room, leave_room
from werkzeug.security import generate_password_hash, check_password_hash
from uuid import uuid4
from datetime import datetime, date
import os
import sqlite3
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = Flask(__name__, static_folder='static', template_folder='templates')
app.secret_key = os.getenv("FLASK_SECRET_KEY") or 'supersecret'

# ==== Redis-safe SocketIO configuration ====
redis_url = os.getenv("REDIS_URL")
try:
    if redis_url:
        import redis
        socketio = SocketIO(app, async_mode='eventlet', message_queue=redis_url)
    else:
        socketio = SocketIO(app, async_mode='eventlet')
except ImportError:
    print("\u26a0\ufe0f Redis not installed or unavailable. Running without message queue.")
    socketio = SocketIO(app, async_mode='eventlet')

# ==== Configuration ====
admin_username = os.getenv("ADMIN_USERNAME")
admin_password_hash = generate_password_hash(os.getenv("ADMIN_PASSWORD"))

# ==== In-memory state ====
waiting_users = []
rooms = {}
connected_users = set()

# ==== Database setup ====
DATABASE = 'database.db'

def log_user_login(user_id):
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_logins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            login_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("INSERT INTO user_logins (user_id, login_time) VALUES (?, ?)", (user_id, datetime.now()))
    conn.commit()
    conn.close()

def get_today_user_count():
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute("""
        SELECT COUNT(DISTINCT user_id) FROM user_logins WHERE DATE(login_time) = ?
    """, (date.today().isoformat(),))
    count = c.fetchone()[0]
    conn.close()
    return count

# ==== Routes ====
alarm_active = {'show': False, 'message': ''}

@app.route('/trigger_alarm')
def trigger_alarm():
    if not session.get('admin'):
        return redirect('/admin')
    alarm_active['show'] = True
    alarm_active['message'] = '\ud83d\udea8 Admin has triggered an alert!'
    socketio.emit('show_alarm', {'message': alarm_active['message']})
    return redirect('/dashboard')

@app.route('/stop_alarm')
def stop_alarm():
    if not session.get('admin'):
        return redirect('/admin')
    alarm_active['show'] = False
    alarm_active['message'] = ''
    socketio.emit('hide_alarm')
    return redirect('/dashboard')

@app.route('/ashore')
def ashore():
    return render_template('ashore.html')

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/manifest.json')
def manifest():
    return send_from_directory('static', 'manifest.json')

@app.route('/service-worker.js')
def sw():
    return send_from_directory('static', 'service-worker.js')

@app.route('/icons/<filename>')
def icons(filename):
    return send_from_directory(os.path.join('static', 'icons'), filename)

@app.route('/favicon.ico')
def favicon():
    return send_from_directory('static/icons', 'icon-192.png')

@app.route('/admin', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if username == admin_username and check_password_hash(admin_password_hash, password):
            session['admin'] = True
            return redirect('/dashboard')
        return render_template('admin.html', error="Invalid credentials")
    return render_template('admin.html')

@app.route('/dashboard')
def admin_dashboard():
    if not session.get('admin'):
        return redirect('/admin')
    daily_users = get_today_user_count()
    return render_template('dashboard.html', users=connected_users, rooms=rooms, waiting=waiting_users, daily_users=daily_users)

@app.route('/logout')
def logout():
    session.pop('admin', None)
    return redirect('/admin')

@app.route('/sitemap.xml')
def sitemap():
    return send_from_directory('static', 'sitemap.xml')

# ==== Admin Update Broadcaster ====
def emit_admin_update():
    socketio.emit('admin_update', {
        'users': list(connected_users),
        'waiting': waiting_users,
        'rooms': rooms
    })

# ==== Socket.IO Events ====
@socketio.on('connect')
def handle_connect():
    print(f"[+] User connected: {request.sid}")
    connected_users.add(request.sid)
    log_user_login(request.sid)
    emit_admin_update()

@socketio.on('admin_connect')
def handle_admin_connect():
    emit_admin_update()

@socketio.on('find_stranger')
def handle_find_stranger():
    if request.sid in rooms or request.sid in waiting_users:
        return
    print(f"[+] {request.sid} is looking for a stranger...")
    if waiting_users:
        partner_sid = waiting_users.pop(0)
        room = f"room_{uuid4().hex}"
        join_room(room, sid=partner_sid)
        join_room(room, sid=request.sid)
        rooms[request.sid] = room
        rooms[partner_sid] = room
        print(f"[\u2713] Matched {request.sid} with {partner_sid} in room {room}")
        emit('stranger_found', False, to=partner_sid)
        emit('stranger_found', True, to=request.sid)
    else:
        waiting_users.append(request.sid)
        print(f"[-] No match found. {request.sid} added to waiting_users.")
    emit_admin_update()

@socketio.on('offer')
def handle_offer(offer):
    room = rooms.get(request.sid)
    if room:
        emit('offer', offer, room=room, include_self=False)

@socketio.on('answer')
def handle_answer(answer):
    room = rooms.get(request.sid)
    if room:
        emit('answer', answer, room=room, include_self=False)

@socketio.on('ice_candidate')
def handle_ice(candidate):
    room = rooms.get(request.sid)
    if room:
        emit('ice_candidate', candidate, room=room, include_self=False)

@socketio.on('chat_message')
def handle_chat_message(message):
    room = rooms.get(request.sid)
    if room:
        emit('chat_message', message, room=room, include_self=False)

@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    print(f"[x] {sid} disconnected")
    connected_users.discard(sid)
    if sid in waiting_users:
        waiting_users.remove(sid)
        print(f"[~] {sid} removed from waiting queue")
    room = rooms.pop(sid, None)
    if room:
        emit('stranger_disconnected', room=room, include_self=False)
        for other_sid in list(rooms):
            if rooms[other_sid] == room:
                rooms.pop(other_sid, None)
        leave_room(room)
    emit_admin_update()

# ==== Run App ====
if __name__ == '__main__':
    debug_mode = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    socketio.run(app, debug=debug_mode, port=8007)
