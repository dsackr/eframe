#!/usr/bin/env python3
import logging
import os
import signal
import socket
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from flask import Flask, abort, jsonify, redirect, render_template, request, send_from_directory, url_for
from PIL import Image, ImageOps
from werkzeug.utils import secure_filename

# --- CONFIGURATION ---
BASE_DIR = Path(__file__).resolve().parent
LIB_DIR = BASE_DIR / "lib"
UPLOAD_DIR = BASE_DIR / "uploads"

# Display Config
PANEL_WIDTH = 1600
PANEL_HEIGHT = 1200
DEFAULT_DISPLAY_ROTATION = 180

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

# Add lib to path
if LIB_DIR.exists():
    sys.path.append(str(LIB_DIR))

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 20 * 1024 * 1024
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp'}
os.makedirs(UPLOAD_DIR, exist_ok=True)

# --- FRAIMIC COMPATIBILITY ---
# Spectra 6 palette — maps nibble values (0-5) back to RGB
# Used to decode .bin files sent by fraimic-controller
SPECTRA6_RGB = [
    (0,   0,   0),    # 0: Black
    (255, 255, 255),  # 1: White
    (0,   255, 0),    # 2: Green
    (0,   0,   255),  # 3: Blue
    (255, 0,   0),    # 4: Red
    (255, 255, 0),    # 5: Yellow
]

# Stable device ID derived from hostname (survives reboots, looks like a real frame)
DEVICE_ID = str(uuid.uuid5(uuid.NAMESPACE_DNS, socket.gethostname()))

# Track the last displayed file so /api/refresh can replay it
_last_displayed: Optional[str] = None


def get_local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def bin_to_image(bin_data: bytes, width: int = PANEL_WIDTH, height: int = PANEL_HEIGHT) -> Image.Image:
    """
    Decode a Fraimic .bin file back to a PIL RGB image.

    .bin format: raw 4bpp, no header
      - high nibble = left pixel color index (0-5)
      - low  nibble = right pixel color index (0-5)
    """
    pixels = []
    for byte in bin_data:
        hi = (byte >> 4) & 0xF
        lo = byte & 0xF
        pixels.append(SPECTRA6_RGB[min(hi, 5)])
        pixels.append(SPECTRA6_RGB[min(lo, 5)])
    img = Image.new('RGB', (width, height))
    img.putdata(pixels)
    return img


# --- UTILITIES ---

def signal_handler(sig, frame):
    logger.info('Shutting down display app...')
    sys.exit(0)


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


def allowed_file(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def get_image_path(filename: str) -> Optional[Path]:
    safe_name = secure_filename(filename)
    if safe_name != filename or not allowed_file(safe_name):
        return None
    path = UPLOAD_DIR / safe_name
    if not path.exists():
        return None
    return path


def get_target_size_for_image(img: Image.Image, base_size=(PANEL_WIDTH, PANEL_HEIGHT)):
    width, height = img.size
    base_width, base_height = base_size
    if height > width:
        return base_height, base_width
    return base_width, base_height


def process_image_for_display(
    img: Image.Image,
    base_size=(PANEL_WIDTH, PANEL_HEIGHT),
    mode: str = 'letterbox',
    rotation_degrees: int = 0,
    bg_color: str = 'white',
    auto_orient: bool = True,
) -> Image.Image:
    if img.mode != 'RGB':
        img = img.convert('RGB')
    img = ImageOps.exif_transpose(img)

    if rotation_degrees % 360 != 0:
        img = img.rotate(rotation_degrees, expand=True)

    target_width, target_height = base_size
    if auto_orient:
        target_width, target_height = get_target_size_for_image(img, base_size)

    img_width, img_height = img.size

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
        canvas = Image.new('RGB', (target_width, target_height), bg_color)
        x = (target_width - new_width) // 2
        y = (target_height - new_height) // 2
        canvas.paste(img, (x, y))
        img = canvas
    return img


def display_on_epaper(img_path: str, mode: str = 'letterbox', rotation_degrees: int = DEFAULT_DISPLAY_ROTATION) -> bool:
    global _last_displayed
    if not os.path.exists(img_path):
        return False
    try:
        import epd13in3E

        logger.info(f"Displaying {img_path} ({mode}, {rotation_degrees} deg)")
        epd = epd13in3E.EPD()
        epd.Init()

        img = Image.open(img_path)
        img_processed = process_image_for_display(
            img,
            (PANEL_WIDTH, PANEL_HEIGHT),
            mode=mode,
            rotation_degrees=rotation_degrees,
        )

        epd.display(epd.getbuffer(img_processed))
        time.sleep(2)
        epd.sleep()
        _last_displayed = img_path
        return True
    except ImportError:
        logger.warning("EPD driver not found (Dev Mode)")
        _last_displayed = img_path
        return False
    except Exception as e:
        logger.error(f"EPD Error: {e}")
        return False


def get_uploaded_files(limit: int = 24):
    files = []
    for fname in os.listdir(UPLOAD_DIR):
        if not allowed_file(fname):
            continue
        path = UPLOAD_DIR / fname
        files.append(
            {
                'name': fname,
                'uploaded': datetime.fromtimestamp(path.stat().st_mtime),
                'size_kb': round(path.stat().st_size / 1024),
            }
        )
    files.sort(key=lambda f: f['uploaded'], reverse=True)
    return files[:limit]


def flip_image_file(path: Path) -> None:
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)
    flipped = img.rotate(180, expand=True)
    flipped.save(path)


# --- ROUTES ---

@app.route('/')
def index():
    files = get_uploaded_files()
    return render_template('index.html', files=files)


@app.route('/upload', methods=['POST'])
def upload():
    # ── Fraimic-compatible path ──────────────────────────────────────────────
    # fraimic-controller sends field name 'image' with a .bin file.
    fraimic_file = request.files.get('image')
    if fraimic_file and fraimic_file.filename.lower().endswith('.bin'):
        bin_data = fraimic_file.read()
        expected = PANEL_WIDTH * PANEL_HEIGHT // 2  # 960,000 bytes for 1600×1200

        if len(bin_data) != expected:
            logger.warning(f"Fraimic upload: unexpected size {len(bin_data)} (expected {expected})")
            return jsonify({'error': f'unexpected size {len(bin_data)}, expected {expected}'}), 400

        img = bin_to_image(bin_data)
        fname = f"fraimic_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        path = UPLOAD_DIR / fname
        img.save(path)

        # Display with the panel's default rotation; image is already 1600×1200
        # so mode doesn't matter — use crop to avoid any re-scaling artefacts
        display_on_epaper(str(path), mode='crop', rotation_degrees=DEFAULT_DISPLAY_ROTATION)
        return jsonify({'ok': True}), 200

    # ── Original web UI path ─────────────────────────────────────────────────
    f = request.files.get('file')
    if f and allowed_file(f.filename):
        fname = secure_filename(f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{f.filename}")
        path = UPLOAD_DIR / fname
        f.save(path)

        mode = request.form.get('mode', 'letterbox')
        if request.form.get('display_immediately'):
            display_on_epaper(str(path), mode=mode)
    return redirect(url_for('index'))


@app.route('/display/<filename>', methods=['POST'])
def display(filename):
    path = get_image_path(filename)
    if not path:
        abort(404)

    mode = request.form.get('mode', 'letterbox')
    rotation = int(request.form.get('rotation', DEFAULT_DISPLAY_ROTATION))
    display_on_epaper(str(path), mode=mode, rotation_degrees=rotation)
    return redirect(url_for('index'))


@app.route('/flip/<filename>', methods=['POST'])
def flip(filename):
    path = get_image_path(filename)
    if not path:
        abort(404)

    flip_image_file(path)
    return redirect(url_for('index'))


@app.route('/preview/<filename>')
def preview(filename):
    path = get_image_path(filename)
    if not path:
        abort(404)
    return send_from_directory(str(UPLOAD_DIR), path.name)


@app.route('/delete/<filename>', methods=['POST'])
def delete(filename):
    path = get_image_path(filename)
    if not path:
        abort(404)

    try:
        path.unlink()
    except OSError as exc:
        logger.error(f"Failed to delete {path}: {exc}")
        abort(500)

    return redirect(url_for('index'))


@app.route('/upload_image')
def upload_page():
    return redirect(url_for('index'))


# --- FRAIMIC-COMPATIBLE API ENDPOINTS ---
# These make eframe look like a standard Fraimic frame on the local network.
# fraimic-controller discovers frames via GET /api/info and pushes images via POST /upload.

@app.route('/api/info', methods=['GET'])
def api_info():
    """Return device status in Fraimic frame format."""
    return jsonify({
        'device_id':       DEVICE_ID,
        'battery_pct':     100,          # Pi is always plugged in
        'firmware_version': 'eframe-1.0.0',
        'ip_address':      get_local_ip(),
        'wifi_ssid':       '',
        'display_type':    'spectra6_13in3',
        'width':           PANEL_WIDTH,
        'height':          PANEL_HEIGHT,
    })


@app.route('/api/refresh', methods=['POST'])
def api_refresh():
    """Re-display the last image — mirrors the Fraimic frame refresh endpoint."""
    if _last_displayed and os.path.exists(_last_displayed):
        display_on_epaper(_last_displayed, mode='crop', rotation_degrees=DEFAULT_DISPLAY_ROTATION)
        return jsonify({'status': 'refresh_started'}), 200
    return jsonify({'error': 'no image to refresh'}), 404


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80)
