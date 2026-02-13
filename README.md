# Eframe

A simple Flask application for uploading, previewing, rotating, and displaying images on an e-paper panel. Images are stored locally and can be sent to the display with letterbox or crop modes.

## Requirements

- Python 3.10+
- Dependencies from `requirements.txt`

## Setup

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Run the server:
   ```bash
   python app.py
   ```
3. Open the web UI at `http://localhost:5000`.

## Usage

- **Upload**: Choose an image file (png, jpg, jpeg, gif, bmp, webp) and optionally send it to the display immediately.
- **Display**: Send any saved image to the e-paper panel using letterbox or crop modes.
- **Rotate**: Rotate saved images left or right before displaying.
- **Preview**: Thumbnails in the gallery load directly from stored files.
- **Delete**: Remove an uploaded image from storage using the delete button on each card.

Uploaded files are stored in the `uploads/` directory. The app attempts to use the e-paper driver if available; otherwise it runs in development mode without display output.

## Image Sizing and Color Guidance

- **Display resolution**: `1600 x 1200` (landscape) or `1200 x 1600` (portrait).
- **For letterbox and crop to produce the same visible result**, use an image that already matches the target aspect ratio:
  - Landscape: **4:3** (examples: `1600x1200`, `2400x1800`, `3200x2400`)
  - Portrait: **3:4** (examples: `1200x1600`, `1800x2400`, `2400x3200`)

When the input image ratio matches the display ratio, neither mode needs to add bars or trim content, so both modes render the same composition.

### Native e-ink color approximations (HTML hex)

The display supports six native colors; these are the closest standard HTML hex values:

- Black: `#000000`
- White: `#FFFFFF`
- Yellow: `#FFFF00`
- Red: `#FF0000`
- Blue: `#0000FF`
- Green: `#00FF00`
