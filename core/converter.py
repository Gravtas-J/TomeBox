import subprocess
import os
import json

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
        base, ext = os.path.splitext(output_path)
        temp_out_path = f"{base}_temp{ext}"

        cmd = ["ffmpeg", "-y"]
        if drm_flags:
            cmd.extend(drm_flags)
            
        cmd.extend(["-i", input_path])

        if cover_path and os.path.exists(cover_path):
            cmd.extend(["-i", cover_path, "-map", "0:a", "-map", "1:v", "-c:v", "mjpeg", "-disposition:v", "attached_pic"])

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