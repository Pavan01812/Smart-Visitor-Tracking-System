import cv2
import face_recognition
import numpy as np
import datetime
import os
import time
import threading
import io
from PIL import Image, ImageDraw, ImageFont
from database import load_all_encodings, save_new_person, update_visit, get_settings
from notifications import send_alert_email
from deepface import DeepFace

SNAPSHOT_FOLDER = os.path.join(os.path.dirname(__file__), 'static', 'snapshots')
if not os.path.exists(SNAPSHOT_FOLDER): os.makedirs(SNAPSHOT_FOLDER)

class VideoCamera:
    def __init__(self):
        self.video = cv2.VideoCapture(0)
        
        self.is_busy = False
        self.lock = threading.Lock()
        self.last_frame_bytes = None
        
        # Data Loading
        self.known_ids = []
        self.known_encodings = []
        self.people_metadata = {}
        self.reload_data() # Load data immediately
        
        self.cooldowns = {}
        self.emotion_cache = {}
        self.email_log = {} 
        self.alert_triggered = False
        
        # Initial Lists
        self.last_face_locations = [] 
        self.face_names = []
        self.face_badges = []
        self.face_colors = []
        self.face_emotions = []
        
        self.frame_count = 0
        self.skip_frames = 3 

    def __del__(self): 
        self.video.release()

    def reload_data(self):
        """Refreshes data from DB without stopping the app"""
        with self.lock:
            self.known_ids, self.known_encodings, self.people_metadata = load_all_encodings()
            s = get_settings()
            self.cooldown_time = s.get('cooldown', 30)
            self.tolerance = s.get('tolerance', 0.5)
            print(f"✅ Camera Memory Refreshed. Loaded {len(self.known_ids)} faces.")

    def reload_settings(self):
        s = get_settings()
        self.cooldown_time = s.get('cooldown', 30)
        self.tolerance = s.get('tolerance', 0.5)

    # --- SAFE ENROLLMENT ---
    def safe_enroll_staff(self, name, file_bytes):
        self.is_busy = True
        time.sleep(0.2)
        try:
            file_stream = io.BytesIO(file_bytes)
            img = face_recognition.load_image_file(file_stream)
            encs = face_recognition.face_encodings(img)
            
            if len(encs) > 0:
                pid = save_new_person(name, encs[0], is_staff=True)
                # Force a full reload to ensure sync
                self.reload_data()
                self.is_busy = False
                return True, "Success"
            
            self.is_busy = False
            return False, "No face detected"
        except Exception as e:
            self.is_busy = False
            return False, str(e)

    # --- SAFE SEARCH ---
    def safe_search_visitor(self, file_bytes):
        self.is_busy = True
        time.sleep(0.2)
        try:
            file_stream = io.BytesIO(file_bytes)
            img = face_recognition.load_image_file(file_stream)
            encs = face_recognition.face_encodings(img)
            
            if not encs: 
                self.is_busy = False; return False, "No face found"
            if not self.known_encodings:
                self.is_busy = False; return False, "Database empty"

            dists = face_recognition.face_distance(self.known_encodings, encs[0])
            best_match_index = np.argmin(dists)
            
            if dists[best_match_index] < self.tolerance:
                pid = self.known_ids[best_match_index]
                result = self.people_metadata.get(pid)
                self.is_busy = False
                return True, result
            
            self.is_busy = False
            return False, "No match found"
        except Exception as e:
            self.is_busy = False
            return False, str(e)

    def get_emotion(self, face_img, pid):
        now = time.time()
        if pid in self.emotion_cache and (now - self.emotion_cache[pid]['last'] < 3.0): 
            return self.emotion_cache[pid]['val']
        try:
            res = DeepFace.analyze(face_img, actions=['emotion'], enforce_detection=False, detector_backend='skip')
            emo = res[0]['dominant_emotion']
            mapping = {'angry':'😠','disgust':'🤢','fear':'😨','happy':'😊','sad':'😢','surprise':'😲','neutral':'😐'}
            val = f"{mapping.get(emo,'')} {emo.capitalize()}"
            self.emotion_cache[pid] = {'val': val, 'last': now}
            return val
        except: return ""

    def draw_text_pil(self, img, text, position, color=(255,255,255)):
        pil_img = Image.fromarray(img)
        draw = ImageDraw.Draw(pil_img)
        try: draw.text(position, text, fill=color)
        except: 
            clean_text = text.encode('ascii', 'ignore').decode('ascii')
            draw.text(position, clean_text, fill=color)
        return np.array(pil_img)

    def get_frame(self):
        if self.is_busy:
            if self.last_frame_bytes: return self.last_frame_bytes, False
            return None, False

        success, frame = self.video.read()
        if not success: return None, False
        
        small = cv2.resize(frame, (0,0), fx=0.25, fy=0.25)
        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        
        self.frame_count += 1
        
        if self.frame_count % self.skip_frames == 0:
            self.last_face_locations = face_recognition.face_locations(rgb)
            self.encodings = face_recognition.face_encodings(rgb, self.last_face_locations)
            
            self.face_names, self.face_badges, self.face_colors, self.face_emotions = [], [], [], []
            self.alert_triggered = False 

            for idx, enc in enumerate(self.encodings):
                matches = face_recognition.compare_faces(self.known_encodings, enc, tolerance=self.tolerance)
                name, pid, data = "Unknown", None, None
                dists = face_recognition.face_distance(self.known_encodings, enc)
                
                if len(dists) > 0:
                    best = np.argmin(dists)
                    if matches[best]:
                        pid = self.known_ids[best]
                        data = self.people_metadata.get(pid)
                        name = data['name']
                
                now = time.time()
                top, right, bottom, left = self.last_face_locations[idx]
                
                if pid:
                    if data['is_banned']:
                        self.alert_triggered = True
                        if now - self.email_log.get(pid, 0) > 300:
                            try:
                                scale = 4
                                face_img = frame[max(0,top*scale-20):min(frame.shape[0],bottom*scale+20), max(0,left*scale-20):min(frame.shape[1],right*scale+20)]
                                snap_path = os.path.join(SNAPSHOT_FOLDER, f"BANNED_{pid}_{int(now)}.jpg")
                                cv2.imwrite(snap_path, face_img)
                                send_alert_email(snap_path, name, datetime.datetime.now().strftime("%H:%M"))
                                self.email_log[pid] = now
                            except: pass
                    elif not data['is_staff'] and (now - self.cooldowns.get(pid, 0) > self.cooldown_time):
                        update_visit(pid, f"{pid}_{int(now)}.jpg")
                        self.people_metadata[pid]['visit_count'] += 1
                        self.cooldowns[pid] = now
                else:
                    pid = save_new_person(f"Visitor_{len(self.known_ids)+1}", enc)
                    self.known_ids.append(pid)
                    self.known_encodings.append(enc)
                    self.people_metadata[pid] = {'name':f"Visitor_{len(self.known_ids)}", 'is_staff':False, 'is_banned':False, 'visit_count':1}
                    self.cooldowns[pid] = now
                    name = self.people_metadata[pid]['name']
                    data = self.people_metadata[pid]

                if data and data['is_staff']: b, c = "STAFF", (255, 0, 0)
                elif data and data['is_banned']: b, c = "BANNED", (0, 0, 255)
                elif data and data['visit_count'] >= 10: b, c = "GOLD", (0, 215, 255)
                elif data and data['visit_count'] >= 5: b, c = "SILVER", (192, 192, 192)
                else: b, c = "BRONZE", (42, 42, 165)
                
                scale = 4
                face_img_full = frame[max(0,top*scale-20):min(frame.shape[0],bottom*scale+20), max(0,left*scale-20):min(frame.shape[1],right*scale+20)]
                emo = self.get_emotion(face_img_full, pid)
                
                self.face_names.append(name)
                self.face_badges.append(b)
                self.face_colors.append(c)
                self.face_emotions.append(emo)

        for (t, r, b, l), name, badge, color, emo in zip(self.last_face_locations, self.face_names, self.face_badges, self.face_colors, self.face_emotions):
            t*=4; r*=4; b*=4; l*=4
            cv2.rectangle(frame, (l, t), (r, b), color, 2)
            cv2.rectangle(frame, (l, b-35), (r, b), color, cv2.FILLED)
            frame = self.draw_text_pil(frame, name, (l+6, b-28))
            cv2.rectangle(frame, (l, t-30), (r, t), color, cv2.FILLED)
            frame = self.draw_text_pil(frame, badge, (l+6, t-25))
            if emo:
                cv2.rectangle(frame, (l, t-60), (l+150, t-35), (0,0,0), cv2.FILLED)
                frame = self.draw_text_pil(frame, emo, (l+5, t-55))

        ret, jpeg = cv2.imencode('.jpg', frame)
        if ret:
            self.last_frame_bytes = jpeg.tobytes()
            return self.last_frame_bytes, self.alert_triggered
        return None, False