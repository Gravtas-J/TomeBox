import os
import urllib.request
from core.utils.fs import safe_unlink

class DownloadCanceledError(Exception):
    pass
class AudiobookDownloader:
    def __init__(self, api_client, logger):
        self.api = api_client
        self.logger = logger

    def download_item(self, asin, title, save_dir, progress_callback=None, check_cancel_callback=None):
        """
        Handles the license request and file streaming. 
        Returns (filepath, a_key, a_iv, ext) on success.
        """
        # 1. Get the URL and Decryption Keys via the API client
        download_url, a_key, a_iv = self.api.get_download_license(asin)

        # 2. Setup file paths
        safe_title = "".join([c for c in title if c.isalpha() or c.isdigit() or c in " _-.'"]).rstrip()
        ext = ".aaxc" if a_key else ".aax"
        filepath = os.path.join(save_dir, f"{safe_title} [{asin}]{ext}")
        temp_filepath = f"{filepath}.part"

        self.logger(f"Downloading {ext} file to: {temp_filepath}")

        # 3. Stream the file
        headers = {"User-Agent": "Audible/6.6.1 (iPhone; iOS 15.5; Scale/3.00)"}
        req = urllib.request.Request(download_url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as response, open(temp_filepath, 'wb') as out_file:
                total_size = int(response.headers.get('content-length', 0))
                downloaded = 0
                last_ui_percent = -1
                last_log_percent = 0

                while True:
                    # Check if the user hit the cancel button in the UI
                    if check_cancel_callback and check_cancel_callback():
                        raise DownloadCanceledError() # <--- Updated to the typed exception here

                    chunk = response.read(32768)
                    if not chunk: 
                        break
                    
                    out_file.write(chunk)

                    # Update Progress
                    if total_size > 0:
                        downloaded += len(chunk)
                        percent_float = (downloaded / total_size) * 100
                        percent_int = int(percent_float)

                        # Only ping the UI callback when a full percentage point changes (saves CPU)
                        if percent_int > last_ui_percent:
                            if progress_callback:
                                progress_callback(percent_float)
                            last_ui_percent = percent_int

                        # Only ping the logger every 10% to prevent spam
                        if percent_int >= last_log_percent + 10:
                            self.logger(f"Download Progress: {percent_int}%")
                            last_log_percent = percent_int

            # 4. Stream complete, finalize the file
            os.replace(temp_filepath, filepath)
            self.logger(f"Download complete: {safe_title}{ext}")
            
            return filepath, a_key, a_iv, ext

        except Exception as e:
            # Clean up the partial file if it fails or gets canceled
            safe_unlink(temp_filepath, self.logger)
            self.logger(f"Cleaned up partial file: {temp_filepath}")
            raise e