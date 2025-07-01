from flask import Flask, render_template, send_from_directory, request, redirect, url_for, session
from flask_socketio import SocketIO, emit, join_room, leave_room
import os

app = Flask(__name__, static_folder='static', template_folder='templates')
app.secret_key = 'secret!'
socketio = SocketIO(app)

# ==== Configuration ====
admin_username = 'adarsh@123'
admin_password = 'ad#.85678'

# ==== State ====
waiting_users = []
rooms = {}
connected_users = set()

# ==== Routes ====

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
        if username == admin_username and password == admin_password:
            session['admin'] = True
            return redirect('/dashboard')
        return render_template('admin.html', error="Invalid credentials")
    return render_template('admin.html')

@app.route('/dashboard')
def admin_dashboard():
    if not session.get('admin'):
        return redirect('/admin')
    return render_template('dashboard.html', users=connected_users, rooms=rooms, waiting=waiting_users)

@app.route('/logout')
def logout():
    session.pop('admin', None)
    return redirect('/admin')

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
    emit_admin_update()

@socketio.on('admin_connect')
def handle_admin_connect():
    emit_admin_update()

@socketio.on('find_stranger')
def handle_find_stranger():
    print(f"[+] {request.sid} is looking for a stranger...")
    if waiting_users:
        partner_sid = waiting_users.pop(0)
        room = f"room_{partner_sid}_{request.sid}"

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









# === Run app ===
if __name__ == '__main__':
    init_db()
    socketio.run(app, debug=False, host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))
