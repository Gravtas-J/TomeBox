import os
import sys
import subprocess
import shutil
import platform
import urllib.request
import zipfile
from pathlib import Path

# --- Configuration ---
APP_NAME = "TomeBox"
MAIN_SCRIPT = "aax_player.py"
REQUIREMENTS_FILE = "requirements.txt"

def print_step(msg):
    print(f"\n[+] {msg}")

def download_portable_ffmpeg(base_dir):
    print_step("Setting up Portable FFmpeg...")
    
    if platform.system() != "Windows":
        print("  -> Non-Windows OS detected. Please install FFmpeg via your package manager (e.g., apt, brew).")
        return

    # We need ffprobe for chapter extraction, alongside ffmpeg and ffplay
    exe_names = ["ffmpeg.exe", "ffplay.exe", "ffprobe.exe"]
    missing = [exe for exe in exe_names if not (base_dir / exe).exists()]
    
    if not missing:
        print("  -> Portable FFmpeg binaries already present in the TomeBox folder. Skipping download.")
        return
        
    print(f"  -> Missing binaries: {', '.join(missing)}")
    print("  -> Downloading official FFmpeg release (this may take a minute depending on your connection)...")
    
    # Official, highly-available Windows build repository
    url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
    zip_path = base_dir / "ffmpeg_temp.zip"
    
    try:
        urllib.request.urlretrieve(url, zip_path)
        
        print("  -> Download complete. Extracting binaries...")
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            for file_info in zip_ref.infolist():
                # Locate the specific .exe files hidden inside the nested folders
                if file_info.filename.endswith(tuple(exe_names)):
                    # Trick the zip extractor into dropping the file directly into the base directory 
                    # instead of recreating the nested folder structure
                    file_info.filename = os.path.basename(file_info.filename)
                    zip_ref.extract(file_info, base_dir)
                    print(f"     Extracted: {file_info.filename}")
        
    except Exception as e:
        print(f"  -> ERROR downloading or extracting FFmpeg: {e}")
        print("  -> You may need to download it manually from https://ffmpeg.org/download.html")
    finally:
        if zip_path.exists():
            os.remove(zip_path)
            print("  -> Cleaned up temporary zip file.")

def install_requirements():
    print_step("Installing Python requirements...")
    if not os.path.exists(REQUIREMENTS_FILE):
        print(f"  -> WARNING: {REQUIREMENTS_FILE} not found. Skipping pip install.")
        return

    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", REQUIREMENTS_FILE])
        print("  -> Requirements installed successfully.")
    except subprocess.CalledProcessError as e:
        print(f"  -> ERROR: Failed to install requirements. Please install them manually.\n{e}")

def create_startup_scripts(base_dir, py_exec):
    print_step("Creating OS-agnostic startup scripts...")
    
    # 1. Windows Batch File
    bat_path = base_dir / "start_tomebox.bat"
    with open(bat_path, "w") as f:
        f.write("@echo off\n")
        f.write(f"cd /d \"{base_dir}\"\n")
        # --- NEW: Run updater before main script ---
        f.write(f"\"{py_exec}\" updater.py\n")
        # ------------------------------------------
        f.write(f"\"{py_exec}\" {MAIN_SCRIPT}\n")
    print(f"  -> Created {bat_path.name}")

    # 2. Unix Shell Script (Linux / macOS)
    sh_path = base_dir / "start_tomebox.sh"
    with open(sh_path, "w", newline='\n') as f:
        f.write("#!/bin/bash\n")
        f.write(f"cd \"{base_dir}\"\n")
        # --- NEW: Run updater before main script ---
        f.write(f"\"{py_exec}\" updater.py\n")
        # ------------------------------------------
        f.write(f"\"{py_exec}\" {MAIN_SCRIPT}\n")
    
    # Make shell script executable
    if platform.system() != "Windows":
        os.chmod(sh_path, 0o755)
    print(f"  -> Created {sh_path.name}")
    
    return bat_path, sh_path

def create_shortcut(base_dir, bat_path, sh_path, py_exec):
    print_step("Creating desktop shortcut...")
    os_name = platform.system()
    desktop_dir = Path.home() / "Desktop"

    if os_name == "Windows":
        shortcut_path = desktop_dir / f"{APP_NAME}.lnk"
        vbs_path = base_dir / "create_shortcut.vbs"
        
        # Use pythonw.exe to run without a terminal window
        pyw_exec = str(py_exec).replace("python.exe", "pythonw.exe")
        
        vbs_content = f"""
Set oWS = WScript.CreateObject("WScript.Shell")
sLinkFile = "{shortcut_path}"
Set oLink = oWS.CreateShortcut(sLinkFile)
oLink.TargetPath = "{pyw_exec}"
oLink.Arguments = "{MAIN_SCRIPT}"
oLink.WorkingDirectory = "{base_dir}"
oLink.Description = "{APP_NAME} Audiobook Manager"
oLink.IconLocation = "{base_dir}\\tomebox.ico"
oLink.Save
"""
        with open(vbs_path, "w") as f:
            f.write(vbs_content)
            
        subprocess.run(["cscript.exe", "//Nologo", str(vbs_path)])
        os.remove(vbs_path)
        print(f"  -> Created stealth Windows shortcut at {shortcut_path}")

    elif os_name == "Linux":
        shortcut_path = desktop_dir / f"{APP_NAME}.desktop"
        desktop_content = f"""[Desktop Entry]
Name={APP_NAME}
Comment=Audiobook Manager
Exec={sh_path}
Icon={base_dir}/tomebox.png
Terminal=false
Type=Application
Categories=AudioVideo;
"""
        with open(shortcut_path, "w") as f:
            f.write(desktop_content)
        os.chmod(shortcut_path, 0o755)
        print(f"  -> Created Linux .desktop file at {shortcut_path}")

    elif os_name == "Darwin":
        # Compile a native Mac .app bundle to hide the terminal
        app_path = desktop_dir / f"{APP_NAME}.app"
        apple_script = f'do shell script "cd \\"{base_dir}\\" && \\"{py_exec}\\" \\"{MAIN_SCRIPT}\\" > /dev/null 2>&1 &"'
        
        subprocess.run(["osacompile", "-e", apple_script, "-o", str(app_path)])
        print(f"  -> Created native macOS App at {app_path}")

def main():
    print(f"=== {APP_NAME} Installer ===")
    
    base_dir = Path(__file__).parent.resolve()
    py_exec = sys.executable

    # 1. Download Portable FFmpeg (Replaces the manual warning check)
    download_portable_ffmpeg(base_dir)
    
    # 2. Install Requirements
    install_requirements()
    
    # 3. Create Runner Scripts
    bat_path, sh_path = create_startup_scripts(base_dir, py_exec)
    
    # 4. Create Desktop Shortcut
    create_shortcut(base_dir, bat_path, sh_path, py_exec)
    
    print_step("Installation Complete!")
    print(f"You can now launch {APP_NAME} from your desktop shortcut.")

if __name__ == "__main__":
    main()