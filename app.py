#!/usr/bin/env python3
import logging
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from flask import Flask, abort, redirect, render_template, request, send_from_directory, url_for
from PIL import Image, ImageOps
from werkzeug.utils import secure_filename

# --- CONFIGURATION ---
BASE_DIR = Path(__file__).resolve().parent
LIB_DIR = BASE_DIR / "lib"
UPLOAD_DIR = BASE_DIR / "uploads"

# Display Config
PANEL_WIDTH = 1600
PANEL_HEIGHT = 1200

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


def display_on_epaper(img_path: str, mode: str = 'letterbox', rotation_degrees: int = 0) -> bool:
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
        return True
    except ImportError:
        logger.warning("EPD driver not found (Dev Mode)")
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


def rotate_image_file(path: Path, degrees: int) -> None:
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)
    rotated = img.rotate(degrees, expand=True)
    rotated.save(path)


# --- ROUTES ---


@app.route('/')
def index():
    files = get_uploaded_files()
    return render_template('index.html', files=files)


@app.route('/upload', methods=['POST'])
def upload():
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
    rotation = int(request.form.get('rotation', 0))
    display_on_epaper(str(path), mode=mode, rotation_degrees=rotation)
    return redirect(url_for('index'))


@app.route('/rotate/<filename>', methods=['POST'])
def rotate(filename):
    path = get_image_path(filename)
    if not path:
        abort(404)

    direction = request.form.get('direction', 'right')
    degrees = -90 if direction == 'left' else 90
    rotate_image_file(path, degrees)
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


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
