from flask import Flask, render_template, Response, request, jsonify
from camera import VideoCamera
from database import init_db, get_db_connection, save_settings
import datetime
import os

app = Flask(__name__)
app.secret_key = 'visitor_track_pro_secret'

# Initialize
init_db()
cam = None

def get_cam():
    global cam
    if cam is None: cam = VideoCamera()
    return cam

@app.route('/')
def index(): return render_template('index.html')

def gen(camera):
    while True:
        frame, alert = camera.get_frame()
        if frame: yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + frame + b'\r\n\r\n')

@app.route('/video_feed')
def video_feed(): return Response(gen(get_cam()), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/api/verify_admin', methods=['POST'])
def verify_admin():
    if request.json.get('password') == "admin123": return jsonify({'success': True})
    return jsonify({'success': False})

@app.route('/api/status')
def api_status():
    c = get_cam()
    count = len(c.last_face_locations) if hasattr(c, 'last_face_locations') else 0
    return jsonify({'alert': c.alert_triggered, 'detected_count': count})

@app.route('/api/analytics')
def api_analytics():
    conn = get_db_connection()
    today = datetime.date.today().isoformat()
    visits_today = conn.execute("SELECT COUNT(*) FROM visits WHERE timestamp LIKE ?", (f'{today}%',)).fetchone()[0]
    unique_visitors = conn.execute("SELECT COUNT(*) FROM people WHERE is_staff = 0").fetchone()[0]
    
    extra_kpis = {
        'avg_duration': "12m 34s", 
        'staff_encounters': conn.execute("SELECT COUNT(*) FROM people WHERE is_staff = 1").fetchone()[0],
        'gold_members': conn.execute("SELECT COUNT(*) FROM people WHERE visit_count >= 10").fetchone()[0],
        'security_alerts': conn.execute("SELECT COUNT(*) FROM visits JOIN people ON visits.person_id = people.id WHERE people.is_banned = 1").fetchone()[0]
    }
    # Mocked emotions for display
    emotions = [{'label': 'Happy', 'icon': '😊', 'count': 45, 'percent': '36%'}, {'label': 'Neutral', 'icon': '😐', 'count': 52, 'percent': '42%'}, {'label': 'Surprised', 'icon': '😲', 'count': 12, 'percent': '10%'}, {'label': 'Angry', 'icon': '😠', 'count': 8, 'percent': '6%'}, {'label': 'Sad', 'icon': '😢', 'count': 7, 'percent': '6%'}]
    hours = [0]*24
    for v in conn.execute("SELECT timestamp FROM visits WHERE timestamp LIKE ?", (f'{today}%',)).fetchall():
        try: hours[datetime.datetime.fromisoformat(v['timestamp']).hour] += 1
        except: pass
    conn.close()
    return jsonify({'visits_today': visits_today, 'unique_visitors': unique_visitors, 'hourly_traffic': hours, 'extra_kpis': extra_kpis, 'emotions': emotions})

@app.route('/api/people')
def api_people():
    conn = get_db_connection()
    ppl = conn.execute('SELECT * FROM people ORDER BY last_seen DESC').fetchall()
    conn.close()
    data = []
    for p in ppl:
        badge = 'STAFF' if p['is_staff'] else ('GOLD' if p['visit_count']>=10 else 'SILVER' if p['visit_count']>=5 else 'BRONZE')
        data.append({'id': p['id'], 'name': p['name'], 'badge': badge, 'is_banned': bool(p['is_banned']), 'visits': p['visit_count']})
    return jsonify(data)

# --- FIX: SEARCH VISITOR (Reads bytes first) ---
@app.route('/api/search_visitor', methods=['POST'])
def search_visitor():
    if 'photo' not in request.files: return jsonify({'found': False, 'msg': 'No file'})
    f = request.files['photo']
    if f.filename == '': return jsonify({'found': False, 'msg': 'No file'})
    
    file_bytes = f.read() # Read into memory
    success, result = get_cam().safe_search_visitor(file_bytes)
    
    if success: return jsonify({'found': True, 'person': result})
    return jsonify({'found': False, 'msg': result})

@app.route('/api/settings', methods=['GET', 'POST'])
def api_settings():
    if request.method == 'POST':
        d = request.json; save_settings(d['cooldown'], d['tolerance']); get_cam().reload_settings()
        return jsonify({'status': 'ok'})
    return jsonify(get_settings())

# --- FIX: ENROLL STAFF (Reads bytes first) ---
@app.route('/enroll_staff', methods=['POST'])
def enroll_staff():
    f = request.files['photo']; n = request.form['name']
    if f and n:
        file_bytes = f.read() # Read into memory
        success, msg = get_cam().safe_enroll_staff(n, file_bytes)
    return Response(status=204)

@app.route('/toggle_ban/<int:id>', methods=['POST'])
def toggle_ban(id):
    conn = get_db_connection()
    # 1. Toggle the ban in Database
    conn.execute('UPDATE people SET is_banned = NOT is_banned WHERE id = ?', (id,))
    conn.commit()
    conn.close()
    
    # 2. CRITICAL FIX: Tell Camera to refresh its memory immediately
    get_cam().reload_data()
    
    return jsonify({'status': 'ok'})

@app.route('/delete_person/<int:id>', methods=['POST'])
def delete_person(id):
    conn = get_db_connection()
    conn.execute('DELETE FROM people WHERE id = ?', (id,))
    conn.execute('DELETE FROM visits WHERE person_id = ?', (id,))
    conn.commit()
    conn.close()
    
    
    get_cam().reload_data()
    return jsonify({'status': 'ok'})

if __name__ == '__main__':
    app.run(debug=True, threaded=True, use_reloader=False, port=5000)