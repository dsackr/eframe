#!/usr/bin/env python3
import sys
import os
import json
import time
import threading
import schedule
import uuid
import signal
import logging
from pathlib import Path
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, send_from_directory
from PIL import Image, ImageDraw, ImageFont, ImageOps
from werkzeug.utils import secure_filename

# --- CONFIGURATION ---
BASE_DIR = Path(__file__).resolve().parent
LIB_DIR = BASE_DIR / "lib"
STATIC_DIR = BASE_DIR / "static"
UPLOAD_DIR = BASE_DIR / "uploads"
DATA_FILE = BASE_DIR / 'data.json'
BACKGROUND_IMAGE = STATIC_DIR / 'background.png'
OUTPUT_IMAGE = STATIC_DIR / 'current_sign.png'

# Font sizes
FONT_SIZE_DAYS = 400
FONT_SIZE_PRIOR_COUNT = 150
FONT_SIZE_INCIDENT = 100
FONT_SIZE_CHECKMARK = 80

# Positioning
DAYS_Y_POSITION = 160
DAYS_X_OFFSET = 0
PRIOR_COUNT_X = 220
PRIOR_COUNT_Y = 630
INCIDENT_X_OFFSET = 70
INCIDENT_Y = 650
CHECKMARK_X = 940
CHECKMARK_CHANGE_Y = 575
CHECKMARK_DEPLOY_Y = 645
CHECKMARK_MISSED_Y = 705

# Display Config
PANEL_WIDTH = 1600
PANEL_HEIGHT = 1200

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Add lib to path
if LIB_DIR.exists():
    sys.path.append(str(LIB_DIR))

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 20 * 1024 * 1024 
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp'}
os.makedirs(UPLOAD_DIR, exist_ok=True)

# --- UTILITIES ---

def signal_handler(sig, frame):
    logger.info('Shutting down Safety Tracker...')
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r') as f:
            return json.load(f)
    return {
        'days_since': 0, 'prior_count': 0, 'incident_number': '000',
        'incident_date': datetime.now().strftime('%Y-%m-%d'),
        'prior_incident_date': datetime.now().strftime('%Y-%m-%d'),
        'reason': 'Change'
    }

def save_data(data):
    with open(DATA_FILE, 'w') as f:
        json.dump(data, f, indent=2)

def get_font(size):
    # Try local font first, then system font, then default
    local_font = STATIC_DIR / 'DejaVuSans-Bold.ttf'
    system_font = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
    
    try:
        if local_font.exists():
            return ImageFont.truetype(str(local_font), size)
        elif os.path.exists(system_font):
            return ImageFont.truetype(system_font, size)
    except:
        pass
    return ImageFont.load_default()

def process_image_for_display(img, target_size=(PANEL_WIDTH, PANEL_HEIGHT), mode='letterbox', bg_color='white'):
    if img.mode != 'RGB':
        img = img.convert('RGB')
    img = ImageOps.exif_transpose(img)
    
    img_width, img_height = img.size
    target_width, target_height = target_size
    
    if mode == 'crop':
        scale = max(target_width / img_width, target_height / img_height)
        new_width = int(img_width * scale)
        new_height = int(img_height * scale)
        img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
        x = (new_width - target_width) // 2
        y = (new_height - target_height) // 2
        img = img.crop((x, y, x + target_width, y + target_height))
    else:
        scale = min(target_width / img_width, target_height / img_height)
        new_width = int(img_width * scale)
        new_height = int(img_height * scale)
        img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
        canvas = Image.new('RGB', target_size, bg_color)
        x = (target_width - new_width) // 2
        y = (target_height - new_height) // 2
        canvas.paste(img, (x, y))
        img = canvas
    return img

def display_on_epaper(img_path, crop_mode=False):
    if not os.path.exists(img_path):
        return False
    try:
        import epd13in3E
        logger.info(f"Displaying {img_path}")
        epd = epd13in3E.EPD()
        epd.Init()
        
        img = Image.open(img_path)
        mode = 'crop' if crop_mode else 'letterbox'
        img_processed = process_image_for_display(img, (PANEL_WIDTH, PANEL_HEIGHT), mode=mode)
        
        epd.display(epd.getbuffer(img_processed))
        time.sleep(2)
        epd.sleep()
        return True
    except ImportError:
        logger.warning("EPD driver not found (Dev Mode)")
        return False
    except Exception as e:
        logger.error(f"EPD Error: {e}")
        return False

def generate_sign(auto_display=False):
    data = load_data()
    
    # Recalculate days
    if 'incident_date' in data:
        incident_date = datetime.strptime(data['incident_date'], '%Y-%m-%d')
        data['days_since'] = (datetime.now() - incident_date).days

    # Recalculate prior record
    if 'prior_incident_date' in data and 'incident_date' in data:
        prior = datetime.strptime(data['prior_incident_date'], '%Y-%m-%d')
        curr = datetime.strptime(data['incident_date'], '%Y-%m-%d')
        data['prior_count'] = (curr - prior).days
        
    # Check if background exists, create placeholder if not
    if not os.path.exists(BACKGROUND_IMAGE):
        img = Image.new('RGB', (PANEL_WIDTH, PANEL_HEIGHT), color='white')
    else:
        img = Image.open(BACKGROUND_IMAGE)
        
    draw = ImageDraw.Draw(img)
    w, h = img.size
    
    # Draw Days
    font_days = get_font(FONT_SIZE_DAYS)
    bbox = draw.textbbox((0,0), str(data['days_since']), font=font_days)
    draw.text(((w - (bbox[2]-bbox[0]))//2 + DAYS_X_OFFSET, DAYS_Y_POSITION), str(data['days_since']), font=font_days, fill='black')
    
    # Draw Prior
    font_count = get_font(FONT_SIZE_PRIOR_COUNT)
    bbox_p = draw.textbbox((0,0), str(data['prior_count']), font=font_count)
    draw.text((PRIOR_COUNT_X - (bbox_p[2]-bbox_p[0])//2, PRIOR_COUNT_Y), str(data['prior_count']), font=font_count, fill='white')
    
    # Draw Incident #
    font_inc = get_font(FONT_SIZE_INCIDENT)
    bbox_i = draw.textbbox((0,0), str(data['incident_number']), font=font_inc)
    draw.text(((w//2) - (bbox_i[2]-bbox_i[0])//2 + INCIDENT_X_OFFSET, INCIDENT_Y), str(data['incident_number']), font=font_inc, fill='white')
    
    # Draw Checkmark
    font_check = get_font(FONT_SIZE_CHECKMARK)
    reason_map = {
        'Change': CHECKMARK_CHANGE_Y,
        'Deploy': CHECKMARK_DEPLOY_Y,
        'Missed': CHECKMARK_MISSED_Y
    }
    if data['reason'] in reason_map:
        draw.text((CHECKMARK_X, reason_map[data['reason']]), '✓', font=font_check, fill='blue')
        
    img.save(OUTPUT_IMAGE)
    if auto_display:
        display_on_epaper(OUTPUT_IMAGE)
    return OUTPUT_IMAGE

# --- ROUTES ---

@app.route('/')
def index():
    return render_template('index.html', data=load_data())

@app.route('/update', methods=['POST'])
def update():
    data = load_data()
    # If date changed, update prior record logic
    new_date = request.form.get('incident_date')
    if new_date and new_date != data.get('incident_date'):
        data['prior_incident_date'] = data.get('incident_date')
    
    data['incident_number'] = request.form.get('incident_number')
    data['incident_date'] = new_date
    data['reason'] = request.form.get('reason')
    save_data(data)
    generate_sign(auto_display=True)
    return redirect(url_for('index'))

@app.route('/upload_image')
def upload_page():
    files = sorted([f for f in os.listdir(UPLOAD_DIR) if allowed_file(f)], reverse=True)[:20]
    return render_template('upload.html', files=files)

@app.route('/upload', methods=['POST'])
def upload():
    f = request.files.get('file')
    if f and allowed_file(f.filename):
        fname = secure_filename(f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{f.filename}")
        path = os.path.join(UPLOAD_DIR, fname)
        f.save(path)
        if request.form.get('display_immediately'):
            display_on_epaper(path, crop_mode=(request.form.get('crop_mode')=='on'))
    return redirect(url_for('upload_page'))

@app.route('/preview/<filename>')
def preview(filename):
    return send_from_directory(UPLOAD_DIR, filename)

@app.route('/force_refresh', methods=['POST'])
def force_refresh():
    generate_sign(auto_display=True)
    return redirect(url_for('index'))

# --- SCHEDULE ---
def run_schedule():
    while True:
        schedule.run_pending()
        time.sleep(60)

schedule.every().day.at("18:00").do(lambda: generate_sign(auto_display=True))
threading.Thread(target=run_schedule, daemon=True).start()

if __name__ == '__main__':
    generate_sign(auto_display=False)
    app.run(host='0.0.0.0', port=5000)
