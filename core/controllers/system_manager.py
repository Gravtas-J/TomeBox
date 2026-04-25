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

    def cleanup_orphaned_files(self, save_dir):
        """Deletes partial downloads or 0-byte corrupted files on startup."""
        if not save_dir or not os.path.exists(save_dir):
            return

        self.logger("Running startup scan for orphaned/partial files...")
        cleaned_count = 0

        try:
            for filename in os.listdir(save_dir):
                filepath = os.path.join(save_dir, filename)
                if not os.path.isfile(filepath):
                    continue

                if filename.endswith(".part") or "_temp." in filename:
                    try:
                        os.remove(filepath)
                        self.logger(f"Deleted partial file: {filename}")
                        cleaned_count += 1
                    except OSError:
                        pass
                    continue

                if filename.lower().endswith(('.aax', '.aaxc', '.m4b', '.mp3')):
                    try:
                        if os.path.getsize(filepath) == 0:
                            os.remove(filepath)
                            self.logger(f"Deleted empty 0-byte file: {filename}")
                            cleaned_count += 1
                    except OSError:
                        pass

            if cleaned_count > 0:
                self.logger(f"Cleanup complete. Removed {cleaned_count} orphaned files.")
        except Exception as e:
            self.logger(f"Failed to run orphaned file cleanup: {e}")

    def get_local_ip(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    def toggle_web_server(self, app_instance, on_started_cb, on_stopped_cb, on_error_cb):
        """Starts or stops the FastAPI mobile companion server."""
        if self.web_server is not None:
            self.logger("Stopping companion server...")
            self.web_server.should_exit = True
            self.web_server = None
            if on_stopped_cb:
                on_stopped_cb()
        else:
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