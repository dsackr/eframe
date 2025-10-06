from flask import Flask, render_template, request, redirect, url_for, send_from_directory
from PIL import Image, ImageEnhance, ImageFilter, ImageDraw, ImageFont
import os
import requests
from datetime import datetime

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

EPD_WIDTH = 800
EPD_HEIGHT = 480
ESP32_IP = "192.168.86.127"

# Use the same palette that works well for grayscale
PALETTE = {
    'black': (0, 0, 0, 0x0),
    'white': (255, 255, 255, 0x1),
    'yellow': (255, 255, 0, 0x2),
    'red': (200, 80, 50, 0x3),
    'blue': (100, 120, 180, 0x5),
    'green': (200, 200, 80, 0x6)
}

def rgb_to_palette_code(r, g, b):
    min_distance = float('inf')
    closest_code = 0x1
    
    for color_name, (pr, pg, pb, code) in PALETTE.items():
        distance = (r - pr)**2 + (g - pg)**2 + (b - pb)**2
        if distance < min_distance:
            min_distance = distance
            closest_code = code
    
    return closest_code

def convert_image_to_binary(img, use_dithering=True):
    if img.mode != 'RGB':
        img = img.convert('RGB')
    
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
    
    if use_dithering:
        palette_data = [
            0, 0, 0, 255, 255, 255, 255, 255, 0,
            200, 80, 50, 100, 120, 180, 200, 200, 80
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

def send_to_esp32(img):
    try:
        binary_data = convert_image_to_binary(img)
        response = requests.post(
            f'http://{ESP32_IP}/display',
            files={'file': ('image.bin', binary_data)},
            headers={'Connection': 'keep-alive'},
            timeout=120
        )
        return response.status_code == 200
    except Exception as e:
        print(f"Error: {e}")
        return False

def display_image_raw(image_path):
    try:
        img = Image.open(image_path)
        if img.mode != 'RGB':
            img = img.convert('RGB')
        
        if img.height > img.width:
            img = img.rotate(90, expand=True)
        
        img.thumbnail((EPD_WIDTH, EPD_HEIGHT), Image.Resampling.LANCZOS)
        display_img = Image.new('RGB', (EPD_WIDTH, EPD_HEIGHT), 'white')
        x = (EPD_WIDTH - img.width) // 2
        y = (EPD_HEIGHT - img.height) // 2
        display_img.paste(img, (x, y))
        
        return send_to_esp32(display_img)
    except Exception as e:
        print(f"Error: {e}")
        return False

def display_image(image_path, use_dithering=False):
    try:
        img = Image.open(image_path)
        
        if img.mode != 'RGB':
            img = img.convert('RGB')
        
        if img.height > img.width:
            img = img.rotate(90, expand=True)
        
        img_ratio = img.width / img.height
        display_ratio = EPD_WIDTH / EPD_HEIGHT
        
        if img_ratio > display_ratio:
            new_width = EPD_WIDTH
            new_height = int(EPD_WIDTH / img_ratio)
        else:
            new_height = EPD_HEIGHT
            new_width = int(EPD_HEIGHT * img_ratio)
        
        img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
        img = img.filter(ImageFilter.SHARPEN)
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(1.3)
        
        display_img = Image.new('RGB', (EPD_WIDTH, EPD_HEIGHT), 'white')
        x = (EPD_WIDTH - img.width) // 2
        y = (EPD_HEIGHT - img.height) // 2
        display_img.paste(img, (x, y))
        
        return send_to_esp32(convert_image_to_binary(display_img, use_dithering))
    except Exception as e:
        print(f"Error: {e}")
        return False

def display_text(text, font_size=80):
    try:
        img = Image.new('RGB', (EPD_WIDTH, EPD_HEIGHT), 'white')
        draw = ImageDraw.Draw(img)
        
        try:
            font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', font_size)
        except:
            font = ImageFont.load_default()
        
        margin = 40
        max_width = EPD_WIDTH - (margin * 2)
        
        lines = []
        words = text.split()
        current_line = []
        
        for word in words:
            test_line = ' '.join(current_line + [word])
            bbox = draw.textbbox((0, 0), test_line, font=font)
            if bbox[2] - bbox[0] <= max_width:
                current_line.append(word)
            else:
                if current_line:
                    lines.append(' '.join(current_line))
                current_line = [word]
        
        if current_line:
            lines.append(' '.join(current_line))
        
        total_height = 0
        line_heights = []
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            height = bbox[3] - bbox[1]
            line_heights.append(height)
            total_height += height + 10
        
        y = (EPD_HEIGHT - total_height) // 2
        
        for i, line in enumerate(lines):
            bbox = draw.textbbox((0, 0), line, font=font)
            width = bbox[2] - bbox[0]
            x = (EPD_WIDTH - width) // 2
            draw.text((x, y), line, font=font, fill='black')
            y += line_heights[i] + 10
        
        return send_to_esp32(img)
    except Exception as e:
        print(f"Error: {e}")
        return False

def display_solid_color(color_name):
    try:
        colors = {
            'black': (0, 0, 0),
            'white': (255, 255, 255),
            'green': (200, 200, 80),
            'blue': (100, 120, 180),
            'red': (200, 80, 50),
            'yellow': (255, 255, 0)
        }
        
        if color_name not in colors:
            return False
        
        img = Image.new('RGB', (EPD_WIDTH, EPD_HEIGHT), colors[color_name])
        return send_to_esp32(img)
    except Exception as e:
        print(f"Error: {e}")
        return False

def display_image_crop(image_path, use_dithering=False):
    try:
        img = Image.open(image_path)
        
        if img.mode != 'RGB':
            img = img.convert('RGB')
        
        if img.height > img.width:
            img = img.rotate(90, expand=True)
        
        img_ratio = img.width / img.height
        display_ratio = EPD_WIDTH / EPD_HEIGHT
        
        if img_ratio > display_ratio:
            new_height = EPD_HEIGHT
            new_width = int(EPD_HEIGHT * img_ratio)
        else:
            new_width = EPD_WIDTH
            new_height = int(EPD_WIDTH / img_ratio)
        
        img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
        img = img.filter(ImageFilter.SHARPEN)
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(1.3)
        
        left = (new_width - EPD_WIDTH) // 2
        top = (new_height - EPD_HEIGHT) // 2
        display_img = img.crop((left, top, left + EPD_WIDTH, top + EPD_HEIGHT))
        
        return send_to_esp32(convert_image_to_binary(display_img, use_dithering))
    except Exception as e:
        print(f"Error: {e}")
        return False

@app.route('/color/<color_name>')
def display_color(color_name):
    if display_solid_color(color_name):
        return f'{color_name.capitalize()} displayed! <a href="/">Go back</a>'
    else:
        return 'Error. <a href="/">Go back</a>'

@app.route('/')
def index():
    uploads = []
    if os.path.exists(app.config['UPLOAD_FOLDER']):
        files = os.listdir(app.config['UPLOAD_FOLDER'])
        image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp'}
        uploads = [f for f in files if os.path.splitext(f)[1].lower() in image_extensions]
        uploads.sort(reverse=True)
    
    return render_template('index.html', uploads=uploads)

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return redirect(url_for('index'))
    
    file = request.files['file']
    
    if file.filename == '':
        return redirect(url_for('index'))
    
    if file:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        ext = os.path.splitext(file.filename)[1]
        filename = f"{timestamp}{ext}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        
        use_dithering = request.form.get('dithering') == 'on'
        
        if display_image(filepath, use_dithering=use_dithering):
            return 'Image displayed! <a href="/">Go back</a>'
        else:
            return 'Error. <a href="/">Go back</a>'

@app.route('/display/<filename>')
def display_from_gallery(filename):
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    
    if os.path.exists(filepath):
        use_dithering = request.args.get('dithering') == 'true'
        use_raw = request.args.get('raw') == 'true'
        use_crop = request.args.get('crop') == 'true'
        
        if use_crop:
            success = display_image_crop(filepath, use_dithering=use_dithering)
        elif use_raw:
            success = display_image_raw(filepath)
        else:
            success = display_image(filepath, use_dithering=use_dithering)
        
        if success:
            return 'Image displayed! <a href="/">Go back</a>'
        else:
            return 'Error. <a href="/">Go back</a>'
    else:
        return 'Not found. <a href="/">Go back</a>'

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/text', methods=['POST'])
def display_text_route():
    text = request.form.get('text', '')
    
    if text:
        if display_text(text):
            return 'Text displayed! <a href="/">Go back</a>'
        else:
            return 'Error. <a href="/">Go back</a>'
    
    return redirect(url_for('index'))

@app.route('/delete/<filename>', methods=['POST'])
def delete_image(filename):
    try:
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        
        if os.path.exists(filepath) and os.path.dirname(os.path.abspath(filepath)) == os.path.abspath(app.config['UPLOAD_FOLDER']):
            os.remove(filepath)
            return 'Deleted', 200
        else:
            return 'Not found', 404
    except Exception as e:
        print(f"Error: {e}")
        return 'Error', 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
