import os
import threading
import traceback
import shutil

try:
    from wakepy import keep
except ImportError:
    class KeepDummy:
        def running(self):
            class ContextDummy:
                def __enter__(self): pass
                def __exit__(self, *args): pass
            return ContextDummy()
    keep = KeepDummy()

class ConversionManager:
    def __init__(self, converter, library_manager, logger, covers_dir, callbacks, get_drm_flags_cb):
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

                authors = ""
                for item in getattr(self.library_manager, 'cloud_items', []):
                    if item.get("asin") == asin:
                        raw_authors = item.get("authors", [])
                        authors = ", ".join([a.get("name", "") for a in raw_authors if isinstance(a, dict)])
                        break

                cover_path = os.path.join(self.covers_dir, f"{asin}.jpg")
                drm_flags = self.get_drm_flags(input_path)

                self.converter.convert_to_m4b(
                    input_path=input_path, output_path=output_path, title=title,
                    authors=authors, cover_path=cover_path, drm_flags=drm_flags,
                    total_duration=total_duration, progress_cb=self.on_progress
                )

                # Update database with new file
                self.library_manager.local_library[output_path] = {
                    "title": title, "format": "M4B", "path": output_path, "asin": asin
                }
                
                # Delete the original encrypted file
                if os.path.exists(input_path):
                    try:
                        os.remove(input_path)
                        self.logger(f"Deleted original file: {input_path}")
                    except Exception as e:
                        self.logger(f"Could not delete original file: {e}")
                        
                if input_path in self.library_manager.local_library:
                    del self.library_manager.local_library[input_path]
                
                self.library_manager.db.save_local_db(self.library_manager.local_library)
                
                if self.on_complete:
                    self.on_complete("File converted and original deleted.")
                if self.on_refresh_required:
                    self.on_refresh_required()

            except Exception as e:
                self.logger(f"Conversion Error: {e}")
                if self.on_error:
                    self.on_error(f"Conversion Failed: {str(e)}")
            finally:
                if self.on_status:
                    self.on_status(f"Ready: {os.path.basename(input_path)}")
                if self.on_progress:
                    self.on_progress(0)

        threading.Thread(target=worker, daemon=True).start()

    def split_book(self, input_path, output_dir, chapters):
        def worker():
            try:
                drm_flags = self.get_drm_flags(input_path)
                original_data = self.library_manager.local_library.get(input_path, {})
                book_title = original_data.get("title", os.path.splitext(os.path.basename(input_path))[0])
                safe_book_title = "".join([c for c in book_title if c.isalnum() or c in [' ', '-', '_']]).rstrip()
                
                target_dir = os.path.join(output_dir, safe_book_title)
                os.makedirs(target_dir, exist_ok=True)

                self.converter.split_into_chapters(
                    input_path=input_path, target_dir=target_dir, chapters=chapters,
                    drm_flags=drm_flags, progress_cb=self.on_progress
                )
                
                # Delete the original file after split
                if os.path.exists(input_path):
                    try:
                        os.remove(input_path)
                    except OSError:
                        pass
                        
                if input_path in self.library_manager.local_library:
                    del self.library_manager.local_library[input_path]
                    
                self.library_manager.db.save_local_db(self.library_manager.local_library)

                if self.on_complete:
                    self.on_complete(f"Audiobook split into {len(chapters)} files.\n\nSaved to:\n{target_dir}\n\nOriginal file deleted.")
                if self.on_refresh_required:
                    self.on_refresh_required()
                
            except Exception as e:
                self.logger(f"Split Error: {e}")
                if self.on_error:
                    self.on_error(f"Split Failed: {str(e)}")
            finally:
                if self.on_status:
                    self.on_status(f"Ready: {os.path.basename(input_path)}")
                if self.on_progress:
                    self.on_progress(0)

        threading.Thread(target=worker, daemon=True).start()

    def convert_batch(self, file_list):
        def worker():
            total = len(file_list)
            try:
                with keep.running():
                    for idx, filepath in enumerate(file_list, 1):
                        if not os.path.exists(filepath): continue
                            
                        data = self.library_manager.local_library.get(filepath, {})
                        title = data.get("title", "Unknown")
                        asin = data.get("asin", "")
                        
                        if self.on_status:
                            self.on_status(f"Converting {idx}/{total}: {title}")
                        
                        base_name, _ = os.path.splitext(filepath)
                        out_path = f"{base_name}.m4b"
                        
                        drm_flags = self.get_drm_flags(filepath)
                        cover_path = os.path.join(self.covers_dir, f"{asin}.jpg")
                        
                        authors = ""
                        for item in getattr(self.library_manager, 'cloud_items', []):
                            if item.get("asin") == asin:
                                raw_authors = item.get("authors", [])
                                authors = ", ".join([a.get("name", "") for a in raw_authors if isinstance(a, dict)])
                                break

                        try:
                            total_duration = self.converter.get_duration(filepath)
                            self.converter.convert_to_m4b(
                                input_path=filepath, output_path=out_path, title=title,
                                authors=authors, cover_path=cover_path, drm_flags=drm_flags,
                                total_duration=total_duration, progress_cb=self.on_progress
                            )
                            
                            self.library_manager.local_library[out_path] = data
                            self.library_manager.local_library[out_path]["format"] = "M4B"
                            self.library_manager.local_library[out_path]["path"] = out_path
                            
                            # Delete the original file after batch convert
                            if os.path.exists(filepath): 
                                try:
                                    os.remove(filepath)
                                except OSError:
                                    pass
                                    
                            if filepath in self.library_manager.local_library:
                                del self.library_manager.local_library[filepath]
                                
                            self.library_manager.db.save_local_db(self.library_manager.local_library)
                            
                            if self.on_refresh_required:
                                self.on_refresh_required()
                                
                        except Exception as e:
                            self.logger(f"Batch Convert Exception on {title}: {e}")
                            
            finally:
                if self.on_status:
                    self.on_status("Idle")
                if self.on_progress:
                    self.on_progress(0)
                if self.on_complete:
                    self.on_complete("Batch conversion complete!")

        threading.Thread(target=worker, daemon=True).start()