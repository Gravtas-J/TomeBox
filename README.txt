Truely Open Audible

A free, open-source desktop application to manage, download, play, and convert Audible audiobooks. Built with Python and Tkinter, this tool provides a standalone alternative to commercial audiobook managers, giving you full control over your purchased media.

FEATURES
- Audible Account Integration: Secure browser-based login or manual auth file loading.
- Cloud Syncing: Fetch and view your entire Audible library.
- Direct Downloading: Download standard AAX and modern AAXC DRM formats.
- AAXC Decryption: Automatically extracts offline vouchers and cryptographic keys for playback.
- Local Library Management: Track downloaded files and import existing M4B/AAX files.
- Built-in Media Player: Play DRM-protected and DRM-free audiobooks directly in the app. Includes chapter navigation, progress tracking, and 30-second skip controls.
- Format Conversion: Lossless conversion from DRM-protected AAX/AAXC to standard M4B using FFmpeg.
- Queue Management: Download multiple titles sequentially or set a default download directory.

PREREQUISITES
1. Python 3.8 or higher.
2. FFmpeg (ffmpeg, ffplay, and ffprobe must be installed and added to your system PATH).

INSTALLATION
1. Clone or download this repository.
2. Install the required Python dependencies:
   pip install -r requirements.txt

3. Install FFmpeg:
   - Windows: Download the essential build from gyan.dev, extract it, and add the 'bin' folder to your System Environment Variables (PATH).
   - macOS: run 'brew install ffmpeg'
   - Linux: run 'sudo apt install ffmpeg'

USAGE
1. Launch the application:
   python aax_player.py

2. Authentication:
   - Navigate to the "Settings & Login" tab.
   - Select your region and click "Browser Login".
   - Follow the prompts to log in via your web browser and paste the resulting URL back into the application.

3. Downloading:
   - Navigate to the "Library" tab.
   - Click "Refresh Cloud Library" to sync your purchases.
   - Select a title and click "Download Selected" or click "Download All" to queue your entire library.

4. Playback and Conversion:
   - Select a file in the Local Library and click "Send to Player".
   - Use the playback controls to listen directly.
   - Click "Convert to DRM-Free .m4b" to create a standard audio file compatible with any media player.

ROADMAP (Upcoming Features)
- Automated Background Syncing
- Chapter Splitting (exporting individual chapters as separate files)
- Advanced Metadata Export (CSV generation)
- Static HTML Web Page Generation for library viewing
- Playback Memory (saving position between sessions)
- Speed and Volume control sliders
- Modern UI Theming and Cover Art display

DISCLAIMER
This software is intended for personal use and archival purposes only. It is designed to allow users to play and manage media they have legally purchased. Do not use this tool to distribute copyrighted material.