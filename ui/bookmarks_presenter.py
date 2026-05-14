import tkinter as tk
from tkinter import messagebox, simpledialog

class BookmarksPresenter:
    def __init__(self, app):
        self.app = app

    def add_bookmark(self):
        if not self.app.file_path:
            messagebox.showwarning("No File", "Please load an audiobook first.")
            return

        was_playing = self.app.playback.is_playing
        if was_playing:
            self.app.pause_audio()

        current_time = self.app.playback.current_play_time
        chapter_idx = self.app.playback.current_chapter_idx

        abs_time = current_time
        if self.app.playback.chapters:
            abs_time += float(self.app.playback.chapters[chapter_idx].get("start_time", 0))

        note = simpledialog.askstring("Add Bookmark", f"Add a note for {self.app.format_time(current_time)}:")

        if was_playing:
            self.app.playback.is_paused = False
            self.app.resume_playback()
            
        if not note: return 

        local_data = self.app.library_manager.local_library.get(self.app.file_path, {})
        if "bookmarks" not in local_data:
            local_data["bookmarks"] = []
            
        local_data["bookmarks"].append({
            "chapter_idx": chapter_idx,
            "time": current_time,
            "abs_time": abs_time,
            "note": note
        })
        
        self.app.db.save_local_db(self.app.library_manager.local_library)
        self.refresh_bookmarks_ui()

    def refresh_bookmarks_ui(self):
        if not hasattr(self.app, 'bm_tree'): return
        
        for row in self.app.bm_tree.get_children():
            self.app.bm_tree.delete(row)
            
        target_path = getattr(self.app, '_selected_local_path', None)
        if not target_path: return
        
        local_data = self.app.library_manager.local_library.get(target_path, {})
        bookmarks = local_data.get("bookmarks", [])

        bookmarks.sort(key=lambda x: x.get("abs_time", 0))
        
        for idx, bm in enumerate(bookmarks):
            chap_idx = bm.get("chapter_idx", 0)
            chap_title = f"Chapter {chap_idx + 1}"
            
            if target_path == self.app.file_path and self.app.playback.chapters and chap_idx < len(self.app.playback.chapters):
                chap_title = self.app.playback.chapters[chap_idx].get("tags", {}).get("title", chap_title)
                
            t_str = self.app.format_time(bm.get("time", 0))
            display_time = f"{chap_title} - {t_str}"

            self.app.bm_tree.insert("", "end", iid=str(idx), values=(display_time, bm.get("note", "")))

    def jump_to_bookmark(self, event=None):
        selected = self.app.bm_tree.focus()
        if not selected: return
        
        idx = int(selected)
        target_path = getattr(self.app, '_selected_local_path', None)
        if not target_path: return
        
        bookmarks = self.app.library_manager.local_library.get(target_path, {}).get("bookmarks", [])
        
        if 0 <= idx < len(bookmarks):
            bm = bookmarks[idx]
            
            if target_path != self.app.file_path:
                self.app.load_specific_file(target_path)
            
            self.app.stop_audio()
            self.app.playback.current_chapter_idx = bm.get("chapter_idx", 0)
            self.app.playback.current_play_time = bm.get("time", 0.0)
            
            self.app.play_chapter()

    def delete_bookmark(self):
        selected = self.app.bm_tree.focus()
        if not selected: return
        
        idx = int(selected)
        target_path = getattr(self.app, '_selected_local_path', None)
        if not target_path: return
        
        bookmarks = self.app.library_manager.local_library.get(target_path, {}).get("bookmarks", [])
        
        if 0 <= idx < len(bookmarks):
            del bookmarks[idx]
            self.app.db.save_local_db(self.app.library_manager.local_library)
            self.refresh_bookmarks_ui()