# TomeBox

TomeBox is a desktop application for managing, downloading, playing, and converting Audible audiobooks. It provides a unified interface for your cloud library and local files, built-in DRM decryption, and chapter-aware playback.

## Get Started

### 1. 
#### Download and extract the TomeBox folder.

### 2. 
#### If you are on Windows, double-click setup.bat.

### 3. 
#### If you are on Mac/Linux, double-click setup.command (Note: Mac/Linux users may need to right-click -> properties -> allow executing as program depending on their security settings).

### 4. 
#### The script will install everything, check for FFmpeg, and drop a TomeBox shortcut right on your desktop!

## Features

### Core Library & Synchronization
* **Unified Data View:** Merges Audible API cloud data with local file system paths into a single `ttk.Treeview`, preventing duplicate entries.
* **Silent Background Polling:** A daemon thread queries the Audible API every 15 minutes to detect new purchases, updating the cache and UI without interrupting user actions.
* **Dynamic Hard Drive Monitoring:** A localized worker thread checks the integrity of `library.json` and the physical files every 2 seconds. If a user deletes an `.m4b` file via the OS file explorer, TomeBox drops it from the UI instantly.
* **Cover Art & Metadata:** Asynchronous workers fetch 500px resolution cover art and extended author/series metadata from the Audible catalog API upon file selection.

### Authentication & Decryption
* **Dual Authentication Paths:** Supports intercepting browser-based OAuth callbacks or loading pre-existing `.json` authorization files. 
* **Multi-Region Support:** Built-in locale switching (US, UK, AU, CA, DE, FR, JP) for accurate catalog querying.
* **Native DRM Handling:** Automatically requests the `Adrm` content license via the API to extract the offline AAXC encryption keys (`audible_key` and `audible_iv`) required for decryption.

### Downloading & Conversion Engine
* **Throttled UI Streaming:** Downloads utilize 32KB chunk streams. UI progress updates are throttled to only fire on integer percentage changes, preventing the Tkinter event loop from deadlocking on high-speed connections.
* **Batch Queue Manager:** A custom `tk.Canvas` drawer handles multiple downloads with individual progress tracking, status updates, and cancellation flags.
* **FFmpeg Piped Conversion:** Bypasses temporary file creation by piping the decrypted stream directly into a standard `.m4b` container format.
* **Chapter Extraction:** Parses metadata to allow splitting a single audiobook into multiple, sequentially numbered `.m4b` files based on chapter timestamps.

### Playback System
* **Persistent State Memory:** Auto-saves the exact timestamp and current chapter index to `library.json` on exit, pause, or skip.
* **Dynamic Speed Control:** Injects the `-af atempo=X` filter into the `ffplay` subprocess, allowing playback speeds from 0.8x to 3.0x without modifying the source file.
* **OS-Level Audio Hooks:** On Windows, utilizes `pycaw` to hook directly into the Windows Volume Mixer, adjusting the specific `ffplay.exe` session volume independently of the master system volume. (Uses process restarts on macOS/Linux).

### User Interface & Theming
* **Dual Engine Architecture:** Supports switching between the modern `sv_ttk` (Windows 11 style) engine and the classic `ttk` engine (requires application restart to flush memory).
* **Recursive Container Painting:** In Classic Mode, a recursive function traverses the Tkinter widget tree to manually paint every raw `tk.Frame`, eliminating the default white structural borders.
* **Developer Palettes:** Eight hardcoded custom themes for the Classic Engine, including Solarized, Dracula, Cyberpunk, and Nordic Slate.
* **Custom Menu Bar:** Strips the native Win32 OS menu bar and replaces it with a styled `ttk.Menubutton` layout to ensure edge-to-edge dark mode consistency.

### Data Export
* **CSV Dumps:** Flattens the unified library dictionary into a standard comma-separated variable file including Title, Author, Series, Duration, ASIN, and Local Path.
* **Interactive HTML Gallery:** Generates an offline, CSS-styled grid layout of the user's library, embedding fetched cover art URLs and status tags directly into the DOM.

## Prerequisites

**1. FFmpeg (Required)**
TomeBox relies on FFmpeg and FFplay for media probing, playback, and conversion.
* Download and install FFmpeg.
* Ensure the `ffmpeg` and `ffplay` executables are added to your system's `PATH` environment variable.

**2. Python Dependencies**
Install the required Python packages using pip:
```bash
pip install audible requests pillow sv-ttk rsa
```

*(Windows Only)* For native volume mixer control during playback, install `pycaw`:
```bash
pip install pycaw comtypes
```

## Installation & Usage

1.  Clone or download the repository.
2.  Run the application:
    ```bash
    python aax_player.py
    ```
3.  **Authentication:** Navigate to the "Audible Authentication" panel on the right. Select your region and click **Browser Login**. Follow the prompts to authenticate via your web browser and paste the resulting URL back into the application.
4.  **Downloading:** Select a "Cloud Only" title from the library list and click **Download Selected**. You will be prompted to select a save directory if a default is not set in the `File` menu.
5.  **Playback & Conversion:** Double-click a downloaded title to begin playback, or select it and use the **Convert Selected** button to extract the audio to `.m4b`.

## Application Data

TomeBox generates the following local files in its root directory to manage state and settings:
* `library.json`: Tracks local file paths, metadata, and playback history.
* `cloud_cache.json`: Caches your Audible library metadata to reduce API calls.
* `my_audible_auth.json`: Stores your active Audible session data.
* `settings.json`: Stores application preferences (UI theme, default download directory).
* `aax_manager.log`: Output log for debugging and process tracking.