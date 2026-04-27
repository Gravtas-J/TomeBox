import os
import threading
import traceback
from core.downloader import AudiobookDownloader
try:
    from wakepy import keep
except ImportError:
    # Safe fallback if wakepy isn't installed
    class KeepDummy:
        def running(self):
            class ContextDummy:
                def __enter__(self): pass
                def __exit__(self, *args): pass
            return ContextDummy()
    keep = KeepDummy()

class DownloadManager:
    def __init__(self, api_client, logger, library_manager, callbacks, thread_pool):
        self.thread_pool = thread_pool
        self.logger = logger
        self.library_manager = library_manager
        self.downloader = AudiobookDownloader(api_client, logger)
        
        # Callbacks to tell the UI what is happening
        self.on_status_change = callbacks.get("on_status")
        self.on_progress = callbacks.get("on_progress")
        self.on_complete = callbacks.get("on_complete")
        self.on_batch_finish = callbacks.get("on_batch_finish")
        
        self.queue = []
        self.active_flags = {}  # Tracks {asin: cancel_boolean}
        self.is_processing = False
        self.queue_lock = threading.Lock()
        self.web_state = {"active_asin": None, "progress": 0, "status": "Idle"}

    def queue_download(self, asin, title, save_dir, post_action=None):
        """Adds a single item to the queue and starts the worker if idle."""
        with self.queue_lock:
            # Prevent duplicate queuing
            if asin in self.active_flags and not self.active_flags[asin]:
                return
                
            self.queue.append({
                "asin": asin,
                "title": title,
                "save_dir": save_dir,
                "post_action": post_action
            })
            self.active_flags[asin] = False

            if not self.is_processing:
                self.is_processing = True
                self.thread_pool.submit(self._process_queue_worker)

    def queue_batch(self, items, save_dir):
        """Adds multiple items to the queue at once."""
        with self.queue_lock:
            for item in items:
                asin = item.get("asin")
                title = item.get("title", "Unknown")
                
                if asin in self.active_flags and not self.active_flags[asin]:
                    continue
                    
                self.queue.append({
                    "asin": asin,
                    "title": title,
                    "save_dir": save_dir,
                    "post_action": None
                })
                self.active_flags[asin] = False
                
            if not self.is_processing:
                self.is_processing = True
                self.thread_pool.submit(self._process_queue_worker)

    def cancel_download(self, asin):
        """Flags an active or pending download for cancellation."""
        if asin in self.active_flags:
            self.active_flags[asin] = True
            if self.on_status_change:
                self.on_status_change(asin, "Canceling...", is_global=False)

    def cancel_all(self):
        """Flags all downloads in the queue for cancellation."""
        with self.queue_lock:
            for asin in self.active_flags:
                if not self.active_flags[asin]:
                    self.active_flags[asin] = True
                    if self.on_status_change:
                        self.on_status_change(asin, "Canceling...", is_global=False)
            self.queue.clear()

    def _process_queue_worker(self):
        """The main background loop that processes downloads one by one."""
        try:
            with keep.running():
                while True:
                    with self.queue_lock:
                        if not self.queue:
                            self.is_processing = False
                            break
                        task = self.queue.pop(0)

                    asin = task["asin"]
                    title = task["title"]
                    save_dir = task["save_dir"]
                    post_action = task["post_action"]

                    if self.active_flags.get(asin, False):
                        if self.on_status_change:
                            self.on_status_change(asin, "Canceled", is_global=False)
                        continue

                    self.web_state["active_asin"] = asin
                    self.web_state["progress"] = 0
                    self.web_state["status"] = "Downloading..."

                    # Update UI to show starting
                    if self.on_status_change:
                        self.on_status_change(asin, f"Downloading: {title}", is_global=True)
                        self.on_status_change(asin, "Starting...", is_global=False)
                    if self.on_progress:
                        self.on_progress(asin, 0, is_global=True)

                    try:
                        self._execute_download(asin, title, save_dir, post_action)
                    except Exception as e:
                        self.logger(f"Download failed for {title}: {e}")
                        if self.on_status_change:
                            self.on_status_change(asin, "Failed", is_global=False)

        finally:
            self.is_processing = False
            self.web_state = {"active_asin": None, "progress": 0, "status": "Idle"}
            
            if self.on_batch_finish:
                self.on_batch_finish()

    def _execute_download(self, asin, title, save_dir, post_action):
        # Bind the UI callbacks dynamically to the downloader
        def progress_cb(percent_float):
            self.web_state["progress"] = percent_float # <-- NEW
            if self.on_progress:
                self.on_progress(asin, percent_float, is_global=True)

        def check_cancel_cb():
            return self.active_flags.get(asin, False)

        filepath, a_key, a_iv, ext = self.downloader.download_item(
            asin=asin, 
            title=title, 
            save_dir=save_dir, 
            progress_callback=progress_cb, 
            check_cancel_callback=check_cancel_cb
        )

        # Check for cancellation before doing any post-processing
        if self.active_flags.get(asin, False):
            return

        final_filepath = filepath
        final_ext = ext

        if ext.lower() in [".aaxc", ".aax"]:
            self.web_state["status"] = "Decrypting to M4B..."
            if self.on_status_change:
                self.on_status_change(asin, "Decrypting to M4B...", is_global=False)
            
            import subprocess
            m4b_filepath = os.path.splitext(filepath)[0] + ".m4b"
            
            # Construct FFmpeg command to copy streams (instant, no re-encoding)
            cmd = ["ffmpeg", "-y"]
            if a_key and a_iv:
                cmd.extend(["-audible_key", a_key, "-audible_iv", a_iv])
            elif a_key: 
                cmd.extend(["-activation_bytes", a_key])
                
            cmd.extend(["-i", filepath, "-c", "copy", m4b_filepath])
            
            try:
                # Hide the terminal window on Windows
                creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
                result = subprocess.run(cmd, capture_output=True, text=True, creationflags=creationflags)
                
                # Verify the conversion worked and the new file actually has data
                if result.returncode == 0 and os.path.exists(m4b_filepath) and os.path.getsize(m4b_filepath) > 0:
                    try:
                        # Success! Clean up the original encrypted .aaxc file
                        os.remove(filepath)
                    except OSError:
                        pass
                    final_filepath = m4b_filepath
                    final_ext = ".m4b"
                else:
                    self.logger(f"Auto-conversion failed for {title}: {result.stderr}")
            except Exception as e:
                self.logger(f"Auto-conversion exception for {title}: {e}")

        if self.on_status_change:
            self.on_status_change(asin, "Complete", is_global=False)
            
        # Update Library Data directly in the controller
        self.library_manager.local_library[final_filepath] = {
            "title": title, 
            "format": final_ext.replace(".", "").upper(), 
            "path": final_filepath,
            "audible_key": a_key,
            "audible_iv": a_iv,
            "asin": asin  
        }
        self.library_manager.db.save_local_db(self.library_manager.local_library)

        # Tell the UI it finished so it can handle redrawing
        if self.on_complete:
            self.on_complete(final_filepath, title, post_action)