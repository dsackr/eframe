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

## Fraimic-Compatible API

`/api/info`, `/api/battery`, `/api/refresh`, `/api/image`, `/api/restart`, and `/api/sleep` mirror the real Fraimic frame's REST API (see the [Fraimic guide](https://github.com/Fraimic/Fraimic_eink_canvas_home_assistant_restAPI_guide)) so tools built for a real frame work unmodified against eframe.

eframe also implements a few features requested against the real frame that aren't shipped there yet:

- **`/api/info`** additionally reports `display.width_px`, `display.height_px`, `display.orientation`, `display.device_type`, and `device.device_key` ([issue #2](https://github.com/Fraimic/Fraimic_eink_canvas_home_assistant_restAPI_guide/issues/2), [issue #3](https://github.com/Fraimic/Fraimic_eink_canvas_home_assistant_restAPI_guide/issues/3)).
- **`GET`/`POST /api/settings`** reads or sets `orientation` (`"portrait"` or `"landscape"`), persisted in `device_config.json`. Changing it also changes the expected size for incoming Fraimic `.bin` uploads.
- **Unique mDNS hostname** — eframe advertises `eframe-<device_key prefix>.local` via `zeroconf` in addition to whatever shared hostname mDNS/avahi already resolves for this host, so multiple frames/eframes on one network don't collide on a single name ([issue #1](https://github.com/Fraimic/Fraimic_eink_canvas_home_assistant_restAPI_guide/issues/1)).

## Portal (`/portal`)

A setup UI that mirrors a real Fraimic frame's on-device portal (WiFi setup, `.bin` upload, device information, logs), rebuilt for eframe:

| Page | Purpose |
| --- | --- |
| `/portal` | Landing page with WiFi/power status and navigation tiles |
| `/portal/wifi` | Scan for networks (`iwlist`) and save credentials (`wpa_cli`) |
| `/portal/upload` | Upload a `.bin` file directly to the display |
| `/portal/info` | Device/network/display details, plus the orientation setting |
| `/portal/logs` | Recent application log lines |
| `/portal/get-started` | Placeholder — eframe has no real Fraimic cloud account to set up |

WiFi scanning/saving needs `iwlist`/`wpa_cli` available to the process (the classic wireless-tools + wpa_supplicant stack); it fails soft with a message in `/portal/logs` if they aren't present or accessible.

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
