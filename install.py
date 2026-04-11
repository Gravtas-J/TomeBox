import os
import sys
import subprocess
import shutil
import platform
from pathlib import Path

# --- Configuration ---
APP_NAME = "TomeBox"
MAIN_SCRIPT = "aax_player.py"
REQUIREMENTS_FILE = "requirements.txt"

def print_step(msg):
    print(f"\n[+] {msg}")

def check_ffmpeg():
    print_step("Checking for system dependencies...")
    ffmpeg_installed = shutil.which("ffmpeg") is not None
    ffplay_installed = shutil.which("ffplay") is not None
    
    if ffmpeg_installed and ffplay_installed:
        print("  -> FFmpeg and FFplay found in system PATH.")
    else:
        print("  -> WARNING: FFmpeg or FFplay is missing from system PATH.")
        print("  -> TomeBox will install, but conversion and playback will not function until FFmpeg is installed.")
        print("  -> Download it here: https://ffmpeg.org/download.html")

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
        f.write(f"\"{py_exec}\" {MAIN_SCRIPT}\n")
    print(f"  -> Created {bat_path.name}")

    # 2. Unix Shell Script (Linux / macOS)
    sh_path = base_dir / "start_tomebox.sh"
    with open(sh_path, "w") as f:
        f.write("#!/bin/bash\n")
        f.write(f"cd \"{base_dir}\"\n")
        f.write(f"\"{py_exec}\" {MAIN_SCRIPT}\n")
    
    # Make shell script executable
    if platform.system() != "Windows":
        os.chmod(sh_path, 0o755)
    print(f"  -> Created {sh_path.name}")
    
    return bat_path, sh_path

def create_shortcut(base_dir, bat_path, sh_path):
    print_step("Creating desktop shortcut...")
    os_name = platform.system()
    desktop_dir = Path.home() / "Desktop"

    if os_name == "Windows":
        # Use a temporary VBScript to create the shortcut without needing pywin32 pip package
        shortcut_path = desktop_dir / f"{APP_NAME}.lnk"
        vbs_path = base_dir / "create_shortcut.vbs"
        
        vbs_content = f"""
Set oWS = WScript.CreateObject("WScript.Shell")
sLinkFile = "{shortcut_path}"
Set oLink = oWS.CreateShortcut(sLinkFile)
oLink.TargetPath = "{bat_path}"
oLink.WorkingDirectory = "{base_dir}"
oLink.Description = "{APP_NAME} Audiobook Manager"
oLink.Save
"""
        with open(vbs_path, "w") as f:
            f.write(vbs_content)
            
        subprocess.run(["cscript.exe", "//Nologo", str(vbs_path)])
        os.remove(vbs_path)
        print(f"  -> Created Windows shortcut at {shortcut_path}")

    elif os_name == "Linux":
        # Create a standard .desktop file
        shortcut_path = desktop_dir / f"{APP_NAME}.desktop"
        desktop_content = f"""[Desktop Entry]
Name={APP_NAME}
Comment=Audiobook Manager
Exec={sh_path}
Icon=utilities-terminal
Terminal=false
Type=Application
Categories=AudioVideo;
"""
        with open(shortcut_path, "w") as f:
            f.write(desktop_content)
        os.chmod(shortcut_path, 0o755)
        print(f"  -> Created Linux .desktop file at {shortcut_path}")

    elif os_name == "Darwin": # macOS
        # Create an executable .command wrapper on the desktop
        shortcut_path = desktop_dir / f"{APP_NAME}.command"
        command_content = f"""#!/bin/bash
"{sh_path}"
"""
        with open(shortcut_path, "w") as f:
            f.write(command_content)
        os.chmod(shortcut_path, 0o755)
        print(f"  -> Created macOS command script at {shortcut_path}")

def main():
    print(f"=== {APP_NAME} Installer ===")
    
    base_dir = Path(__file__).parent.resolve()
    py_exec = sys.executable

    # 1. Dependency Check
    check_ffmpeg()
    
    # 2. Install Requirements
    install_requirements()
    
    # 3. Create Runner Scripts
    bat_path, sh_path = create_startup_scripts(base_dir, py_exec)
    
    # 4. Create Desktop Shortcut
    create_shortcut(base_dir, bat_path, sh_path)
    
    print_step("Installation Complete!")
    print(f"You can now launch {APP_NAME} from your desktop shortcut.")

if __name__ == "__main__":
    main()