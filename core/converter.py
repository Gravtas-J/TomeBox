import subprocess
import os
import json

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
        # Suppress the black command prompt window on Windows
        self.creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0

    def get_metadata_and_chapters(self, filepath):
        cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_chapters", filepath]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", check=True, creationflags=self.creationflags)
            return json.loads(result.stdout)
        except Exception as e:
            self.logger(f"FFprobe error on {filepath}: {e}")
            return {}

    def get_duration(self, filepath):
        try:
            cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", filepath]
            res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", creationflags=self.creationflags)
            return float(res.stdout.strip())
        except Exception:
            return 0.0

    def convert_to_m4b(self, input_path, output_path, title, authors, cover_path, drm_flags, total_duration, progress_cb=None):
        import os
        import subprocess

        # --- SMART COVER RESOLUTION ---
        actual_cover = None
        if cover_path:
            cover_dir = os.path.dirname(cover_path)
            filename = os.path.basename(cover_path)
            raw_asin, _ = os.path.splitext(filename)
            padded_asin = str(raw_asin).zfill(10)

            # Hunt for the cover handling dropped zeros and scraper defaults
            candidates = [
                cover_path,                                    # The expected path
                os.path.join(cover_dir, f"{padded_asin}.jpg"), # Padded 10-digit ASIN
                os.path.join(cover_dir, f"{padded_asin}.png"), # PNG variant
                os.path.join(cover_dir, "cover.jpg"),          # Standard scraper output
                os.path.join(cover_dir, "folder.jpg")          # Standard scraper output
            ]
            
            for candidate in candidates:
                if os.path.exists(candidate):
                    actual_cover = candidate
                    break
        # ------------------------------

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
        
        try:
            process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, 
                universal_newlines=True, creationflags=self.creationflags
            )

            last_percent = -1
            for line in process.stdout:
                line = line.strip()
                if line.startswith("out_time_us=") and total_duration > 0:
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

            process.wait()
            
            if process.returncode != 0:
                raise Exception(f"FFmpeg process failed with exit code {process.returncode}.")
            
            os.replace(temp_out_path, output_path)
            return True
            
        except Exception as e:
            if os.path.exists(temp_out_path):
                try: os.remove(temp_out_path)
                except OSError: pass
            raise e

    def split_into_chapters(self, input_path, target_dir, chapters, drm_flags, progress_cb=None):
        total_chaps = len(chapters)
        for idx, chapter in enumerate(chapters):
            if progress_cb:
                progress_cb(((idx + 1) / total_chaps) * 100)
                
            chap_title = chapter.get("tags", {}).get("title", f"Chapter {idx + 1}")
            safe_chap_title = "".join([c for c in chap_title if c.isalnum() or c in [' ', '-', '_']]).rstrip()
            
            out_name = f"{idx + 1:03d} - {safe_chap_title}.m4b"
            out_path = os.path.join(target_dir, out_name)

            start = chapter.get("start_time", 0)
            end = chapter.get("end_time", 0)

            cmd = ["ffmpeg", "-y"]
            if drm_flags:
                cmd.extend(drm_flags)
            cmd.extend(["-i", input_path, "-ss", str(start), "-to", str(end), "-c", "copy", out_path])
            
            subprocess.run(cmd, check=True, creationflags=self.creationflags)
        return True