#!/usr/bin/env python3
import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time
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

from lib import device_config, log_buffer, mdns_advertise, wifi_control  # noqa: E402

# Mirror the same log lines going to stdout into an in-memory ring buffer,
# so the /portal/logs page has something real to show.
log_buffer.install(logging.getLogger())

# Persistent per-device settings (device_key, orientation) - see
# lib/device_config.py for why these exist.
DEVICE_CONFIG_PATH = BASE_DIR / "device_config.json"
device_config.init(DEVICE_CONFIG_PATH)

# Matches the physical panel driven by lib/epd13in3E.py (EPD_WIDTH=1200,
# EPD_HEIGHT=1600) and the "13.3\" E-Ink" Device Type shown on a real
# frame's /info admin page.
DEVICE_TYPE = '13.3" E-Ink'

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 20 * 1024 * 1024
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp'}
os.makedirs(UPLOAD_DIR, exist_ok=True)

# --- FRAIMIC COMPATIBILITY ---
# Byte layout and dimensions match the fraimic-homeassistant integration's
# image_converter.py (github.com/dsackr/fraimic-homeassistant): 4-bit device
# codes (0x4 intentionally unused), two pixels per byte. Each row's left
# half and right half are packed separately — all left-half bytes for every
# row come first, then all right-half bytes.
#
# 1200x1600 portrait matches FRAME_RESOLUTIONS["13.3"] in that integration's
# const.py, and the physical panel's native orientation (see EPD_WIDTH/
# EPD_HEIGHT in lib/epd13in3E.py). Unlike a real frame, eframe's /api/info
# *does* report the effective width/height/orientation (see
# get_effective_display_dims() below) - ahead of the real frame, which
# still requires picking "13.3" manually in the Home Assistant integration
# (see FRAIMIC_WIDTH/FRAIMIC_HEIGHT as the underlying native/default size).
FRAIMIC_WIDTH = 1200
FRAIMIC_HEIGHT = 1600
FRAIMIC_CODE_TO_RGB = {
    0x0: (0,   0,   0),    # Black
    0x1: (255, 255, 255),  # White
    0x2: (255, 255, 0),    # Yellow
    0x3: (255, 0,   0),    # Red
    0x5: (0,   0,   255),  # Blue
    0x6: (0,   255, 0),    # Green
}

# Real frame limit per the Fraimic REST API guide ("file exceeds 1 MB")
MAX_FRAIMIC_IMAGE_BYTES = 1_000_000

START_TIME = time.monotonic()

# Track whatever was most recently displayed so /api/refresh can replay it.
# Fraimic pushes are kept in memory only (Home Assistant already stores the
# source image, so eframe doesn't need its own copy on disk).
_last_displayed: Optional[str] = None       # file path, for gallery-sourced displays
_last_fraimic_bin: Optional[bytes] = None   # raw .bin bytes, for Fraimic-sourced displays
_last_display_source: Optional[str] = None  # 'path' or 'fraimic' — whichever is most recent
_last_refresh_at: Optional[datetime] = None

# Held while a Fraimic .bin is being decoded/pushed to the panel, so a
# concurrent upload gets "buffer_not_ready" instead of racing the display.
_display_lock = threading.Lock()


def get_local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def get_wifi_ssid() -> str:
    try:
        result = subprocess.run(['iwgetid', '-r'], capture_output=True, text=True, timeout=2)
        return result.stdout.strip()
    except Exception:
        return ''


def get_wifi_rssi() -> Optional[int]:
    # /proc/net/wireless columns: face status link level noise ...
    try:
        with open('/proc/net/wireless') as f:
            lines = f.readlines()[2:]
        for line in lines:
            parts = line.split()
            if len(parts) >= 4:
                return int(float(parts[3]))
    except Exception:
        pass
    return None


def get_mac_address(iface: str = 'wlan0') -> str:
    try:
        with open(f'/sys/class/net/{iface}/address') as f:
            return f.read().strip().upper()
    except OSError:
        return '00:00:00:00:00:00'


def get_effective_display_dims():
    """Return (width_px, height_px, orientation) reflecting the current
    orientation setting (see lib/device_config.py) rather than assuming
    the panel's native portrait shape.

    Added for https://github.com/Fraimic/Fraimic_eink_canvas_home_assistant_restAPI_guide/issues/2
    and /issues/3 - integrations shouldn't have to hardcode a guess about
    a frame's resolution or orientation.
    """
    orientation = device_config.get().get('orientation', 'portrait')
    if orientation == 'landscape':
        return FRAIMIC_HEIGHT, FRAIMIC_WIDTH, 'landscape'
    return FRAIMIC_WIDTH, FRAIMIC_HEIGHT, 'portrait'


def bin_to_image(bin_data: bytes, width: int = FRAIMIC_WIDTH, height: int = FRAIMIC_HEIGHT) -> Image.Image:
    """
    Decode a Fraimic .bin file back to a PIL RGB image.

    Inverse of fraimic_bin_converter's generate_binary_file(): each row is
    split into a packed left half (cols 0..width/2-1) and packed right half
    (cols width/2..width-1), with all left halves (row-major) coming before
    all right halves.
    """
    half_width = width // 2
    bytes_per_half_row = half_width // 2
    half_size = bytes_per_half_row * height

    left_bytes = bin_data[:half_size]
    right_bytes = bin_data[half_size:half_size * 2]

    pixels = [(0, 0, 0)] * (width * height)
    for row in range(height):
        row_offset = row * width
        half_row_offset = row * bytes_per_half_row
        for col_byte in range(bytes_per_half_row):
            left_byte = left_bytes[half_row_offset + col_byte]
            right_byte = right_bytes[half_row_offset + col_byte]
            col = col_byte * 2
            pixels[row_offset + col] = FRAIMIC_CODE_TO_RGB.get(left_byte >> 4, (0, 0, 0))
            pixels[row_offset + col + 1] = FRAIMIC_CODE_TO_RGB.get(left_byte & 0xF, (0, 0, 0))
            pixels[row_offset + half_width + col] = FRAIMIC_CODE_TO_RGB.get(right_byte >> 4, (0, 0, 0))
            pixels[row_offset + half_width + col + 1] = FRAIMIC_CODE_TO_RGB.get(right_byte & 0xF, (0, 0, 0))

    img = Image.new('RGB', (width, height))
    img.putdata(pixels)
    return img


# --- UTILITIES ---

def signal_handler(sig, frame):
    logger.info('Shutting down display app...')
    mdns_advertise.stop()
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


def render_to_epaper(img: Image.Image, mode: str = 'letterbox', rotation_degrees: int = DEFAULT_DISPLAY_ROTATION) -> bool:
    """Push a PIL image to the e-paper panel.

    Returns True on real success or when the driver is simply absent (dev
    mode) — both count as "displayed" for /api/refresh's purposes — and
    False only on a genuine hardware/runtime error, which shouldn't be
    replayed as if it were the current image.
    """
    global _last_refresh_at
    try:
        import epd13in3E

        logger.info(f"Displaying image ({mode}, {rotation_degrees} deg)")
        epd = epd13in3E.EPD()
        epd.Init()

        img_processed = process_image_for_display(
            img,
            (PANEL_WIDTH, PANEL_HEIGHT),
            mode=mode,
            rotation_degrees=rotation_degrees,
        )

        epd.display(epd.getbuffer(img_processed))
        time.sleep(2)
        epd.sleep()
        _last_refresh_at = datetime.now()
        return True
    except ImportError:
        logger.warning("EPD driver not found (Dev Mode)")
        _last_refresh_at = datetime.now()
        return True
    except Exception as e:
        logger.error(f"EPD Error: {e}")
        return False


def display_on_epaper(img_path: str, mode: str = 'letterbox', rotation_degrees: int = DEFAULT_DISPLAY_ROTATION) -> bool:
    global _last_displayed, _last_display_source
    if not os.path.exists(img_path):
        return False
    ok = render_to_epaper(Image.open(img_path), mode=mode, rotation_degrees=rotation_degrees)
    if ok:
        _last_displayed = img_path
        _last_display_source = 'path'
    return ok


def display_fraimic_bin_on_epaper(bin_data: bytes, mode: str = 'crop', rotation_degrees: int = DEFAULT_DISPLAY_ROTATION) -> bool:
    """Decode a Fraimic .bin and push it straight to the panel — no copy
    is written to disk, since Home Assistant already holds the source."""
    global _last_fraimic_bin, _last_display_source
    width, height, _ = get_effective_display_dims()
    ok = render_to_epaper(bin_to_image(bin_data, width, height), mode=mode, rotation_degrees=rotation_degrees)
    if ok:
        _last_fraimic_bin = bin_data
        _last_display_source = 'fraimic'
    return ok


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


def decode_and_display_fraimic_bin(bin_data: bytes) -> None:
    """Decode a Fraimic .bin payload and push it straight to the panel.

    No copy is saved to uploads/ — Home Assistant (or whatever sent the
    .bin) already holds the source image, so persisting another copy here
    would just accumulate a duplicate on every re-send.
    """
    # Image is already panel-sized, so mode doesn't matter — use crop to
    # avoid any re-scaling artefacts.
    display_fraimic_bin_on_epaper(bin_data, mode='crop', rotation_degrees=DEFAULT_DISPLAY_ROTATION)


def get_battery_status() -> dict:
    # eframe runs on mains power, not a battery — these are fixed values
    # rather than a real fuel-gauge/ADC reading.
    return {
        'percent': 100,
        'voltage_mv': None,
        'charging': True,
        'cable_connected': True,
        'source': 'mains',
    }


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
        width, height, _ = get_effective_display_dims()
        expected = width * height // 2  # 960,000 bytes for 1200×1600
        if len(bin_data) != expected:
            logger.warning(f"Fraimic upload: unexpected size {len(bin_data)} (expected {expected})")
            return jsonify({'error': f'unexpected size {len(bin_data)}, expected {expected}'}), 400

        decode_and_display_fraimic_bin(bin_data)
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
# These mirror the real Fraimic frame's REST API (see
# github.com/Fraimic/Fraimic_eink_canvas_home_assistant_restAPI_guide) so
# tools built for a real frame — Home Assistant, fraimic-controller — work
# unmodified against eframe.

@app.route('/api/info', methods=['GET'])
def api_info():
    """Return device status in the real Fraimic frame's JSON shape.

    `display.width_px`/`height_px`/`orientation` and `device.device_key`
    are ahead of what a real frame currently reports - see
    https://github.com/Fraimic/Fraimic_eink_canvas_home_assistant_restAPI_guide/issues/2
    and /issues/3, which this app deliberately mimics early so integrations
    built against eframe don't have to hardcode a per-panel guess.
    """
    cfg = device_config.get()
    width_px, height_px, orientation = get_effective_display_dims()
    return jsonify({
        'firmware_version': 'eframe-1.0.0',
        'wifi': {
            'connected': True,
            'ssid':      get_wifi_ssid(),
            'rssi':      get_wifi_rssi(),
            'channel':   None,   # not exposed by the OS in a portable way
            'ip':        get_local_ip(),
        },
        'battery': get_battery_status(),
        'device': {
            'registered':      False,  # not paired to a Fraimic cloud account
            'account_created': False,
            'device_key':      cfg.get('device_key'),
            'time_synced':     True,
            'local_time':      datetime.now().isoformat(),
            'uptime_s':        int(time.monotonic() - START_TIME),
        },
        'settings': {
            'voice_recording': False,  # no microphone hardware
            'keep_awake':      True,   # /api/sleep is a no-op, so effectively always awake
        },
        'display': {
            'device_type':   DEVICE_TYPE,
            'width_px':       width_px,
            'height_px':      height_px,
            'orientation':    orientation,
            'last_refresh':   _last_refresh_at.isoformat() if _last_refresh_at else None,
            'next_refresh':   None,  # eframe doesn't schedule refreshes
        },
    })


@app.route('/api/settings', methods=['GET', 'POST'])
def api_settings():
    """Read or update user-settable device settings. Currently just
    `orientation` (see issue #3 linked above) - the effective displayed
    orientation, independent of the panel's native/mounted shape."""
    if request.method == 'POST':
        payload = request.get_json(silent=True) or request.form
        orientation = (payload.get('orientation') or '').strip().lower()
        if orientation not in ('portrait', 'landscape'):
            return jsonify({'error': 'invalid_orientation', 'allowed': ['portrait', 'landscape']}), 400
        device_config.update(orientation=orientation)
        return jsonify({'status': 'ok', 'orientation': orientation}), 200

    return jsonify({'orientation': device_config.get().get('orientation', 'portrait')}), 200


@app.route('/api/battery', methods=['GET'])
def api_battery():
    """Lightweight battery-only status, mirroring the Fraimic frame endpoint."""
    return jsonify(get_battery_status())


@app.route('/api/refresh', methods=['POST'])
def api_refresh():
    """Re-display the last image — mirrors the Fraimic frame refresh endpoint."""
    if _last_display_source == 'fraimic' and _last_fraimic_bin:
        display_fraimic_bin_on_epaper(_last_fraimic_bin, mode='crop', rotation_degrees=DEFAULT_DISPLAY_ROTATION)
        return jsonify({'status': 'refresh_started'}), 200
    if _last_display_source == 'path' and _last_displayed and os.path.exists(_last_displayed):
        display_on_epaper(_last_displayed, mode='crop', rotation_degrees=DEFAULT_DISPLAY_ROTATION)
        return jsonify({'status': 'refresh_started'}), 200
    return jsonify({'error': 'no image to refresh'}), 404


@app.route('/api/image', methods=['POST'])
def api_image():
    """Upload a Fraimic .bin as a raw binary body (application/octet-stream)."""
    if not _display_lock.acquire(blocking=False):
        return jsonify({'error': 'buffer_not_ready'}), 503

    try:
        if request.mimetype and request.mimetype != 'application/octet-stream':
            return jsonify({'error': 'unsupported_content_type'}), 501

        if request.content_length is not None and request.content_length > MAX_FRAIMIC_IMAGE_BYTES:
            return jsonify({'error': 'file_too_large'}), 400

        bin_data = request.get_data()
        if len(bin_data) > MAX_FRAIMIC_IMAGE_BYTES:
            return jsonify({'error': 'file_too_large'}), 400

        width, height, _ = get_effective_display_dims()
        expected = width * height // 2  # 960,000 bytes for 1200×1600
        if len(bin_data) != expected:
            logger.warning(f"Fraimic /api/image: unexpected size {len(bin_data)} (expected {expected})")
            return jsonify({'error': 'invalid_image_size'}), 400

        decode_and_display_fraimic_bin(bin_data)
        return jsonify({'status': 'rendering', 'bytes_received': len(bin_data)}), 200
    finally:
        _display_lock.release()


@app.route('/api/restart', methods=['POST'])
def api_restart():
    """Restart the eframe service. eframe.service has Restart=always, so
    exiting the process is enough to bring it back up."""
    def _restart():
        time.sleep(0.5)
        os._exit(0)

    threading.Thread(target=_restart, daemon=True).start()
    return jsonify({'status': 'restarting'}), 200


@app.route('/api/sleep', methods=['POST'])
def api_sleep():
    """No-op: eframe runs on mains power, so there's no real sleep state
    to enter. Always reports success for API parity with the real frame."""
    return jsonify({'status': 'sleeping'}), 200


# --- PORTAL (mimics the real Fraimic frame's on-device setup UI) ---------
# Routed under /portal/... (rather than reusing e.g. /upload, /wifi) since
# those top-level paths are already taken by eframe's own gallery UI above.
# Visually this mirrors http://<a real frame>/portal and its sub-pages;
# "Get Started" / "Fraimic Dashboard" are inert placeholders since eframe
# has no real Fraimic cloud account to set up or sign into.

@app.route('/portal')
def portal_home():
    return render_template(
        'portal_home.html',
        wifi_connected=bool(get_wifi_ssid()),
        battery=get_battery_status(),
        firmware_version='eframe-1.0.0',
    )


@app.route('/portal/wifi', methods=['GET', 'POST'])
def portal_wifi():
    error = None
    success = None
    if request.method == 'POST':
        ssid = (request.form.get('ssid') or request.form.get('manual_ssid') or '').strip()
        password = request.form.get('password') or ''
        if not ssid:
            error = 'Choose or enter a network name.'
        elif wifi_control.save_credentials(ssid, password):
            success = f'Saved credentials for "{ssid}". The device will reconnect using them.'
        else:
            error = 'Could not save WiFi credentials. Check the logs for details.'

    return render_template(
        'portal_wifi.html',
        networks=wifi_control.scan_networks(),
        error=error,
        success=success,
    )


@app.route('/portal/upload', methods=['GET', 'POST'])
def portal_upload():
    error = None
    success = None
    if request.method == 'POST':
        f = request.files.get('file')
        if not f or not f.filename.lower().endswith('.bin'):
            error = 'Choose a .bin file to upload.'
        else:
            bin_data = f.read()
            width, height, _ = get_effective_display_dims()
            expected = width * height // 2
            if len(bin_data) > MAX_FRAIMIC_IMAGE_BYTES:
                error = 'File exceeds the 1 MB limit.'
            elif len(bin_data) != expected:
                error = f'Unexpected file size ({len(bin_data)} bytes; expected {expected}).'
            else:
                decode_and_display_fraimic_bin(bin_data)
                success = 'Image uploaded and sent to the display.'

    return render_template('portal_upload.html', error=error, success=success)


@app.route('/portal/get-started')
def portal_get_started():
    return render_template('portal_get_started.html')


@app.route('/portal/info')
def portal_info():
    cfg = device_config.get()
    width_px, height_px, orientation = get_effective_display_dims()
    return render_template(
        'portal_info.html',
        device_type=DEVICE_TYPE,
        mac=get_mac_address(),
        device_key=cfg.get('device_key', ''),
        firmware_version='eframe-1.0.0',
        orientation=orientation,
        width_px=width_px,
        height_px=height_px,
        battery=get_battery_status(),
        wifi_connected=bool(get_wifi_ssid()),
        wifi_ssid=get_wifi_ssid(),
        wifi_ip=get_local_ip(),
        wifi_rssi=get_wifi_rssi(),
        last_refresh=_last_refresh_at,
        now=datetime.now(),
    )


@app.route('/portal/settings/orientation', methods=['POST'])
def portal_set_orientation():
    orientation = (request.form.get('orientation') or '').strip().lower()
    if orientation in ('portrait', 'landscape'):
        device_config.update(orientation=orientation)
    return redirect(url_for('portal_info'))


@app.route('/portal/logs')
def portal_logs():
    return render_template('portal_logs.html', entries=log_buffer.get_entries())


@app.route('/portal/logs/clear', methods=['POST'])
def portal_logs_clear():
    log_buffer.clear()
    return redirect(url_for('portal_logs'))


if __name__ == '__main__':
    cfg = device_config.get()
    mdns_advertise.start(cfg['device_key'], get_local_ip(), port=80)
    app.run(host='0.0.0.0', port=80)
