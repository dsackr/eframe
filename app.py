from flask import Flask, request, render_template, jsonify, send_from_directory
from PIL import Image, ImageDraw, ImageFont
import requests
import os
import io
import textwrap

app = Flask(__name__)

# --- Configuration ---
# Your ESP32 IP address
ESP32_IP = "192.168.86.127"
# Display resolution
DISPLAY_WIDTH = 800
DISPLAY_HEIGHT = 480
# Directory to store uploaded and processed images
STORAGE_DIR = 'stored_images'
if not os.path.exists(STORAGE_DIR):
    os.makedirs(STORAGE_DIR)

# --- 6-Color Palette (Used for conversion) ---
PALETTE = {
    'black': (0, 0, 0, 0x0),
    'white': (255, 255, 255, 0x1),
    'yellow': (255, 255, 0, 0x2),
    'red': (200, 80, 50, 0x3),
    'blue': (100, 120, 180, 0x5),
    'green': (200, 200, 80, 0x6)
}

# --- Core Conversion Functions ---

def rgb_to_palette_code(r, g, b):
    """Find closest color in palette and return its 4-bit code"""
    min_distance = float('inf')
    closest_code = 0x1
    
    for color_name, (pr, pg, pb, code) in PALETTE.items():
        distance = (r - pr)**2 + (g - pg)**2 + (b - pb)**2
        if distance < min_distance:
            min_distance = distance
            closest_code = code
    
    return closest_code

def generate_binary_data(img):
    """Converts a final 800x480 RGB image to the 4-bit binary format"""
    binary_data = bytearray(DISPLAY_WIDTH * DISPLAY_HEIGHT // 2)
    
    for row in range(DISPLAY_HEIGHT):
        for col in range(0, DISPLAY_WIDTH, 2):
            r1, g1, b1 = img.getpixel((col, row))
            r2, g2, b2 = img.getpixel((col + 1, row))
            
            code1 = rgb_to_palette_code(r1, g1, b1)
            code2 = rgb_to_palette_code(r2, g2, b2)
            
            byte_index = row * (DISPLAY_WIDTH // 2) + col // 2
            binary_data[byte_index] = (code1 << 4) | code2
    
    return bytes(binary_data)

def apply_palette_and_dithering(img):
    """Applies color reduction and dithering to an image"""
    palette_data = [
        0, 0, 0,
        255, 255, 255,
        255, 255, 0,
        200, 80, 50,
        100, 120, 180,
        200, 200, 80
    ]
    
    # Create the internal Pillow palette
    palette_img = Image.new('P', (1, 1))
    palette_img.putpalette(palette_data + [0] * (256 * 3 - len(palette_data)))
    
    # Quantize with dithering
    img = img.quantize(palette=palette_img, dither=Image.Dither.FLOYDSTEINBERG)
    return img.convert('RGB') # Convert back to RGB for pixel analysis

def process_image(img, mode='crop'):
    """Applies rotation, scaling, and the selected fit mode."""
    if img.mode != 'RGB':
        img = img.convert('RGB')
    
    # 1. Auto-rotate
    if img.height > img.width:
        img = img.rotate(90, expand=True)
    
    # 2. Downscale very large images first (for performance)
    if img.width > 2400 or img.height > 1440:
        img.thumbnail((2400, 1440), Image.Resampling.LANCZOS)
    
    # 3. Apply selected mode
    if mode == 'crop':
        # Crop-to-fill (existing functionality)
        img_ratio = img.width / img.height
        display_ratio = DISPLAY_WIDTH / DISPLAY_HEIGHT
        
        if img_ratio > display_ratio:
            new_height = DISPLAY_HEIGHT
            new_width = int(DISPLAY_HEIGHT * img_ratio)
        else:
            new_width = DISPLAY_WIDTH
            new_height = int(DISPLAY_WIDTH / img_ratio)
        
        img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
        left = (new_width - DISPLAY_WIDTH) // 2
        top = (new_height - DISPLAY_HEIGHT) // 2
        img = img.crop((left, top, left + DISPLAY_WIDTH, top + DISPLAY_HEIGHT))
        
    elif mode == 'fit':
        # Fit-to-screen (maintains aspect ratio, black bars if needed)
        img.thumbnail((DISPLAY_WIDTH, DISPLAY_HEIGHT), Image.Resampling.LANCZOS)
        
        # Create a blank white canvas
        canvas = Image.new('RGB', (DISPLAY_WIDTH, DISPLAY_HEIGHT), color='white')
        
        # Calculate centering
        left = (DISPLAY_WIDTH - img.width) // 2
        top = (DISPLAY_HEIGHT - img.height) // 2
        
        canvas.paste(img, (left, top))
        img = canvas
        
    else: # mode == 'stretch' (or default)
        # Stretch-to-fill
        img = img.resize((DISPLAY_WIDTH, DISPLAY_HEIGHT), Image.Resampling.LANCZOS)
        
    # 4. Apply palette and dithering
    img = apply_palette_and_dithering(img)
    
    return generate_binary_data(img)

# --- New Functionality: Text Display ---

def process_text(text_input, font_size=40):
    """
    Creates an image from text and converts it to binary data.
    Uses ImageFont.load_default() which is always available.
    """
    
    # Use Pillow's default font (always available)
    font = ImageFont.load_default()
    # The default font is small, adjust the size guess and line spacing accordingly
    default_font_size = 18
    
    # 1. Create a white canvas
    img = Image.new('RGB', (DISPLAY_WIDTH, DISPLAY_HEIGHT), color='white')
    draw = ImageDraw.Draw(img)
    
    # 2. Word wrap and layout
    margin = 40
    line_spacing = 1.2 * default_font_size
    y_position = margin
    
    # Determine the characters per line for wrapping
    # Estimate based on the small default font width
    char_width_guess = 6 # Default font chars are typically 6-7 pixels wide
    max_chars = int((DISPLAY_WIDTH - 2 * margin) / char_width_guess)
    
    # Wrap the text
    wrapped_lines = textwrap.fill(text_input, width=max_chars, subsequent_indent='  ')
    
    for line in wrapped_lines.splitlines():
        # Draw the text in black (0, 0, 0)
        draw.text((margin, y_position), line, fill=(0, 0, 0), font=font)
        y_position += int(line_spacing)
        
        # Stop if we run out of vertical space
        if y_position > DISPLAY_HEIGHT - margin:
            break
            
    # 3. Apply palette and conversion
    img = apply_palette_and_dithering(img)
    return generate_binary_data(img)

# --- ESP32 Communication ---

def send_to_esp32(binary_data):
    """Sends the binary image data to the ESP32."""
    response = requests.post(
        f'http://{ESP32_IP}/display',
        files={'file': ('image.bin', binary_data)},
        headers={'Connection': 'keep-alive'},
        timeout=120
    )
    return response

# --- Flask Routes ---

@app.route('/')
def index():
    # Retrieve stored files for the front-end
    stored_files = [f for f in os.listdir(STORAGE_DIR) if f.endswith(('.png', '.jpg', '.jpeg'))]
    # NOTE: This requires a 'templates/index.html' file to exist!
    return render_template('index.html', stored_files=stored_files)

@app.route('/upload_and_display', methods=['POST'])
def upload_and_display():
    mode = request.form.get('mode', 'crop') # Get crop/fit/stretch mode
    
    # Handle image upload and display
    if 'image' in request.files and request.files['image'].filename != '':
        file = request.files['image']
        filename = os.path.join(STORAGE_DIR, file.filename)
        
        # Save the file (Storage/Reuse feature)
        file.save(filename)
        
        try:
            img = Image.open(filename)
            binary_data = process_image(img, mode=mode)
            response = send_to_esp32(binary_data)
            
            if response.status_code == 200:
                return jsonify({'success': True, 'message': f'Image displayed using {mode} mode!'})
            else:
                return jsonify({'error': f'ESP32 error: {response.status_code}'}), 500
                
        except Exception as e:
            return jsonify({'error': f'Error processing image: {str(e)}'}), 500

    # Handle text display
    elif 'text_input' in request.form and request.form['text_input'].strip() != '':
        text_input = request.form['text_input']
        
        try:
            binary_data = process_text(text_input)
            response = send_to_esp32(binary_data)
            
            if response.status_code == 200:
                return jsonify({'success': True, 'message': 'Text displayed!'})
            else:
                return jsonify({'error': f'ESP32 error: {response.status_code}'}), 500
                
        except Exception as e:
            return jsonify({'error': f'Error processing text: {str(e)}'}), 500
            
    return jsonify({'error': 'No file or text provided'}), 400

@app.route('/display_stored/<filename>', methods=['POST'])
def display_stored(filename):
    """Retrieves and displays a stored image."""
    mode = request.form.get('mode', 'crop')
    filename_path = os.path.join(STORAGE_DIR, filename)
    
    if not os.path.exists(filename_path):
        return jsonify({'error': 'Stored file not found'}), 404
        
    try:
        img = Image.open(filename_path)
        binary_data = process_image(img, mode=mode)
        response = send_to_esp32(binary_data)
        
        if response.status_code == 200:
            return jsonify({'success': True, 'message': f'Stored image "{filename}" displayed using {mode} mode!'})
        else:
            return jsonify({'error': f'ESP32 error: {response.status_code}'}), 500
            
    except Exception as e:
        return jsonify({'error': f'Error processing stored image: {str(e)}'}), 500


@app.route('/files/<filename>')
def stored_file(filename):
    """Serve stored files for preview (optional feature)."""
    return send_from_directory(STORAGE_DIR, filename)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
