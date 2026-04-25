import os
import threading
import subprocess
import requests
import shutil
try:
    import audible
except ImportError:
    pass
from core.utils.process_runner import ProcessRunner

class MetadataManager:
    def __init__(self, api_client, library_manager, logger, covers_dir, callbacks):
        self.api = api_client
        self.library_manager = library_manager
        self.logger = logger
        self.covers_dir = covers_dir
        
        # Callbacks to update the UI
        self.on_search_complete = callbacks.get("on_search_complete")
        self.on_apply_complete = callbacks.get("on_apply_complete")
        self.on_display_ready = callbacks.get("on_display_ready")
        self.on_error = callbacks.get("on_error")

    def search_catalog(self, filepath, query):
        """Searches Audible and returns a list of matching products to the UI."""
        def worker():
            try:
                client = audible.Client(auth=self.api.auth)
                resp = client.get("1.0/catalog/products", title=query, num_results=5, response_groups="product_desc,product_attrs,contributors")
                products = resp.get("products", [])
                
                if self.on_search_complete:
                    self.on_search_complete(filepath, products)
            except Exception as e:
                self.logger(f"Scrape search error: {e}")
                if self.on_error:
                    self.on_error(f"Search Failed: {str(e)}")
                    
        threading.Thread(target=worker, daemon=True).start()

    def apply_scraped_metadata(self, filepath, asin):
        """Fetches full metadata, downloads the cover, updates the DB, and embeds tags via FFmpeg."""
        def worker():
            try:
                client = audible.Client(auth=self.api.auth)
                resp = client.get(f"1.0/catalog/products/{asin}", response_groups="product_desc,product_attrs,contributors,media,series")
                product = resp.get("product", {})
                
                if not product:
                    raise Exception("Audible API returned no data for this ASIN.")
                    
                title = product.get("title", "Unknown Title")
                
                raw_authors = product.get("authors", [])
                authors = ", ".join([a.get("name", "") for a in raw_authors])
                
                raw_series = product.get("series", [])
                series_list = []
                for s in raw_series:
                    if isinstance(s, dict) and s.get("title"):
                        series_list.append(f"{s.get('title')} (Bk {s.get('sequence', '')})")
                series_str = ", ".join(series_list) if series_list else ""
                
                duration_min = product.get("runtime_length_min", 0)

                cover_path = os.path.join(self.covers_dir, f"{asin}.jpg")
                images = product.get("product_images", {})
                img_url = images.get("500") or images.get("252")
                
                if img_url:
                    img_resp = requests.get(img_url, timeout=10)
                    if img_resp.status_code == 200:
                        with open(cover_path, "wb") as f:
                            f.write(img_resp.content)

                # Update the Database
                data = self.library_manager.local_library.get(filepath, {})
                data["title"] = title
                data["authors"] = authors
                data["series"] = series_str
                data["duration_min"] = duration_min
                data["asin"] = asin
                self.library_manager.local_library[filepath] = data
                self.library_manager.db.save_local_db(self.library_manager.local_library)

                # Embed tags if M4B/MP3
                ext = data.get("format", "").upper()
                if ext in ["M4B", "MP3"]:
                    base_name, original_ext = os.path.splitext(filepath)
                    temp_path = f"{base_name}_temp{original_ext}"
                    
                    cmd = ["ffmpeg", "-y", "-i", filepath]
                    
                    if os.path.exists(cover_path):
                        cmd.extend(["-i", cover_path, "-map", "0:a", "-map", "1:v", "-c:v", "mjpeg", "-disposition:v", "attached_pic"])
                    else:
                        cmd.extend(["-map", "0:a"])
                        
                    cmd.extend([
                        "-c:a", "copy",
                        "-metadata", f"title={title}",
                        "-metadata", f"album={title}",
                        "-metadata", f"artist={authors}",
                        "-metadata", f"album_artist={authors}",
                        "-metadata", "genre=Audiobook",
                        temp_path
                    ])
                    
                    res = ProcessRunner.run_blocking(cmd, capture_output=False, stderr=subprocess.PIPE)
                    
                    if res.returncode == 0:
                        shutil.move(temp_path, filepath)
                    else:
                        if os.path.exists(temp_path): os.remove(temp_path)
                        self.logger(f"FFmpeg Embed Error: {res.stderr}")
                        raise Exception("FFmpeg failed to embed metadata. Check log for details.")

                if self.on_apply_complete:
                    self.on_apply_complete(filepath, title)
                    
            except Exception as e:
                self.logger(f"Scrape Error: {e}")
                if self.on_error:
                    self.on_error(str(e))
                    
        threading.Thread(target=worker, daemon=True).start()

    def fetch_display_metadata(self, filepath):
        """Fetches the cover art and author info for the side panel."""
        def worker():
            local_data = self.library_manager.local_library.get(filepath, {})
            title = local_data.get("title", "")
            asin = local_data.get("asin")
            authors = ""

            # 1. Try to find existing data in the cloud cache
            for item in getattr(self.library_manager, 'cloud_items', []):
                if item.get("title") == title or item.get("asin") == asin:
                    asin = item.get("asin")
                    raw_authors = item.get("authors", [])
                    authors = ", ".join([a.get("name", "") for a in raw_authors if isinstance(a, dict)])
                    break
            
            if not asin:
                if self.on_display_ready:
                    self.on_display_ready(filepath, None, authors, "Metadata Unavailable")
                return

            cover_path = os.path.join(self.covers_dir, f"{asin}.jpg")

            # 2. Return local cover if it exists
            if os.path.exists(cover_path):
                if self.on_display_ready:
                    self.on_display_ready(filepath, cover_path, authors, "")
                return 
                
            # 3. Fallback to fetching it from Audible dynamically
            if not self.api.auth:
                return
                
            try:
                client = audible.Client(auth=self.api.auth)
                resp = client.get(f"1.0/catalog/products/{asin}", response_groups="media,product_attrs")
                product = resp.get("product", {})
                
                if not authors:
                    raw_authors = product.get("authors", [])
                    authors = ", ".join([a.get("name", "") for a in raw_authors if isinstance(a, dict)])
                
                images = product.get("product_images", {})
                image_url = images.get("500") or images.get("252")
                
                if image_url:
                    img_data = requests.get(image_url).content
                    with open(cover_path, "wb") as f:
                        f.write(img_data)
                        
                    if self.on_display_ready:
                        self.on_display_ready(filepath, cover_path, authors, "")
                else:
                    if self.on_display_ready:
                        self.on_display_ready(filepath, None, authors, "No Cover Art Found")
                    
            except Exception as e:
                self.logger(f"Metadata Fetch Error: {e}")
                if self.on_display_ready:
                    self.on_display_ready(filepath, None, authors, "Failed to load metadata")

        threading.Thread(target=worker, daemon=True).start()

    def sync_missing_covers(self, on_complete_cb=None):
        """Background worker to download missing covers for cloud items."""
        def worker():
            self.logger("Starting background cover sync...")
            covers_downloaded = 0
            
            for item in getattr(self.library_manager, 'cloud_items', []):
                asin = item.get("asin")
                if not asin: continue
                    
                cover_path = os.path.join(self.covers_dir, f"{asin}.jpg")
                if os.path.exists(cover_path):
                    continue 
                    
                images = item.get("product_images", {})
                img_url = images.get("500") or images.get("252")
                
                if img_url:
                    try:
                        img_data = requests.get(img_url, timeout=10).content
                        with open(cover_path, "wb") as f:
                            f.write(img_data)
                        covers_downloaded += 1
                    except requests.RequestException as e:
                        self.logger(f"Network error downloading cover for {asin}: {e}")
                    except Exception as e:
                        self.logger(f"Unexpected error saving cover for {asin}: {e}")
                        
            if covers_downloaded > 0:
                self.logger(f"Downloaded {covers_downloaded} new covers.")
                if on_complete_cb:
                    on_complete_cb()
                    
        threading.Thread(target=worker, daemon=True).start()