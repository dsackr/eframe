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
