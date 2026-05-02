import os
import sys
import socket
import threading

class SystemManager:
    def __init__(self, logger):
        self.logger = logger
        self.web_server = None
        self.lock_socket = None
        self.lock_port = 43128
        
        # Set Windows async policy once on boot
        import sys
        if sys.platform == 'win32':
            import asyncio
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    def enforce_single_instance(self, on_wake_callback):
        """Ensures only one instance of TomeBox is running via TCP port locking."""
        self.lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.lock_socket.bind(('127.0.0.1', self.lock_port))
            self.lock_socket.listen(1)
            threading.Thread(target=self._instance_listener_worker, args=(on_wake_callback,), daemon=True).start()
        except socket.error:
            self.logger("Another instance detected. Sending wake signal...")
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.connect(('127.0.0.1', self.lock_port))
                s.sendall(b"WAKEUP")
                s.close()
            except Exception:
                pass
            # Kill this duplicate instance immediately
            sys.exit(0)

    def _instance_listener_worker(self, on_wake_callback):
        while True:
            try:
                conn, addr = self.lock_socket.accept()
                data = conn.recv(1024)
                if data == b"WAKEUP":
                    self.logger("Wake signal received. Bringing window to front.")
                    if on_wake_callback:
                        on_wake_callback()
                conn.close()
            except Exception as e:
                self.logger(f"Socket listener error: {e}")
                break

    def toggle_system_sleep(self, prevent_sleep=True):
        """Prevents Windows from sleeping during active background tasks."""
        if os.name != 'nt':
            return
        try:
            import ctypes
            if prevent_sleep:
                self.logger("Applying sleep prevention for active background task.")
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
            else:
                self.logger("Releasing system sleep prevention.")
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        except Exception as e:
            self.logger(f"Failed to toggle sleep state: {e}")

    def cleanup_orphaned_files(self, download_dir, library_paths=None):
        """Deletes partial downloads, cancelled conversions, or 0-byte corrupted files on startup."""
        import os
        directories_to_scan = set()

        # 1. Target the main download directory
        if download_dir and os.path.exists(download_dir):
            directories_to_scan.add(download_dir)

        # 2. Target parent folders of existing library items
        if library_paths:
            for path in library_paths:
                parent = os.path.dirname(path)
                if os.path.exists(parent):
                    directories_to_scan.add(parent)

        if not directories_to_scan:
            return

        if getattr(self, 'logger', None):
            self.logger("Running startup scan for orphaned/partial files...")
            
        cleaned_count = 0

        try:
            for directory in directories_to_scan:
                try:
                    for filename in os.listdir(directory):
                        filepath = os.path.join(directory, filename)
                        if not os.path.isfile(filepath):
                            continue

                        # Clean up cancelled FFmpeg conversions (.tmp.m4b) and interrupted downloads (.part)
                        if filename.endswith(".part") or "_temp." in filename or filename.endswith(".tmp.m4b"):
                            try:
                                os.remove(filepath)
                                if getattr(self, 'logger', None):
                                    self.logger(f"Deleted partial/temp file: {filename}")
                                cleaned_count += 1
                            except OSError:
                                pass
                            continue

                        # Clean up 0-byte corrupted audio files
                        if filename.lower().endswith(('.aax', '.aaxc', '.m4b', '.mp3')):
                            try:
                                if os.path.getsize(filepath) == 0:
                                    os.remove(filepath)
                                    if getattr(self, 'logger', None):
                                        self.logger(f"Deleted empty 0-byte file: {filename}")
                                    cleaned_count += 1
                            except OSError:
                                pass
                except OSError:
                    pass

            if cleaned_count > 0 and getattr(self, 'logger', None):
                self.logger(f"Cleanup complete. Removed {cleaned_count} orphaned files.")
                
        except Exception as e:
            if getattr(self, 'logger', None):
                self.logger(f"Failed to run orphaned file cleanup: {e}")
                
    def get_local_ip(self):
        try:
            # We don't actually send any data, just routing the packet reveals the true interface
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80)) 
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"
    def _is_firewall_rule_installed(self, port=8000):
        """Checks if the TomeBox firewall rule already exists."""
        import subprocess
        try:
            # Check for our specific rule name
            cmd = ['netsh', 'advfirewall', 'firewall', 'show', 'rule', 'name=TomeBox Web Server']
            result = subprocess.run(cmd, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
            # If the rule exists, netsh returns info. If not, it returns an error saying no rules match.
            return "No rules match" not in result.stdout
        except Exception:
            return False

    def _add_firewall_rule(self, port=8000):
        """Triggers a UAC prompt to add the firewall rule."""
        import ctypes
        import sys
        
        rule_name = "TomeBox Web Server"
        # The netsh command to open the port
        cmd_args = f'advfirewall firewall add rule name="{rule_name}" dir=in action=allow protocol=TCP localport={port}'
        
        self.logger("Requesting Administrator privileges to add firewall rule...")
        
        try:
            # ShellExecuteW with 'runas' forces the Windows UAC Admin prompt
            result = ctypes.windll.shell32.ShellExecuteW(
                None, 
                "runas", 
                "netsh", 
                cmd_args, 
                None, 
                0 # 0 hides the command prompt window from flashing on screen
            )
            
            # Result > 32 means success in ShellExecute
            if result > 32:
                self.logger("Firewall rule added successfully via UAC.")
                return True
            else:
                self.logger(f"User declined UAC prompt or it failed. Code: {result}")
                return False
        except Exception as e:
            self.logger(f"Failed to trigger UAC for firewall: {e}")
            return False
        

    def toggle_web_server(self, app_instance, on_started_cb, on_stopped_cb, on_error_cb):
        """Starts or stops the FastAPI mobile companion server."""
        if self.web_server is not None:
            self.logger("Stopping companion server...")
            self.web_server.should_exit = True
            self.web_server = None
            if on_stopped_cb:
                on_stopped_cb()
        else:
            import sys
            if sys.platform == 'win32':
                if not self._is_firewall_rule_installed(port=8000):
                    self._add_firewall_rule(port=8000)
            try:
                import uvicorn
                import asyncio
                from server.web_app import create_server_app

                # Pass the TomeBox app instance to the server so it can read library/settings
                api = create_server_app(app_instance)
                config = uvicorn.Config(api, host="0.0.0.0", port=8000, log_config=None)
                self.web_server = uvicorn.Server(config)
                
                threading.Thread(target=self.web_server.run, daemon=True).start()
                
                local_ip = self.get_local_ip()
                self.logger(f"Server started on http://{local_ip}:8000")
                
                if on_started_cb:
                    on_started_cb()
                    
            except ImportError:
                if on_error_cb:
                    on_error_cb("Missing Libraries", "Please install the required server packages first:\n\npip install fastapi uvicorn")
            except Exception as e:
                self.logger(f"Failed to start server: {e}")
                if on_error_cb:
                    on_error_cb("Server Error", f"Could not start the server.\n\n{e}")

    def stop_server_sync(self):
        if self.web_server is not None:
            self.web_server.should_exit = True

    def has_enough_disk_space(self, target_dir, required_bytes):
        import shutil
        import os
        try:
            check_dir = target_dir
            while not os.path.exists(check_dir) and os.path.dirname(check_dir) != check_dir:
                check_dir = os.path.dirname(check_dir)
            total, used, free = shutil.disk_usage(check_dir)
            return free > required_bytes
        except Exception as e:
            self.logger(f"Disk space check failed: {e}")
            return True # Fail open so we don't accidentally block valid operations