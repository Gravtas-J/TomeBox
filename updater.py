import os
import sys
import subprocess
import urllib.request
import urllib.error
import json

# --- Configuration ---
REPO_OWNER = "Gravtas-J"
REPO_NAME = "TomeBox"
BRANCH = "main"
VERSION_FILE = ".tomebox_version"

# All project files across the new modular directory structure
TARGET_FILES = [
    "main.py",
    "requirements.txt",
    "updater.py",
    "install.py",
    "api/audible_client.py",
    "core/converter.py",
    "core/database.py",
    "core/downloader.py",
    "core/exporter.py",
    "core/player.py",
    "server/mobile_ui.html",
    "server/web_app.py",
    "ui/app_window.py",
    "ui/dialogs.py",
    "ui/theme.py",
    "ui/tomebox.ico",
    "ui/tomebox.png"
]

def get_headers():
    return {"User-Agent": "TomeBox-Updater"}

def get_latest_commit_sha():
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/commits/{BRANCH}"
    req = urllib.request.Request(url, headers=get_headers())
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
            return data.get("sha")
    except Exception as e:
        print(f"[Updater] Failed to check for updates: {e}")
        return None

def download_file(filename):
    """Downloads a file and ensures the local directory structure exists."""
    url = f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/{BRANCH}/{filename}"
    req = urllib.request.Request(url, headers=get_headers())
    
    # Ensure subdirectories (core, ui, etc.) exist locally
    local_dir = os.path.dirname(filename)
    if local_dir and not os.path.exists(local_dir):
        os.makedirs(local_dir, exist_ok=True)
        
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            content = response.read()
            with open(filename, "wb") as f:
                f.write(content)
            return True
    except Exception as e:
        print(f"[Updater] Failed to download {filename}: {e}")
        return False

def update_requirements():
    if not os.path.exists("requirements.txt"):
        return
        
    print("[Updater] Verifying dependencies...")
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"],
            stdout=subprocess.DEVNULL, 
            stderr=subprocess.DEVNULL
        )
        print("[Updater] Dependencies up to date.")
    except subprocess.CalledProcessError as e:
        print(f"[Updater] Warning: Failed to automatically install dependencies: {e}")

def main():
    print("[Updater] Checking for updates...")
    latest_sha = get_latest_commit_sha()
    if not latest_sha:
        return

    local_sha = ""
    if os.path.exists(VERSION_FILE):
        with open(VERSION_FILE, "r") as f:
            local_sha = f.read().strip()

    if latest_sha != local_sha:
        print("[Updater] New version found! Downloading updates...")
        
        all_success = True
        for file in TARGET_FILES:
            print(f"  -> Fetching {file}...")
            if not download_file(file):
                all_success = False

        if all_success:
            update_requirements()
            with open(VERSION_FILE, "w") as f:
                f.write(latest_sha)
            print("[Updater] Update complete.")
        else:
            print("[Updater] Some files failed to download. Update incomplete.")
    else:
        print("[Updater] TomeBox is up to date.")

if __name__ == "__main__":
    main()