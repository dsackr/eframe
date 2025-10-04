from flask import Flask, render_template, request, redirect, url_for, send_from_directory
from PIL import Image, ImageEnhance, ImageFilter, ImageDraw, ImageFont
import os
import sys
from datetime import datetime

# Add Waveshare library path
picdir = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'pic')
libdir = os.path.join(os.path.expanduser('~'), 'e-Paper/RaspberryPi_JetsonNano/python/lib')
if os.path.exists(libdir):
    sys.path.append(libdir)

from waveshare_epd import epd7in3e

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# Display dimensions
EPD_WIDTH = 800
EPD_HEIGHT = 480

def display_image_raw(image_path):
    """Display image with minimal processing"""
    try:
        epd = epd7in3e.EPD()
        epd.init()
        epd.Clear()
        
        # Open and resize only - no filters
        img = Image.open(image_path)
        if img.mode != 'RGB':
            img = img.convert('RGB')
        
        # Auto-rotate portrait images to landscape
        if img.height > img.width:
            img = img.rotate(90, expand=True)
        
        # Simple resize to fit
        img.thumbnail((EPD_WIDTH, EPD_HEIGHT), Image.Resampling.LANCZOS)
        
        # Create canvas and center
        display_img = Image.new('RGB', (EPD_WIDTH, EPD_HEIGHT), 'white')
        x = (EPD_WIDTH - img.width) // 2
        y = (EPD_HEIGHT - img.height) // 2
        display_img.paste(img, (x, y))
        
        # Display directly
        epd.display(epd.getbuffer(display_img))
        epd.sleep()
        
        return True
    except Exception as e:
        print(f"Error: {e}")
        return False

def display_image(image_path, use_dithering=False):
    """Display an image on the e-paper display with optional dithering"""
    try:
        epd = epd7in3e.EPD()
        epd.init()
        epd.Clear()
        
        # Open image
        img = Image.open(image_path)
        
        # Convert to RGB if needed
        if img.mode != 'RGB':
            img = img.convert('RGB')
        
        # Auto-rotate portrait images to landscape
        if img.height > img.width:
            print(f"Portrait image detected ({img.width}x{img.height}), rotating 90 degrees")
            img = img.rotate(90, expand=True)
        
        # Calculate scaling to fill display while maintaining aspect ratio
        img_ratio = img.width / img.height
        display_ratio = EPD_WIDTH / EPD_HEIGHT
        
        if img_ratio > display_ratio:
            # Image is wider - fit to width
            new_width = EPD_WIDTH
            new_height = int(EPD_WIDTH / img_ratio)
        else:
            # Image is taller - fit to height
            new_height = EPD_HEIGHT
            new_width = int(EPD_HEIGHT * img_ratio)
        
        # Use LANCZOS for high-quality downsampling
        img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
        
        # Apply sharpening filter
        img = img.filter(ImageFilter.SHARPEN)
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(1.3)  # Contrast boost
        
        # Create white background and center image
        display_img = Image.new('RGB', (EPD_WIDTH, EPD_HEIGHT), 'white')
        x = (EPD_WIDTH - img.width) // 2
        y = (EPD_HEIGHT - img.height) // 2
        display_img.paste(img, (x, y))
        
        # Apply dithering for better color representation
        if use_dithering:
            # Define the 7 colors available on the display
            # Black, White, Green, Blue, Red, Yellow, Orange
            palette = [
                0, 0, 0,        # Black
                255, 255, 255,  # White
                0, 255, 0,      # Green
                0, 0, 255,      # Blue
                255, 0, 0,      # Red
                255, 255, 0,    # Yellow
                255, 128, 0     # Orange
            ]
            
            # Create palette image
            palette_img = Image.new('P', (1, 1))
            palette_img.putpalette(palette + [0] * (256 * 3 - len(palette)))
            
            # Convert with Floyd-Steinberg dithering
            display_img = display_img.quantize(palette=palette_img, dither=Image.Dither.FLOYDSTEINBERG)
            display_img = display_img.convert('RGB')
        
        # Display on e-paper
        epd.display(epd.getbuffer(display_img))
        epd.sleep()
        
        return True
    except Exception as e:
        print(f"Error displaying image: {e}")
        return False
        
def display_text(text, font_size=40):
    """Display text on the e-paper display"""
    try:
        epd = epd7in3e.EPD()
        epd.init()
        epd.Clear()
        
        # Create image
        img = Image.new('RGB', (EPD_WIDTH, EPD_HEIGHT), 'white')
        draw = ImageDraw.Draw(img)
        
        # Use default font
        try:
            font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', font_size)
        except:
            font = ImageFont.load_default()
        
        # Word wrap and draw text
        margin = 20
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
        
        # Draw lines
        y = margin
        for line in lines:
            draw.text((margin, y), line, font=font, fill='black')
            bbox = draw.textbbox((0, 0), line, font=font)
            y += (bbox[3] - bbox[1]) + 10
        
        # Display
        epd.display(epd.getbuffer(img))
        epd.sleep()
        
        return True
    except Exception as e:
        print(f"Error displaying text: {e}")
        return False

def display_solid_color(color_name):
    """Display a solid color on the e-paper display"""
    try:
        epd = epd7in3e.EPD()
        epd.init()
        
        # Define the 7 colors
        colors = {
            'black': (0, 0, 0),
            'white': (255, 255, 255),
            'green': (0, 255, 0),
            'blue': (0, 0, 255),
            'red': (255, 0, 0),
            'yellow': (255, 255, 0),
            'orange': (255, 128, 0)
        }
        
        if color_name not in colors:
            return False
        
        # Create solid color image
        img = Image.new('RGB', (EPD_WIDTH, EPD_HEIGHT), colors[color_name])
        
        # Display
        epd.display(epd.getbuffer(img))
        epd.sleep()
        
        return True
    except Exception as e:
        print(f"Error displaying color: {e}")
        return False
def display_image_crop(image_path, use_dithering=False):
    """Display image cropped to fill entire screen"""
    try:
        epd = epd7in3e.EPD()
        epd.init()
        epd.Clear()
        
        # Open image
        img = Image.open(image_path)
        
        # Convert to RGB if needed
        if img.mode != 'RGB':
            img = img.convert('RGB')
        
        # Auto-rotate portrait images to landscape
        if img.height > img.width:
            print(f"Portrait image detected ({img.width}x{img.height}), rotating 90 degrees")
            img = img.rotate(90, expand=True)
        
        # Calculate scaling to FILL display (crop excess)
        img_ratio = img.width / img.height
        display_ratio = EPD_WIDTH / EPD_HEIGHT
        
        if img_ratio > display_ratio:
            # Image is wider - fit to height, crop width
            new_height = EPD_HEIGHT
            new_width = int(EPD_HEIGHT * img_ratio)
        else:
            # Image is taller - fit to width, crop height
            new_width = EPD_WIDTH
            new_height = int(EPD_WIDTH / img_ratio)
        
        # Resize
        img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
        
        # Apply sharpening filter
        img = img.filter(ImageFilter.SHARPEN)
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(1.3)  # Contrast boost
        
        # Crop to exact display size (center crop)
        left = (new_width - EPD_WIDTH) // 2
        top = (new_height - EPD_HEIGHT) // 2
        right = left + EPD_WIDTH
        bottom = top + EPD_HEIGHT
        
        display_img = img.crop((left, top, right, bottom))
        
        # Apply dithering for better color representation
        if use_dithering:
            palette = [
                0, 0, 0,        # Black
                255, 255, 255,  # White
                0, 255, 0,      # Green
                0, 0, 255,      # Blue
                255, 0, 0,      # Red
                255, 255, 0,    # Yellow
                255, 128, 0     # Orange
            ]
            
            palette_img = Image.new('P', (1, 1))
            palette_img.putpalette(palette + [0] * (256 * 3 - len(palette)))
            
            display_img = display_img.quantize(palette=palette_img, dither=Image.Dither.FLOYDSTEINBERG)
            display_img = display_img.convert('RGB')
        
        # Display on e-paper
        epd.display(epd.getbuffer(display_img))
        epd.sleep()
        
        return True
    except Exception as e:
        print(f"Error displaying image: {e}")
        return False
        
@app.route('/color/<color_name>')
def display_color(color_name):
    if display_solid_color(color_name):
        return f'{color_name.capitalize()} displayed successfully! <a href="/">Go back</a>'
    else:
        return 'Error displaying color. <a href="/">Go back</a>'

@app.route('/')
def index():
    # Get list of uploaded images
    uploads = []
    if os.path.exists(app.config['UPLOAD_FOLDER']):
        files = os.listdir(app.config['UPLOAD_FOLDER'])
        # Filter for image files and sort by newest first
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
        # Save with timestamp
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        ext = os.path.splitext(file.filename)[1]
        filename = f"{timestamp}{ext}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        
        # Get dithering preference from form
        use_dithering = request.form.get('dithering') == 'on'
        
        if display_image(filepath, use_dithering=use_dithering):
            return 'Image displayed successfully! <a href="/">Go back</a>'
        else:
            return 'Error displaying image. <a href="/">Go back</a>'

@app.route('/display/<filename>')
def display_from_gallery(filename):
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    
    if os.path.exists(filepath):
        # Check parameters
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
            return 'Image displayed successfully! <a href="/">Go back</a>'
        else:
            return 'Error displaying image. <a href="/">Go back</a>'
    else:
        return 'Image not found. <a href="/">Go back</a>'
        
@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/text', methods=['POST'])
def display_text_route():
    text = request.form.get('text', '')
    
    if text:
        if display_text(text):
            return 'Text displayed successfully! <a href="/">Go back</a>'
        else:
            return 'Error displaying text. <a href="/">Go back</a>'
    
    return redirect(url_for('index'))

@app.route('/delete/<filename>', methods=['POST'])
def delete_image(filename):
    """Delete an uploaded image"""
    try:
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        
        # Security check - make sure it's actually in the uploads folder
        if os.path.exists(filepath) and os.path.dirname(os.path.abspath(filepath)) == os.path.abspath(app.config['UPLOAD_FOLDER']):
            os.remove(filepath)
            return 'Deleted', 200
        else:
            return 'Not found', 404
    except Exception as e:
        print(f"Error deleting: {e}")
        return 'Error', 500
        
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
