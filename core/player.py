import os
import subprocess
import threading
import time

class AudioPlayer:
    def __init__(self, logger, on_complete_cb, on_error_cb):
        self.process = None
        self.is_playing = False
        self.is_paused = False
        
        self.logger = logger
        self.on_complete = on_complete_cb
        self.on_error = on_error_cb

    def play(self, filepath, start_time, remaining_duration, speed, volume, voice_boost, skip_silence, drm_flags=None):
        self.stop()
        
        cmd = [
            "ffplay", "-nodisp", "-autoexit", "-loglevel", "error", 
            "-ss", str(start_time), "-t", str(remaining_duration)
        ]
        
        if os.name != 'nt':
            cmd.extend(["-volume", str(volume)])
            
        filters = []
        if speed != 1.0: filters.append(f"atempo={speed}")
        if voice_boost: filters.append("acompressor=threshold=-15dB:ratio=3:makeup=5dB")
        if skip_silence: filters.append("silenceremove=stop_periods=-1:stop_duration=0.5:stop_threshold=-40dB")
        
        if filters: cmd.extend(["-af", ",".join(filters)])
        if drm_flags: cmd.extend(drm_flags)
        cmd.append(filepath)

        self.logger(f"Starting player: {' '.join(cmd)}")

        self.process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        )
        
        self.is_playing = True
        self.is_paused = False
        
        # Monitor the process in the background
        threading.Thread(target=self._monitor, args=(self.process,), daemon=True).start()

    def _monitor(self, proc):
        for line in proc.stderr:
            if line.strip(): 
                self.logger(f"[PLAYER ERROR]: {line.strip()}")
        
        proc.wait()
        
        if self.process == proc and self.is_playing:
            if proc.returncode == 0:
                self.on_complete()  # Trigger next chapter
            else:
                self.on_error(proc.returncode)

    def stop(self):
        self.is_playing = False
        self.is_paused = False
        if self.process:
            self.process.terminate()
            self.process = None

    def set_volume(self, volume):
        if os.name == 'nt':
            try:
                from pycaw.pycaw import AudioUtilities, ISimpleAudioVolume
                vol_float = float(volume) / 100.0
                sessions = AudioUtilities.GetAllSessions()
                for session in sessions:
                    if session.Process and session.Process.name() == "ffplay.exe":
                        session._ctl.QueryInterface(ISimpleAudioVolume).SetMasterVolume(vol_float, None)
            except Exception as e:
                self.logger(f"Volume change error: {e}")