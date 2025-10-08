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

def convert_image_to_binary(image_path, mode='crop', use_dithering=True):
    """
    Convert image to 800x480 binary format
    
    mode:
        'fit' - longest axis fits 800px, leaves borders if needed
        'crop' - shortest axis fits 480px, crops excess
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
    
    # Calculate dimensions based on mode
    img_ratio = img.width / img.height
    display_ratio = 800 / 480
    
    if mode == 'fit':
        # Fit mode: longest axis = 800px, add borders if needed
        if img_ratio > display_ratio:
            # Image is wider than display
            new_width = 800
            new_height = int(800 / img_ratio)
        else:
            # Image is taller than display
            new_height = 480
            new_width = int(480 * img_ratio)
        
        # Resize image
        img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
        
        # Create white background and paste image centered
        final_img = Image.new('RGB', (800, 480), (255, 255, 255))
        x_offset = (800 - new_width) // 2
        y_offset = (480 - new_height) // 2
        final_img.paste(img, (x_offset, y_offset))
        img = final_img
        
    else:  # crop mode
        # Crop mode: shortest axis = 480px, crop excess
        if img_ratio > display_ratio:
            # Image is wider than display - fit height, crop width
            new_height = 480
            new_width = int(480 * img_ratio)
        else:
            # Image is taller than display - fit width, crop height
            new_width = 800
            new_height = int(800 / img_ratio)
        
        img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
        
        # Crop to center
        left = (new_width - 800) // 2
        top = (new_height - 480) // 2
        img = img.crop((left, top, left + 800, top + 480))
    
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
    if 'image' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    
    file = request.files['image']
    
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    if not allowed_file(file.filename):
        return jsonify({'error': 'Invalid file type'}), 400
    
    try:
        mode = request.form.get('mode', 'crop')
        save_image = request.form.get('save', 'false').lower() == 'true'
        
        # Save file if requested
        stored_filename = None
        if save_image:
            filename = secure_filename(file.filename)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            stored_filename = f"{timestamp}_{filename}"
            filepath = os.path.join(UPLOAD_FOLDER, stored_filename)
            file.save(filepath)
            
            # Update metadata
            metadata = load_metadata()
            metadata.append({
                'filename': stored_filename,
                'original_name': file.filename,
                'upload_date': datetime.now().isoformat(),
                'mode': mode
            })
            save_metadata(metadata)
            
            # Use saved file for conversion
            file_to_convert = filepath
        else:
            # Use uploaded file directly
            file_to_convert = file
        
        binary_data = convert_image_to_binary(file_to_convert, mode=mode)
        
        response = requests.post(
            f'http://{ESP32_IP}/display',
            files={'file': ('image.bin', binary_data)},
            headers={'Connection': 'keep-alive'},
            timeout=120
        )
        
        if response.status_code == 200:
            return jsonify({
                'success': True, 
                'message': 'Image displayed!',
                'saved': save_image,
                'filename': stored_filename if save_image else None
            })
        else:
            return jsonify({'error': f'ESP32 error: {response.status_code}'}), 500
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/stored-images', methods=['GET'])
def get_stored_images():
    """Get list of stored images"""
    metadata = load_metadata()
    return jsonify(metadata)

@app.route('/display-stored', methods=['POST'])
def display_stored():
    """Display a previously stored image"""
    data = request.get_json()
    filename = data.get('filename')
    mode = data.get('mode', 'crop')
    
    if not filename:
        return jsonify({'error': 'No filename provided'}), 400
    
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    
    if not os.path.exists(filepath):
        return jsonify({'error': 'File not found'}), 404
    
    try:
        binary_data = convert_image_to_binary(filepath, mode=mode)
        
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
