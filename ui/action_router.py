import os
import tkinter as tk
from tkinter import messagebox

from PIL import Image, ImageTk

from core.events import default_bus
from ui.components.dialogs import open_cover_modal


class ActionRouter:
    def __init__(self, app, event_bus=None):
        self.app = app
        self.event_bus = event_bus or default_bus
        self._subscribe_events()

    def _subscribe_events(self):
        # --- Download Events ---
        self.event_bus.subscribe(
            "download.status",
            lambda **kw: self.on_dl_status(
                kw.get("asin"), kw.get("status"), kw.get("is_global", False)
            ),
        )
        self.event_bus.subscribe(
            "download.progress",
            lambda **kw: self.on_dl_progress(
                kw.get("asin"), kw.get("percent"), kw.get("is_global", False)
            ),
        )
        self.event_bus.subscribe(
            "download.complete",
            lambda **kw: self.on_dl_complete(
                kw.get("filepath"), kw.get("title"), kw.get("post_action")
            ),
        )
        self.event_bus.subscribe(
            "download.batch_finish", lambda **kw: self.on_dl_batch_finish()
        )

        # --- Metadata Events ---
        self.event_bus.subscribe(
            "metadata.search_complete",
            lambda **kw: self.on_scrape_search_results(
                kw.get("filepath"), kw.get("products")
            ),
        )
        self.event_bus.subscribe(
            "metadata.apply_complete",
            lambda **kw: self.on_scrape_apply_complete(
                kw.get("filepath"), kw.get("title")
            ),
        )
        self.event_bus.subscribe(
            "metadata.display_ready",
            lambda **kw: self.on_display_metadata_ready(
                kw.get("filepath"),
                kw.get("cover_path"),
                kw.get("authors"),
                kw.get("msg"),
            ),
        )
        self.event_bus.subscribe(
            "metadata.error", lambda **kw: self.on_scrape_error(kw.get("error_msg"))
        )

        # --- Conversion Events ---
        self.event_bus.subscribe(
            "conversion.status",
            lambda **kw: self.app.root.after(
                0, self.update_global_status, kw.get("status")
            ),
        )
        self.event_bus.subscribe(
            "conversion.progress",
            lambda **kw: self.app.root.after(
                0, self.update_global_progress, kw.get("percent")
            ),
        )
        self.event_bus.subscribe(
            "conversion.complete",
            lambda **kw: self.app.root.after(
                0, lambda: messagebox.showinfo("Conversion Success", kw.get("message"))
            ),
        )
        self.event_bus.subscribe(
            "conversion.error",
            lambda **kw: self.on_task_error(
                kw.get("filepath"), kw.get("action"), kw.get("error_msg")
            ),
        )
        self.event_bus.subscribe(
            "conversion.refresh_required",
            lambda **kw: self.app.root.after(
                0, self.app.library_presenter.refresh_library_ui
            ),
        )

        # --- Library Events ---
        self.event_bus.subscribe(
            "library.queue.empty", lambda **kw: self.on_import_queue_empty()
        )
        self.event_bus.subscribe(
            "library.file_removed",
            lambda **kw: self.on_library_file_removed(kw.get("filepath")),
        )

        # --- UI Dialog Events (Safe Threading) ---
        self.event_bus.subscribe(
            "ui.show_error",
            lambda **kw: self.app.root.after(
                0,
                lambda: messagebox.showerror(
                    kw.get("title", "Error"), kw.get("message", "An error occurred.")
                ),
            ),
        )
        self.event_bus.subscribe(
            "ui.show_info",
            lambda **kw: self.app.root.after(
                0,
                lambda: messagebox.showinfo(
                    kw.get("title", "Information"), kw.get("message", "")
                ),
            ),
        )
        self.event_bus.subscribe(
            "ui.show_warning",
            lambda **kw: self.app.root.after(
                0,
                lambda: messagebox.showwarning(
                    kw.get("title", "Warning"), kw.get("message", "")
                ),
            ),
        )

    # --- Global UI Updaters ---
    def reset_ui_if_idle(self):
        is_importing = (
            getattr(self.app.library_manager, "_is_importing", False)
            or len(self.app.library_manager.import_queue) > 0
        )
        is_downloading = getattr(self.app.download_manager, "is_processing", False)
        is_converting = getattr(self.app.converter, "current_process", None) is not None

        if not is_importing and not is_downloading and not is_converting:
            self.app.ui_state.dl_status.set("Idle")
            self.app.ui_state.dl_progress.set(0)

    def update_global_status(self, msg):
        if msg in ["Idle", ""]:
            self.reset_ui_if_idle()
        else:
            self.app.ui_state.dl_status.set(msg)

    def update_global_progress(self, pct):
        if pct == 0:
            self.reset_ui_if_idle()
        else:
            self.app.ui_state.dl_progress.set(pct)

    # --- Library Management ---
    def on_library_file_removed(self, filepath):
        """If the deleted file is the one currently loaded, tear down player state."""
        if filepath and filepath == self.app.file_path:
            self.app.root.after(0, self.app.playback_presenter.unload_current_file)

    # --- Queue Drawer Management ---
    def remove_queue_ui_row(self, task_id):
        if task_id in self.app.queue_ui_elements:
            self.app.queue_ui_elements[task_id]["frame"].destroy()
            del self.app.queue_ui_elements[task_id]

        if not self.app.queue_ui_elements:
            self.app.import_session.toggle_queue_drawer(False)

    def _schedule_row_removal(self, task_id):
        self.app.root.after(3000, lambda: self.remove_queue_ui_row(task_id))

    # --- Download Callbacks ---
    def on_dl_status(self, asin, status_text, is_global=False):
        def update():
            if is_global:
                self.update_global_status(status_text)
            elif asin in self.app.queue_ui_elements:
                self.app.queue_ui_elements[asin]["status_var"].set(status_text)

        self.app.root.after(0, update)

    def on_dl_progress(self, asin, percent, is_global=False):
        def update():
            if is_global:
                self.update_global_progress(percent)
            if asin in self.app.queue_ui_elements:
                self.app.queue_ui_elements[asin]["prog_var"].set(percent)
                self.app.queue_ui_elements[asin]["status_var"].set(f"{int(percent)}%")

        self.app.root.after(0, update)

    def on_dl_complete(self, filepath, title, post_action):
        def update():
            self.app.stats_manager.add_stat("books_downloaded", 1)
            self.app.library_presenter.refresh_library_ui()

            if post_action in ["play", "convert"]:
                self.app.playback_presenter.load_specific_file(filepath)
                if post_action == "play":
                    self.app.root.after(500, self.app.playback_presenter.master_play)
                elif post_action == "convert":
                    self.app.root.after(500, self.app.start_convert_thread)

        self.app.root.after(0, update)

    def on_dl_batch_finish(self):
        def update():
            self.update_global_status("All downloads completed.")
            if hasattr(self.app, "dl_all_btn"):
                self.app.dl_all_btn.config(state=tk.NORMAL)
            self.app.root.after(3000, self.reset_ui_if_idle)

            for task_id in list(self.app.queue_ui_elements.keys()):
                if not str(task_id).startswith("import_"):
                    self.remove_queue_ui_row(task_id)

        self.app.root.after(0, update)

    # --- Import Callbacks ---
    def on_import_status(self, task_id, msg):
        self.on_dl_status(task_id, msg, is_global=False)
        self.app.root.after(0, lambda: self.update_global_status(msg))

    def on_import_progress(self, task_id, pct):
        self.on_dl_progress(task_id, pct, is_global=False)
        self.app.root.after(0, lambda: self.update_global_progress(pct))

    def on_import_finished(self, path, added_count, total_found=0, task_id=None):
        self.app.system_manager.remove_pending_import(self.app.db.data_dir, path)
        self.on_import_complete(added_count, total_found)
        if task_id:
            if task_id in getattr(self.app.library_manager, "canceled_tasks", set()):
                status = "Canceled"
            else:
                status = "Complete" if added_count > 0 else "Finished"
            self.on_dl_status(task_id, status, is_global=False)
            self._schedule_row_removal(task_id)

    def on_import_queue_empty(self):
        def update():
            self.update_global_status("All queued imports completed.")
            self.app.root.after(500, self.app.root.bell)
            self.app.root.after(3000, self.reset_ui_if_idle)

        self.app.root.after(0, update)

    def on_import_complete(self, added_count, total_found=0):
        def update():
            try:
                self.app.library_presenter.refresh_library_ui()
            except Exception:
                import traceback

                traceback.print_exc()
            if added_count > 0:
                self.app.ui_state.dl_status.set(
                    f"Successfully imported {added_count} files."
                )
            elif total_found > 0:
                self.app.ui_state.dl_status.set("Files already in library.")
            else:
                self.app.ui_state.dl_status.set("No valid audiobooks found to import.")

        self.app.root.after(0, update)

    def on_book_start(self, sub_task_id, title):
        self.app.root.after(
            0, lambda: self.app.import_session.add_queue_ui_row(sub_task_id, title)
        )

    def on_book_progress(self, sub_task_id, pct):
        self.on_dl_progress(sub_task_id, pct, is_global=False)

    def on_book_complete(self, sub_task_id, success):
        status = "Complete" if success else "Failed"
        self.on_dl_status(sub_task_id, status, is_global=False)
        self._schedule_row_removal(sub_task_id)
        if success:
            self.app.root.after(0, self.app.library_presenter.refresh_library_ui)

    # --- Error Callbacks ---
    def on_task_error(self, filepath, action_type, error_msg):
        def update():
            self.app.failed_tasks.append(
                {"path": filepath, "action": action_type, "error": error_msg}
            )
            self.app.ui_state.error_btn.set(f"Errors ({len(self.app.failed_tasks)})")
            self.app.error_btn.config(state=tk.NORMAL)
            self.app.ui_state.dl_status.set(
                f"Task failed: {os.path.basename(filepath)}"
            )
            self.app.root.after(4000, self.reset_ui_if_idle)

        self.app.root.after(0, update)

    # --- Metadata Callbacks ---
    def on_scrape_search_results(self, filepath, products):
        self.app.root.after(0, self.reset_ui_if_idle)

    def on_scrape_apply_complete(self, filepath, title, is_manual=False):
        def update():
            self.reset_ui_if_idle()
            self.app.library_presenter.cover_cache.clear()
            self.app.library_presenter.refresh_library_ui()
            self.app.metadata_manager.fetch_display_metadata(filepath)
            if self.app.file_path == filepath:
                self.app.playback_presenter.load_specific_file(filepath)

        self.app.root.after(0, update)

        if not is_manual:
            self.event_bus.publish(
                "ui.show_info", title="Success", message=f"Metadata applied to {title}"
            )

    def on_display_metadata_ready(self, filepath, cover_path, authors, error_text):
        def update():
            if getattr(self.app, "_selected_local_path", None) != filepath:
                return
            self.app.author_label.config(text=authors)

            local_data = self.app.library_manager.local_library.get(filepath, {})
            asin = local_data.get("asin", "Unknown")
            title = local_data.get("title", "Unknown")

            if cover_path and os.path.exists(cover_path):
                try:
                    img = Image.open(cover_path)
                    img.thumbnail((400, 400))
                    photo = ImageTk.PhotoImage(img)
                    self.app.current_cover_photo = photo
                    self.app.cover_label.config(image=photo, text="")
                    self.app.cover_label.bind(
                        "<Button-1>",
                        lambda e, a=asin, t=title, p=cover_path: open_cover_modal(
                            self.app, a, t, explicit_path=p
                        ),
                    )
                except Exception:
                    self.app.cover_label.config(image="", text="Image Error")
                    self.app.cover_label.unbind("<Button-1>")
            else:
                self.app.cover_label.config(image="", text=error_text)
                self.app.cover_label.unbind("<Button-1>")

        self.app.root.after(0, update)

    def on_scrape_error(self, err_msg):
        def update():
            self.reset_ui_if_idle()
            err_lower = err_msg.lower()
            if "rate limit" in err_lower or "429" in err_lower:
                user_msg = "Audible API rate limit reached. Pausing scrape for 60s."
                self.app.update_api_health("Rate Limited", is_error=True)
            elif "timeout" in err_lower or "unavailable" in err_lower:
                user_msg = "Metadata server unresponsive. Using local tags."
                self.app.update_api_health("Offline", is_error=True)
            else:
                user_msg = "Failed to fetch metadata. Using local tags."
                self.app.update_api_health("Error", is_error=True)
            self.app.ui_state.dl_status.set(user_msg)
            if hasattr(self.app, "logger"):
                self.app.logger.error(f"UI caught scrape error: {err_msg}")

        self.app.root.after(0, update)
