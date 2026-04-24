import os
import subprocess
import threading
import time
from core.utils.process_runner import ProcessRunner

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
        if speed != 1.0: 
            # FFmpeg atempo must be between 0.5 and 2.0. Chain them for extreme speeds.
            temp_speed = speed
            while temp_speed > 2.0:
                filters.append("atempo=2.0")
                temp_speed /= 2.0
            while temp_speed < 0.5:
                filters.append("atempo=0.5")
                temp_speed *= 2.0
            filters.append(f"atempo={temp_speed}")
            
        if voice_boost: filters.append("acompressor=threshold=-15dB:ratio=3:makeup=5dB")
        if skip_silence: filters.append("silenceremove=stop_periods=-1:stop_duration=0.5:stop_threshold=-40dB")
        
        if filters: cmd.extend(["-af", ",".join(filters)])
        if drm_flags: cmd.extend(drm_flags)
        cmd.append(filepath)

        self.logger(f"Starting player: {' '.join(cmd)}")

        # stdout/stderr go to DEVNULL to prevent buffer lockups.
        # stdin stays as PIPE to keep FFplay alive and prevent instant EOF exit.
        self.process = ProcessRunner.run_async(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.PIPE
        )
        
        self.is_playing = True
        self.is_paused = False
        
        threading.Thread(target=self._monitor, args=(self.process,), daemon=True).start()

    def _monitor(self, proc):
        # We simply wait for FFplay to hit its -t limit and exit naturally.
        proc.wait()
        
        if self.process == proc and self.is_playing:
            if proc.returncode == 0:
                self.on_complete()  # Trigger next chapter
            else:
                self.logger(f"FFplay exited with error code: {proc.returncode}")
                self.on_error(proc.returncode)

    def stop(self):
        self.is_playing = False
        self.is_paused = False
        
        if self.process:
            try:
                if os.name == 'nt':
                    ProcessRunner.run_blocking(
                        ['taskkill', '/F', '/T', '/PID', str(self.process.pid)], 
                        capture_output=False,
                        stdout=subprocess.DEVNULL, 
                        stderr=subprocess.DEVNULL
                    )
                else:
                    self.process.kill()
            except Exception as e:
                self.logger(f"Clean kill failed, forcing terminate: {e}")
                try:
                    self.process.terminate()
                except OSError as e:
                    self.logger.error(f"CRITICAL: Failed to terminate FFplay zombie process: {e}")
                    
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