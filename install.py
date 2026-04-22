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
MAIN_SCRIPT = "main.py"  # Now points to the new entry point
REQUIREMENTS_FILE = "requirements.txt"

def print_step(msg):
    print(f"\n[+] {msg}")

def download_portable_ffmpeg(base_dir):
    print_step("Setting up Portable FFmpeg...")
    if platform.system() != "Windows":
        print("  -> Non-Windows OS detected. Please install FFmpeg via your package manager.")
        return

    exe_names = ["ffmpeg.exe", "ffplay.exe", "ffprobe.exe"]
    missing = [exe for exe in exe_names if not (base_dir / exe).exists()]
    
    if not missing:
        print("  -> Portable FFmpeg binaries already present.")
        return
        
    print(f"  -> Downloading official FFmpeg release...")
    url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
    zip_path = base_dir / "ffmpeg_temp.zip"
    
    try:
        urllib.request.urlretrieve(url, zip_path)
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            for file_info in zip_ref.infolist():
                if file_info.filename.endswith(tuple(exe_names)):
                    file_info.filename = os.path.basename(file_info.filename)
                    zip_ref.extract(file_info, base_dir)
        print("  -> Extraction complete.")
    except Exception as e:
        print(f"  -> ERROR downloading FFmpeg: {e}")
    finally:
        if zip_path.exists():
            os.remove(zip_path)

def install_requirements():
    print_step("Installing Python requirements...")
    if not os.path.exists(REQUIREMENTS_FILE):
        return
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", REQUIREMENTS_FILE])
        print("  -> Requirements installed successfully.")
    except subprocess.CalledProcessError as e:
        print(f"  -> ERROR: Failed to install requirements.\n{e}")

def create_startup_scripts(base_dir, py_exec):
    print_step("Creating OS-agnostic startup scripts...")
    
    # --- NEW: Use pythonw.exe to hide the console completely ---
    pyw_exec = str(py_exec).replace("python.exe", "pythonw.exe")
    
    # 1. Windows VBScript Launcher (Replaces the .bat file)
    vbs_launcher_path = base_dir / "start_tomebox.vbs"
    with open(vbs_launcher_path, "w") as f:
        f.write('Set objShell = CreateObject("WScript.Shell")\n')
        f.write(f'objShell.CurrentDirectory = "{base_dir}"\n')
        # Run updater silently (0) and wait for it to finish (True)
        f.write(f'objShell.Run """{pyw_exec}"" updater.py", 0, True\n')
        # Run app silently (0) and do NOT wait (False)
        f.write(f'objShell.Run """{pyw_exec}"" {MAIN_SCRIPT}", 0, False\n')
    print(f"  -> Created {vbs_launcher_path.name}")

    # 2. Unix Shell Script (Linux / macOS)
    sh_path = base_dir / "start_tomebox.sh"
    with open(sh_path, "w", newline='\n') as f:
        f.write("#!/bin/bash\n")
        f.write(f"cd \"{base_dir}\"\n")
        f.write(f"\"{py_exec}\" updater.py\n")
        f.write(f"\"{py_exec}\" {MAIN_SCRIPT}\n")
    
    if platform.system() != "Windows":
        os.chmod(sh_path, 0o755)
    
    return vbs_launcher_path, sh_path

def create_shortcut(base_dir, vbs_launcher_path, sh_path, py_exec):
    print_step("Creating desktop shortcut...")
    os_name = platform.system()
    desktop_dir = Path.home() / "Desktop"
    if os_name == "Windows":
        py_exec = str(base_dir / "venv" / "Scripts" / "pythonw.exe") # pythonw hides the console
    else:
        py_exec = str(base_dir / "venv" / "bin" / "python3")
    if os_name == "Windows":
        shortcut_maker_path = base_dir / "make_shortcut.vbs"
        shortcut_path = desktop_dir / f"{APP_NAME}.lnk"
        
        # Notice TargetPath is now {py_exec}, not a raw pythonw.exe string
        vbs_content = f"""
Set oWS = WScript.CreateObject("WScript.Shell")
sLinkFile = "{shortcut_path}"
Set oLink = oWS.CreateShortcut(sLinkFile)
oLink.TargetPath = "{py_exec}"
oLink.Arguments = "{MAIN_SCRIPT}"
oLink.WorkingDirectory = "{base_dir}"
oLink.Description = "{APP_NAME} Audiobook Manager"
oLink.IconLocation = "{base_dir}\\ui\\tomebox.ico"
oLink.Save
"""
        with open(shortcut_maker_path, "w") as f:
            f.write(vbs_content)
            
        subprocess.run(["cscript.exe", "//Nologo", str(shortcut_maker_path)])
        os.remove(shortcut_maker_path)
        print(f"  -> Created stealth Windows shortcut at {shortcut_path}")

    elif os_name == "Linux":
        shortcut_path = desktop_dir / f"{APP_NAME}.desktop"
        desktop_content = f"""[Desktop Entry]
Name={APP_NAME}
Comment=Audiobook Manager
Exec={sh_path}
Icon={base_dir}/ui/tomebox.png
Terminal=false
Type=Application
Categories=AudioVideo;
"""
        with open(shortcut_path, "w") as f:
            f.write(desktop_content)
        os.chmod(shortcut_path, 0o755)

    elif os_name == "Darwin":
        app_path = desktop_dir / f"{APP_NAME}.app"
        apple_script = f'do shell script "cd \\"{base_dir}\\" && \\"{py_exec}\\" \\"{MAIN_SCRIPT}\\" > /dev/null 2>&1 &"'
        subprocess.run(["osacompile", "-e", apple_script, "-o", str(app_path)])

def main():
    print(f"=== {APP_NAME} Installer ===")
    base_dir = Path(__file__).parent.resolve()
    py_exec = sys.executable

    download_portable_ffmpeg(base_dir)
    install_requirements()
    vbs_launcher_path, sh_path = create_startup_scripts(base_dir, py_exec)
    create_shortcut(base_dir, vbs_launcher_path, sh_path, py_exec)
    
    print_step("Installation Complete!")

if __name__ == "__main__":
    main()