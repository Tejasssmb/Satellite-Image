from flask import Flask, jsonify, request, render_template, redirect, url_for, session
import cv2
import numpy as np
import os
import secrets
from urllib.parse import urlparse
from werkzeug.utils import secure_filename
import sqlite3
from datetime import datetime, timedelta
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests
import rasterio
from rasterio.windows import from_bounds
from rasterio.warp import transform_bounds
from dotenv import load_dotenv
from functools import wraps
import bcrypt
import random
import string
import pyotp
from geopy.geocoders import Nominatim

# Load environment variables
load_dotenv()

app = Flask(__name__, template_folder='templates')
app.secret_key = os.getenv('SECRET_KEY') or secrets.token_hex(32)
app.url_map.strict_slashes = False
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=os.getenv('SESSION_COOKIE_SECURE', '0') == '1'
)

# Configuration
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
ALLOWED_IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.tif', '.tiff', '.webp'}

# Database setup
DB_PATH = os.path.join(os.path.dirname(__file__), 'analysis.db')

# Sentinel Hub credentials
SENTINELHUB_CLIENT_ID = os.getenv('SENTINELHUB_CLIENT_ID')
SENTINELHUB_CLIENT_SECRET = os.getenv('SENTINELHUB_CLIENT_SECRET')

# Geolocation
geolocator = Nominatim(user_agent="geoview_app")


def safe_next_url(candidate):
    """Allow only local relative redirects after authentication."""
    if not candidate:
        return None
    parsed = urlparse(candidate)
    if parsed.scheme or parsed.netloc or not candidate.startswith('/'):
        return None
    return candidate


def save_uploaded_image(upload, prefix):
    """Save a validated image under a collision-resistant server filename."""
    original_name = secure_filename(upload.filename or '')
    extension = os.path.splitext(original_name)[1].lower()
    if not original_name or extension not in ALLOWED_IMAGE_EXTENSIONS:
        raise ValueError('Unsupported image format')

    filename = f'{prefix}_{secrets.token_hex(12)}{extension}'
    path = os.path.join(UPLOAD_FOLDER, filename)
    upload.save(path)
    if cv2.imread(path) is None:
        os.remove(path)
        raise ValueError('Uploaded file is not a valid image')
    return filename, path


def init_db():
    """Initialize SQLite database with all required tables."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Users table
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            phone TEXT,
            password_hash TEXT NOT NULL,
            totp_secret TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            is_active BOOLEAN DEFAULT 1
        )
    ''')
    
    # OTP tokens table
    c.execute('''
        CREATE TABLE IF NOT EXISTS otp_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            otp_code TEXT NOT NULL,
            otp_type TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            expires_at DATETIME NOT NULL,
            is_used BOOLEAN DEFAULT 0,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    ''')
    
    # Analysis table
    c.execute('''
        CREATE TABLE IF NOT EXISTS analysis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            area_name TEXT,
            before_filename TEXT NOT NULL,
            after_filename TEXT NOT NULL,
            change_value INTEGER NOT NULL,
            alert TEXT DEFAULT '',
            latitude REAL,
            longitude REAL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'success',
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    ''')
    
    # Reports table
    c.execute('''
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            user_name TEXT,
            land_owner TEXT,
            description TEXT,
            latitude REAL,
            longitude REAL,
            before_image TEXT,
            after_image TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'pending',
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    ''')
    
    # Landowners table
    c.execute('''
        CREATE TABLE IF NOT EXISTS landowners (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            name TEXT NOT NULL,
            contact TEXT,
            notes TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    ''')
    
    # Area search cache
    c.execute('''
        CREATE TABLE IF NOT EXISTS area_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            area_name TEXT UNIQUE NOT NULL,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            cached_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Upgrade databases created by the legacy app without discarding data.
    migrations = {
        'analysis': [
            ('user_id', 'INTEGER'), ('area_name', 'TEXT'),
            ('latitude', 'REAL'), ('longitude', 'REAL'),
            ('from_date', 'TEXT'), ('to_date', 'TEXT'),
            ('before_scene_date', 'TEXT'), ('after_scene_date', 'TEXT'),
            ('before_clear_percent', 'REAL'), ('after_clear_percent', 'REAL'),
            ('changed_ratio', 'REAL'), ('temporal_analysis_json', 'TEXT')
        ],
        'reports': [('user_id', 'INTEGER')],
        'landowners': [('user_id', 'INTEGER')]
    }
    for table, columns in migrations.items():
        existing_columns = {row[1] for row in c.execute(f'PRAGMA table_info({table})')}
        for column, column_type in columns:
            if column not in existing_columns:
                c.execute(f'ALTER TABLE {table} ADD COLUMN {column} {column_type}')
    
    conn.commit()
    conn.close()

init_db()


# ==================== Authentication Functions ====================

def hash_password(password):
    """Hash a password using bcrypt."""
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verify_password(password, password_hash):
    """Verify a password against its hash."""
    return bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8'))

def get_user_by_username(username):
    """Get user from database by username."""
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute('SELECT * FROM users WHERE username = ?', (username,))
        return c.fetchone()

def get_user_by_id(user_id):
    """Get user from database by ID."""
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute('SELECT * FROM users WHERE id = ?', (user_id,))
        return c.fetchone()

def username_exists(username):
    """Check if username already exists."""
    return get_user_by_username(username) is not None

def email_exists(email):
    """Check if email already exists."""
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        c = conn.cursor()
        c.execute('SELECT id FROM users WHERE email = ?', (email,))
        return c.fetchone() is not None

def generate_otp():
    """Generate a 6-digit OTP."""
    return f'{secrets.randbelow(1000000):06d}'

def send_otp_email(email, otp_code):
    """Send OTP via email."""
    try:
        sender_email = os.getenv('ALERT_EMAIL', 'your-email@gmail.com')
        sender_password = os.getenv('ALERT_EMAIL_PASSWORD', 'your-password')
        smtp_host = os.getenv('SMTP_HOST', 'smtp.gmail.com')
        smtp_port = int(os.getenv('SMTP_PORT', '587'))
        
        # For testing without email configured, print to console
        if not sender_email or '@' not in sender_email or not sender_password or 'your-' in sender_password:
            print('Email OTP is not configured; set ALERT_EMAIL and ALERT_EMAIL_PASSWORD in .env')
            return False
        
        msg = MIMEMultipart()
        msg['From'] = sender_email
        msg['To'] = email
        msg['Subject'] = '🔐 GeoView - Your OTP for Password Reset'
        
        body = f"""
        Hello,
        
        Your One-Time Password (OTP) for password reset is:
        
        {otp_code}
        
        This OTP expires in 10 minutes.
        If you didn't request this, please ignore this email.
        
        Best regards,
        GeoView Team
        """
        
        msg.attach(MIMEText(body, 'plain'))
        
        server = smtplib.SMTP(smtp_host, smtp_port, timeout=20)
        server.starttls()
        server.login(sender_email, sender_password)
        server.sendmail(sender_email, email, msg.as_string())
        server.quit()
        
        return True
    except Exception as e:
        print(f"Failed to send OTP email: {e}")
        return False

def send_otp_sms(phone, otp_code):
    """Send OTP via SMS using Twilio."""
    try:
        account_sid = os.getenv('TWILIO_ACCOUNT_SID', 'your-sid')
        auth_token = os.getenv('TWILIO_AUTH_TOKEN', 'your-token')
        from_number = os.getenv('TWILIO_FROM_NUMBER', '+1234567890')
        
        # For testing without SMS configured, print to console
        if account_sid == 'your-sid' or not auth_token or auth_token == 'your-token':
            print(f"\n{'='*60}")
            print(f"📱 OTP for Testing: {otp_code}")
            print(f"   Phone: {phone}")
            print(f"   (Check console for OTP since SMS is not configured)")
            print(f"{'='*60}\n")
            return True  # Return True to allow testing
        
        from twilio.rest import Client
        client = Client(account_sid, auth_token)
        
        message = client.messages.create(
            body=f'Your GeoView OTP is: {otp_code}. Valid for 10 minutes.',
            from_=from_number,
            to=phone
        )
        
        return True
    except Exception as e:
        print(f"Failed to send OTP SMS: {e}")
        return False

def verify_otp_code(user_id, otp_code):
    """Verify OTP and mark as used."""
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        c = conn.cursor()
        c.execute('''
            SELECT id FROM otp_tokens 
            WHERE user_id = ? AND otp_code = ? AND is_used = 0 AND expires_at > ?
            ORDER BY created_at DESC LIMIT 1
        ''', (user_id, otp_code, datetime.now()))
        result = c.fetchone()
        
        if result:
            c.execute('UPDATE otp_tokens SET is_used = 1 WHERE id = ?', (result[0],))
            conn.commit()
            return True
        return False

def create_otp_token(user_id, otp_type):
    """Create an OTP token for a user."""
    otp_code = generate_otp()
    expires_at = datetime.now() + timedelta(minutes=10)
    
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        c = conn.cursor()
        c.execute('''
            INSERT INTO otp_tokens (user_id, otp_code, otp_type, expires_at)
            VALUES (?, ?, ?, ?)
        ''', (user_id, otp_code, otp_type, expires_at))
        conn.commit()
    
    return otp_code

def login_required(view_func):
    """Decorator to protect views that require authentication."""
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not session.get('user_id'):
            return redirect(url_for('login', next=request.path))
        return view_func(*args, **kwargs)
    return wrapper


# ==================== Satellite Image Functions ====================

def get_area_coordinates(area_name):
    """Get latitude and longitude for an area name."""
    try:
        coordinate_parts = [float(part.strip()) for part in area_name.split(',')]
        if len(coordinate_parts) == 2 and -90 <= coordinate_parts[0] <= 90 and -180 <= coordinate_parts[1] <= 180:
            return coordinate_parts[0], coordinate_parts[1]
    except ValueError:
        pass
    # Check cache first
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        c = conn.cursor()
        c.execute('SELECT latitude, longitude FROM area_cache WHERE area_name = ?', (area_name.lower(),))
        result = c.fetchone()
        if result:
            return result[0], result[1]
    
    # Geocode the area
    try:
        location = geolocator.geocode(area_name, timeout=10)
        if location:
            lat, lon = location.latitude, location.longitude
            # Cache the result
            with sqlite3.connect(DB_PATH, timeout=10) as conn:
                c = conn.cursor()
                c.execute('''
                    INSERT OR IGNORE INTO area_cache (area_name, latitude, longitude)
                    VALUES (?, ?, ?)
                ''', (area_name.lower(), lat, lon))
                conn.commit()
            return lat, lon
    except Exception as e:
        print(f"Error geocoding area: {e}")
    
    return None, None

def fetch_satellite_image(
    lat,
    lon,
    date_str,
    output_path,
    area_size=0.01
):
    """Download a nearby Sentinel-2 scene and reject cloudy or obscured areas."""
    requested_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    start_date = requested_date - timedelta(days=14)
    end_date = requested_date + timedelta(days=14)
    bbox = [lon - area_size, lat - area_size, lon + area_size, lat + area_size]

    if SENTINELHUB_CLIENT_ID and SENTINELHUB_CLIENT_SECRET:
        try:
            token_response = requests.post(
                "https://services.sentinel-hub.com/oauth/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": SENTINELHUB_CLIENT_ID,
                    "client_secret": SENTINELHUB_CLIENT_SECRET
                },
                timeout=15
            )
            token_response.raise_for_status()
            access_token = token_response.json().get("access_token")
            evalscript = """
            //VERSION=3
            function setup() {
              return { input: ["B04", "B03", "B02"], output: { bands: 3 } };
            }
            function evaluatePixel(sample) { return [sample.B04, sample.B03, sample.B02]; }
            """
            payload = {
                "input": {"bounds": {"bbox": bbox}, "data": [{
                    "type": "sentinel-2-l2a",
                    "dataFilter": {
                        "timeRange": {"from": f"{start_date}T00:00:00Z", "to": f"{end_date}T23:59:59Z"},
                        "maxCloudCoverage": 80,
                        "mosaickingOrder": "leastCC"
                    }
                }]},
                "output": {"width": 512, "height": 512, "responses": [{"identifier": "default", "format": {"type": "image/png"}}]},
                "evalscript": evalscript
            }
            response = requests.post(
                "https://services.sentinel-hub.com/api/v1/process",
                headers={"Authorization": f"Bearer {access_token}"}, json=payload, timeout=60
            )
            response.raise_for_status()
            with open(output_path, "wb") as file:
                file.write(response.content)
            image = cv2.imread(output_path)
            if image is not None and float(image.std()) > 12:
                raise ValueError(
                    "Sentinel Hub response has no per-pixel cloud mask."
                )
        except (requests.RequestException, ValueError, OSError) as error:
            print("Using Sentinel-2 Scene Classification Layer for cloud screening:", error)

    # Public Sentinel-2 COG fallback: 10 m true-color imagery without credentials.
    try:
        search_url = "https://earth-search.aws.element84.com/v1/search"
        search_payload = {
            "collections": ["sentinel-2-l2a"],
            "datetime": f"{start_date}T00:00:00Z/{end_date}T23:59:59Z",
            "bbox": bbox,
            "limit": 100
        }
        search_response = requests.post(search_url, json=search_payload, timeout=30)
        search_response.raise_for_status()
        features = search_response.json().get("features", [])
        if features:
            dated_features = []
            for feature in features:
                properties = feature.get("properties", {})
                acquired_at = properties.get("datetime")
                if not acquired_at:
                    continue
                try:
                    acquired_date = datetime.fromisoformat(
                        acquired_at.replace("Z", "+00:00")
                    ).date()
                except ValueError:
                    continue
                cloud_cover = properties.get("eo:cloud_cover")
                dated_features.append((
                    abs((acquired_date - requested_date).days),
                    cloud_cover if cloud_cover is not None else 100,
                    feature,
                    acquired_date,
                    cloud_cover
                ))

            dated_features.sort(key=lambda item: (item[0], item[1]))
            for _, _, feature, acquired_date, cloud_cover in dated_features:
                assets = feature.get("assets", {})
                visual_asset = assets.get("visual", {})
                visual_url = visual_asset.get("href")
                scl_asset = assets.get("scl") or assets.get("SCL")
                scl_url = scl_asset.get("href") if scl_asset else None
                if not visual_url or not scl_url:
                    continue
                with rasterio.open(visual_url) as source:
                    source_bounds = transform_bounds(
                        "EPSG:4326", source.crs, *bbox, densify_pts=21
                    )
                    window = from_bounds(*source_bounds, transform=source.transform)
                    image = source.read(
                        out_shape=(3, 512, 512),
                        window=window,
                        boundless=True,
                        fill_value=0,
                        resampling=rasterio.enums.Resampling.bilinear
                    )
                with rasterio.open(scl_url) as source:
                    scl_bounds = transform_bounds(
                        "EPSG:4326", source.crs, *bbox, densify_pts=21
                    )
                    scl_window = from_bounds(
                        *scl_bounds, transform=source.transform
                    )
                    scl = source.read(
                        1,
                        out_shape=(512, 512),
                        window=scl_window,
                        boundless=True,
                        fill_value=0,
                        resampling=rasterio.enums.Resampling.nearest
                    )

                clear_mask = np.isin(scl, (4, 5, 6)).astype(np.uint8) * 255
                area_clear_percent = (
                    float(np.count_nonzero(clear_mask)) / clear_mask.size * 100
                )
                if area_clear_percent < 80:
                    continue

                rgb_image = np.moveaxis(image, 0, -1).astype(np.uint8)
                bgr_image = cv2.cvtColor(rgb_image, cv2.COLOR_RGB2BGR)
                bgr_image[clear_mask == 0] = (145, 145, 145)
                if cv2.imwrite(
                    output_path,
                    bgr_image
                ) and cv2.imread(output_path) is not None:
                    return {
                        "date": acquired_date.isoformat(),
                        "cloud_cover": (
                            round(float(cloud_cover), 2)
                            if cloud_cover is not None else None
                        ),
                        "area_clear_percent": round(area_clear_percent, 2),
                        "clear_mask": clear_mask
                    }
    except (requests.RequestException, rasterio.errors.RasterioError, KeyError, ValueError, OSError) as error:
        print("Public Sentinel-2 imagery unavailable:", error)
    return None

# ==================== Change Detection Functions ====================

def detect_changes(before, after, valid_mask=None):
    """Detect changes between two images."""
    if not os.path.isfile(before) or not os.path.isfile(after):
        raise FileNotFoundError(f"Input files not found")

    img1 = cv2.imread(before)
    img2 = cv2.imread(after)

    if img1 is None or img2 is None:
        raise ValueError("Could not read images")

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

    gray1 = cv2.GaussianBlur(cv2.cvtColor(img1, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    gray2 = cv2.GaussianBlur(cv2.cvtColor(img2, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    diff = cv2.absdiff(gray1, gray2)
    _, thresh = cv2.threshold(diff, 30, 255, cv2.THRESH_BINARY)
    thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    if valid_mask is not None:
        if valid_mask.shape != thresh.shape:
            valid_mask = cv2.resize(
                valid_mask,
                (thresh.shape[1], thresh.shape[0]),
                interpolation=cv2.INTER_NEAREST
            )
        thresh[valid_mask == 0] = 0
    return int(np.count_nonzero(thresh))

def detect_anomalies(before, after, min_area=500):
    """Return change value, significant bounding boxes, and image dimensions."""
    if not os.path.isfile(before) or not os.path.isfile(after):
        raise FileNotFoundError("Input files not found")

    img1 = cv2.imread(before)
    img2 = cv2.imread(after)
    if img1 is None or img2 is None:
        raise ValueError("Could not read images")

    if img1.shape[:2] != img2.shape[:2]:
        target_h = min(img1.shape[0], img2.shape[0])
        target_w = min(img1.shape[1], img2.shape[1])
        img1 = cv2.resize(img1, (target_w, target_h), interpolation=cv2.INTER_AREA)
        img2 = cv2.resize(img2, (target_w, target_h), interpolation=cv2.INTER_AREA)

    diff = cv2.absdiff(
        cv2.GaussianBlur(cv2.cvtColor(img1, cv2.COLOR_BGR2GRAY), (5, 5), 0),
        cv2.GaussianBlur(cv2.cvtColor(img2, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    )
    _, threshold = cv2.threshold(diff, 30, 255, cv2.THRESH_BINARY)
    threshold = cv2.morphologyEx(threshold, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(threshold, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = [cv2.boundingRect(contour) for contour in contours if cv2.contourArea(contour) >= min_area]
    return int(np.count_nonzero(threshold)), boxes, img2.shape[1], img2.shape[0]

def create_change_visualization(before_path, after_path, output_path, valid_mask=None):
    """Create visualization of changes."""
    try:
        img1 = cv2.imread(before_path)
        img2 = cv2.imread(after_path)
        
        if img1 is None or img2 is None:
            return False
            
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

        diff = cv2.absdiff(img1, img2)

        gray_diff = cv2.cvtColor(
            diff,
            cv2.COLOR_BGR2GRAY
        )

        _, thresh = cv2.threshold(
            gray_diff,
            30,
            255,
            cv2.THRESH_BINARY
        )
        if valid_mask is not None:
            if valid_mask.shape != thresh.shape:
                valid_mask = cv2.resize(
                    valid_mask,
                    (thresh.shape[1], thresh.shape[0]),
                    interpolation=cv2.INTER_NEAREST
                )
            thresh[valid_mask == 0] = 0

        contours, _ = cv2.findContours(
            thresh,
            cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_SIMPLE
        )

        result = img2.copy()

        change_count = 0

        for contour in contours:

            area = cv2.contourArea(contour)

            if area < 200:
                continue

            x, y, w, h = cv2.boundingRect(contour)

            cv2.rectangle(
                result,
                (x, y),
                (x + w, y + h),
                (0, 0, 255),
                2
            )

            change_count += 1

        cv2.putText(
            result,
            f"Detected Regions: {change_count}",
            (10, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (0, 0, 255),
            2
        )

        cv2.imwrite(
            output_path,
            result
        )
        return True
    except Exception as e:
        print(f"Error creating visualization: {e}")
        return False

def send_email_alert(alert_type, change_value, analysis_id):
    """Send email alert."""
    try:
        sender_email = os.getenv('ALERT_EMAIL', 'your-email@gmail.com')
        sender_password = os.getenv('ALERT_EMAIL_PASSWORD', 'your-password')
        receiver_email = os.getenv('ALERT_RECEIVER_EMAIL', 'admin@example.com')
        smtp_host = os.getenv('SMTP_HOST', 'smtp.gmail.com')
        smtp_port = int(os.getenv('SMTP_PORT', '587'))
        
        if not sender_password or sender_password == 'your-password':
            return
        
        msg = MIMEMultipart()
        msg['From'] = sender_email
        msg['To'] = receiver_email
        msg['Subject'] = f'🚨 GeoView Alert: {alert_type} Detected'
        
        body = f"CRITICAL ALERT: {alert_type} detected! Change: {change_value}, ID: {analysis_id}"
        msg.attach(MIMEText(body, 'plain'))
        
        server = smtplib.SMTP(smtp_host, smtp_port, timeout=20)
        server.starttls()
        server.login(sender_email, sender_password)
        server.sendmail(sender_email, receiver_email, msg.as_string())
        server.quit()
    except Exception as e:
        print(f"Failed to send alert: {e}")


# ==================== Routes ====================

@app.route("/register", methods=["GET", "POST"])
def register():
    """User registration."""
    error = None
    success = None
    
    if request.method == "POST":
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip().lower()
        phone = request.form.get('phone', '').strip()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        
        # Validation
        if not username or len(username) < 3:
            error = "Username must be at least 3 characters"
        elif username_exists(username):
            error = "Username already exists. Please choose another."
        elif not email or '@' not in email:
            error = "Invalid email address"
        elif email_exists(email):
            error = "Email already registered"
        elif password != confirm_password:
            error = "Passwords do not match"
        elif len(password) < 6:
            error = "Password must be at least 6 characters"
        else:
            # Create user
            try:
                password_hash = hash_password(password)
                with sqlite3.connect(DB_PATH, timeout=10) as conn:
                    c = conn.cursor()
                    c.execute('''
                        INSERT INTO users (username, email, phone, password_hash)
                        VALUES (?, ?, ?, ?)
                    ''', (username, email, phone, password_hash))
                    conn.commit()
                
                success = "Account created successfully! Please login."
                return redirect(url_for('login'))
            except Exception as e:
                error = f"Registration failed: {str(e)}"
    
    return render_template('register.html', error=error, success=success)


@app.route("/login", methods=["GET", "POST"])
def login():
    """User login."""
    error = None
    
    if request.method == "POST":
        username = request.form.get('username', '')
        password = request.form.get('password', '')
        
        user = get_user_by_username(username)
        
        if user and verify_password(password, user['password_hash']):
            session['user_id'] = user['id']
            session['username'] = user['username']
            next_url = safe_next_url(request.args.get('next')) or url_for('dashboard')
            return redirect(next_url)
        else:
            error = "Invalid username or password"
    
    return render_template('login.html', error=error)


@app.route("/email-login", methods=["POST"])
def email_login():
    """Start passwordless login for an existing account email."""
    email = request.form.get('email', '').strip().lower()
    if not email or '@' not in email:
        return render_template('login.html', error='Enter a valid account email address.')

    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        user = conn.execute(
            'SELECT * FROM users WHERE lower(email) = ? AND is_active = 1', (email,)
        ).fetchone()

    if not user:
        return render_template('login.html', error='No active account uses that email address.')

    otp_code = create_otp_token(user['id'], 'email_login')
    if not send_otp_email(user['email'], otp_code):
        return render_template('login.html', error='We could not send the OTP. Check your email settings.')

    session['email_login_user_id'] = user['id']
    session['otp_verified'] = False
    return redirect(url_for('verify_otp', method='email_login'))


@app.route("/logout")
def logout():
    """User logout."""
    session.clear()
    return redirect(url_for('login'))


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    """Forgot password flow."""
    error = None
    success = None
    
    if request.method == "POST":
        username_or_email = request.form.get('username_or_email', '').strip()
        
        # Recovery codes are sent only to the email stored on an existing account.
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            user = conn.execute(
                'SELECT * FROM users WHERE lower(email) = ?', (username_or_email.lower(),)
            ).fetchone()
        
        if user:
            # Generate and send OTP
            otp_code = create_otp_token(user['id'], 'password_reset')
            
            # Try to send via email first
            if send_otp_email(user['email'], otp_code):
                session['reset_user_id'] = user['id']
                session['otp_verified'] = False
                return redirect(url_for('verify_otp', method='email'))
            # Fallback to SMS if provided
            elif user['phone'] and send_otp_sms(user['phone'], otp_code):
                session['reset_user_id'] = user['id']
                session['otp_verified'] = False
                return redirect(url_for('verify_otp', method='sms'))
            else:
                error = "Could not send OTP. Please configure email or SMS settings."
        else:
            error = "User not found"
    
    return render_template('forgot_password.html', error=error)


@app.route("/verify-otp/<method>", methods=["GET", "POST"])
def verify_otp(method):
    """Verify OTP for password reset."""
    if method == 'email_login' and 'email_login_user_id' not in session:
        return redirect(url_for('login'))
    if method != 'email_login' and 'reset_user_id' not in session:
        return redirect(url_for('forgot_password'))
    
    error = None
    
    if request.method == "POST":
        otp_code = request.form.get('otp', '').strip()
        
        user_id = session.get('email_login_user_id') if method == 'email_login' else session.get('reset_user_id')
        if verify_otp_code(user_id, otp_code):
            if method == 'email_login':
                user = get_user_by_id(user_id)
                session.clear()
                session['user_id'] = user['id']
                session['username'] = user['username']
                return redirect(url_for('dashboard'))
            session['otp_verified'] = True
            return redirect(url_for('reset_password'))
        else:
            error = "Invalid or expired OTP"
    
    return render_template('verify_otp.html', error=error, method=method)


@app.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    """Reset password."""
    if 'reset_user_id' not in session or not session.get('otp_verified'):
        return redirect(url_for('forgot_password'))
    
    error = None
    
    if request.method == "POST":
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        
        if password != confirm_password:
            error = "Passwords do not match"
        elif len(password) < 6:
            error = "Password must be at least 6 characters"
        else:
            password_hash = hash_password(password)
            with sqlite3.connect(DB_PATH, timeout=10) as conn:
                c = conn.cursor()
                c.execute(
                    'UPDATE users SET password_hash = ? WHERE id = ?',
                    (password_hash, session['reset_user_id'])
                )
                conn.commit()
            
            session.clear()
            return redirect(url_for('login'))
    
    return render_template('reset_password.html', error=error)


# ==================== Area Search Routes ====================

@app.route("/api/search-areas")
def search_areas():
    """Return location suggestions using cache + Nominatim."""
    query = request.args.get("q", "").strip()
    if len(query) < 2:
        return jsonify([])

    results = []
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        rows = conn.execute(
            "SELECT area_name, latitude, longitude FROM area_cache WHERE area_name LIKE ? LIMIT 5",
            (f"%{query.lower()}%",)
        ).fetchall()
    results.extend({"name": row[0], "latitude": row[1], "longitude": row[2]} for row in rows)

    if len(results) < 8:
        try:
            locations = geolocator.geocode(
                query, exactly_one=False, limit=10, addressdetails=True, language="en"
            ) or []
            with sqlite3.connect(DB_PATH, timeout=10) as conn:
                for location in locations:
                    name = (location.address or "").strip()
                    if not name or any(item["name"].lower() == name.lower() for item in results):
                        continue
                    result = {"name": name, "latitude": location.latitude, "longitude": location.longitude}
                    results.append(result)
                    conn.execute(
                        "INSERT OR IGNORE INTO area_cache (area_name, latitude, longitude) VALUES (?, ?, ?)",
                        (name.lower(), location.latitude, location.longitude)
                    )
                    if len(results) >= 8:
                        break
                conn.commit()
        except Exception as error:
            app.logger.warning("Area suggestion lookup failed: %s", error)
    return jsonify(results[:8])


@app.route("/api/reverse-geocode")
@login_required
def reverse_geocode():
    try:
        latitude = float(request.args.get("lat", ""))
        longitude = float(request.args.get("lon", ""))
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise ValueError
        location = geolocator.reverse((latitude, longitude), exactly_one=True, language="en", timeout=10)
        return jsonify({
            "name": location.address if location else f"{latitude:.6f}, {longitude:.6f}",
            "latitude": latitude,
            "longitude": longitude
        })
    except (TypeError, ValueError):
        return jsonify({"error": "Enter valid latitude and longitude values."}), 400
    except Exception as error:
        app.logger.warning("Reverse geocoding failed: %s", error)
        return jsonify({"error": "Could not find a place for those coordinates."}), 502

def publish_static_image(filename):

    static_dir = os.path.join(
        os.path.dirname(__file__),
        "static_uploads"
    )

    os.makedirs(
        static_dir,
        exist_ok=True
    )

    source = os.path.join(
        UPLOAD_FOLDER,
        filename
    )

    destination = os.path.join(
        static_dir,
        filename
    )

    if os.path.isfile(source):

        image = cv2.imread(source)

        if image is not None:

            cv2.imwrite(
                destination,
                image
            )

            return url_for(
                "static_uploads",
                filename=filename
            )

    return None
@app.route("/auto-analyze", methods=["GET", "POST"])
@login_required
def auto_analyze():
    if request.method == "GET":
        return render_template("auto_analyze.html")

    try:

        # --------------------------------
        # Read request
        # --------------------------------

        area_name = request.form.get(
            "area_name",
            ""
        ).strip()
        requested_latitude = request.form.get("latitude", "").strip()
        requested_longitude = request.form.get("longitude", "").strip()

        from_date = request.form.get(
            "from_date",
            ""
        ).strip()

        to_date = request.form.get(
            "to_date",
            ""
        ).strip()

        if not area_name and not requested_latitude and not requested_longitude:
            return jsonify({
                "error": "Enter a place name or both coordinates."
            }), 400

        try:
            if requested_latitude and requested_longitude:
                lat = float(requested_latitude)
                lon = float(requested_longitude)
                if not -90 <= lat <= 90 or not -180 <= lon <= 180:
                    raise ValueError
                if not area_name:
                    location = geolocator.reverse((lat, lon), exactly_one=True, language="en", timeout=10)
                    area_name = location.address if location else f"{lat:.6f}, {lon:.6f}"
            else:
                lat, lon = get_area_coordinates(area_name)
        except ValueError:
            return jsonify({"error": "Coordinates must be latitude -90..90 and longitude -180..180."}), 400

        if lat is None or lon is None:
            return jsonify({
                "error": "Location could not be found."
            }), 404

        if not from_date or not to_date:
            return jsonify({
                "error": "Both dates are required."
            }), 400

        try:
            parsed_from = datetime.strptime(from_date, "%Y-%m-%d").date()
            parsed_to = datetime.strptime(to_date, "%Y-%m-%d").date()
        except ValueError:
            return jsonify({"error": "Dates must use YYYY-MM-DD format."}), 400
        if parsed_from >= parsed_to:
            return jsonify({"error": "From date must be earlier than To date."}), 400
        if parsed_to > datetime.now().date():
            return jsonify({"error": "To date cannot be in the future."}), 400

        temporal_analysis = None
        if request.form.get("temporal_analysis") == "1":
            from temporal_monitor import (
                assess_temporal_patterns,
                fetch_dynamic_world_series,
            )

            try:
                monthly_series = fetch_dynamic_world_series(
                    lat,
                    lon,
                    from_date,
                    to_date,
                    area_size=0.01,
                    project=os.getenv("EARTH_ENGINE_PROJECT"),
                )
            except (RuntimeError, ValueError) as error:
                return jsonify({"error": str(error)}), 503

            temporal_analysis = assess_temporal_patterns(monthly_series)
            temporal_analysis["monthly_series"] = monthly_series

        before_date = from_date
        after_date = to_date
        # --------------------------------
        # File paths
        # --------------------------------

        safe_area = secure_filename(
            area_name.replace(" ", "_")
        )

        before_filename = (
            f"before_{safe_area}_{before_date}.png"
        )

        after_filename = (
            f"after_{safe_area}_{after_date}.png"
        )

        before_path = os.path.join(
            UPLOAD_FOLDER,
            before_filename
        )

        after_path = os.path.join(
            UPLOAD_FOLDER,
            after_filename
        )

        # --------------------------------
        # Download earlier image
        # --------------------------------

        before_scene = fetch_satellite_image(
            lat,
            lon,
            before_date,
            before_path,
            area_size=0.01
        )

        if before_scene is None:
            return jsonify({
                "error":
                    f"No Sentinel-2 scene with at least 80% clear pixels over "
                    f"the analyzed area is available within 14 days of "
                    f"{before_date}. Choose another date."
            }), 502

        # --------------------------------
        # Download recent image
        # --------------------------------

        after_scene = fetch_satellite_image(
            lat,
            lon,
            after_date,
            after_path,
            area_size=0.01
        )

        if after_scene is None:
            return jsonify({
                "error":
                    f"No Sentinel-2 scene with at least 80% clear pixels over "
                    f"the analyzed area is available within 14 days of "
                    f"{after_date}. Choose another date."
            }), 502

        # --------------------------------
        # Publish satellite images
        # --------------------------------

        before_url = publish_static_image(
            before_filename
        )

        after_url = publish_static_image(
            after_filename
        )

        # --------------------------------
        # OpenCV change detection
        # --------------------------------

        valid_mask = cv2.bitwise_and(
            before_scene["clear_mask"],
            after_scene["clear_mask"]
        )
        valid_pixel_count = int(np.count_nonzero(valid_mask))
        if valid_pixel_count == 0:
            return jsonify({
                "error": "The two scenes have no overlapping clear pixels to compare."
            }), 422

        change_value = detect_changes(
            before_path,
            after_path,
            valid_mask
        )

        # --------------------------------
        # Determine alert
        # --------------------------------

        alert = "No Significant Change"

        changed_ratio = change_value / valid_pixel_count
        temporal_pattern_detected = (
            temporal_analysis is not None
            and temporal_analysis["status"] == "assessed"
            and any(
                pattern["type"] != "no_strong_regional_pattern"
                for pattern in temporal_analysis["patterns"]
            )
        )
        if temporal_pattern_detected:
            alert = "Potential Temporal Land-cover Pattern"
        elif changed_ratio >= 0.005:
            alert = "Observed Image Change"

        # --------------------------------
        # Save analysis
        # --------------------------------

        with sqlite3.connect(
            DB_PATH,
            timeout=10
        ) as conn:

            c = conn.cursor()

            c.execute("""
                INSERT INTO analysis (
                    user_id,
                    area_name,
                    before_filename,
                    after_filename,
                    change_value,
                    alert,
                    latitude,
                    longitude,
                    from_date,
                    to_date,
                    before_scene_date,
                    after_scene_date,
                    before_clear_percent,
                    after_clear_percent,
                    changed_ratio,
                    temporal_analysis_json,
                    status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                session.get("user_id"),
                area_name,
                before_filename,
                after_filename,
                change_value,
                alert,
                lat,
                lon,
                before_date,
                after_date,
                before_scene["date"],
                after_scene["date"],
                before_scene["area_clear_percent"],
                after_scene["area_clear_percent"],
                round(changed_ratio, 4),
                json.dumps(temporal_analysis) if temporal_analysis else None,
                "success"
            ))

            analysis_id = c.lastrowid

            conn.commit()

        # --------------------------------
        # Create visualization
        # --------------------------------

        visualization_filename = (
            f"change_{analysis_id}_{safe_area}.png"
        )

        visualization_path = os.path.join(
            UPLOAD_FOLDER,
            visualization_filename
        )

        visualization_created = (
            create_change_visualization(
                before_path,
                after_path,
                visualization_path,
                valid_mask
            )
        )

        visualization_url = None

        if visualization_created:

            static_dir = os.path.join(
                os.path.dirname(__file__),
                "static_uploads"
            )

            os.makedirs(
                static_dir,
                exist_ok=True
            )

            static_path = os.path.join(
                static_dir,
                visualization_filename
            )

            image = cv2.imread(
                visualization_path
            )

            if image is not None:
                cv2.imwrite(
                    static_path,
                    image
                )

                visualization_url = url_for(
                    "static_uploads",
                    filename=visualization_filename
                )

        # --------------------------------
        # Response
        # --------------------------------

        return jsonify({

            "status": "success",

            "area": area_name,

            "latitude": lat,

            "longitude": lon,

            "from_date": before_date,
            "to_date": after_date,
            "before_date": before_scene["date"],
            "after_date": after_scene["date"],
            "before_scene_cloud_cover": before_scene["cloud_cover"],
            "after_scene_cloud_cover": after_scene["cloud_cover"],
            "before_area_clear_percent": before_scene["area_clear_percent"],
            "after_area_clear_percent": after_scene["area_clear_percent"],
            "change_value":
                change_value,

            "changed_ratio":
                round(changed_ratio, 4),

            "before_land_cover": "unclassified",
            "after_land_cover": "unclassified",
            "land_cover_note":
                "True-color imagery alone cannot reliably classify land cover.",
            "legality": "undetermined",
            "temporal_analysis": temporal_analysis or {
                "status": "not_requested",
                "model": "Google Dynamic World V1 (pre-trained land-cover model)",
                "legality": "unverified",
            },

            "alert":
                alert,

            "id":
                analysis_id,

            "before_image":
                before_url,

            "after_image":
                after_url,

            "change_visualization":
                visualization_url
        })

    except ValueError as e:

        return jsonify({
            "error": str(e)
        }), 400

    except Exception as e:

        print(
            "AUTO ANALYZE ERROR:",
            repr(e)
        )

        return jsonify({
            "error":
                "Automatic analysis failed. "
                "Check the server terminal "
                "for details."
        }), 500
# ==================== Traditional Upload Routes ====================

@app.route("/upload")
@login_required
def upload_page():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
@login_required
def upload():
    """Upload and analyze images manually."""
    if 'before' not in request.files or 'after' not in request.files:
        return jsonify({"status": "error", "message": "Both images required"}), 400

    before_file = request.files['before']
    after_file = request.files['after']

    if before_file.filename == '' or after_file.filename == '':
        return jsonify({"status": "error", "message": "Empty filename"}), 400

    try:
        before_fname, before_path = save_uploaded_image(before_file, 'before')
        after_fname, after_path = save_uploaded_image(after_file, 'after')
    except ValueError as error:
        return jsonify({"status": "error", "message": str(error)}), 400

    try:
        result = detect_changes(before_path, after_path)
        alert = 'No Significant Change'
        
        if result > 1000:
            alert = 'Observed Image Change'
        
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            c = conn.cursor()
            c.execute('''
                INSERT INTO analysis (user_id, before_filename, after_filename, change_value, alert, status)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (session.get('user_id'), before_fname, after_fname, result, alert, 'success'))
            analysis_id = c.lastrowid
            conn.commit()
        
        change_viz_filename = f"change_{analysis_id}_{before_fname}"
        change_viz_path = os.path.join(UPLOAD_FOLDER, change_viz_filename)
        viz_created = create_change_visualization(before_path, after_path, change_viz_path)
        static_viz_url = None
        if viz_created:
            static_dir = os.path.join(os.path.dirname(__file__), 'static_uploads')
            os.makedirs(static_dir, exist_ok=True)
            static_viz_path = os.path.join(static_dir, change_viz_filename)
            cv2.imwrite(static_viz_path, cv2.imread(change_viz_path))
            static_viz_url = url_for('static_uploads', filename=change_viz_filename)
        
        return jsonify({
            "status": "Change Detected",
            "value": result,
            "alert": alert,
            "id": analysis_id,
            "change_visualization": static_viz_url
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400


@app.route("/detect")
@login_required
def detect():
    before = request.args.get("before", "before.png")
    after = request.args.get("after", "after.png")
    try:
        return jsonify({"status": "Change Detected", "value": detect_changes(before, after)})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400


@app.route("/analyze", methods=["GET", "POST"])
@login_required
def analyze():
    if request.method == "GET":
        return render_template("analyze.html")

    if "before" not in request.files or "after" not in request.files:
        return jsonify({"error": "Both before and after images required."}), 400

    before_file = request.files["before"]
    after_file = request.files["after"]
    if not before_file.filename or not after_file.filename:
        return jsonify({"error": "Empty filename provided."}), 400

    try:
        coordinates = [
            float(request.form[field])
            for field in ("tl_lat", "tl_lon", "br_lat", "br_lon")
        ]
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "Invalid geographic coordinates."}), 400

    try:
        before_fname, before_path = save_uploaded_image(before_file, 'analyze_before')
        after_fname, after_path = save_uploaded_image(after_file, 'analyze_after')
    except ValueError as error:
        return jsonify({"error": str(error)}), 400

    try:
        value, boxes, width, height = detect_anomalies(before_path, after_path)
        static_dir = os.path.join(os.path.dirname(__file__), "static_uploads")
        os.makedirs(static_dir, exist_ok=True)
        static_after_path = os.path.join(static_dir, after_fname)
        cv2.imwrite(static_after_path, cv2.imread(after_path))
        return jsonify({
            "change_value": value,
            "boxes": boxes,
            "width": width,
            "height": height,
            "tl_lat": coordinates[0],
            "tl_lon": coordinates[1],
            "br_lat": coordinates[2],
            "br_lon": coordinates[3],
            "after_url": url_for("static_uploads", filename=after_fname)
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/dashboard")
@login_required
def dashboard():
    """Display dashboard."""
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute('''
            SELECT * FROM analysis WHERE user_id = ? ORDER BY timestamp DESC LIMIT 20
        ''', (session.get('user_id'),))
        analyses = c.fetchall()
    
    return render_template("dashboard.html", analyses=analyses)


@app.route("/api/analysis", methods=["GET"])
@login_required
def get_analysis_history():
    """Get user's analysis history."""
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute('''
            SELECT * FROM analysis WHERE user_id = ? ORDER BY timestamp DESC
        ''', (session.get('user_id'),))
        rows = c.fetchall()
    
    analyses = [dict(row) for row in rows]
    return jsonify(analyses)


@app.route("/api/stats", methods=["GET"])
@login_required
def get_stats():
    """Get user's statistics."""
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        c = conn.cursor()
        
        c.execute(
            "SELECT COUNT(*) as total FROM analysis WHERE user_id = ?",
            (session.get('user_id'),)
        )
        total = c.fetchone()[0]
        
        c.execute("""
            SELECT
                AVG(change_value),
                MAX(change_value),
                MIN(change_value),
                SUM(CASE
                    WHEN alert != 'No Significant Change' AND status = 'success'
                    THEN 1 ELSE 0
                END)
            FROM analysis
            WHERE user_id = ?
        """, (session.get('user_id'),))
        avg_change, max_change, min_change, potential_alerts = c.fetchone()
    
    return jsonify({
        "total_analyses": total,
        "average_change": round(avg_change or 0, 2),
        "max_change": max_change or 0,
        "min_change": min_change or 0,
        "potential_alerts": potential_alerts or 0
    })


@app.route("/")
def index():
    """Home page."""
    if session.get('user_id'):
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))


@app.route("/api/analysis/<int:analysis_id>", methods=["DELETE"])
@login_required
def delete_analysis(analysis_id):
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM analysis WHERE id = ? AND user_id = ?",
            (analysis_id, session.get("user_id"))
        )
        if cursor.rowcount == 0:
            return jsonify({"error": "Analysis not found"}), 404
        conn.commit()
    return jsonify({"status": "deleted", "id": analysis_id})


@app.route("/report", methods=["GET", "POST"])
@login_required
def report():
    if request.method == "GET":
        return render_template("report.html")

    before_file = request.files.get("before_image")
    after_file = request.files.get("after_image")
    try:
        before_filename = save_uploaded_image(before_file, 'report_before')[0] if before_file and before_file.filename else None
        after_filename = save_uploaded_image(after_file, 'report_after')[0] if after_file and after_file.filename else None
    except ValueError as error:
        return jsonify({"error": str(error)}), 400

    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        conn.execute(
            """INSERT INTO reports
            (user_id, user_name, land_owner, description, latitude, longitude, before_image, after_image)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session.get("user_id"), request.form.get("user_name", session.get("username", "")),
                request.form.get("land_owner", ""), request.form.get("description", ""),
                request.form.get("latitude", type=float), request.form.get("longitude", type=float),
                before_filename, after_filename
            )
        )
        conn.commit()
    return jsonify({"status": "submitted"})


@app.route("/reports")
@login_required
def reports_view():
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        rows = conn.execute(
            """SELECT id, user_name, land_owner, description, latitude, longitude, timestamp, status
            FROM reports WHERE user_id = ? ORDER BY timestamp DESC""",
            (session.get("user_id"),)
        ).fetchall()
    return render_template("reports.html", reports=rows)


@app.route("/landowners", methods=["GET", "POST"])
@login_required
def landowners():
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        if request.method == "POST":
            conn.execute(
                "INSERT INTO landowners (user_id, name, contact, notes) VALUES (?, ?, ?, ?)",
                (session.get("user_id"), request.form.get("name", ""),
                 request.form.get("contact", ""), request.form.get("notes", ""))
            )
            conn.commit()
        owners = conn.execute(
            "SELECT id, name, contact, notes FROM landowners WHERE user_id = ?",
            (session.get("user_id"),)
        ).fetchall()
    return render_template("landowners.html", owners=owners)


@app.route("/visualization")
@login_required
def visualization():
    return render_template("visualization.html")


@app.route("/api/reports")
@login_required
def api_reports():
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        points = conn.execute(
            "SELECT latitude, longitude FROM reports WHERE user_id = ? AND latitude IS NOT NULL AND longitude IS NOT NULL",
            (session.get("user_id"),)
        ).fetchall()
    return jsonify([[latitude, longitude, 0.5] for latitude, longitude in points])


@app.route("/map")
@login_required
def map_view():
    return render_template("map.html")


@app.route("/area-select")
@login_required
def area_select():
    return render_template("area_select.html")


@app.route("/api/save_area", methods=["POST"])
@login_required
def save_area():
    payload = request.get_json(silent=True) or {}
    south_west = payload.get("sw") or {}
    north_east = payload.get("ne") or {}
    try:
        area_name = f"area:{float(south_west['lat']):.5f},{float(south_west['lng']):.5f}:{float(north_east['lat']):.5f},{float(north_east['lng']):.5f}"
        center_lat = (float(south_west['lat']) + float(north_east['lat'])) / 2
        center_lon = (float(south_west['lng']) + float(north_east['lng'])) / 2
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "Invalid area coordinates"}), 400

    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO area_cache (area_name, latitude, longitude) VALUES (?, ?, ?)",
            (area_name, center_lat, center_lon)
        )
        conn.commit()
    return jsonify({"status": "saved", "area_name": area_name})


@app.route('/static_uploads/<path:filename>')
def static_uploads(filename):
    from flask import send_from_directory
    return send_from_directory(os.path.join(os.path.dirname(__file__), 'static_uploads'), filename)


@app.errorhandler(404)
def not_found(error):
    """404 handler."""
    if session.get('user_id'):
        return render_template('404.html'), 404
    return redirect(url_for('login', next=request.path))


if __name__ == "__main__":
    app.run(
        host=os.getenv('FLASK_HOST', '127.0.0.1'),
        port=int(os.getenv('FLASK_PORT', '8000')),
        debug=os.getenv('FLASK_DEBUG', '0') == '1',
        use_reloader=False
    )
