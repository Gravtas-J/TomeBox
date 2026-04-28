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

    def extract_embedded_cover(self, filepath, output_path):
            """Extracts embedded cover art from an audio file using FFmpeg."""
            import os
            from core.utils.process_runner import ProcessRunner

            cmd = [
                "ffmpeg", "-y",
                "-i", filepath,
                "-an",             # Skip audio processing entirely
                "-vcodec", "copy", # Copy the image stream exactly as it is
                output_path
            ]

            try:
                result = ProcessRunner.run_blocking(cmd, capture_output=True)
                # Verify FFmpeg actually produced a valid, non-empty image file
                return result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0
            except Exception as e:
                if self.logger:
                    self.logger(f"Failed to extract embedded cover for {filepath}: {e}")
                return False
        """Extracts embedded cover art from an audio file using FFmpeg."""
        import os
        from core.utils.process_runner import ProcessRunner

        cmd = [
            "ffmpeg", "-y",
            "-i", filepath,
            "-an",             # Skip audio processing entirely
            "-vcodec", "copy", # Copy the image stream exactly as it is
            output_path
        ]

        try:
            result = ProcessRunner.run_blocking(cmd, capture_output=True)
            # Verify FFmpeg actually produced a valid, non-empty image file
            return result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0
        except Exception as e:
            if self.logger:
                self.logger(f"Failed to extract embedded cover for {filepath}: {e}")
            return False

    def search_google_books(self, query):
        """Helper to fetch search results from Google Books."""
        import requests
        results = []
        try:
            url = f"https://www.googleapis.com/books/v1/volumes?q=intitle:{requests.utils.quote(query)}&maxResults=5"
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                for item in resp.json().get("items", []):
                    vol = item.get("volumeInfo", {})
                    results.append({
                        "title": vol.get("title", "Unknown Title"),
                        "authors": [{"name": a} for a in vol.get("authors", ["Unknown Author"])],
                        "asin": "GB_" + item.get("id", ""),  # Use Volume ID as a fake ASIN
                        "source": "Google"
                    })
        except Exception as e:
            if hasattr(self, 'logger'): self.logger(f"Google Search Error: {e}")
        return results

    def search_catalog(self, filepath, query):
        """Searches both Audible and Google Books for matches."""
        import threading
        def worker():
            products = []
            
            # 1. Try Audible (if logged in)
            if getattr(self, 'api', None) and self.api.auth:
                try:
                    import audible
                    client = audible.Client(auth=self.api.auth)
                    resp = client.get("1.0/catalog/products", title=query, response_groups="product_attrs", num_results=5)
                    for p in resp.get("products", []):
                        p["source"] = "Audible"
                        products.append(p)
                except Exception as e:
                    if hasattr(self, 'logger'): self.logger(f"Audible search failed: {e}")
            
            # 2. Add Google Books results
            products.extend(self.search_google_books(query))
            
            if hasattr(self, 'on_search_complete') and self.on_search_complete:
                self.on_search_complete(filepath, products)
                
        threading.Thread(target=worker, daemon=True).start()

    def apply_scraped_metadata(self, filepath, selected_asin):
        """Fetches the final cover/details from the chosen source and embeds it additively."""
        import threading
        def worker():
            import requests
            import os
            from core.utils.process_runner import ProcessRunner
            
            local_data = self.library_manager.local_library.get(filepath, {})
            
            # Read existing values
            title = local_data.get("title", os.path.basename(filepath))
            authors = local_data.get("authors", "Unknown Author")
            series = local_data.get("series", "")
            old_asin = local_data.get("asin")  # Capture the old ASIN for cleanup
            
            cover_path = os.path.join(self.covers_dir, f"{selected_asin}.jpg")
            
            # Logic flags to determine if we should overwrite
            title_is_filename = title == os.path.basename(filepath) or title.endswith(('.m4b', '.mp3', '.aax', '.aaxc'))
            authors_is_unknown = authors in ["", "Unknown", "Unknown Author", "Local File"]
            
            try:
                # --- GOOGLE BOOKS ROUTING ---
                if str(selected_asin).startswith("GB_"):
                    vol_id = selected_asin.replace("GB_", "")
                    url = f"https://www.googleapis.com/books/v1/volumes/{vol_id}"
                    resp = requests.get(url, timeout=5)
                    if resp.status_code == 200:
                        vol = resp.json().get("volumeInfo", {})
                        
                        api_title = vol.get("title", "")
                        if api_title and title_is_filename:
                            title = api_title
                            
                        api_authors = ", ".join(vol.get("authors", ["Unknown Author"]))
                        if api_authors != "Unknown Author" and authors_is_unknown:
                            authors = api_authors
                        
                        images = vol.get("imageLinks", {})
                        cover_url = images.get("thumbnail") or images.get("smallThumbnail")
                        if cover_url:
                            cover_url = cover_url.replace("http:", "https:")
                            img_data = requests.get(cover_url, timeout=5).content
                            with open(cover_path, "wb") as f:
                                f.write(img_data)
                                
                # --- AUDIBLE ROUTING ---
                elif getattr(self, 'api', None) and self.api.auth:
                    import audible
                    client = audible.Client(auth=self.api.auth)
                    
                    resp = client.get(f"1.0/catalog/products/{selected_asin}", response_groups="media,product_attrs,series")
                    product = resp.get("product", {})
                    
                    api_title = product.get("title", "")
                    if api_title and title_is_filename:
                        title = api_title
                        
                    raw_authors = product.get("authors", [])
                    api_authors = ", ".join([a.get("name", "") for a in raw_authors if isinstance(a, dict)])
                    if api_authors and authors_is_unknown:
                        authors = api_authors
                        
                    raw_series = product.get("series", [])
                    if raw_series and not series:
                        series = ", ".join([f"{s.get('title')} (Bk {s.get('sequence', '')})" for s in raw_series if isinstance(s, dict) and s.get("title")])
                    
                    images = product.get("product_images", {})
                    image_url = images.get("500") or images.get("252")
                    if image_url:
                        img_data = requests.get(image_url).content
                        with open(cover_path, "wb") as f:
                            f.write(img_data)

                # --- COVER CLEANUP & RENAME ---
                if old_asin and old_asin != selected_asin:
                    old_cover_path = os.path.join(self.covers_dir, f"{old_asin}.jpg")
                    if os.path.exists(old_cover_path):
                        if os.path.exists(cover_path):
                            # We downloaded a new official cover, so delete the old orphaned one
                            try: os.remove(old_cover_path)
                            except: pass
                        else:
                            # We didn't get a new cover, so rename the old one to match the new ASIN
                            try: os.rename(old_cover_path, cover_path)
                            except: pass

                # 3. Save to database
                local_data["title"] = title
                local_data["authors"] = authors
                local_data["asin"] = selected_asin
                if series:
                    local_data["series"] = series
                    
                self.library_manager.local_library[filepath] = local_data
                self.library_manager.db.save_local_db(self.library_manager.local_library)

                # 4. Embed into file using FFmpeg
                if os.path.exists(cover_path) and filepath.endswith(('.m4b', '.mp3')):
                    temp_out = filepath + ".tmp.m4b"
                    
                    cmd = [
                        "ffmpeg", "-y", "-i", filepath, "-i", cover_path,
                        "-map", "0", "-map", "1", "-c", "copy",
                        "-disposition:v", "attached_pic",
                        "-metadata", f"title={title}",
                        "-metadata", f"artist={authors}"
                    ]
                    
                    if series:
                        cmd.extend(["-metadata", f"show={series}", "-metadata", f"series={series}"])
                        
                    cmd.append(temp_out)
                    
                    res = ProcessRunner.run_blocking(cmd)
                    if res.returncode == 0 and os.path.exists(temp_out):
                        os.replace(temp_out, filepath)

                if hasattr(self, 'on_apply_complete') and self.on_apply_complete:
                    self.on_apply_complete(filepath, title)

            except Exception as e:
                if hasattr(self, 'logger'): self.logger(f"Apply Metadata Error: {e}")
                if hasattr(self, 'on_error') and self.on_error:
                    self.on_error("Failed to fetch and apply metadata. Check connection.")

        threading.Thread(target=worker, daemon=True).start()
    def fetch_from_google_books(self, title):
            """Fetches basic metadata and cover URL from Google Books API."""
            import requests
            try:
                query = requests.utils.quote(title)
                url = f"https://www.googleapis.com/books/v1/volumes?q=intitle:{query}&maxResults=1"
                resp = requests.get(url, timeout=5)
                
                if resp.status_code == 200:
                    data = resp.json()
                    items = data.get("items", [])
                    if items:
                        volume_info = items[0].get("volumeInfo", {})
                        authors = ", ".join(volume_info.get("authors", ["Unknown Author"]))
                        
                        image_links = volume_info.get("imageLinks", {})
                        cover_url = image_links.get("thumbnail") or image_links.get("smallThumbnail")
                        
                        if cover_url:
                            # Google Books often returns http. Force https to prevent redirect failures.
                            cover_url = cover_url.replace("http:", "https:")
                        
                        return authors, cover_url
            except Exception as e:
                if hasattr(self, 'logger'): 
                    self.logger(f"Google Books API error: {e}")
                
            return None, None

        threading.Thread(target=worker, daemon=True).start()
    def fetch_from_google_books(self, title):
            """Fetches basic metadata and cover URL from Google Books API."""
            import requests
            try:
                query = requests.utils.quote(title)
                url = f"https://www.googleapis.com/books/v1/volumes?q=intitle:{query}&maxResults=1"
                resp = requests.get(url, timeout=5)
                
                if resp.status_code == 200:
                    data = resp.json()
                    items = data.get("items", [])
                    if items:
                        volume_info = items[0].get("volumeInfo", {})
                        authors = ", ".join(volume_info.get("authors", ["Unknown Author"]))
                        
                        image_links = volume_info.get("imageLinks", {})
                        cover_url = image_links.get("thumbnail") or image_links.get("smallThumbnail")
                        
                        if cover_url:
                            # Google Books often returns http. Force https to prevent redirect failures.
                            cover_url = cover_url.replace("http:", "https:")
                        
                        return authors, cover_url
            except Exception as e:
                if hasattr(self, 'logger'): 
                    self.logger(f"Google Books API error: {e}")
                
            return None, None
    
    def fetch_display_metadata(self, filepath):
        """Fetches the cover art and author info for the side panel."""
        import os
        import threading
        import requests
        import hashlib
        try:
            import audible
        except ImportError:
            pass

        def worker():
            local_data = self.library_manager.local_library.get(filepath, {})
            title = local_data.get("title", "")
            asin = local_data.get("asin")
            authors = local_data.get("authors", "Unknown Author")

            # 1. Try to find existing data in the cloud cache
            for item in getattr(self.library_manager, 'cloud_items', []):
                if item.get("title") == title or item.get("asin") == asin:
                    asin = item.get("asin")
                    raw_authors = item.get("authors", [])
                    if raw_authors:
                        authors = ", ".join([a.get("name", "") for a in raw_authors if isinstance(a, dict)])
                    break

            cover_path = None
            if asin:
                test_path = os.path.join(self.covers_dir, f"{asin}.jpg")
                if os.path.exists(test_path):
                    cover_path = test_path

            # 2. Try Audible API (Only if no local cover, real ASIN, and logged in)
            if not cover_path and asin and not str(asin).startswith("LOCAL_") and getattr(self, 'api', None) and self.api.auth:
                try:
                    client = audible.Client(auth=self.api.auth)
                    resp = client.get(f"1.0/catalog/products/{asin}", response_groups="media,product_attrs")
                    product = resp.get("product", {})
                    
                    if authors == "Unknown Author":
                        raw_authors = product.get("authors", [])
                        authors = ", ".join([a.get("name", "") for a in raw_authors if isinstance(a, dict)])
                    
                    images = product.get("product_images", {})
                    image_url = images.get("500") or images.get("252")
                    
                    if image_url:
                        img_data = requests.get(image_url).content
                        dl_path = os.path.join(self.covers_dir, f"{asin}.jpg")
                        with open(dl_path, "wb") as f:
                            f.write(img_data)
                        cover_path = dl_path
                except Exception as e:
                    if hasattr(self, 'logger'): self.logger(f"Audible Cover Fetch Error: {e}")

            # 3. Try to rip embedded cover directly from the file
            if not cover_path and os.path.exists(filepath):
                file_hash = hashlib.md5(filepath.encode()).hexdigest()[:10]
                embedded_cover_path = os.path.join(self.covers_dir, f"LOCAL_{file_hash}.jpg")
                
                if os.path.exists(embedded_cover_path):
                    cover_path = embedded_cover_path
                elif hasattr(self, 'extract_embedded_cover') and self.extract_embedded_cover(filepath, embedded_cover_path):
                    cover_path = embedded_cover_path
                    if not asin:
                        local_data["asin"] = f"LOCAL_{file_hash}"
                        self.library_manager.local_library[filepath] = local_data
                        self.library_manager.db.save_local_db(self.library_manager.local_library)

            # 4. PUBLIC API FALLBACK: Google Books
            if not cover_path and title:
                api_authors, api_cover_url = self.fetch_from_google_books(title)
                
                if api_authors and authors in ["Unknown Author", "Local File"]:
                    authors = api_authors
                    
                if api_cover_url:
                    try:
                        img_data = requests.get(api_cover_url, timeout=5).content
                        safe_id = asin if asin and not str(asin).startswith("LOCAL_") else "GB_" + hashlib.md5(title.encode()).hexdigest()[:10]
                        dl_path = os.path.join(self.covers_dir, f"{safe_id}.jpg")
                        
                        with open(dl_path, "wb") as f:
                            f.write(img_data)
                        cover_path = dl_path
                        
                        # Save the new ASIN and authors to the database
                        if not asin or str(asin).startswith("LOCAL_"):
                            local_data["asin"] = safe_id
                            if api_authors and local_data.get("authors") in ["Unknown", "Unknown Author"]:
                                local_data["authors"] = authors
                            self.library_manager.local_library[filepath] = local_data
                            self.library_manager.db.save_local_db(self.library_manager.local_library)
                    except Exception as e:
                        if hasattr(self, 'logger'): self.logger(f"Google Books cover download failed: {e}")

            # 5. Push to UI
            if hasattr(self, 'on_display_ready') and self.on_display_ready:
                if cover_path:
                    self.on_display_ready(filepath, cover_path, authors, "")
                else:
                    self.on_display_ready(filepath, None, authors, "No Cover Art Found")

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