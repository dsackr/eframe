#!/usr/bin/env python3
"""
Image converter service for ESP32 E-Paper Display
Handles image conversion to 6-color e-paper format
"""

from flask import Flask, request, send_file, jsonify, render_template_string
from PIL import Image, ImageDraw, ImageFont
import io
import struct

app = Flask(__name__)

# E-Paper specs
EPD_WIDTH = 800
EPD_HEIGHT = 480

# 6-color e-paper palette (verified working)
PALETTE = {
    'black': (0, 0, 0, 0x0),
    'white': (255, 255, 255, 0x1),
    'yellow': (255, 255, 0, 0x2),
    'red': (200, 80, 50, 0x3),
    'blue': (100, 120, 180, 0x5),
    'green': (200, 200, 80, 0x6)
}

def rgb_to_palette_code(r, g, b):
    """Find closest color in palette"""
    min_distance = float('inf')
    closest_code = 0x1  # Default to white
    
    for color_name, (pr, pg, pb, code) in PALETTE.items():
        distance = (r - pr)**2 + (g - pg)**2 + (b - pb)**2
        if distance < min_distance:
            min_distance = distance
            closest_code = code
    
    return closest_code

def convert_image_to_epaper_format(image_file, use_dithering=True):
    """
    Convert an image file to e-paper binary format
    Returns bytes suitable for direct upload to ESP32
    """
    # Open image
    img = Image.open(image_file)
    
    # Convert to RGB if needed
    if img.mode != 'RGB':
        img = img.convert('RGB')
    
    # Auto-rotate portrait images to landscape
    if img.height > img.width:
        img = img.rotate(90, expand=True)
        print(f"Rotated portrait image to landscape: {img.width}x{img.height}")
    
    # Pre-scale very large images for performance
    if img.width > 2400 or img.height > 1440:
        img.thumbnail((2400, 1440), Image.Resampling.LANCZOS)
        print(f"Pre-scaled large image to {img.width}x{img.height}")
    
    # Calculate crop-to-fill dimensions (better than letterbox)
    img_ratio = img.width / img.height
    display_ratio = EPD_WIDTH / EPD_HEIGHT
    
    if img_ratio > display_ratio:
        # Image is wider - scale by height
        new_height = EPD_HEIGHT
        new_width = int(EPD_HEIGHT * img_ratio)
    else:
        # Image is taller - scale by width
        new_width = EPD_WIDTH
        new_height = int(EPD_WIDTH / img_ratio)
    
    # Resize and crop to fill display
    img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
    left = (new_width - EPD_WIDTH) // 2
    top = (new_height - EPD_HEIGHT) // 2
    img = img.crop((left, top, left + EPD_WIDTH, top + EPD_HEIGHT))
    
    print(f"Final image size: {img.width}x{img.height}")
    
    # Apply dithering for better color representation
    if use_dithering:
        # Create palette image for dithering
        palette_data = []
        for color_name in ['black', 'white', 'yellow', 'red', 'blue', 'green']:
            r, g, b, _ = PALETTE[color_name]
            palette_data.extend([r, g, b])
        
        # Pad palette to 256 colors
        palette_img = Image.new('P', (1, 1))
        palette_img.putpalette(palette_data + [0] * (256 * 3 - len(palette_data)))
        
        # Apply Floyd-Steinberg dithering
        img = img.quantize(palette=palette_img, dither=Image.Dither.FLOYDSTEINBERG)
        img = img.convert('RGB')
        print("Applied Floyd-Steinberg dithering")
    
    # Convert to e-paper format (4 bits per pixel, 2 pixels per byte)
    binary_data = bytearray(EPD_WIDTH * EPD_HEIGHT // 2)
    
    for row in range(EPD_HEIGHT):
        for col in range(0, EPD_WIDTH, 2):
            # Get two pixels
            r1, g1, b1 = img.getpixel((col, row))
            r2, g2, b2 = img.getpixel((col + 1, row))
            
            # Convert to palette codes
            code1 = rgb_to_palette_code(r1, g1, b1)
            code2 = rgb_to_palette_code(r2, g2, b2)
            
            # Pack two pixels into one byte
            byte_index = row * (EPD_WIDTH // 2) + col // 2
            binary_data[byte_index] = (code1 << 4) | code2
    
    print(f"Converted to binary: {len(binary_data)} bytes")
    return bytes(binary_data)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>E-Paper Image Converter</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {
            font-family: Arial, sans-serif;
            max-width: 800px;
            margin: 50px auto;
            padding: 20px;
        }
        .upload-area {
            border: 2px dashed #ccc;
            border-radius: 10px;
            padding: 40px;
            text-align: center;
            margin: 20px 0;
        }
        button {
            background-color: #4CAF50;
            color: white;
            padding: 15px 32px;
            font-size: 16px;
            border: none;
            border-radius: 5px;
            cursor: pointer;
        }
        button:hover {
            background-color: #45a049;
        }
        button:disabled {
            background-color: #cccccc;
            cursor: not-allowed;
        }
        #preview {
            max-width: 100%;
            margin: 20px 0;
            border: 1px solid #ddd;
        }
        .status {
            padding: 10px;
            margin: 10px 0;
            border-radius: 5px;
        }
        .success {
            background-color: #d4edda;
            color: #155724;
        }
        .error {
            background-color: #f8d7da;
            color: #721c24;
        }
        .info {
            background-color: #cce5ff;
            color: #004085;
        }
        .color-palette {
            display: flex;
            justify-content: center;
            gap: 10px;
            margin: 20px 0;
        }
        .color-swatch {
            width: 60px;
            height: 60px;
            border: 2px solid #333;
            border-radius: 5px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 10px;
            color: white;
            text-shadow: 1px 1px 2px black;
        }
    </style>
</head>
<body>
    <h1>E-Paper Image Converter</h1>
    <p>Upload any image to convert and display on your e-paper screen</p>
    
    <div class="info status">
        <strong>Display Size:</strong> 800×480 pixels<br>
        <strong>Supported Formats:</strong> JPG, PNG, BMP, GIF, WebP<br>
        <strong>Processing:</strong> Auto-rotation, crop-to-fill, Floyd-Steinberg dithering
    </div>
    
    <div class="color-palette">
        <div class="color-swatch" style="background-color: rgb(0,0,0);">Black</div>
        <div class="color-swatch" style="background-color: rgb(255,255,255); color: black; text-shadow: none;">White</div>
        <div class="color-swatch" style="background-color: rgb(255,255,0); color: black; text-shadow: none;">Yellow</div>
        <div class="color-swatch" style="background-color: rgb(200,80,50);">Red</div>
        <div class="color-swatch" style="background-color: rgb(100,120,180);">Blue</div>
        <div class="color-swatch" style="background-color: rgb(200,200,80); color: black; text-shadow: none;">Green</div>
    </div>
    
    <div class="upload-area">
        <input type="file" id="fileInput" accept="image/*" style="display:none;">
        <button onclick="document.getElementById('fileInput').click()">Choose Image</button>
        <p id="fileName"></p>
    </div>
    
    <img id="preview" style="display:none;">
    
    <button id="uploadBtn" style="display:none;" onclick="uploadImage()">
        Convert and Send to Display
    </button>
    
    <div id="status"></div>
    
    <script>
        const fileInput = document.getElementById('fileInput');
        const preview = document.getElementById('preview');
        const uploadBtn = document.getElementById('uploadBtn');
        const fileName = document.getElementById('fileName');
        const status = document.getElementById('status');
        
        fileInput.addEventListener('change', function(e) {
            const file = e.target.files[0];
            if (file) {
                fileName.textContent = file.name;
                const reader = new FileReader();
                reader.onload = function(e) {
                    preview.src = e.target.result;
                    preview.style.display = 'block';
                    uploadBtn.style.display = 'inline-block';
                };
                reader.readAsDataURL(file);
            }
        });
        
        async function uploadImage() {
            const file = fileInput.files[0];
            if (!file) return;
            
            status.innerHTML = '<div class="info status">Converting image (this may take 5-10 seconds)...</div>';
            uploadBtn.disabled = true;
            
            try {
                const formData = new FormData();
                formData.append('image', file);
                
                // Convert the image
                const convertResponse = await fetch('/convert', {
                    method: 'POST',
                    body: formData
                });
                
                if (!convertResponse.ok) {
                    throw new Error('Conversion failed');
                }
                
                const binaryData = await convertResponse.arrayBuffer();
                
                status.innerHTML = '<div class="info status">Sending to display...</div>';
                
                // Send to ESP32 (update with your ESP32 IP)
                const espIP = prompt('Enter ESP32 IP address:', '192.168.86.127');
                if (!espIP) {
                    throw new Error('No IP address provided');
                }
                
                const uploadResponse = await fetch(`http://${espIP}/display`, {
                    method: 'POST',
                    body: binaryData
                });
                
                if (uploadResponse.ok) {
                    status.innerHTML = '<div class="success status">✓ Image sent successfully! Display will refresh in ~30 seconds.</div>';
                } else {
                    throw new Error('Upload to ESP32 failed');
                }
                
            } catch (error) {
                status.innerHTML = `<div class="error status">✗ Error: ${error.message}</div>`;
            } finally {
                uploadBtn.disabled = false;
            }
        }
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    """Serve the upload page"""
    return render_template_string(HTML_TEMPLATE)

@app.route('/convert', methods=['POST'])
def convert():
    """Convert uploaded image to e-paper format"""
    if 'image' not in request.files:
        return jsonify({'error': 'No image provided'}), 400
    
    file = request.files['image']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    try:
        print(f"\n=== Converting image: {file.filename} ===")
        
        # Convert the image
        binary_data = convert_image_to_epaper_format(file, use_dithering=True)
        
        print(f"Conversion complete: {len(binary_data)} bytes\n")
        
        # Return as binary data
        return send_file(
            io.BytesIO(binary_data),
            mimetype='application/octet-stream',
            as_attachment=True,
            download_name='epaper_image.bin'
        )
    except Exception as e:
        print(f"Error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({'status': 'ok', 'service': 'epaper-converter', 'colors': 6})

if __name__ == '__main__':
    print("E-Paper Image Converter Service")
    print("================================")
    print("6-Color Palette: Black, White, Yellow, Red, Blue, Green")
    print("Starting server on port 5000...")
    print("Upload images at http://<pi-ip>:5000")
    app.run(host='0.0.0.0', port=5000, debug=False)#!/usr/bin/env python3
"""
Image converter service for ESP32 E-Paper Display
Handles image conversion to 7-color e-paper format
"""

from flask import Flask, request, send_file, jsonify, render_template_string
from PIL import Image, ImageDraw, ImageFont
import io
import struct

app = Flask(__name__)

# E-Paper specs
EPD_WIDTH = 800
EPD_HEIGHT = 480

# 7-color e-paper palette (4-bit color values)
COLORS = {
    'black': 0x0,
    'white': 0x1,
    'green': 0x2,
    'blue': 0x3,
    'red': 0x4,
    'yellow': 0x5,
    'orange': 0x6
}

# RGB values for each color (for quantization)
COLOR_RGB = [
    (0, 0, 0),        # Black
    (255, 255, 255),  # White
    (0, 255, 0),      # Green
    (0, 0, 255),      # Blue
    (255, 0, 0),      # Red
    (255, 255, 0),    # Yellow
    (255, 128, 0)     # Orange
]

def find_closest_color(rgb):
    """Find the closest e-paper color to the given RGB value"""
    r, g, b = rgb
    min_distance = float('inf')
    closest_color = 0
    
    for i, (cr, cg, cb) in enumerate(COLOR_RGB):
        distance = (r - cr)**2 + (g - cg)**2 + (b - cb)**2
        if distance < min_distance:
            min_distance = distance
            closest_color = i
    
    return closest_color

def convert_image_to_epaper_format(image_file):
    """
    Convert an image file to e-paper binary format
    Returns bytes suitable for direct upload to ESP32
    """
    # Open and resize image
    img = Image.open(image_file)
    
    # Convert to RGB if needed
    if img.mode != 'RGB':
        img = img.convert('RGB')
    
    # Resize to fit display, maintaining aspect ratio
    img.thumbnail((EPD_WIDTH, EPD_HEIGHT), Image.Resampling.LANCZOS)
    
    # Create a white background
    background = Image.new('RGB', (EPD_WIDTH, EPD_HEIGHT), (255, 255, 255))
    
    # Center the image
    x_offset = (EPD_WIDTH - img.width) // 2
    y_offset = (EPD_HEIGHT - img.height) // 2
    background.paste(img, (x_offset, y_offset))
    
    # Convert to e-paper format (4 bits per pixel)
    buffer = bytearray(EPD_WIDTH * EPD_HEIGHT // 2)
    
    pixels = background.load()
    for y in range(EPD_HEIGHT):
        for x in range(EPD_WIDTH):
            rgb = pixels[x, y]
            color = find_closest_color(rgb)
            
            # Pack two pixels per byte
            buffer_index = y * (EPD_WIDTH // 2) + x // 2
            if x % 2 == 0:
                buffer[buffer_index] = (color << 4) | (buffer[buffer_index] & 0x0F)
            else:
                buffer[buffer_index] = (buffer[buffer_index] & 0xF0) | color
    
    return bytes(buffer)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>E-Paper Image Converter</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {
            font-family: Arial, sans-serif;
            max-width: 800px;
            margin: 50px auto;
            padding: 20px;
        }
        .upload-area {
            border: 2px dashed #ccc;
            border-radius: 10px;
            padding: 40px;
            text-align: center;
            margin: 20px 0;
        }
        button {
            background-color: #4CAF50;
            color: white;
            padding: 15px 32px;
            font-size: 16px;
            border: none;
            border-radius: 5px;
            cursor: pointer;
        }
        button:hover {
            background-color: #45a049;
        }
        #preview {
            max-width: 100%;
            margin: 20px 0;
            border: 1px solid #ddd;
        }
        .status {
            padding: 10px;
            margin: 10px 0;
            border-radius: 5px;
        }
        .success {
            background-color: #d4edda;
            color: #155724;
        }
        .error {
            background-color: #f8d7da;
            color: #721c24;
        }
        .info {
            background-color: #cce5ff;
            color: #004085;
        }
    </style>
</head>
<body>
    <h1>E-Paper Image Converter</h1>
    <p>Upload any image to convert and display on your e-paper screen</p>
    
    <div class="info status">
        <strong>Display Size:</strong> 800×480 pixels<br>
        <strong>Supported Formats:</strong> JPG, PNG, BMP, GIF<br>
        <strong>Colors:</strong> Black, White, Red, Yellow, Green, Blue, Orange
    </div>
    
    <div class="upload-area">
        <input type="file" id="fileInput" accept="image/*" style="display:none;">
        <button onclick="document.getElementById('fileInput').click()">Choose Image</button>
        <p id="fileName"></p>
    </div>
    
    <img id="preview" style="display:none;">
    
    <button id="uploadBtn" style="display:none;" onclick="uploadImage()">
        Convert and Send to Display
    </button>
    
    <div id="status"></div>
    
    <script>
        const fileInput = document.getElementById('fileInput');
        const preview = document.getElementById('preview');
        const uploadBtn = document.getElementById('uploadBtn');
        const fileName = document.getElementById('fileName');
        const status = document.getElementById('status');
        
        fileInput.addEventListener('change', function(e) {
            const file = e.target.files[0];
            if (file) {
                fileName.textContent = file.name;
                const reader = new FileReader();
                reader.onload = function(e) {
                    preview.src = e.target.result;
                    preview.style.display = 'block';
                    uploadBtn.style.display = 'inline-block';
                };
                reader.readAsDataURL(file);
            }
        });
        
        async function uploadImage() {
            const file = fileInput.files[0];
            if (!file) return;
            
            status.innerHTML = '<div class="info status">Converting image...</div>';
            uploadBtn.disabled = true;
            
            try {
                const formData = new FormData();
                formData.append('image', file);
                
                // Convert the image
                const convertResponse = await fetch('/convert', {
                    method: 'POST',
                    body: formData
                });
                
                if (!convertResponse.ok) {
                    throw new Error('Conversion failed');
                }
                
                const binaryData = await convertResponse.arrayBuffer();
                
                status.innerHTML = '<div class="info status">Sending to display...</div>';
                
                // Send to ESP32 (update with your ESP32 IP)
                const espIP = prompt('Enter ESP32 IP address:', '192.168.1.100');
                if (!espIP) {
                    throw new Error('No IP address provided');
                }
                
                const uploadResponse = await fetch(`http://${espIP}/display`, {
                    method: 'POST',
                    body: binaryData
                });
                
                if (uploadResponse.ok) {
                    status.innerHTML = '<div class="success status">✓ Image sent successfully!</div>';
                } else {
                    throw new Error('Upload to ESP32 failed');
                }
                
            } catch (error) {
                status.innerHTML = `<div class="error status">✗ Error: ${error.message}</div>`;
            } finally {
                uploadBtn.disabled = false;
            }
        }
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    """Serve the upload page"""
    return render_template_string(HTML_TEMPLATE)

@app.route('/convert', methods=['POST'])
def convert():
    """Convert uploaded image to e-paper format"""
    if 'image' not in request.files:
        return jsonify({'error': 'No image provided'}), 400
    
    file = request.files['image']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    try:
        # Convert the image
        binary_data = convert_image_to_epaper_format(file)
        
        # Return as binary data
        return send_file(
            io.BytesIO(binary_data),
            mimetype='application/octet-stream',
            as_attachment=True,
            download_name='epaper_image.bin'
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({'status': 'ok', 'service': 'epaper-converter'})

if __name__ == '__main__':
    print("E-Paper Image Converter Service")
    print("================================")
    print("Starting server on port 5000...")
    print("Upload images at http://<pi-ip>:5000")
    app.run(host='0.0.0.0', port=5000, debug=False)
