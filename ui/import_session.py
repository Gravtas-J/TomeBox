import os
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from core.utils.paths import parse_dnd_paths


class ImportSession:
    def __init__(self, app):
        self.app = app

    def _prompt_resume_imports(self):
        pending = self.app.system_manager.load_pending_imports(self.app.db.data_dir)
        valid_pending = [p for p in pending if os.path.exists(p["path"])]

        if not valid_pending:
            if pending:
                self.app.system_manager.clear_all_pending_imports(self.app.db.data_dir)
            return

        if messagebox.askyesno(
            "Interrupted Imports Found",
            f"TomeBox recovered {len(valid_pending)} interrupted import tasks from a previous session.\n\n"
            "Would you like to resume importing them now?",
        ):
            self.app.ui_state.dl_status.set("Resuming interrupted imports...")
            for task in valid_pending:
                path = task["path"]
                is_folder = task["is_folder"]
                if is_folder:
                    self.app.library_manager.import_folder(
                        folder_path=path,
                        converter=self.app.converter,
                        active_profile=self.app.active_profile,
                        on_status_cb=lambda msg: self.app.root.after(
                            0, self.app.ui_state.dl_status.set, msg
                        ),
                        on_complete_cb=lambda c, t=0, p=path: (
                            self.app.action_router.on_import_finished(p, c, t)
                        ),
                        logger=self.app.logger,
                        on_progress_cb=lambda pct: self.app.root.after(
                            0, lambda: self.app.ui_state.dl_progress.set(pct)
                        ),
                    )
                else:
                    self.app.library_manager.import_files(
                        file_paths=[path],
                        converter=self.app.converter,
                        active_profile=self.app.active_profile,
                        on_status_cb=lambda msg: self.app.root.after(
                            0, self.app.ui_state.dl_status.set, msg
                        ),
                        on_complete_cb=lambda c, t=0, p=path: (
                            self.app.action_router.on_import_finished(p, c, t)
                        ),
                        logger=self.app.logger,
                    )
        else:
            self.app.system_manager.clear_all_pending_imports(self.app.db.data_dir)

    def on_file_drop(self, event):
        dropped_paths = parse_dnd_paths(event.data)
        if not dropped_paths:
            return

        has_folders = any(os.path.isdir(p) for p in dropped_paths)
        import_mode = "merge"

        if has_folders:
            choice = messagebox.askyesnocancel(
                "Import Method",
                "You are importing folders containing multiple audio files.\n\n"
                "Yes = Merge into a single .m4b file (Slower, requires disk space)\n"
                "No = Play In-Place as a Playlist (Instant)\n"
                "Cancel = Abort",
            )
            if choice is None:
                self.app.ui_state.dl_status.set("Import cancelled.")
                return
            import_mode = "merge" if choice else "playlist"

        self.app.ui_state.dl_status.set("Processing dropped items...")

        for path in dropped_paths:
            if not os.path.exists(path):
                continue

            task_id = f"import_{int(time.time() * 1000)}_{os.path.basename(path).replace(' ', '_')}"

            if os.path.isdir(path):
                self.app.system_manager.add_pending_import(
                    self.app.db.data_dir, path, True
                )

                self.toggle_queue_drawer(True)
                self.add_queue_ui_row(task_id, f"Importing: {os.path.basename(path)}")
                self.app.root.update_idletasks()

                self.app.library_manager.import_folder(
                    folder_path=path,
                    converter=self.app.converter,
                    active_profile=self.app.active_profile,
                    on_status_cb=lambda msg, tid=task_id: (
                        self.app.action_router.on_import_status(tid, msg)
                    ),
                    on_complete_cb=lambda c, t=0, p=path, tid=task_id: (
                        self.app.action_router.on_import_finished(p, c, t, tid)
                    ),
                    logger=self.app.logger,
                    on_progress_cb=lambda pct, tid=task_id: (
                        self.app.action_router.on_import_progress(tid, pct)
                    ),
                    task_id=task_id,
                    import_mode=import_mode,
                )
            else:
                ext = os.path.splitext(path)[1].lower()
                if ext in [".aax", ".aaxc", ".m4b", ".mp3"]:
                    self.app.system_manager.add_pending_import(
                        self.app.db.data_dir, path, False
                    )
                    self.toggle_queue_drawer(True)
                    self.add_queue_ui_row(
                        task_id, f"Importing File: {os.path.basename(path)}"
                    )
                    self.app.root.update_idletasks()

                    self.app.library_manager.import_files(
                        file_paths=[path],
                        converter=self.app.converter,
                        active_profile=self.app.active_profile,
                        on_status_cb=lambda msg, tid=task_id: (
                            self.app.action_router.on_import_status(tid, msg)
                        ),
                        on_complete_cb=lambda c, t=0, p=path, tid=task_id: (
                            self.app.action_router.on_import_finished(p, c, t, tid)
                        ),
                        logger=self.app.logger,
                        task_id=task_id,
                    )

    def add_local_file(self):
        filepath = filedialog.askopenfilename(
            filetypes=[("Audiobooks", "*.aax *.m4b *.mp3")]
        )
        if not filepath:
            return

        task_id = f"import_{int(time.time() * 1000)}_{os.path.basename(filepath).replace(' ', '_')}"
        self.app.system_manager.add_pending_import(
            self.app.db.data_dir, filepath, False
        )

        self.toggle_queue_drawer(True)
        self.add_queue_ui_row(task_id, f"Importing: {os.path.basename(filepath)}")
        self.app.root.update_idletasks()

        self.app.library_manager.import_files(
            file_paths=[filepath],
            converter=self.app.converter,
            active_profile=self.app.active_profile,
            on_status_cb=lambda msg, tid=task_id: (
                self.app.action_router.on_import_status(tid, msg)
            ),
            on_complete_cb=lambda c, t=0, p=filepath, tid=task_id: (
                self.app.action_router.on_import_finished(p, c, t, tid)
            ),
            logger=self.app.logger,
            task_id=task_id,
        )

    def import_folder(self):
        folder = filedialog.askdirectory(title="Select Folder Containing Audiobooks")
        if not folder:
            return

        choice = messagebox.askyesnocancel(
            "Import Method",
            "TomeBox will now scan this folder.\n\n"
            "Yes = Merge into a single .m4b file (Slower, requires disk space)\n"
            "No = Play In-Place as a Playlist (Instant)\n"
            "Cancel = Abort",
        )
        if choice is None:
            return

        import_mode = "merge" if choice else "playlist"
        task_id = f"import_{int(time.time() * 1000)}_{os.path.basename(folder).replace(' ', '_')}"
        self.app.system_manager.add_pending_import(self.app.db.data_dir, folder, True)

        self.toggle_queue_drawer(True)
        self.add_queue_ui_row(task_id, f"Importing Folder: {os.path.basename(folder)}")
        self.app.root.update_idletasks()

        try:
            self.app.library_manager.import_folder(
                folder_path=folder,
                converter=self.app.converter,
                active_profile=self.app.active_profile,
                on_status_cb=lambda msg, tid=task_id: (
                    self.app.action_router.on_import_status(tid, msg)
                ),
                on_complete_cb=lambda c, t=0, p=folder, tid=task_id: (
                    self.app.action_router.on_import_finished(p, c, t, tid)
                ),
                logger=self.app.logger,
                on_progress_cb=lambda pct, tid=task_id: (
                    self.app.action_router.on_import_progress(tid, pct)
                ),
                task_id=task_id,
                import_mode=import_mode,
                on_book_start_cb=self.app.action_router.on_book_start,
                on_book_progress_cb=self.app.action_router.on_book_progress,
                on_book_complete_cb=self.app.action_router.on_book_complete,
            )
        except Exception:
            import traceback

            traceback.print_exc()

    def toggle_queue_visibility(self):
        current_panes = self.app.main_paned.panes()
        queue_str = str(self.app.queue_frame)
        if queue_str in current_panes:
            self.app.main_paned.forget(self.app.queue_frame)
        else:
            self.app.main_paned.add(self.app.queue_frame, weight=0)

    def toggle_queue_drawer(self, show=True):
        current_panes = self.app.main_paned.panes()
        queue_str = str(self.app.queue_frame)
        if show and queue_str not in current_panes:
            self.app.main_paned.add(self.app.queue_frame, weight=0)
        elif not show and queue_str in current_panes:
            self.app.main_paned.forget(self.app.queue_frame)

    def add_queue_ui_row(self, task_id, title):
        row_frame = tk.Frame(self.app.queue_inner, bg="#1c1c1c")
        row_frame.pack(fill="x", pady=2, padx=5)

        title_lbl = ttk.Label(
            row_frame,
            text=title[:40] + ("..." if len(title) > 40 else ""),
            width=35,
            anchor="w",
        )
        title_lbl.pack(side=tk.LEFT, padx=(0, 10))

        prog_var = tk.DoubleVar()
        prog_bar = ttk.Progressbar(
            row_frame, variable=prog_var, maximum=100, length=200
        )
        prog_bar.pack(side=tk.LEFT, fill="x", expand=True, padx=(0, 10))

        status_var = tk.StringVar(value="Waiting...")
        status_lbl = ttk.Label(row_frame, textvariable=status_var, width=15, anchor="w")
        status_lbl.pack(side=tk.LEFT, padx=(0, 10))

        cancel_btn = ttk.Button(
            row_frame,
            text="✕",
            width=3,
            command=lambda a=task_id: self.app.cancel_task(a),
        )
        cancel_btn.pack(side=tk.RIGHT, padx=(2, 0))

        # NEW: Pause/Resume Toggle Button
        pause_btn = ttk.Button(row_frame, text="⏸", width=3)
        pause_btn.config(
            command=lambda a=task_id, b=pause_btn: self.app.toggle_pause_task(a, b)
        )
        pause_btn.pack(side=tk.RIGHT, padx=(5, 2))

        self.app.queue_ui_elements[task_id] = {
            "frame": row_frame,
            "prog_var": prog_var,
            "status_var": status_var,
            "pause_btn": pause_btn,
        }
