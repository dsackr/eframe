from flask import Flask, request, render_template, jsonify, send_from_directory
from PIL import Image
import requests
import io
import os
import json
from datetime import datetime
from werkzeug.utils import secure_filename

app = Flask(__name__)

# Configuration
ESP32_IP = "192.168.86.127"
UPLOAD_FOLDER = 'stored_images'
METADATA_FILE = 'image_metadata.json'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp'}

# Create upload folder if it doesn't exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Corrected 6-color palette
PALETTE = {
    'black': (0, 0, 0, 0x0),
    'white': (255, 255, 255, 0x1),
    'yellow': (255, 255, 0, 0x2),
    'red': (200, 80, 50, 0x3),
    'blue': (100, 120, 180, 0x5),
    'green': (200, 200, 80, 0x6)
}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def load_metadata():
    """Load image metadata from JSON file"""
    if os.path.exists(METADATA_FILE):
        with open(METADATA_FILE, 'r') as f:
            return json.load(f)
    return []

def save_metadata(metadata):
    """Save image metadata to JSON file"""
    with open(METADATA_FILE, 'w') as f:
        json.dump(metadata, f, indent=2)

def prepare_image_800x480(image_path):
    """
    STEP 1: Simply resize/rotate image to 800x480
    This is just the basic prep - no dithering or conversion
    """
    img = Image.open(image_path)
    
    if img.mode != 'RGB':
        img = img.convert('RGB')
    
    # Auto-rotate portrait to landscape
    if img.height > img.width:
        img = img.rotate(90, expand=True)
        print(f"Rotated portrait image to landscape")
    
    # Downscale very large images first
    if img.width > 2400 or img.height > 1440:
        img.thumbnail((2400, 1440), Image.Resampling.LANCZOS)
        print(f"Pre-scaled large image to {img.width}x{img.height}")
    
    # Calculate crop-to-fill dimensions (ORIGINAL CROP LOGIC)
    img_ratio = img.width / img.height
    display_ratio = 800 / 480
    
    if img_ratio > display_ratio:
        new_height = 480
        new_width = int(480 * img_ratio)
    else:
        new_width = 800
        new_height = int(800 / img_ratio)
    
    img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
    left = (new_width - 800) // 2
    top = (new_height - 480) // 2
    img = img.crop((left, top, left + 800, top + 480))
    
    print(f"Final prepared image: {img.size}, mode: {img.mode}")
    return img

def rgb_to_palette_code(r, g, b):
    """Find closest color in palette"""
    min_distance = float('inf')
    closest_code = 0x1
    
    for color_name, (pr, pg, pb, code) in PALETTE.items():
        distance = (r - pr)**2 + (g - pg)**2 + (b - pb)**2
        if distance < min_distance:
            min_distance = distance
            closest_code = code
    
    return closest_code

def convert_image_to_binary(image_path, use_dithering=True):
    """
    STEP 2: EXACT ORIGINAL CONVERSION LOGIC
    Convert 800x480 image to binary format with dithering
    This is the ORIGINAL working code - unchanged
    """
    img = Image.open(image_path)
    
    if img.mode != 'RGB':
        img = img.convert('RGB')
    
    # Image should already be 800x480 at this point
    if img.size != (800, 480):
        raise ValueError(f"Image must be 800x480, got {img.size}")
    
    if use_dithering:
        palette_data = [
            0, 0, 0,
            255, 255, 255,
            255, 255, 0,
            200, 80, 50,
            100, 120, 180,
            200, 200, 80
        ]
        
        palette_img = Image.new('P', (1, 1))
        palette_img.putpalette(palette_data + [0] * (256 * 3 - len(palette_data)))
        
        img = img.quantize(palette=palette_img, dither=Image.Dither.FLOYDSTEINBERG)
        img = img.convert('RGB')
    
    binary_data = bytearray(192000)
    
    for row in range(480):
        for col in range(0, 800, 2):
            r1, g1, b1 = img.getpixel((col, row))
            r2, g2, b2 = img.getpixel((col + 1, row))
            
            code1 = rgb_to_palette_code(r1, g1, b1)
            code2 = rgb_to_palette_code(r2, g2, b2)
            
            byte_index = row * 400 + col // 2
            binary_data[byte_index] = (code1 << 4) | code2
    
    return bytes(binary_data)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload():
    """STEP 1: Upload image, resize to 800x480, save to library"""
    if 'image' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    
    file = request.files['image']
    
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    if not allowed_file(file.filename):
        return jsonify({'error': 'Invalid file type'}), 400
    
    try:
        # Save uploaded file temporarily
        filename = secure_filename(file.filename)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        temp_path = os.path.join(UPLOAD_FOLDER, f"temp_{filename}")
        file.save(temp_path)
        
        # Prepare to 800x480
        prepared_img = prepare_image_800x480(temp_path)
        
        # Save as PNG
        final_filename = f"{timestamp}_{filename}"
        if not final_filename.lower().endswith('.png'):
            final_filename = final_filename.rsplit('.', 1)[0] + '.png'
        final_path = os.path.join(UPLOAD_FOLDER, final_filename)
        prepared_img.save(final_path, 'PNG')
        
        # Remove temp file
        os.remove(temp_path)
        
        # Update metadata
        metadata = load_metadata()
        metadata.append({
            'filename': final_filename,
            'original_name': file.filename,
            'upload_date': datetime.now().isoformat()
        })
        save_metadata(metadata)
        
        return jsonify({
            'success': True,
            'message': 'Image saved to library!',
            'filename': final_filename
        })
            
    except Exception as e:
        print(f"Error during upload: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/stored-images', methods=['GET'])
def get_stored_images():
    """Get list of stored images"""
    metadata = load_metadata()
    return jsonify(metadata)

@app.route('/display', methods=['POST'])
def display():
    """STEP 2: Use EXACT ORIGINAL conversion logic and send to display"""
    data = request.get_json()
    filename = data.get('filename')
    
    if not filename:
        return jsonify({'error': 'No filename provided'}), 400
    
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    
    if not os.path.exists(filepath):
        return jsonify({'error': 'File not found'}), 404
    
    try:
        print(f"Converting {filename} using ORIGINAL conversion logic...")
        
        # Use EXACT original conversion
        binary_data = convert_image_to_binary(filepath)
        
        print(f"Sending {len(binary_data)} bytes to ESP32...")
        response = requests.post(
            f'http://{ESP32_IP}/display',
            files={'file': ('image.bin', binary_data)},
            headers={'Connection': 'keep-alive'},
            timeout=120
        )
        
        if response.status_code == 200:
            return jsonify({'success': True, 'message': 'Image displayed!'})
        else:
            return jsonify({'error': f'ESP32 error: {response.status_code}'}), 500
            
    except Exception as e:
        print(f"Error during display: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/delete-stored/<filename>', methods=['DELETE'])
def delete_stored(filename):
    """Delete a stored image"""
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    
    if not os.path.exists(filepath):
        return jsonify({'error': 'File not found'}), 404
    
    try:
        os.remove(filepath)
        
        # Update metadata
        metadata = load_metadata()
        metadata = [m for m in metadata if m['filename'] != filename]
        save_metadata(metadata)
        
        return jsonify({'success': True, 'message': 'Image deleted'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/thumbnails/<filename>')
def get_thumbnail(filename):
    """Serve image thumbnails"""
    return send_from_directory(UPLOAD_FOLDER, filename)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
