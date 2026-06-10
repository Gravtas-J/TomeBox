import os
import socket
import sys
import threading
import uvicorn

from core.events import default_bus


class SystemManager:
    def __init__(self, logger, event_bus=None):
        self.logger = logger
        self.event_bus = event_bus or default_bus
        self.web_server = None
        self.lock_socket = None
        self.lock_port = 43128
        self.import_lock = threading.Lock()

        if sys.platform == "win32":
            import asyncio

            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    def enforce_single_instance(self, on_wake_callback):
        """Ensures only one instance of TomeBox is running via file lock."""
        import os
        import socket
        import sys
        import tempfile
        import threading

        try:
            import portalocker

            lock_path = os.path.join(tempfile.gettempdir(), "tomebox_instance.lock")
            self.lock_file = open(lock_path, "w")
            # If the file is locked by another instance, this will instantly raise an exception
            portalocker.lock(self.lock_file, portalocker.LOCK_EX | portalocker.LOCK_NB)
        except ImportError:
            self.logger(
                "WARNING: portalocker not installed. Falling back to port-based locking."
            )
            self.lock_file = None
        except Exception:
            # The file is locked: We are instance #2
            self.logger("Another instance detected. Sending wake signal...")
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.connect(("127.0.0.1", self.lock_port))
                s.sendall(b"WAKEUP")
                s.close()
            except Exception:
                pass
            sys.exit(0)

        # We are instance #1. Bind the port to receive WAKEUP signals from future instances.
        # SO_REUSEADDR ensures we can bind immediately even if the port was recently closed.
        self.lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.lock_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        try:
            self.lock_socket.bind(("127.0.0.1", self.lock_port))
            self.lock_socket.listen(1)
            threading.Thread(
                target=self._instance_listener_worker,
                args=(on_wake_callback,),
                daemon=True,
            ).start()
        except socket.error as e:
            if not getattr(self, "lock_file", None):
                # Fallback: If portalocker isn't installed and the port bind failed, assume duplicate
                self.logger(
                    "Another instance detected via socket bind. Sending wake signal..."
                )
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s.connect(("127.0.0.1", self.lock_port))
                    s.sendall(b"WAKEUP")
                    s.close()
                except Exception:
                    pass
                sys.exit(0)
            else:
                self.logger(f"Could not bind wake listener port: {e}")

    def _instance_listener_worker(self, on_wake_callback):
        import time

        while True:
            try:
                conn, addr = self.lock_socket.accept()
                data = conn.recv(1024)
                if data == b"WAKEUP":
                    self.logger("Wake signal received. Bringing window to front.")
                    self.event_bus.publish("system.wake_requested", args="WAKEUP")
                    if on_wake_callback:
                        on_wake_callback()
                conn.close()
            except Exception as e:
                self.logger(f"Socket listener error: {e}")
                time.sleep(1)
                continue

    def open_file_location(self, filepath):
        """Opens the native OS file explorer and highlights the target file."""
        import os
        import platform
        import subprocess

        if not filepath or not os.path.exists(filepath):
            return

        filepath = os.path.normpath(filepath)

        try:
            if platform.system() == "Windows":
                subprocess.Popen(["explorer", "/select,", filepath])
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", "-R", filepath])
            else:
                subprocess.Popen(["xdg-open", os.path.dirname(filepath)])
        except Exception as e:
            if hasattr(self, "logger") and self.logger:
                self.logger(f"Failed to open file location: {e}")

    def toggle_system_sleep(self, prevent_sleep=True):
        """Prevents Windows and the display from sleeping during active background tasks."""
        if os.name != "nt":
            return
        try:
            import ctypes

            # Windows API Constants
            ES_CONTINUOUS = 0x80000000
            ES_SYSTEM_REQUIRED = 0x00000001
            ES_DISPLAY_REQUIRED = 0x00000002

            if prevent_sleep:
                self.logger(
                    "Applying system and display sleep prevention for active task."
                )
                ctypes.windll.kernel32.SetThreadExecutionState(
                    ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
                )
            else:
                self.logger("Releasing system and display sleep prevention.")
                ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)

        except Exception as e:
            self.logger(f"Failed to toggle sleep state: {e}")

    def cleanup_orphaned_files(self, download_dir, library_paths=None):
        """Deletes partial downloads, cancelled conversions, or 0-byte corrupted files on startup."""
        import os

        directories_to_scan = set()

        if download_dir and os.path.exists(download_dir):
            directories_to_scan.add(download_dir)

        if library_paths:
            for path in library_paths:
                parent = os.path.dirname(path)
                if os.path.exists(parent):
                    directories_to_scan.add(parent)

        if not directories_to_scan:
            return

        logger = getattr(self, "logger", None)
        if logger:
            logger("Running startup scan for orphaned/partial files...")

        cleaned_count = 0

        temp_suffixes = (
            ".part",
            ".tmp.m4b",
            "_temp.m4b",
            "_temp.mp3",
            "_temp.aax",
            "_temp.aaxc",
        )
        audio_exts = (".aax", ".aaxc", ".m4b", ".mp3")

        try:
            for directory in directories_to_scan:
                try:
                    for filename in os.listdir(directory):
                        filepath = os.path.join(directory, filename)
                        if not os.path.isfile(filepath):
                            continue

                        if filename.endswith(temp_suffixes) or "_temp." in filename:
                            try:
                                os.remove(filepath)
                                if logger:
                                    logger(f"Deleted partial/temp file: {filename}")
                                cleaned_count += 1
                            except OSError:
                                pass
                            continue

                        if filename.lower().endswith(audio_exts):
                            try:
                                if os.path.getsize(filepath) == 0:
                                    os.remove(filepath)
                                    if logger:
                                        logger(f"Deleted empty 0-byte file: {filename}")
                                    cleaned_count += 1
                            except OSError:
                                pass
                except OSError:
                    pass

            if cleaned_count > 0 and logger:
                logger(f"Cleanup complete. Removed {cleaned_count} orphaned files.")

        except Exception as e:
            if logger:
                logger(f"Failed to run orphaned file cleanup: {e}")

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
            cmd = [
                "netsh",
                "advfirewall",
                "firewall",
                "show",
                "rule",
                "name=TomeBox Web Server",
            ]
            kwargs = {"capture_output": True, "text": True}
            if hasattr(subprocess, "CREATE_NO_WINDOW"):
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            result = subprocess.run(cmd, **kwargs)
            return "No rules match" not in result.stdout
        except Exception:
            return False

    def _add_firewall_rule(self, port=8000):
        """Triggers a UAC prompt to add the firewall rule."""
        import ctypes

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
                0,  # 0 hides the command prompt window from flashing on screen
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

    def remove_firewall_rule(self):
        """Triggers a UAC prompt to remove the TomeBox firewall rule."""
        import ctypes

        rule_name = "TomeBox Web Server"

        # Check if it actually exists first so we don't annoy them with a useless UAC prompt
        if not self._is_firewall_rule_installed():
            self.logger("Firewall rule not found. Nothing to remove.")
            return True

        cmd_args = f'advfirewall firewall delete rule name="{rule_name}"'

        self.logger("Requesting Administrator privileges to remove firewall rule...")

        try:
            result = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", "netsh", cmd_args, None, 0
            )

            if result > 32:
                self.logger("Firewall rule removed successfully via UAC.")
                return True
            else:
                self.logger(f"User declined UAC prompt or it failed. Code: {result}")
                return False
        except Exception as e:
            self.logger(f"Failed to trigger UAC for firewall removal: {e}")
            return False

    def toggle_web_server(
        self, app_instance, on_started_cb, on_stopped_cb, on_error_cb
    ):
        """Starts or stops the FastAPI mobile companion server."""
        if self.web_server is not None:
            self.logger("Stopping companion server...")
            self.web_server.should_exit = True
            self.web_server = None
            if on_stopped_cb:
                on_stopped_cb()
        else:
            import sys

            if sys.platform == "win32":
                if not self._is_firewall_rule_installed(port=8000):
                    self._add_firewall_rule(port=8000)
            try:
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
                    on_error_cb(
                        "Missing Libraries",
                        "Please install the required server packages first:\n\npip install fastapi uvicorn",
                    )
            except Exception as e:
                self.logger(f"Failed to start server: {e}")
                if on_error_cb:
                    on_error_cb("Server Error", f"Could not start the server.\n\n{e}")

    def stop_server_sync(self):
        if self.web_server is not None:
            self.web_server.should_exit = True

    def has_enough_disk_space(self, target_dir, required_bytes):
        import os
        import shutil

        try:
            check_dir = target_dir
            while (
                not os.path.exists(check_dir)
                and os.path.dirname(check_dir) != check_dir
            ):
                check_dir = os.path.dirname(check_dir)
            total, used, free = shutil.disk_usage(check_dir)
            return free > required_bytes
        except Exception as e:
            self.logger(f"Disk space check failed: {e}")
            return True  # Fail open so we don't accidentally block valid operations

    def get_pending_imports_file(self, data_dir):
        import os

        return os.path.join(data_dir, "pending_imports.json")

    def add_pending_import(self, data_dir, path, is_folder):
        import json
        import os

        with self.import_lock:
            file_path = self.get_pending_imports_file(data_dir)
            imports = []
            if os.path.exists(file_path):
                try:
                    with open(file_path, "r") as f:
                        imports = json.load(f)
                except Exception:
                    pass

            entry = {"path": path, "is_folder": is_folder}
            if entry not in imports:
                imports.append(entry)
                try:
                    with open(file_path, "w") as f:
                        json.dump(imports, f)
                    self.event_bus.publish(
                        "system.pending_imports_changed", action="added", path=path
                    )
                except Exception:
                    pass

    def remove_pending_import(self, data_dir, path):
        import json
        import os

        with self.import_lock:
            file_path = self.get_pending_imports_file(data_dir)
            if not os.path.exists(file_path):
                return
            try:
                with open(file_path, "r") as f:
                    imports = json.load(f)
                imports = [i for i in imports if i["path"] != path]
                with open(file_path, "w") as f:
                    json.dump(imports, f)
                self.event_bus.publish(
                    "system.pending_imports_changed", action="removed", path=path
                )
            except Exception:
                pass

    def load_pending_imports(self, data_dir):
        import json
        import os

        file_path = self.get_pending_imports_file(data_dir)
        if os.path.exists(file_path):
            try:
                with open(file_path, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return []

    def clear_all_pending_imports(self, data_dir):
        import os

        file_path = self.get_pending_imports_file(data_dir)
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except OSError:
                pass
