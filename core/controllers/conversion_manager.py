import os

from core.events import default_bus
from core.utils.fs import safe_unlink
from core.utils.wake import keep


class ConversionManager:
    def __init__(
        self,
        converter,
        library_manager,
        logger,
        covers_dir,
        callbacks,
        get_drm_flags_cb,
        thread_pool,
        event_bus=None,
    ):
        self.thread_pool = thread_pool
        self.converter = converter
        self.library_manager = library_manager
        self.logger = logger
        self.covers_dir = covers_dir
        self.get_drm_flags = get_drm_flags_cb

        # Callbacks to update the UI safely
        self.on_status = callbacks.get("on_status")
        self.on_progress = callbacks.get("on_progress")
        self.on_complete = callbacks.get("on_complete")
        self.on_error = callbacks.get("on_error")
        self.on_refresh_required = callbacks.get("on_refresh_required")

        self.event_bus = event_bus or default_bus

        callbacks = callbacks or {}
        if callbacks.get("on_status"):
            self.event_bus.subscribe(
                "conversion.status",
                lambda **kw: callbacks["on_status"](kw.get("status")),
            )
        if callbacks.get("on_progress"):
            self.event_bus.subscribe(
                "conversion.progress",
                lambda **kw: callbacks["on_progress"](kw.get("percent")),
            )
        if callbacks.get("on_complete"):
            self.event_bus.subscribe(
                "conversion.complete",
                lambda **kw: callbacks["on_complete"](kw.get("message")),
            )
        if callbacks.get("on_error"):
            self.event_bus.subscribe(
                "conversion.error",
                lambda **kw: callbacks["on_error"](
                    kw.get("filepath"), kw.get("action"), kw.get("error_msg")
                ),
            )
        if callbacks.get("on_refresh_required"):
            self.event_bus.subscribe(
                "conversion.refresh_required",
                lambda **kw: callbacks["on_refresh_required"](),
            )

    def convert_single(self, input_path, output_path, chapters):
        def worker():
            try:
                total_duration = 0
                if chapters:
                    total_duration = float(chapters[-1].get("end_time", 0))
                if total_duration == 0:
                    total_duration = self.converter.get_duration(input_path)

                original_data = self.library_manager.local_library.get(input_path, {})
                title = original_data.get("title", os.path.basename(output_path))
                asin = original_data.get("asin", "")

                authors = self.library_manager.get_authors_for_asin(asin)
                cover_path = (
                    os.path.join(self.covers_dir, f"{asin}.jpg") if asin else None
                )

                drm_flags = self.get_drm_flags(input_path)

                self.converter.convert_to_m4b(
                    input_path=input_path,
                    output_path=output_path,
                    title=title,
                    authors=authors,
                    cover_path=cover_path,
                    drm_flags=drm_flags,
                    total_duration=total_duration,
                    progress_cb=self.on_progress,
                )

                new_data = original_data.copy()
                new_data.update(
                    {"title": title, "format": "M4B", "path": output_path, "asin": asin}
                )

                # Update database with new file
                self.library_manager.local_library[output_path] = {
                    "title": title,
                    "format": "M4B",
                    "path": output_path,
                    "asin": asin,
                }

                # Delete the original encrypted file
                safe_unlink(input_path, self.logger)
                self.logger(f"Cleanup attempt finished for original file: {input_path}")

                if input_path in self.library_manager.local_library:
                    del self.library_manager.local_library[input_path]

                self.library_manager.db.save_local_db(
                    self.library_manager.local_library
                )

                self.event_bus.publish(
                    "conversion.complete",
                    message="File converted and original deleted.",
                )
                self.event_bus.publish("conversion.refresh_required")

            except Exception as e:
                self.logger(f"Conversion Error: {e}")
                self.event_bus.publish(
                    "conversion.error",
                    filepath=input_path,
                    action="Convert",
                    error_msg=str(e),
                )
            finally:
                self.event_bus.publish(
                    "conversion.status", status=f"Ready: {os.path.basename(input_path)}"
                )
                self.event_bus.publish("conversion.progress", percent=0)

        self.thread_pool.submit(worker)

    def split_book(self, input_path, output_dir, chapters):
        def worker():
            try:
                drm_flags = self.get_drm_flags(input_path)
                original_data = self.library_manager.local_library.get(input_path, {})

                book_title = original_data.get(
                    "title", os.path.splitext(os.path.basename(input_path))[0]
                )
                asin = original_data.get("asin", "UNKNOWN")
                safe_book_title = "".join(
                    [c for c in book_title if c.isalnum() or c in " _-.'"]
                ).rstrip()

                target_dir = os.path.join(output_dir, f"{safe_book_title} [{asin}]")
                os.makedirs(target_dir, exist_ok=True)

                self.converter.split_into_chapters(
                    input_path=input_path,
                    target_dir=target_dir,
                    chapters=chapters,
                    drm_flags=drm_flags,
                    progress_cb=self.on_progress,
                )

                self.event_bus.publish(
                    "conversion.complete",
                    message=f"Audiobook split into {len(chapters)} files.\n\nSaved to:\n{target_dir}\n\nOriginal file preserved.",
                )

            except Exception as e:
                self.logger(f"Split Error: {e}")
                self.event_bus.publish(
                    "conversion.error",
                    filepath=input_path,
                    action="Split",
                    error_msg=str(e),
                )
            finally:
                self.event_bus.publish(
                    "conversion.status", status=f"Ready: {os.path.basename(input_path)}"
                )
                self.event_bus.publish("conversion.progress", percent=0)

        self.thread_pool.submit(worker)

    def convert_batch(self, file_list):
        def worker():
            total = len(file_list)
            error_count = 0

            try:
                with keep.running():
                    for idx, filepath in enumerate(file_list, 1):
                        if not os.path.exists(filepath):
                            continue

                        data = self.library_manager.local_library.get(filepath, {})
                        title = data.get("title", "Unknown")
                        asin = data.get("asin", "")

                        self.event_bus.publish(
                            "conversion.status",
                            status=f"Converting {idx}/{total}: {title}",
                        )

                        base_name, _ = os.path.splitext(filepath)
                        out_path = f"{base_name}.m4b"

                        drm_flags = self.get_drm_flags(filepath)
                        cover_path = (
                            os.path.join(self.covers_dir, f"{asin}.jpg")
                            if asin
                            else None
                        )
                        authors = self.library_manager.get_authors_for_asin(asin)

                        try:
                            total_duration = self.converter.get_duration(filepath)
                            self.converter.convert_to_m4b(
                                input_path=filepath,
                                output_path=out_path,
                                title=title,
                                authors=authors,
                                cover_path=cover_path,
                                drm_flags=drm_flags,
                                total_duration=total_duration,
                                progress_cb=self.on_progress,
                            )

                            self.library_manager.local_library[out_path] = data
                            self.library_manager.local_library[out_path]["format"] = (
                                "M4B"
                            )
                            self.library_manager.local_library[out_path]["path"] = (
                                out_path
                            )

                            # Delete the original file after batch convert
                            safe_unlink(filepath, self.logger)

                            if filepath in self.library_manager.local_library:
                                del self.library_manager.local_library[filepath]

                            self.library_manager.db.save_local_db(
                                self.library_manager.local_library
                            )
                            self.event_bus.publish("conversion.refresh_required")

                            if self.on_refresh_required:
                                self.on_refresh_required()

                        except Exception as e:
                            self.logger(f"Batch Convert Exception on {title}: {e}")
                            error_count += 1
                            self.event_bus.publish(
                                "conversion.error",
                                filepath=filepath,
                                action="Batch Convert",
                                error_msg=str(e),
                            )

            finally:
                self.event_bus.publish("conversion.status", status="Idle")
                self.event_bus.publish("conversion.progress", percent=0)

                if error_count > 0:
                    self.event_bus.publish(
                        "conversion.complete",
                        message=f"Batch conversion finished with {error_count} error(s).\n\nCheck the Errors window for details.",
                    )
                else:
                    self.event_bus.publish(
                        "conversion.complete", message="Batch conversion complete!"
                    )

        self.thread_pool.submit(worker)
