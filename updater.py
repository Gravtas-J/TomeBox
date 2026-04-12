import os
import urllib.request
import urllib.error
import json

# --- Configuration ---
REPO_OWNER = "Gravtas-J"
REPO_NAME = "Truely-Open-Audible"
BRANCH = "main"
TARGET_FILE = "aax_player.py"
VERSION_FILE = ".tomebox_version"

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

def download_latest_file():
    url = f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/{BRANCH}/{TARGET_FILE}"
    req = urllib.request.Request(url, headers=get_headers())
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            content = response.read()
            with open(TARGET_FILE, "wb") as f:
                f.write(content)
            return True
    except Exception as e:
        print(f"[Updater] Failed to download update: {e}")
        return False

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
        print("[Updater] New version found! Downloading...")
        if download_latest_file():
            with open(VERSION_FILE, "w") as f:
                f.write(latest_sha)
            print("[Updater] Update complete.")
    else:
        print("[Updater] TomeBox is up to date.")

if __name__ == "__main__":
    main()