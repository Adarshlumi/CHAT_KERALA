from flask import Flask, render_template, request, redirect, session, send_from_directory, url_for
from flask_socketio import SocketIO, emit
import sqlite3
import os
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename

# === App Setup ===
app = Flask(__name__)
app.secret_key = 'secret!'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet', manage_session=False)

# === Upload Config ===
UPLOAD_FOLDER = 'uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 MB max

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

connected_users = set()
user_sid_map = {}

def india_now_str():
    dt = datetime.utcnow() + timedelta(hours=5, minutes=30)
    return dt.strftime('%Y-%m-%d %H:%M:%S')

def init_db():
    with sqlite3.connect("database.db", check_same_thread=False) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                phone TEXT UNIQUE,
                username TEXT
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT,
                message TEXT,
                timestamp TEXT,
                reply_to INTEGER,
                deleted INTEGER DEFAULT 0,
                image_url TEXT
            )
        ''')
        conn.commit()

@socketio.on('register_user')
def register_user(data):
    username = data.get('username')
    if username:
        connected_users.add(username)
        user_sid_map[request.sid] = username
        socketio.emit('update_user_count', {'count': len(connected_users)})

@socketio.on('disconnect')
def handle_disconnect():
    username = user_sid_map.get(request.sid)
    if username and username in connected_users:
        connected_users.remove(username)
        user_sid_map.pop(request.sid, None)
        socketio.emit('update_user_count', {'count': len(connected_users)})

@app.route('/', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        phone = request.form['phone']
        username = request.form['username']
        with sqlite3.connect("database.db", check_same_thread=False) as conn:
            try:
                conn.execute("INSERT INTO users (phone, username) VALUES (?, ?)", (phone, username))
                conn.commit()
            except sqlite3.IntegrityError:
                pass
        session['username'] = username
        return redirect('/chat')
    return render_template('signup.html')

@app.route('/chat')
def chat():
    if 'username' not in session:
        return redirect('/')

    cutoff = (datetime.utcnow() + timedelta(hours=5, minutes=30)) - timedelta(days=1)
    cutoff_str = cutoff.strftime('%Y-%m-%d %H:%M:%S')

    with sqlite3.connect("database.db", check_same_thread=False) as conn:
        cur = conn.cursor()
        try:
            cur.execute("DELETE FROM messages WHERE timestamp < ?", (cutoff_str,))
            conn.commit()
        except sqlite3.OperationalError:
            pass

        cur.execute("""
            SELECT 
                m.id, 
                m.username, 
                m.message, 
                m.timestamp, 
                (SELECT message FROM messages WHERE id = m.reply_to), 
                m.image_url,
                m.reply_to
            FROM messages m 
            WHERE m.deleted = 0 
            ORDER BY m.timestamp ASC
        """)
        messages = cur.fetchall()

    return render_template('chat.html', username=session['username'], messages=messages)

@app.route('/upload', methods=['POST'])
def upload():
    if 'file' not in request.files:
        return 'No file uploaded', 400
    file = request.files['file']
    if file.filename == '':
        return 'Empty filename', 400
    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)

    image_url = url_for('uploaded_file', filename=filename)
    username = session.get('username', 'Unknown')
    timestamp = india_now_str()

    reply_to = request.headers.get('X-Reply-To')
    reply_to = int(reply_to) if reply_to and reply_to.isdigit() else None

    reply_to_text = None
    with sqlite3.connect("database.db", check_same_thread=False) as conn:
        cur = conn.cursor()
        if reply_to:
            cur.execute("SELECT message, image_url FROM messages WHERE id = ?", (reply_to,))
            row = cur.fetchone()
            if row:
                reply_msg = row[0].strip() if row[0] else ''
                reply_to_text = reply_msg if reply_msg else '[Image]'

        cur.execute(
            "INSERT INTO messages (username, message, timestamp, reply_to, deleted, image_url) VALUES (?, ?, ?, ?, ?, ?)",
            (username, '', timestamp, reply_to, 0, image_url)
        )
        msg_id = cur.lastrowid
        conn.commit()

    socketio.emit('message', {
        'id': msg_id,
        'username': username,
        'message': '',
        'timestamp': timestamp,
        'reply_to': reply_to,
        'reply_to_text': reply_to_text,
        'reply_to_id': reply_to,
        'image_url': image_url
    })

    return '', 204

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@socketio.on('message')
def handle_message(data):
    username = data.get('username')
    message = data.get('message')
    reply_to = data.get('reply_to')
    if not username or (not message and not data.get('image_url')):
        return
    ts = india_now_str()

    reply_to_text = None
    reply_to_image_url = None

    with sqlite3.connect("database.db", check_same_thread=False) as conn:
        cur = conn.cursor()
        if reply_to:
            cur.execute("SELECT message, image_url FROM messages WHERE id = ?", (reply_to,))
            row = cur.fetchone()
            if row:
                reply_msg = row[0].strip() if row[0] else ''
                reply_to_text = reply_msg if reply_msg else '[Image]'
                reply_to_image_url = row[1] if not reply_msg else None

        cur.execute(
            "INSERT INTO messages (username, message, timestamp, reply_to, deleted) VALUES (?, ?, ?, ?, 0)",
            (username.strip(), message.strip(), ts, reply_to)
        )
        msg_id = cur.lastrowid
        conn.commit()

    socketio.emit('message', {
        'id': msg_id,
        'username': username,
        'message': message,
        'timestamp': ts,
        'reply_to': reply_to,
        'reply_to_text': reply_to_text,
        'reply_to_id': reply_to,
        'image_url': None
    })

@socketio.on('delete_message')
def delete_message(data):
    msg_id = data.get('id')
    if not msg_id:
        return
    with sqlite3.connect("database.db", check_same_thread=False) as conn:
        conn.execute("UPDATE messages SET deleted = 1 WHERE id = ?", (msg_id,))
        conn.commit()
    socketio.emit('message_deleted', { 'id': msg_id })

@socketio.on('typing')
def handle_typing(data):
    emit('typing', data, broadcast=True, include_self=False)

@socketio.on('stop_typing')
def handle_stop_typing(data):
    emit('stop_typing', data, broadcast=True, include_self=False)





#if __name__ == '__main__':
   # init_db()
    #socketio.run(app, debug=False, host='0.0.0.0', port=int(os.environ.get('PORT', 10001)))



# === Run app ===
if __name__ == '__main__':
    init_db()
    socketio.run(app, debug=False, host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))
