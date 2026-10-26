from flask import Flask, jsonify, request, render_template, redirect, url_for, session
import cv2
import numpy as np
import os
from werkzeug.utils import secure_filename
import sqlite3
from datetime import datetime
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests  # for SMS if using API
from dotenv import load_dotenv
from functools import wraps

# Load environment variables
load_dotenv()

app = Flask(__name__, template_folder='templates')
app.secret_key = os.getenv('SECRET_KEY', 'dev-secret-change-me')
# Accept both with/without trailing slash on routes
app.url_map.strict_slashes = False
# location to store uploaded images
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB limit

# Database setup
DB_PATH = os.path.join(os.path.dirname(__file__), 'analysis.db')
LOGIN_USER = os.getenv('LOGIN_USERNAME', 'admin')
LOGIN_PASS = os.getenv('LOGIN_PASSWORD', 'admin123')

# Chatbot and place data removed per latest requirements


def init_db():
    """Initialize SQLite database for storing analysis results."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS analysis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            before_filename TEXT NOT NULL,
            after_filename TEXT NOT NULL,
            change_value INTEGER NOT NULL,
            alert TEXT DEFAULT '',
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'success'
        )
    ''')
    # new tables for citizen reporting and landowner management
    c.execute('''
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_name TEXT,
            land_owner TEXT,
            description TEXT,
            latitude REAL,
            longitude REAL,
            before_image TEXT,
            after_image TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'pending'
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS landowners (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            contact TEXT,
            notes TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def detect_changes(before, after):
    """Simple change value between two images (used by upload).
    Returns sum of binary difference.
    """
    # ensure the files exist
    if not os.path.isfile(before) or not os.path.isfile(after):
        raise FileNotFoundError(f"Input files not found: {before}, {after}")

    img1 = cv2.imread(before)
    img2 = cv2.imread(after)

    if img1 is None or img2 is None:
        raise ValueError("One or both images could not be read (cv2.imread returned None)")

    # Get image dimensions
    h1, w1 = img1.shape[:2]
    h2, w2 = img2.shape[:2]
    
    # If images have different sizes, resize them to match
    if (h1, w1) != (h2, w2):
        # Resize both images to the smaller dimensions to preserve detail
        target_h = min(h1, h2)
        target_w = min(w1, w2)
        img1 = cv2.resize(img1, (target_w, target_h), interpolation=cv2.INTER_AREA)
        img2 = cv2.resize(img2, (target_w, target_h), interpolation=cv2.INTER_AREA)
    
    # Ensure both images have the same number of channels
    if len(img1.shape) != len(img2.shape) or img1.shape[2] != img2.shape[2]:
        # Convert both to BGR if they differ
        if len(img1.shape) == 2:  # Grayscale
            img1 = cv2.cvtColor(img1, cv2.COLOR_GRAY2BGR)
        if len(img2.shape) == 2:  # Grayscale
            img2 = cv2.cvtColor(img2, cv2.COLOR_GRAY2BGR)

    gray1 = cv2.cvtColor(img1, cv2.COLOR_BGR2GRAY)
    gray2 = cv2.cvtColor(img2, cv2.COLOR_BGR2GRAY)

    diff = cv2.absdiff(gray1, gray2)

    _, thresh = cv2.threshold(diff, 30, 255, cv2.THRESH_BINARY)

    return int(np.sum(thresh))


def create_change_visualization(before_path, after_path, output_path):
    """Create a visual representation of changes between two images."""
    try:
        img1 = cv2.imread(before_path)
        img2 = cv2.imread(after_path)
        
        if img1 is None or img2 is None:
            return False
            
        # Resize to match dimensions
        h1, w1 = img1.shape[:2]
        h2, w2 = img2.shape[:2]
        if (h1, w1) != (h2, w2):
            target_h = min(h1, h2)
            target_w = min(w1, w2)
            img1 = cv2.resize(img1, (target_w, target_h), interpolation=cv2.INTER_AREA)
            img2 = cv2.resize(img2, (target_w, target_h), interpolation=cv2.INTER_AREA)
        
        # Ensure same channels
        if len(img1.shape) != len(img2.shape) or img1.shape[2] != img2.shape[2]:
            if len(img1.shape) == 2:
                img1 = cv2.cvtColor(img1, cv2.COLOR_GRAY2BGR)
            if len(img2.shape) == 2:
                img2 = cv2.cvtColor(img2, cv2.COLOR_GRAY2BGR)
        
        # Create difference image
        diff = cv2.absdiff(img1, img2)
        
        # Convert to grayscale for thresholding
        gray_diff = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
        
        # Create binary mask of changes
        _, thresh = cv2.threshold(gray_diff, 30, 255, cv2.THRESH_BINARY)
        
        # Create colored overlay (red for changes)
        overlay = img2.copy()
        overlay[thresh > 127] = [0, 0, 255]  # Red color for changes
        
        # Blend original with overlay
        alpha = 0.7
        blended = cv2.addWeighted(img2, 1-alpha, overlay, alpha, 0)
        
        # Save the visualization
        cv2.imwrite(output_path, blended)
        return True
        
    except Exception as e:
        print(f"Error creating change visualization: {e}")
        return False


def detect_anomalies(before, after, min_area=500):
    """Detect and return bounding boxes of significant change areas.
    Returns (value, boxes, processed_image) where boxes are pixel coordinates.
    """
    # reuse same preprocessing as detect_changes
    if not os.path.isfile(before) or not os.path.isfile(after):
        raise FileNotFoundError(f"Input files not found: {before}, {after}")

    img1 = cv2.imread(before)
    img2 = cv2.imread(after)
    if img1 is None or img2 is None:
        raise ValueError("One or both images could not be read (cv2.imread returned None)")

    h1, w1 = img1.shape[:2]
    h2, w2 = img2.shape[:2]
    if (h1, w1) != (h2, w2):
        target_h = min(h1, h2)
        target_w = min(w1, w2)
        img1 = cv2.resize(img1, (target_w, target_h), interpolation=cv2.INTER_AREA)
        img2 = cv2.resize(img2, (target_w, target_h), interpolation=cv2.INTER_AREA)
    if len(img1.shape) != len(img2.shape) or img1.shape[2] != img2.shape[2]:
        if len(img1.shape) == 2:
            img1 = cv2.cvtColor(img1, cv2.COLOR_GRAY2BGR)
        if len(img2.shape) == 2:
            img2 = cv2.cvtColor(img2, cv2.COLOR_GRAY2BGR)

    gray1 = cv2.cvtColor(img1, cv2.COLOR_BGR2GRAY)
    gray2 = cv2.cvtColor(img2, cv2.COLOR_BGR2GRAY)
    diff = cv2.absdiff(gray1, gray2)
    _, thresh = cv2.threshold(diff, 30, 255, cv2.THRESH_BINARY)

    # find contours
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area >= min_area:
            x, y, w, h = cv2.boundingRect(cnt)
            boxes.append((x, y, w, h))
    value = int(np.sum(thresh))
    return value, boxes, thresh


def login_required(view_func):
    """Decorator to protect views that require authentication."""
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not session.get('user'):
            return redirect(url_for('login', next=request.path))
        return view_func(*args, **kwargs)
    return wrapper

def classify_land_cover(image_path):
    """Classify land cover type based on dominant colors."""
    image = cv2.imread(image_path)
    if image is None:
        return 'unknown'
    
    # Calculate average color
    avg_color = cv2.mean(image)[:3]
    r, g, b = avg_color
    
    # Simple classification based on RGB values
    if g > r and g > b and g > 100:
        return 'forest'  # High green
    elif b > r and b > g and b > 100:
        return 'water'   # High blue
    elif abs(r - g) < 30 and abs(r - b) < 30 and r < 150:
        return 'building'  # Gray tones
    elif r > g and r > b and r > 100:
        return 'bare_land'  # High red/brown
    else:
        return 'unknown'


def send_email_alert(alert_type, change_value, analysis_id):
    """Send email notification for illegal activity alerts."""
    try:
        # Email configuration - replace with your settings
        sender_email = os.getenv('ALERT_EMAIL', 'your-email@gmail.com')
        sender_password = os.getenv('ALERT_EMAIL_PASSWORD', 'your-password')
        receiver_email = os.getenv('ALERT_RECEIVER_EMAIL', 'admin@example.com')
        
        if not sender_password or sender_password == 'your-password':
            print("Email not configured, skipping email alert")
            return
        
        msg = MIMEMultipart()
        msg['From'] = sender_email
        msg['To'] = receiver_email
        msg['Subject'] = f'🚨 Satellite Alert: {alert_type} Detected'
        
        body = f"""
        CRITICAL ALERT: Illegal Activity Detected
        
        Alert Type: {alert_type}
        Change Value: {change_value}
        Analysis ID: {analysis_id}
        Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
        
        Immediate investigation required.
        
        Satellite Change Detection System
        """
        
        msg.attach(MIMEText(body, 'plain'))
        
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(sender_email, sender_password)
        text = msg.as_string()
        server.sendmail(sender_email, receiver_email, text)
        server.quit()
        
        print(f"Email alert sent for {alert_type}")
    except Exception as e:
        print(f"Failed to send email alert: {e}")


def send_sms_alert(alert_type, change_value, analysis_id):
    """Send SMS notification for illegal activity alerts."""
    try:
        # Twilio configuration - replace with your settings
        account_sid = os.getenv('TWILIO_ACCOUNT_SID', 'your-sid')
        auth_token = os.getenv('TWILIO_AUTH_TOKEN', 'your-token')
        from_number = os.getenv('TWILIO_FROM_NUMBER', '+1234567890')
        to_number = os.getenv('TWILIO_TO_NUMBER', '+0987654321')
        
        if account_sid == 'your-sid' or not auth_token:
            print("SMS not configured, skipping SMS alert")
            return
        
        from twilio.rest import Client
        client = Client(account_sid, auth_token)
        
        message = client.messages.create(
            body=f'🚨 ALERT: {alert_type} detected! Change: {change_value}, ID: {analysis_id}',
            from_=from_number,
            to=to_number
        )
        
        print(f"SMS alert sent: {message.sid}")
    except ImportError:
        print("Twilio not installed, install with: pip install twilio")
    except Exception as e:
        print(f"Failed to send SMS alert: {e}")


@app.route("/upload")
@login_required
def upload_page():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
@login_required
def upload():
    """Accept multipart form with 'before' and 'after' image files."""
    if 'before' not in request.files or 'after' not in request.files:
        return jsonify({
            "status": "error",
            "message": "Please upload both 'before' and 'after' files."
        }), 400

    before_file = request.files['before']
    after_file = request.files['after']

    if before_file.filename == '' or after_file.filename == '':
        return jsonify({
            "status": "error",
            "message": "Empty filename provided."
        }), 400

    # secure filenames and save
    before_fname = secure_filename(before_file.filename)
    after_fname = secure_filename(after_file.filename)
    before_path = os.path.join(app.config['UPLOAD_FOLDER'], before_fname)
    after_path = os.path.join(app.config['UPLOAD_FOLDER'], after_fname)
    before_file.save(before_path)
    after_file.save(after_path)

    try:
        result = detect_changes(before_path, after_path)
        
        # Classify land cover and determine alert
        classify_before = classify_land_cover(before_path)
        classify_after = classify_land_cover(after_path)
        alert = 'No Significant Change'
        if result > 1000:  # Threshold for significant change
            if classify_before == 'forest' and classify_after == 'building':
                alert = 'Illegal Construction'
            elif classify_before == 'water' and classify_after == 'bare_land':
                alert = 'Lake Encroachment'
            elif classify_before == 'forest' and classify_after == 'bare_land':
                alert = 'Deforestation'
            else:
                alert = 'Change Detected'
        
        # Save result to database
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            c = conn.cursor()
            c.execute('''
                INSERT INTO analysis (before_filename, after_filename, change_value, alert, status)
                VALUES (?, ?, ?, ?, ?)
            ''', (before_fname, after_fname, result, alert, 'success'))
            analysis_id = c.lastrowid
        
        # Create change visualization
        change_viz_filename = f"change_{analysis_id}_{before_fname}"
        change_viz_path = os.path.join(app.config['UPLOAD_FOLDER'], change_viz_filename)
        viz_created = create_change_visualization(before_path, after_path, change_viz_path)
        
        # Copy to static_uploads for web access
        static_dir = os.path.join(os.path.dirname(__file__), 'static_uploads')
        os.makedirs(static_dir, exist_ok=True)
        static_viz_path = os.path.join(static_dir, change_viz_filename)
        if viz_created:
            cv2.imwrite(static_viz_path, cv2.imread(change_viz_path))
            change_viz_url = f"/static_uploads/{change_viz_filename}"
        else:
            change_viz_url = None
        
        # Send external notifications for illegal activities
        if alert in ['Illegal Construction', 'Lake Encroachment', 'Deforestation']:
            send_email_alert(alert, result, analysis_id)
            send_sms_alert(alert, result, analysis_id)
        
        return jsonify({
            "status": "Change Detected",
            "value": result,
            "alert": alert,
            "id": analysis_id,
            "change_visualization": change_viz_url
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 400


@app.route("/detect")
def detect():
    # allow query parameters for filenames
    before = request.args.get("before", "before.png")
    after = request.args.get("after", "after.png")

    try:
        result = detect_changes(before, after)
        return jsonify({
            "status": "Change Detected",
            "value": result
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 400


@app.route("/analyze", methods=["GET"])
@login_required
def analyze_page():
    """Render the anomaly analysis upload form."""
    return render_template('analyze.html')


@app.route("/analyze", methods=["POST"])
@login_required
def analyze():
    """Perform geospatial anomaly analysis."""    # handle file upload and coordinates
    if 'before' not in request.files or 'after' not in request.files:
        return jsonify({"error": "Both before and after images required."}), 400
    before_file = request.files['before']
    after_file = request.files['after']
    for fname in (before_file.filename, after_file.filename):
        if fname == '':
            return jsonify({"error": "Empty filename provided."}), 400
    before_fname = secure_filename(before_file.filename)
    after_fname = secure_filename(after_file.filename)
    before_path = os.path.join(app.config['UPLOAD_FOLDER'], before_fname)
    after_path = os.path.join(app.config['UPLOAD_FOLDER'], after_fname)
    before_file.save(before_path)
    after_file.save(after_path)

    # parse coordinates
    try:
        tl_lat = float(request.form['tl_lat'])
        tl_lon = float(request.form['tl_lon'])
        br_lat = float(request.form['br_lat'])
        br_lon = float(request.form['br_lon'])
    except Exception as e:
        return jsonify({"error": "Invalid geographic coordinates."}), 400

    try:
        value, boxes, mask = detect_anomalies(before_path, after_path)
        h, w = cv2.imread(after_path).shape[:2]
        # prepare file urls for overlay (serve static via uploads)
        # ensure upload folder is served
        after_url = f"/static_uploads/{after_fname}"
        # copy file to static_uploads if necessary
        static_dir = os.path.join(os.path.dirname(__file__), 'static_uploads')
        os.makedirs(static_dir, exist_ok=True)
        cv2.imwrite(os.path.join(static_dir, after_fname), cv2.imread(after_path))

        return jsonify({
            "change_value": value,
            "boxes": boxes,
            "width": w,
            "height": h,
            "tl_lat": tl_lat,
            "tl_lon": tl_lon,
            "br_lat": br_lat,
            "br_lon": br_lon,
            "after_url": after_url
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/dashboard")
@login_required
def dashboard():
    """Display dashboard with analysis history."""
    return render_template("dashboard.html")


@app.route("/api/analysis", methods=["GET"])
def get_analysis_history():
    """API endpoint to fetch all analysis results."""
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM analysis ORDER BY timestamp DESC")
        rows = c.fetchall()
    
    analyses = []
    for row in rows:
        analyses.append({
            "id": row["id"],
            "before_filename": row["before_filename"],
            "after_filename": row["after_filename"],
            "change_value": row["change_value"],
            "alert": row["alert"],
            "timestamp": row["timestamp"],
            "status": row["status"]
        })
    
    return jsonify(analyses)


@app.route("/api/stats", methods=["GET"])
def get_stats():
    """Get statistics about analyses."""
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        c = conn.cursor()
        
        c.execute("SELECT COUNT(*) as total FROM analysis")
        total = c.fetchone()[0]
        
        c.execute("SELECT AVG(change_value) as avg_change FROM analysis")
        avg_change = c.fetchone()[0] or 0
        
        c.execute("SELECT MAX(change_value) as max_change FROM analysis")
        max_change = c.fetchone()[0] or 0
        
        c.execute("SELECT MIN(change_value) as min_change FROM analysis WHERE change_value > 0")
        min_change = c.fetchone()[0] or 0
    
    return jsonify({
        "total_analyses": total,
        "average_change": round(avg_change, 2),
        "max_change": max_change,
        "min_change": min_change
    })


@app.route("/api/analysis/<int:analysis_id>", methods=["DELETE"])
def delete_analysis(analysis_id):
    """Delete a specific analysis record."""
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        c = conn.cursor()
        c.execute("DELETE FROM analysis WHERE id = ?", (analysis_id,))
        conn.commit()
    
    return jsonify({"status": "deleted", "id": analysis_id})


@app.route("/")
@login_required
def home():
    """Display landing page."""
    return render_template("home.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    """Simple username/password login."""
    error = None
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if username == LOGIN_USER and password == LOGIN_PASS:
            session['user'] = username
            next_url = request.args.get('next') or url_for('dashboard')
            return redirect(next_url)
        error = "Invalid credentials"
    return render_template("login.html", error=error)


# Chatbot routes and helper removed since feature deprecated





# serve uploaded files from static_uploads folder
@app.route('/static_uploads/<path:filename>')
def static_uploads(filename):
    from flask import send_from_directory
    return send_from_directory(os.path.join(os.path.dirname(__file__), 'static_uploads'), filename)


# citizen reporting portal
@app.route('/report', methods=['GET', 'POST'])
@login_required
def report():
    if request.method == 'POST':
        user_name = request.form.get('user_name', '')
        land_owner = request.form.get('land_owner', '')
        description = request.form.get('description', '')
        latitude = request.form.get('latitude', type=float)
        longitude = request.form.get('longitude', type=float)
        before_file = request.files.get('before_image')
        after_file = request.files.get('after_image')
        before_path = None
        after_path = None
        if before_file:
            before_filename = secure_filename(before_file.filename)
            before_path = os.path.join(app.config['UPLOAD_FOLDER'], before_filename)
            before_file.save(before_path)
        if after_file:
            after_filename = secure_filename(after_file.filename)
            after_path = os.path.join(app.config['UPLOAD_FOLDER'], after_filename)
            after_file.save(after_path)
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            c = conn.cursor()
            c.execute(
                'INSERT INTO reports (user_name, land_owner, description, latitude, longitude, before_image, after_image) VALUES (?,?,?,?,?,?,?)',
                (user_name, land_owner, description, latitude, longitude, before_path, after_path)
            )
            conn.commit()
        return jsonify({'status': 'submitted'})
    return render_template('report.html')


@app.route('/reports')
@login_required
def reports_view():
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        c = conn.cursor()
        c.execute('SELECT id, user_name, land_owner, description, latitude, longitude, timestamp, status FROM reports ORDER BY timestamp DESC')
        rows = c.fetchall()
    return render_template('reports.html', reports=rows)


# landowner management
@app.route('/landowners', methods=['GET', 'POST'])
@login_required
def landowners():
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        c = conn.cursor()
        if request.method == 'POST':
            name = request.form.get('name', '')
            contact = request.form.get('contact', '')
            notes = request.form.get('notes', '')
            c.execute('INSERT INTO landowners (name, contact, notes) VALUES (?,?,?)', (name, contact, notes))
            conn.commit()
        c.execute('SELECT id, name, contact, notes FROM landowners')
        owners = c.fetchall()
    return render_template('landowners.html', owners=owners)


@app.route('/visualization')
@login_required
def visualization():
    # page with advanced interactive visualization
    return render_template('visualization.html')


@app.route('/api/reports')
def api_reports():
    """Return report locations and severity score for heatmap."""
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        c = conn.cursor()
        c.execute('SELECT latitude, longitude FROM reports WHERE latitude IS NOT NULL AND longitude IS NOT NULL')
        pts = c.fetchall()
    # convert to [lat, lon, intensity] list
    data = [[row[0], row[1], 0.5] for row in pts]
    return jsonify(data)


@app.route('/map')
@login_required
def map_view():
    """Render the map visualization page; data passed via query string."""
    return render_template('map.html')


@app.errorhandler(404)
def not_found(error):
    """Graceful 404 that guides users to valid pages."""
    if session.get('user'):
        return render_template('404.html'), 404
    # if not authenticated, send to login with original path hinted
    return redirect(url_for('login', next=request.path))


if __name__ == "__main__":
    # Bind to all interfaces so the app is reachable beyond localhost (e.g., from phones on same network)
    app.run(host="0.0.0.0", port=8000, debug=True, use_reloader=False)
