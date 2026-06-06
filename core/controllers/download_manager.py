import os
import threading
import time

from core.downloader import AudiobookDownloader, DownloadCanceledError
from core.events import default_bus
from core.utils.fs import safe_unlink
from core.utils.process_runner import ProcessRunner
from core.utils.wake import keep


class DownloadManager:
    def __init__(
        self,
        api_client,
        logger,
        library_manager,
        callbacks,
        thread_pool,
        start_workers=True,
        event_bus=None,
    ):
        self.thread_pool = thread_pool
        self.logger = logger
        self.library_manager = library_manager
        self.downloader = AudiobookDownloader(api_client, logger)

        self.event_bus = event_bus or default_bus

        self.queue = []
        self.active_flags = {}  # Tracks {asin: cancel_boolean}

        # --- NEW: Pause State Tracking ---
        self.is_paused = False
        self.paused_items = set()

        self.is_processing = False
        self.queue_lock = threading.RLock()
        self.web_state = {"active_asin": None, "progress": 0, "status": "Idle"}

        self.start_workers = start_workers

        callbacks = callbacks or {}
        if callbacks.get("on_status"):
            self.event_bus.subscribe(
                "download.status",
                lambda **kw: callbacks["on_status"](
                    kw.get("asin"),
                    kw.get("status"),
                    is_global=kw.get("is_global", False),
                ),
            )
        if callbacks.get("on_progress"):
            self.event_bus.subscribe(
                "download.progress",
                lambda **kw: callbacks["on_progress"](
                    kw.get("asin"),
                    kw.get("percent"),
                    is_global=kw.get("is_global", False),
                ),
            )
        if callbacks.get("on_complete"):
            self.event_bus.subscribe(
                "download.complete",
                lambda **kw: callbacks["on_complete"](
                    kw.get("filepath"), kw.get("title"), kw.get("post_action")
                ),
            )
        if callbacks.get("on_batch_finish"):
            self.event_bus.subscribe(
                "download.batch_finish", lambda **kw: callbacks["on_batch_finish"]()
            )

    # --- NEW: Pause Controls ---
    def pause_all(self):
        with self.queue_lock:
            self.is_paused = True
            self.event_bus.publish(
                "download.status", asin="global", status="Queue Paused", is_global=True
            )

    def resume_all(self):
        with self.queue_lock:
            self.is_paused = False
            self.event_bus.publish(
                "download.status", asin="global", status="Queue Active", is_global=True
            )
            if self.queue and not self.is_processing and self.start_workers:
                self.is_processing = True
                self.thread_pool.submit(self._process_queue_worker)

    def pause_download(self, asin):
        with self.queue_lock:
            if any(t["asin"] == asin for t in self.queue):
                self.paused_items.add(asin)
                self.event_bus.publish(
                    "download.status", asin=asin, status="Paused", is_global=False
                )

    def resume_download(self, asin):
        with self.queue_lock:
            if asin in self.paused_items:
                self.paused_items.remove(asin)
                self.event_bus.publish(
                    "download.status", asin=asin, status="Queued", is_global=False
                )

                # Kickstart worker if it went to sleep because everything was paused
                if not self.is_processing and self.start_workers:
                    self.is_processing = True
                    self.thread_pool.submit(self._process_queue_worker)

    def queue_download(self, asin, title, save_dir, post_action=None):
        with self.queue_lock:
            if asin in self.active_flags and not self.active_flags[asin]:
                return

            self.queue.append(
                {
                    "asin": asin,
                    "title": title,
                    "save_dir": save_dir,
                    "post_action": post_action,
                }
            )
            self.active_flags[asin] = False

            if not self.is_processing and self.start_workers:
                self.is_processing = True
                self.thread_pool.submit(self._process_queue_worker)

    def queue_batch(self, items, save_dir):
        with self.queue_lock:
            for item in items:
                asin = item.get("asin")
                title = item.get("title", "Unknown")

                if asin in self.active_flags and not self.active_flags[asin]:
                    continue

                self.queue.append(
                    {
                        "asin": asin,
                        "title": title,
                        "save_dir": save_dir,
                        "post_action": None,
                    }
                )
                self.active_flags[asin] = False

            if not self.is_processing and self.start_workers:
                self.is_processing = True
                self.thread_pool.submit(self._process_queue_worker)

    def cancel_download(self, asin):
        with self.queue_lock:
            if asin in self.active_flags:
                self.active_flags[asin] = True
                self.paused_items.discard(asin)  # Clean up pause state if cancelled
                self.event_bus.publish(
                    "download.status", asin=asin, status="Canceling...", is_global=False
                )

    def cancel_all(self):
        with self.queue_lock:
            for asin in self.active_flags:
                if not self.active_flags[asin]:
                    self.active_flags[asin] = True
                    self.event_bus.publish(
                        "download.status",
                        asin=asin,
                        status="Canceling...",
                        is_global=False,
                    )
            self.queue.clear()
            self.paused_items.clear()

    def _process_queue_worker(self):
        """The main background loop that processes downloads one by one."""
        try:
            with keep.running():
                while True:
                    # 1. Exit cleanly if queue is empty
                    with self.queue_lock:
                        if not self.queue:
                            self.is_processing = False
                            break

                    # 2. Check Global Pause
                    if self.is_paused:
                        time.sleep(0.5)
                        continue

                    # 3. Find the next unpaused task
                    with self.queue_lock:
                        task_index = -1
                        for i, t in enumerate(self.queue):
                            if t["asin"] not in self.paused_items:
                                task_index = i
                                break

                        if task_index == -1:
                            # All items remaining in the queue are individually paused
                            all_paused = True
                        else:
                            task = self.queue.pop(task_index)
                            all_paused = False

                    # 4. Spin if everything is paused
                    if all_paused:
                        time.sleep(0.5)
                        continue

                    # --- Execution Phase ---
                    asin = task["asin"]
                    title = task["title"]
                    save_dir = task["save_dir"]
                    post_action = task["post_action"]

                    if self.active_flags.get(asin, False):
                        self.event_bus.publish(
                            "download.status",
                            asin=asin,
                            status="Canceled",
                            is_global=False,
                        )
                        continue

                    self.web_state["active_asin"] = asin
                    self.web_state["progress"] = 0
                    self.web_state["status"] = "Downloading..."

                    self.event_bus.publish(
                        "download.status",
                        asin=asin,
                        status=f"Downloading: {title}",
                        is_global=True,
                    )
                    self.event_bus.publish(
                        "download.status",
                        asin=asin,
                        status="Starting...",
                        is_global=False,
                    )
                    self.event_bus.publish(
                        "download.progress", asin=asin, percent=0, is_global=True
                    )

                    try:
                        self._execute_download(asin, title, save_dir, post_action)
                    except DownloadCanceledError:
                        self.event_bus.publish(
                            "download.status",
                            asin=asin,
                            status="Canceled",
                            is_global=False,
                        )
                        self.logger(f"Download for {asin} gracefully canceled.")
                    except Exception as e:
                        self.logger(f"Download failed for {title}: {e}")
                        self.event_bus.publish(
                            "download.status",
                            asin=asin,
                            status="Failed",
                            is_global=False,
                        )
        finally:
            self.is_processing = False
            self.web_state = {"active_asin": None, "progress": 0, "status": "Idle"}
            self.event_bus.publish("download.batch_finish")

    def _execute_download(self, asin, title, save_dir, post_action):
        def progress_cb(percent_float):
            self.web_state["progress"] = percent_float
            self.event_bus.publish(
                "download.progress", asin=asin, percent=percent_float, is_global=True
            )

        def check_cancel_cb():
            return self.active_flags.get(asin, False)

        filepath, a_key, a_iv, ext = self.downloader.download_item(
            asin=asin,
            title=title,
            save_dir=save_dir,
            progress_callback=progress_cb,
            check_cancel_callback=check_cancel_cb,
        )

        if self.active_flags.get(asin, False):
            safe_unlink(filepath, self.logger)
            return

        final_filepath = filepath
        final_ext = ext

        if ext.lower() in [".aaxc", ".aax"]:
            self.web_state["status"] = "Decrypting to M4B..."
            self.event_bus.publish(
                "download.status",
                asin=asin,
                status="Decrypting to M4B...",
                is_global=False,
            )

            m4b_filepath = os.path.splitext(filepath)[0] + ".m4b"

            cmd = ["ffmpeg", "-y"]
            if a_key and a_iv:
                cmd.extend(["-audible_key", a_key, "-audible_iv", a_iv])
            else:
                act_bytes = self.downloader.api.get_activation_bytes()
                if act_bytes:
                    cmd.extend(["-activation_bytes", act_bytes])

            cmd.extend(["-i", filepath, "-c", "copy", m4b_filepath])

            try:
                result = ProcessRunner.run_blocking(cmd, capture_output=True)

                if self.active_flags.get(asin, False):
                    safe_unlink(m4b_filepath, self.logger)
                    safe_unlink(filepath, self.logger)
                    return

                if (
                    result.returncode == 0
                    and os.path.exists(m4b_filepath)
                    and os.path.getsize(m4b_filepath) > 0
                ):
                    safe_unlink(filepath, self.logger)
                    final_filepath = m4b_filepath
                    final_ext = ".m4b"
                else:
                    self.logger(f"Auto-conversion failed for {title}: {result.stderr}")
                    safe_unlink(m4b_filepath, self.logger)
            except Exception as e:
                self.logger(f"Auto-conversion exception for {title}: {e}")
                safe_unlink(m4b_filepath, self.logger)

        self.event_bus.publish(
            "download.status", asin=asin, status="Complete", is_global=False
        )

        self.library_manager.local_library[final_filepath] = {
            "title": title,
            "format": final_ext.replace(".", "").upper(),
            "path": final_filepath,
            "audible_key": a_key,
            "audible_iv": a_iv,
            "asin": asin,
        }
        self.library_manager.db.save_local_db(self.library_manager.local_library)

        self.event_bus.publish(
            "download.complete",
            filepath=final_filepath,
            title=title,
            post_action=post_action,
        )
