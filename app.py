from flask import Flask, render_template, request, redirect, url_for, session, send_from_directory
from flask_socketio import SocketIO, emit, join_room, leave_room
from uuid import uuid4
import os

app = Flask(__name__, static_folder='static', template_folder='templates')
app.secret_key = 'super_secret_key'

# SocketIO setup
socketio = SocketIO(app)

# ===== In-memory storage =====
connected_users = set()   # set of session IDs
waiting_users = []        # queue of waiting users
rooms = {}                # sid -> room name

# ===== Routes =====
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/admin')
def admin():
    return f"<h1>Online users: {len(connected_users)}</h1>"

@app.route('/manifest.json')
def manifest():
    return send_from_directory('static', 'manifest.json')

@app.route('/service-worker.js')
def service_worker():
    return send_from_directory('static', 'service-worker.js')

# ===== Socket.IO Events =====

@socketio.on('connect')
def handle_connect():
    print(f"[+] User connected: {request.sid}")
    connected_users.add(request.sid)
    socketio.emit('online_users', len(connected_users))

@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    print(f"[x] {sid} disconnected")
    connected_users.discard(sid)

    # Remove from waiting list
    if sid in waiting_users:
        waiting_users.remove(sid)
        print(f"[~] {sid} removed from waiting queue")

    # If in room, notify the other user
    room = rooms.pop(sid, None)
    if room:
        emit('stranger_disconnected', room=room, include_self=False)
        for other_sid in list(rooms):
            if rooms.get(other_sid) == room:
                rooms.pop(other_sid, None)
        leave_room(room)

    socketio.emit('online_users', len(connected_users))

@socketio.on('find_stranger')
def handle_find_stranger():
    sid = request.sid
    print(f"[?] {sid} wants to find a stranger")

    if waiting_users and waiting_users[0] != sid:
        stranger_sid = waiting_users.pop(0)
        room = f"room_{uuid4().hex[:8]}"
        join_room(room, sid)
        join_room(room, stranger_sid)
        rooms[sid] = room
        rooms[stranger_sid] = room

        emit('stranger_found', False, room=sid)
        emit('stranger_found', True, room=stranger_sid)
        print(f"[✓] Connected {sid} and {stranger_sid} in {room}")
    else:
        waiting_users.append(sid)
        print(f"[⏳] {sid} added to waiting queue")

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
def handle_ice_candidate(candidate):
    room = rooms.get(request.sid)
    if room:
        emit('ice_candidate', candidate, room=room, include_self=False)

@socketio.on('chat_message')
def handle_chat_message(msg):
    room = rooms.get(request.sid)
    if room:
        emit('chat_message', msg, room=room, include_self=False)

@socketio.on('show_alarm')
def show_alarm():
    emit('show_alarm', broadcast=True)

@socketio.on('hide_alarm')
def hide_alarm():
    emit('hide_alarm', broadcast=True)

# ===== Main Entrypoint =====
if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=8080, debug=True)
