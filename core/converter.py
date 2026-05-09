import subprocess
import os
import json
from core.utils.process_runner import ProcessRunner

def resolve_cover_path(base_cover_path, asin):
    """
    Intelligently hunts for the cover art to handle Audible's dropped leading 
    zeros and external metadata scraper naming conventions.
    """
    if not base_cover_path:
        return None

    cover_dir = os.path.dirname(base_cover_path)
    padded_asin = str(asin).zfill(10)

    # List of possible filenames your system/scraper might have saved
    candidates = [
        base_cover_path,                                  # The raw 9-digit expectation
        os.path.join(cover_dir, f"{padded_asin}.jpg"),    # The 10-digit padded reality
        os.path.join(cover_dir, f"{padded_asin}.png"),    # Scraper might have grabbed a PNG
        os.path.join(cover_dir, "cover.jpg"),             # Standard generic scraper output
        os.path.join(cover_dir, "folder.jpg")             # Alternative standard output
    ]

    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate  # Found it!
            
    return None # Truly missing

class AudioConverter:
    def __init__(self, logger):
        self.logger = logger

        self.current_process = None
        self.is_cancelled = False

    def concat_to_m4b(self, file_paths, output_path, title="Audiobook", logger=None, progress_cb=None):
        import tempfile
        import os
        import subprocess

        if not file_paths:
            return False

        needs_reencode = any(f.lower().endswith(".mp3") for f in file_paths)

        fd_concat, concat_txt_path = tempfile.mkstemp(suffix=".txt", text=True)
        fd_meta, metadata_txt_path = tempfile.mkstemp(suffix=".txt", text=True)
        
        base, ext = os.path.splitext(output_path)
        temp_out_path = f"{output_path}.tmp.m4b"

        try:
            with os.fdopen(fd_concat, 'w', encoding='utf-8') as f_concat:
                for path in file_paths:
                    safe_path = path.replace('\\', '/').replace("'", "'\\''")
                    f_concat.write(f"file '{safe_path}'\n")

            first_artist = "Unknown Author"
            first_album = title
            first_series = ""
            total_duration_sec = 0  
            
            try:
                first_data = self.get_metadata_and_chapters(file_paths[0])
                first_tags = first_data.get("format", {}).get("tags", {})
                first_artist = first_tags.get("artist") or first_tags.get("album_artist") or "Unknown Author"
                first_album = first_tags.get("album") or title
                first_series = first_tags.get("series") or first_tags.get("show") or ""
            except Exception:
                pass

            with os.fdopen(fd_meta, 'w', encoding='utf-8') as f_meta:
                f_meta.write(";FFMETADATA1\n")
                f_meta.write(f"title={title}\n")
                f_meta.write(f"artist={first_artist}\n")
                f_meta.write(f"album_artist={first_artist}\n")
                f_meta.write(f"album={first_album}\n")
                
                if first_series:
                    f_meta.write(f"show={first_series}\n")
                    f_meta.write(f"series={first_series}\n")
                    
                f_meta.write("genre=Audiobook\n\n")

                current_start_ms = 0
                for path in file_paths:
                    try:
                        data = self.get_metadata_and_chapters(path)
                        duration_sec = float(data.get("format", {}).get("duration", 0))
                    except Exception:
                        duration_sec = 0

                    if duration_sec > 0:
                        total_duration_sec += duration_sec
                        duration_ms = int(duration_sec * 1000)
                        end_ms = current_start_ms + duration_ms
                        chap_title = os.path.splitext(os.path.basename(path))[0]

                        f_meta.write("[CHAPTER]\n")
                        f_meta.write("TIMEBASE=1/1000\n")
                        f_meta.write(f"START={current_start_ms}\n")
                        f_meta.write(f"END={end_ms}\n")
                        f_meta.write(f"title={chap_title}\n\n")

                        current_start_ms = end_ms

            cmd = [
                "ffmpeg", "-y",
                "-fflags", "+genpts",
                "-f", "concat",
                "-safe", "0",
                "-i", concat_txt_path,
                "-i", metadata_txt_path,
                "-i", file_paths[0],
                "-map", "0:a",
                "-map", "2:v?",
                "-map_metadata", "1",
                "-c:v", "copy",
                "-disposition:v", "attached_pic"
            ]

            if needs_reencode:
                cmd.extend(["-c:a", "aac", "-b:a", "128k", "-ac", "2", "-ar", "44100"])
            else:
                cmd.extend(["-c:a", "copy"])
                
            # NEW: Pipe progress output to the safe temporary file
            cmd.extend(["-movflags", "+faststart+use_metadata_tags", "-progress", "pipe:1", temp_out_path]) 

            self.is_cancelled = False
            self.current_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='replace',
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )

            last_percent = -1
            for line in self.current_process.stdout:
                if getattr(self, 'is_cancelled', False):
                    self.current_process.terminate()
                    break 

                line = line.strip()
                if line.startswith("out_time_us=") and total_duration_sec > 0:
                    try:
                        val = line.split("=")[1]
                        if val != "N/A":
                            out_time_us = int(val)
                            if out_time_us > 0:
                                current_time_sec = out_time_us / 1000000.0
                                percent = int((current_time_sec / total_duration_sec) * 100)
                                if percent > last_percent and percent <= 100:
                                    if progress_cb: progress_cb(percent)
                                    last_percent = percent
                    except ValueError:
                        pass

            self.current_process.wait()
            
            # --- CANCELLATION & CLEANUP LOGIC ---
            if getattr(self, 'is_cancelled', False):
                if os.path.exists(temp_out_path): 
                    os.remove(temp_out_path)
                return False
                
            success = self.current_process.returncode == 0 and os.path.exists(temp_out_path) and os.path.getsize(temp_out_path) > 0
            
            if success:
                # Rename the successful temp file to the final intended name
                os.replace(temp_out_path, output_path)
                return True
            else:
                # Nuke the broken file if FFmpeg errored out naturally
                if os.path.exists(temp_out_path): 
                    os.remove(temp_out_path)
                return False

        except Exception as e:
            if getattr(self, 'current_process', None):
                self.current_process.kill()
            if os.path.exists(temp_out_path):
                try: os.remove(temp_out_path)
                except: pass
            if logger: logger(f"Concat aborted: {e}")
            return False
            
        finally:
            if os.path.exists(concat_txt_path):
                try: os.remove(concat_txt_path)
                except: pass
            if os.path.exists(metadata_txt_path):
                try: os.remove(metadata_txt_path)
                except: pass
            
            if os.path.exists(temp_out_path):
                try: os.remove(temp_out_path)
                except: pass
                
            self.current_process = None

    def cancel(self):
        self.is_cancelled = True
        if self.current_process:
            try:
                self.current_process.terminate()  # Sends SIGTERM to ffmpeg.exe
                self.logger("FFmpeg process terminated by user.")
            except Exception as e:
                pass # Process might have already finished
            
    def get_metadata_and_chapters(self, filepath):
        cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_chapters", filepath]
        try:
            result = ProcessRunner.run_blocking(cmd, capture_output=True, text=True, encoding="utf-8", check=True)
            return json.loads(result.stdout)
        except Exception as e:
            self.logger(f"FFprobe error on {filepath}: {e}")
            return {}

    def get_duration(self, filepath):
        try:
            cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", filepath]
            res = ProcessRunner.run_blocking(cmd, capture_output=True, text=True, encoding="utf-8")
            return float(res.stdout.strip())
        except Exception:
            return 0.0

    def convert_to_m4b(self, input_path, output_path, title, authors, cover_path, drm_flags, total_duration, progress_cb=None):
        import os
        import subprocess
        actual_cover = None
        if cover_path:
            raw_asin, _ = os.path.splitext(os.path.basename(cover_path))
            actual_cover = resolve_cover_path(cover_path, raw_asin)

        base, ext = os.path.splitext(output_path)
        temp_out_path = f"{base}_temp{ext}"

        cmd = ["ffmpeg", "-y"]
        if drm_flags:
            cmd.extend(drm_flags)
            
        cmd.extend(["-i", input_path])

        # --- APPLY RESOLVED COVER ---
        if actual_cover:
            cmd.extend([
                "-i", actual_cover, 
                "-map", "0:a", 
                "-map", "1:v", 
                "-c:v", "mjpeg", 
                "-disposition:v", "attached_pic"
            ])
        else:
            # Fallback: Convert audio only without crashing if cover is truly gone
            cmd.extend(["-map", "0:a"])

        cmd.extend([
            "-c:a", "copy",
            "-metadata", f"title={title}",
            "-metadata", f"album={title}",
            "-metadata", "genre=Audiobook"
        ])
        
        if authors:
            cmd.extend(["-metadata", f"artist={authors}", "-metadata", f"album_artist={authors}"])

        cmd.extend(["-progress", "pipe:1", temp_out_path])
        
        # Reset the cancellation flag for this specific run
        self.is_cancelled = False 
        
        try:
            # TRAP 1: MISSING FFMPEG
            try:
                self.current_process = ProcessRunner.run_async(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, 
                    universal_newlines=True
                )
            except FileNotFoundError:
                raise Exception("CRITICAL: FFmpeg not found. Please ensure FFmpeg is installed and added to your system PATH.")

            last_percent = -1
            for line in self.current_process.stdout:
                
                # INSTANT BAILOUT if the user clicked cancel or hit Alt-F4
                if getattr(self, 'is_cancelled', False):
                    self.current_process.terminate()
                    break 

                line = line.strip()
                # Safety check added here to ensure total_duration isn't None
                if line.startswith("out_time_us=") and total_duration and total_duration > 0:
                    try:
                        val = line.split("=")[1]
                        if val != "N/A":
                            out_time_us = int(val)
                            if out_time_us > 0:
                                current_time_sec = out_time_us / 1000000.0
                                percent = int((current_time_sec / total_duration) * 100)
                                if percent > last_percent and percent <= 100:
                                    if progress_cb: progress_cb(percent)
                                    last_percent = percent
                    except ValueError:
                        pass

            self.current_process.wait()
            
            # Post-processing checks
            if getattr(self, 'is_cancelled', False):
                raise Exception("Conversion cancelled by user.")
                
            if self.current_process.returncode != 0:
                raise Exception(f"FFmpeg process failed with exit code {self.current_process.returncode}.")
            
            # TRAP 2: PERMISSION DENIED / DRIVE FULL
            try:
                os.replace(temp_out_path, output_path)
            except OSError as e:
                raise Exception(f"File System Error: Could not save the final audiobook. (Check permissions and drive space). Details: {e}")
                
            return True
            
        except Exception as e:
            # THE AFTERMATH CLEANUP
            if os.path.exists(temp_out_path):
                try: 
                    os.remove(temp_out_path)
                except OSError: 
                    pass
            raise e
        finally:
            self.current_process = None
            if os.path.exists(temp_out_path):
                try: os.remove(temp_out_path)
                except OSError: pass

    def split_into_chapters(self, input_path, target_dir, chapters, drm_flags, progress_cb=None):
        import subprocess
        import time
        from core.utils.process_runner import ProcessRunner
        total_chaps = len(chapters)
        self.is_cancelled = False 
        
        created_files = [] 
        
        for idx, chapter in enumerate(chapters):
            if getattr(self, 'is_cancelled', False):
                self._cleanup_split_files(created_files, target_dir)
                raise Exception("Chapter splitting cancelled by user.")
                
            if progress_cb:
                progress_cb(((idx + 1) / total_chaps) * 100)
                
            chap_title = chapter.get("tags", {}).get("title", f"Chapter {idx + 1}")
            safe_chap_title = "".join([c for c in chap_title if c.isalnum() or c in [' ', '-', '_']]).rstrip()
            
            out_name = f"{idx + 1:03d} - {safe_chap_title}.m4b"
            out_path = os.path.join(target_dir, out_name)
            
            base, ext = os.path.splitext(out_path)
            temp_out_path = f"{out_path}.tmp.m4b"

            start = chapter.get("start_time", 0)
            end = chapter.get("end_time", 0)

            cmd = ["ffmpeg", "-y"]
            if drm_flags:
                cmd.extend(drm_flags)
            cmd.extend(["-i", input_path, "-ss", str(start), "-to", str(end), "-c", "copy", temp_out_path])
            
            try:
                self.current_process = ProcessRunner.run_async(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                
                # Active polling loop to ensure instant cancellation
                while self.current_process.poll() is None:
                    if getattr(self, 'is_cancelled', False):
                        self.current_process.terminate()
                        break
                    time.sleep(0.5)
            except Exception:
                pass
            finally:
                self.current_process = None
                
            if getattr(self, 'is_cancelled', False):
                if os.path.exists(temp_out_path):
                    try: os.remove(temp_out_path)
                    except OSError: pass
                self._cleanup_split_files(created_files, target_dir)
                raise Exception("Chapter splitting cancelled by user.")
                
            if os.path.exists(temp_out_path):
                try:
                    os.replace(temp_out_path, out_path)
                    created_files.append(out_path)
                except OSError:
                    try: os.remove(temp_out_path)
                    except OSError: pass
                
        return True

    def _cleanup_split_files(self, created_files, target_dir):
        """Helper to nuke all generated chapter files if the user aborts."""
        import os
        for f in created_files:
            if os.path.exists(f):
                try: os.remove(f)
                except OSError: pass
        try: os.rmdir(target_dir)
        except OSError: pass