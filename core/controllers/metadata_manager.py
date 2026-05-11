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
from core.utils.text import format_series_list
from core.events import default_bus
class MetadataManager:
    def __init__(self, api_client, library_manager, logger, covers_dir, callbacks, thread_pool, start_workers=True, event_bus=None):
        self.api = api_client
        self.library_manager = library_manager
        self.logger = logger
        self.covers_dir = covers_dir
        self.thread_pool = thread_pool
        self.start_workers = start_workers  # Store the flag
        
        # Callbacks to update the UI
        self.on_search_complete = callbacks.get("on_search_complete")
        self.on_apply_complete = callbacks.get("on_apply_complete")
        self.on_display_ready = callbacks.get("on_display_ready")
        self.on_error = callbacks.get("on_error")

        self.event_bus = event_bus or default_bus

        callbacks = callbacks or {}
        if callbacks.get("on_search_complete"):
            self.event_bus.subscribe("metadata.search_complete", lambda **kw: callbacks["on_search_complete"](kw.get("filepath"), kw.get("products")))
        if callbacks.get("on_apply_complete"):
            self.event_bus.subscribe("metadata.apply_complete", lambda **kw: callbacks["on_apply_complete"](kw.get("filepath"), kw.get("title")))
        if callbacks.get("on_display_ready"):
            self.event_bus.subscribe("metadata.display_ready", lambda **kw: callbacks["on_display_ready"](kw.get("filepath"), kw.get("cover_path"), kw.get("authors"), kw.get("msg")))
        if callbacks.get("on_error"):
            self.event_bus.subscribe("metadata.error", lambda **kw: callbacks["on_error"](kw.get("error_msg")))

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
            if hasattr(self, 'logger'):
                self.logger(f"Failed to extract embedded cover for {filepath}: {e}")
            return False

    def search_google_books(self, query):
        """Helper to fetch search results from Google Books."""
        import requests
        results = []
        try:
            url = f"https://www.googleapis.com/books/v1/volumes?q={requests.utils.quote(query)}&maxResults=5"
            resp = requests.get(url, timeout=5)
            
            if resp.status_code == 200:
                for item in resp.json().get("items", []):
                    vol = item.get("volumeInfo", {})
                    images = vol.get("imageLinks", {})
                    results.append({
                        "title": vol.get("title", "Unknown Title"),
                        "authors": [{"name": a} for a in vol.get("authors", ["Unknown Author"])],
                        "asin": "GB_" + item.get("id", ""),
                        "source": "Google",
                        "cover_url": images.get("thumbnail") or images.get("smallThumbnail")
                    })
            elif resp.status_code == 429:
                if hasattr(self, 'logger'): self.logger("Google Books API rate limit reached (HTTP 429).")
            else:
                if hasattr(self, 'logger'): self.logger(f"Google Books API returned status code: {resp.status_code}")
                
        except requests.exceptions.Timeout:
            if hasattr(self, 'logger'): self.logger("Google Books search timed out.")
        except requests.exceptions.RequestException as e:
            if hasattr(self, 'logger'): self.logger(f"Google Books network error: {e}")
        except Exception as e:
            if hasattr(self, 'logger'): self.logger(f"Unexpected Google Books search error: {e}")
            
        return results

    def search_catalog(self, filepath, query):
        """Searches Audible -> Google Books -> Local tags in a strict fallback cascade."""
        import threading
        def worker():
            products = []
            audible_failed = False
            
            # 1. Try Audible (if logged in)
            if getattr(self, 'api', None) and self.api.auth:
                try:
                    # Reroute to use the api_client so we catch the new structured errors
                    raw_products = self.api.search_catalog(query, num_results=5)
                    for p in raw_products:
                        # ASIN Parsing Update: Check 'asin', fallback to 'id' if storefront structure changed
                        asin = p.get("asin") or p.get("id")
                        if asin:
                            p["asin"] = asin
                            p["source"] = "Audible"
                            products.append(p)
                except Exception as e:
                    audible_failed = True
                    err_type = type(e).__name__
                    if hasattr(self, 'logger'): self.logger(f"Audible search failed ({err_type}): {e}")
                    
                    if "RateLimitError" in err_type:
                        self.event_bus.publish("metadata.error", error_msg="Audible API rate limit reached. Falling back to alternative sources.")

            # 2. Strict Fallback: Try Google Books if Audible failed or returned 0 results
            if audible_failed or not products:
                products.extend(self.search_google_books(query))
            
            # 3. Strict Fallback: Local Tag Extraction via converter.py/ffprobe
            if not products:
                try:
                    from core.converter import AudioConverter
                    converter = AudioConverter(self.logger)
                    data = converter.get_metadata_and_chapters(filepath)
                    tags = data.get("format", {}).get("tags", {})
                    
                    if tags:
                        title = tags.get("title", os.path.basename(filepath))
                        artist = tags.get("artist") or tags.get("album_artist") or "Unknown Author"
                        
                        products.append({
                            "title": title,
                            "authors": [{"name": artist}],
                            "asin": "LOCAL_TAGS",
                            "source": "Local File Tags"
                        })
                except Exception as e:
                    if hasattr(self, 'logger'): self.logger(f"Local tag extraction failed: {e}")

            if hasattr(self, 'on_search_complete') and self.on_search_complete:
                self.event_bus.publish("metadata.search_complete", filepath=filepath, products=products)
            pass
        if self.start_workers:
            self.thread_pool.submit(worker, task_type="api")

    def apply_scraped_metadata(self, filepath, selected_asin, fields_to_apply=None):
        """Fetches the final cover/details from the chosen source and embeds it additively."""
        if fields_to_apply is None:
            fields_to_apply = {"title": True, "author": True, "series": True, "cover": True}
            
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
            old_asin = local_data.get("asin")  
            
            cover_path = os.path.join(self.covers_dir, f"{selected_asin}.jpg")
            
            try:
                # --- GOOGLE BOOKS ROUTING ---
                if str(selected_asin).startswith("GB_"):
                    vol_id = selected_asin.replace("GB_", "")
                    url = f"https://www.googleapis.com/books/v1/volumes/{vol_id}"
                    resp = requests.get(url, timeout=5)
                    if resp.status_code == 200:
                        vol = resp.json().get("volumeInfo", {})
                        
                        api_title = vol.get("title", "")
                        if api_title and fields_to_apply.get("title", True):
                            title = api_title
                            
                        api_authors = ", ".join(vol.get("authors", ["Unknown Author"]))
                        if api_authors != "Unknown Author" and fields_to_apply.get("author", True):
                            authors = api_authors
                        
                        if fields_to_apply.get("cover", True):
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
                    if api_title and fields_to_apply.get("title", True):
                        title = api_title
                        
                    raw_authors = product.get("authors", [])
                    api_authors = ", ".join([a.get("name", "") for a in raw_authors if isinstance(a, dict)])
                    if api_authors and fields_to_apply.get("author", True):
                        authors = api_authors
                        
                    # --- IMPROVED SERIES PARSING ---
                    raw_series = product.get("series", [])
                    if raw_series and fields_to_apply.get("series", True):
                        formatted = format_series_list(raw_series)
                        if formatted:
                            series = formatted
                            
                    # --- IMPROVED DURATION PARSING ---
                    duration_min = product.get("runtime_length_min")
                    if duration_min:
                        local_data["duration_min"] = duration_min
                    
                    if fields_to_apply.get("cover", True):
                        images = product.get("product_images", {})
                        image_url = images.get("500") or images.get("252")
                        if image_url:
                            img_data = requests.get(image_url, timeout=10).content
                            with open(cover_path, "wb") as f:
                                f.write(img_data)

                # --- COVER CLEANUP & RENAME ---
                if old_asin and old_asin != selected_asin:
                    old_cover_path = os.path.join(self.covers_dir, f"{old_asin}.jpg")
                    if os.path.exists(old_cover_path):
                        if os.path.exists(cover_path):
                            try: os.remove(old_cover_path)
                            except: pass
                        else:
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
                if filepath.endswith(('.m4b', '.mp3')):
                    temp_out = filepath + ".tmp.m4b"
                    
                    cmd = [
                        "ffmpeg", "-y", "-i", filepath
                    ]
                    
                    # Only map new cover if we applied a new cover and it exists
                    if fields_to_apply.get("cover", True) and os.path.exists(cover_path):
                        cmd.extend(["-i", cover_path, "-map", "0", "-map", "1", "-c", "copy", "-disposition:v", "attached_pic"])
                    else:
                        cmd.extend(["-map", "0", "-c", "copy"])
                        
                    cmd.extend([
                        "-metadata", f"title={title}",
                        "-metadata", f"artist={authors}"
                    ])
                    
                    if series:
                        cmd.extend(["-metadata", f"show={series}", "-metadata", f"series={series}"])
                        
                    cmd.append(temp_out)
                    
                    res = ProcessRunner.run_blocking(cmd)
                    if res.returncode == 0 and os.path.exists(temp_out):
                        os.replace(temp_out, filepath)

                if hasattr(self, 'on_apply_complete') and self.on_apply_complete:
                    self.event_bus.publish("metadata.apply_complete", filepath=filepath, title=title)

            except Exception as e:
                if hasattr(self, 'logger'): self.logger(f"Apply Metadata Error: {e}")
                self.event_bus.publish("metadata.error", error_msg="Failed to fetch and apply metadata. Check connection.")
            pass
        if self.start_workers:
            self.thread_pool.submit(worker, task_type="api")

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
                        img_data = requests.get(image_url, timeout=10).content
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
            if cover_path:
                self.event_bus.publish("metadata.display_ready", filepath=filepath, cover_path=cover_path, authors=authors, msg="")
            else:
                self.event_bus.publish("metadata.display_ready", filepath=filepath, cover_path=None, authors=authors, msg="No Cover Art Found")
            pass
        if self.start_workers:
            self.thread_pool.submit(worker, task_type="api")

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
                    
        self.thread_pool.submit(worker, task_type="api")