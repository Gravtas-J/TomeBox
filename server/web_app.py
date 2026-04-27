import os
import json
import subprocess
from fastapi import FastAPI, Request, HTTPException, status, Request
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from core.utils.paths import get_resource_path
def create_server_app(tomebox):
    api = FastAPI()
    static_dir = get_resource_path("server", "static")
    api.mount("/static", StaticFiles(directory=static_dir), name="static")
    if not hasattr(tomebox, '_web_task_state'):
        tomebox._web_task_state = {
            "downloads": {"active_asin": None, "progress": 0, "status": "Idle"},
            "conversions": {"active_path": None, "progress": 0, "status": "Idle"}
        }
    @api.middleware("http")
    async def token_auth_middleware(request: Request, call_next):
        # Allow static files, auth, and pairing endpoints to bypass entirely
        if request.url.path in ["/auth", "/favicon.ico", "/pairing"] or request.url.path.startswith("/static"):
            return await call_next(request)
        
        client_ip = request.client.host if request.client else None
        is_localhost = client_ip in ("127.0.0.1", "::1")
        
        # /desktop is localhost-only — reject networked attempts so the mobile UI handles them
        if request.url.path == "/desktop":
            if is_localhost:
                return await call_next(request)
            else:
                # Networked devices should use the mobile companion at /, not /desktop
                return RedirectResponse(url="/", status_code=302)
        
        # Normal token check for all other endpoints
        server_token = tomebox.db.load_settings().get("auth_token")
        client_token = request.cookies.get("tomebox_token")
        
        if not client_token:
            auth_header = request.headers.get("Authorization")
            if auth_header and auth_header.startswith("Bearer "):
                client_token = auth_header.split(" ")[1]
        
        if client_token != server_token:
            return JSONResponse(
                status_code=401,
                content={"detail": "Unauthorized: Invalid or missing session."}
            )
        
        return await call_next(request)

    @api.get("/api/pairing-info")
    def get_pairing_info(request: Request):
        """Returns pairing URL info as JSON for the desktop UI."""
        import socket
        
        server_token = tomebox.db.load_settings().get("auth_token")
        
        # Find the host machine's primary LAN IP — not the request's Host header,
        # which would be 'localhost' when called from the desktop UI itself
        local_ip = "127.0.0.1"
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
        except Exception:
            pass
        
        # Use the same port the request came in on
        port = request.url.port or 8000
        pairing_url = f"http://{local_ip}:{port}/auth?token={server_token}"
        
        return {
            "pairing_url": pairing_url,
            "token": server_token
        }

    @api.get("/api/profiles/active")
    def get_active_profile():
        """Returns the currently active profile name."""
        return {
            "active": tomebox.settings.get("active_profile", "Main"),
            "available": tomebox.settings.get("profiles", ["Main"])
        }


    @api.post("/api/library/refresh")
    def refresh_library():
        """Triggers a cloud library sync from Audible."""
        if not tomebox.api_client.is_authenticated():
            raise HTTPException(status_code=401, detail="Not signed into Audible")
        
        try:
            # Reuse the existing fetch logic
            tomebox.library_manager.fetch_cloud_library()
            return {
                "status": "success",
                "items_count": len(tomebox.library_manager.cloud_items)
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Refresh failed: {str(e)}")

    @api.get("/auth")
    def authenticate_device(token: str):
        server_token = tomebox.db.load_settings().get("auth_token")
        
        if token == server_token:
            # Create a redirect back to the clean root interface
            redirect = RedirectResponse(url="/", status_code=302)
            
            # Set the secure cookie
            # httponly=True prevents malicious JavaScript from reading the token
            redirect.set_cookie(
                key="tomebox_token", 
                value=token, 
                httponly=True, 
                max_age=31536000, # Valid for 1 year
                samesite="lax"
            )
            return redirect
        else:
            return HTMLResponse("<h1>Invalid Pairing Token</h1><p>Please scan the QR code from the TomeBox desktop app again.</p>", status_code=401)
    
    @api.get("/", response_class=HTMLResponse)
    def web_interface():
        html_path = get_resource_path("server", "mobile_ui.html")
        try:
            with open(html_path, "r", encoding="utf-8") as f:
                return HTMLResponse(content=f.read())
        except FileNotFoundError:
            return HTMLResponse(content="<h1>Error: mobile_ui.html not found</h1>", status_code=404)

    @api.get("/api/profiles")
    def get_profiles():
        profs = tomebox.settings.get("profiles")
        if not profs or not isinstance(profs, list): return ["Main"]
        return profs

    @api.get("/api/last_played/{profile}")
    def get_last_played(profile: str):
        path = tomebox.settings.get(f"last_played_{profile}")
        if path and path in tomebox.library_manager.local_library:
            return {"path": path}
        return {"path": None}

    @api.get("/desktop", response_class=HTMLResponse)
    def desktop_landing(request: Request):
        """Localhost-only landing page for the new web UI. Auto-pairs on first visit."""
        html_path = get_resource_path("server", "desktop_ui.html")
        
        try:
            with open(html_path, "r", encoding="utf-8") as f:
                response = HTMLResponse(content=f.read())
        except FileNotFoundError:
            return HTMLResponse(content="<h1>Error: desktop_ui.html not found</h1>", status_code=404)
        
        # Set the auth cookie so subsequent API calls succeed without manual pairing.
        # This is safe because the middleware already verified this is a localhost connection.
        server_token = tomebox.db.load_settings().get("auth_token")
        response.set_cookie(
            key="tomebox_token",
            value=server_token,
            httponly=True,
            max_age=31536000,
            samesite="lax"
        )
        return response

    @api.get("/api/library")
    def get_web_library():
        enriched_lib = {}
        shelves_db = tomebox.settings.get("shelves_db", {})
        
        master_metadata = {}
        data_dir = os.path.join(tomebox.base_dir, "data")
        
        if os.path.exists(data_dir):
            for f in os.listdir(data_dir):
                if f.startswith("cloud_") and f.endswith(".json") or f == "cloud_cache.json":
                    try:
                        with open(os.path.join(data_dir, f), "r") as file:
                            for item in json.load(file):
                                if item.get("asin"): master_metadata[item["asin"]] = item
                                if item.get("title"): master_metadata[item["title"]] = item
                    except json.JSONDecodeError as e: 
                        print(f"[ERROR] Corrupted cloud cache file {f}: {e}")
                    except OSError as e:
                        print(f"[ERROR] File access error on {f}: {e}")

        for item in getattr(tomebox.library_manager, 'cloud_items', []):
            if item.get("asin"): master_metadata[item["asin"]] = item
            if item.get("title"): master_metadata[item["title"]] = item

        for path, data in tomebox.library_manager.local_library.items():
            item_copy = dict(data)
            asin = item_copy.get("asin")
            item_copy["shelves"] = shelves_db.get(asin, [])
            item_copy["download_status"] = "downloaded"

            if "progress" not in item_copy: item_copy["progress"] = {}
                
            existing_auth = item_copy.get("authors", "")
            if isinstance(existing_auth, list):
                item_copy["authors"] = ", ".join([a.get("name", "") if isinstance(a, dict) else str(a) for a in existing_auth])
            
            meta = master_metadata.get(asin) or master_metadata.get(item_copy.get("title"), {})
            if meta:
                if not item_copy.get("authors") or item_copy.get("authors") in ["Unknown", "Unknown Author"]:
                    raw_authors = meta.get("authors", [])
                    item_copy["authors"] = ", ".join([a.get("name", "") if isinstance(a, dict) else str(a) for a in raw_authors])
                if not asin:
                    item_copy["asin"] = meta.get("asin")
                    
            enriched_lib[path] = item_copy
        # Add cloud-only items that aren't yet downloaded
        local_titles = {data.get("title") for data in tomebox.library_manager.local_library.values()}
        local_asins = {data.get("asin") for data in tomebox.library_manager.local_library.values() if data.get("asin")}

        for cloud_item in getattr(tomebox.library_manager, 'cloud_items', []):
            title = cloud_item.get("title")
            asin = cloud_item.get("asin")
            
            # Skip if already in local library (matched by title or ASIN)
            if title in local_titles or asin in local_asins:
                continue
            
            raw_authors = cloud_item.get("authors", [])
            authors_str = ", ".join([
                a.get("name", "") if isinstance(a, dict) else str(a) for a in raw_authors
            ])
            
            # Use a synthetic key since cloud-only items don't have a filepath yet
            enriched_lib[f"cloud:{asin}"] = {
                "title": title,
                "authors": authors_str,
                "asin": asin,
                "format": "",
                "shelves": shelves_db.get(asin, []),
                "download_status": "cloud_only",
                "duration_min": cloud_item.get("runtime_length_min", 0)
            }

        return enriched_lib

    @api.get("/api/cover/{asin}")
    def get_cover(asin: str):
        from core.converter import resolve_cover_path
        
        covers_dir = getattr(tomebox, 'covers_dir', tomebox.base_dir)
        base_path = os.path.join(covers_dir, f"{asin}.jpg")
        
        # Use the same smart resolver the desktop uses
        resolved = resolve_cover_path(base_path, asin)
        if resolved:
            return FileResponse(resolved)
        
        raise HTTPException(status_code=404, detail="Cover not found")

    @api.post("/api/progress")
    async def update_progress(request: Request):
        try:
            data = await request.json()
            path = data.get("path")
            position = data.get("position")
            profile = data.get("profile", "Main")

            # CHANGED: All tomebox.local_library instances updated to tomebox.library_manager.local_library
            if path and path in tomebox.library_manager.local_library:
                if "progress" not in tomebox.library_manager.local_library[path]:
                    tomebox.library_manager.local_library[path]["progress"] = {}
                    
                tomebox.library_manager.local_library[path]["progress"][profile] = position
                tomebox.library_manager.local_library[path]["last_position"] = position
                
                tomebox.settings[f"last_played_{profile}"] = path
                
                tomebox.db.save_settings(tomebox.settings)
                
                # CHANGED: Save the correct dictionary
                tomebox.db.save_local_db(tomebox.library_manager.local_library)

                if getattr(tomebox, 'file_path', None) == path:
                    if tomebox.root:  # Only sync to GUI if GUI exists
                        tomebox.root.after(0, lambda: tomebox.sync_playhead_from_remote(position))
                    
        except Exception as e: 
            print(f"Web Server Sync Error: {e}")
        return {"status": "success"}
    @api.get("/pairing", response_class=HTMLResponse)
    def show_pairing_page(request: Request):
        """Renders a pairing QR code page accessible to already-paired devices."""
        # Allow access if already paired OR if explicitly bypassing for first-time setup
        server_token = tomebox.db.load_settings().get("auth_token")
        client_token = request.cookies.get("tomebox_token")
        
        if client_token != server_token:
            # Not authenticated — show a minimal page explaining how to pair
            return HTMLResponse("""
            <!DOCTYPE html>
            <html><head><title>TomeBox Pairing</title>
            <style>
                body { font-family: -apple-system, sans-serif; background: #121212; color: #e0e0e0;
                    display: flex; align-items: center; justify-content: center; min-height: 100vh;
                    margin: 0; padding: 20px; text-align: center; }
                .box { max-width: 500px; }
                h1 { color: #bb86fc; }
            </style></head>
            <body><div class="box">
                <h1>Pairing Required</h1>
                <p>To pair this device, run the following on the TomeBox server:</p>
                <pre style="background: #1e1e1e; padding: 15px; border-radius: 8px;">tomebox --show-qr</pre>
                <p>Or check the server's startup logs for the pairing URL.</p>
            </div></body></html>
            """, status_code=401)
        
        # Authenticated — show the QR for adding new devices
        import qrcode
        import io
        import base64
        
        # Determine which IP the request came in on so we generate a usable QR
        host = request.headers.get("host", "localhost:8000")
        pairing_url = f"http://{host}/auth?token={server_token}"
        
        qr = qrcode.QRCode(box_size=10, border=2)
        qr.add_data(pairing_url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        qr_b64 = base64.b64encode(buf.getvalue()).decode()
        
        return HTMLResponse(f"""
        <!DOCTYPE html>
        <html><head><title>TomeBox - Add Device</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{ font-family: -apple-system, sans-serif; background: #121212; color: #e0e0e0;
                display: flex; flex-direction: column; align-items: center; justify-content: center;
                min-height: 100vh; margin: 0; padding: 20px; }}
            .box {{ background: #1e1e1e; padding: 30px; border-radius: 12px; text-align: center;
                    max-width: 90%; box-shadow: 0 4px 20px rgba(0,0,0,0.5); }}
            h1 {{ color: #bb86fc; margin-top: 0; }}
            img {{ background: white; padding: 15px; border-radius: 8px; margin: 20px 0; max-width: 280px; }}
            .url {{ background: #2a2a2a; padding: 12px; border-radius: 6px; word-break: break-all;
                    font-family: monospace; font-size: 0.85em; margin-top: 15px; }}
            .back {{ display: inline-block; margin-top: 20px; color: #bb86fc; text-decoration: none; }}
        </style></head>
        <body><div class="box">
            <h1>Add a New Device</h1>
            <p>Scan this QR code with the device you want to pair.</p>
            <img src="data:image/png;base64,{qr_b64}" alt="Pairing QR Code">
            <p style="font-size: 0.9em; opacity: 0.8;">Or copy this URL:</p>
            <div class="url">{pairing_url}</div>
            <a href="/" class="back">&larr; Back to Library</a>
        </div></body></html>
        """)
    @api.get("/api/chapters")
    def get_chapters(path: str):
        if not path or not os.path.exists(path): return []
        try:
            cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_chapters", path]
            result = subprocess.run(cmd, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            data = json.loads(result.stdout)
            chapters = []
            for ch in data.get("chapters", []):
                start_time = float(ch.get("start_time", 0))
                title = ch.get("tags", {}).get("title", f"Chapter {ch.get('id', 0) + 1}")
                chapters.append({"start": start_time, "title": title})
            return chapters
        except Exception: return []

    @api.get("/api/stream")
    def stream_audio(path: str, request: Request):
        mime_type = "audio/mpeg" if path.lower().endswith(".mp3") else "audio/mp4"
        if not path or not os.path.exists(path):
            raise HTTPException(status_code=404, detail="Audio file not found.")

        file_size = os.path.getsize(path)
        range_header = request.headers.get("Range")

        if not range_header:
            headers = {"Accept-Ranges": "bytes", "Content-Length": str(file_size), "Content-Type": mime_type}
            def full_file_iterator():
                with open(path, "rb") as f: yield from f
            return StreamingResponse(full_file_iterator(), headers=headers)

        byte_range = range_header.replace("bytes=", "").split("-")
        start_byte = int(byte_range[0])
        end_byte = int(byte_range[1]) if len(byte_range) > 1 and byte_range[1] else file_size - 1

        if start_byte >= file_size or end_byte >= file_size:
            raise HTTPException(status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE, detail="Invalid Range")

        chunk_size = (end_byte - start_byte) + 1

        def chunk_generator():
            with open(path, "rb") as f:
                f.seek(start_byte)
                bytes_left = chunk_size
                while bytes_left > 0:
                    read_size = min(65536, bytes_left) 
                    data = f.read(read_size)
                    if not data: break
                    bytes_left -= len(data)
                    yield data

        headers = {
            "Content-Range": f"bytes {start_byte}-{end_byte}/{file_size}", 
            "Accept-Ranges": "bytes", 
            "Content-Length": str(chunk_size), 
            "Content-Type": mime_type
        }
        return StreamingResponse(chunk_generator(), status_code=206, headers=headers)

    @api.get("/api/profiles/list")
    def list_profiles_detailed():
        """Returns all profiles with their auth status."""
        profiles = tomebox.settings.get("profiles", ["Main"])
        if not profiles:
            profiles = ["Main"]
        
        active = tomebox.settings.get("active_profile", profiles[0] if profiles else "Main")
        
        # Check which profiles have auth files
        result = []
        for name in profiles:
            auth_path = tomebox.db.get_auth_path(name)
            result.append({
                "name": name,
                "is_active": name == active,
                "is_authenticated": os.path.exists(auth_path)
            })
        
        return {"profiles": result, "active": active}


    @api.post("/api/profiles/create")
    async def create_profile(request: Request):
        """Create a new unauthenticated profile."""
        data = await request.json()
        name = data.get("name", "").strip()
        
        if not name:
            raise HTTPException(status_code=400, detail="Profile name is required")
        
        if not name.replace("_", "").replace("-", "").replace(" ", "").isalnum():
            raise HTTPException(status_code=400, detail="Profile name must be alphanumeric")
        
        profiles = tomebox.settings.get("profiles", ["Main"])
        if name in profiles:
            raise HTTPException(status_code=400, detail="Profile already exists")
        
        profiles.append(name)
        tomebox.settings["profiles"] = profiles
        tomebox.db.save_settings(tomebox.settings)
        
        return {"status": "success", "name": name}


    @api.post("/api/profiles/{name}/activate")
    def activate_profile(name: str):
        """Switch to the given profile, loading its auth and library."""
        profiles = tomebox.settings.get("profiles", ["Main"])
        if name not in profiles:
            raise HTTPException(status_code=404, detail="Profile not found")
        
        tomebox.settings["active_profile"] = name
        tomebox.db.save_settings(tomebox.settings)
        
        # Try to load auth for this profile
        auth_path = tomebox.db.get_auth_path(name)
        if os.path.exists(auth_path):
            try:
                tomebox.api_client.load_auth_from_file(auth_path)
                # Trigger a library refresh in the background
                try:
                    tomebox.library_manager.fetch_cloud_library()
                except Exception as e:
                    # Auth loaded but library fetch failed — that's recoverable
                    if hasattr(tomebox, 'logger'):
                        tomebox.logger(f"Library fetch failed during profile switch: {e}")
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to load auth: {str(e)}")
        else:
            # Profile has no auth — clear the current api_client
            tomebox.api_client.auth = None
        
        return {"status": "success", "active": name, "authenticated": os.path.exists(auth_path)}


    @api.delete("/api/profiles/{name}")
    def delete_profile(name: str):
        """Delete a profile and its auth file."""
        profiles = tomebox.settings.get("profiles", ["Main"])
        
        if name not in profiles:
            raise HTTPException(status_code=404, detail="Profile not found")
        
        if len(profiles) == 1:
            raise HTTPException(status_code=400, detail="Cannot delete the only profile")
        
        # If deleting the active profile, switch to another one first
        active = tomebox.settings.get("active_profile")
        new_active = active
        if active == name:
            new_active = next(p for p in profiles if p != name)
            tomebox.settings["active_profile"] = new_active
        
        profiles.remove(name)
        tomebox.settings["profiles"] = profiles
        tomebox.db.save_settings(tomebox.settings)
        
        # Delete the auth file if it exists
        auth_path = tomebox.db.get_auth_path(name)
        if os.path.exists(auth_path):
            try:
                os.remove(auth_path)
            except OSError:
                pass  # Best-effort cleanup
        
        # If we changed the active profile, load the new one's auth
        if active == name and new_active != name:
            new_auth_path = tomebox.db.get_auth_path(new_active)
            if os.path.exists(new_auth_path):
                tomebox.api_client.load_auth_from_file(new_auth_path)
            else:
                tomebox.api_client.auth = None
        
        return {"status": "success", "active": new_active}


    @api.post("/api/auth/login-start")
    async def auth_login_start(request: Request):
        import threading
        
        data = await request.json()
        locale = data.get("locale", "us")
        profile = data.get("profile", "").strip()
        
        if not profile:
            raise HTTPException(status_code=400, detail="Profile name is required")
        
        # We now track THREE events to perfectly sync the frontend and backend
        if not hasattr(tomebox, '_login_states'):
            tomebox._login_states = {}
            
        state = {
            "auth_url": None,
            "callback_url": None,
            "url_ready": threading.Event(),
            "callback_ready": threading.Event(),
            "login_finished": threading.Event(), # <-- NEW: Tracks actual completion
            "error": None
        }
        tomebox._login_states[profile] = state
        
        def background_login_task():
            import audible
            try:
                def url_interceptor(url):
                    state["auth_url"] = url
                    state["url_ready"].set()
                    
                    waited = state["callback_ready"].wait(timeout=300)
                    if not waited:
                        raise Exception("Login timed out waiting for callback URL")
                    
                    if state.get("error"):
                        raise Exception("Login cancelled")
                        
                    return state["callback_url"]

                # This blocks until Amazon responds with the final tokens
                auth = audible.Authenticator.from_login_external(
                    locale=locale,
                    login_url_callback=url_interceptor,
                )
                
                # Success! Save the token to disk
                auth_path = tomebox.db.get_auth_path(profile)
                auth.to_file(auth_path)
                
                # FIX 1: Add a default fallback so this evaluates to "Main", not None
                active = tomebox.settings.get("active_profile", "Main")
                
                if active == profile:
                    # FIX 2: Directly assign the auth object into live memory immediately!
                    tomebox.api_client.auth = auth
                    
                    try:
                        tomebox.library_manager.fetch_cloud_library()
                    except Exception as e:
                        if hasattr(tomebox, 'logger'):
                            tomebox.logger(f"Library fetch failed: {e}")
                            
            except Exception as e:
                state["error"] = str(e)
                state["url_ready"].set()
            finally:
                # Trigger the final event so the API endpoint knows it is safe to proceed
                state["login_finished"].set()
        
        threading.Thread(target=background_login_task, daemon=True).start()
        
        if not state["url_ready"].wait(timeout=15):
            raise HTTPException(status_code=500, detail="Failed to generate login URL in time")
            
        if state.get("error"):
            raise HTTPException(status_code=500, detail=state["error"])
            
        return {"auth_url": state["auth_url"]}


    @api.post("/api/auth/login-complete")
    async def auth_login_complete(request: Request):
        data = await request.json()
        profile = data.get("profile", "").strip()
        callback_url = data.get("callback_url", "").strip()
        
        state = getattr(tomebox, '_login_states', {}).get(profile)
        if not state:
            raise HTTPException(status_code=400, detail="No pending login for this profile")
            
        valid_starts = ("audible://", "https://www.amazon", "https://www.audible")
        if not callback_url.startswith(valid_starts):
            raise HTTPException(status_code=400, detail="Invalid callback URL.")
            
        # Hand the URL over to the waiting background thread
        state["callback_url"] = callback_url
        state["callback_ready"].set()
        
        # Wait for the Audible library to finish the OAuth exchange (No more time.sleep!)
        waited = state["login_finished"].wait(timeout=30)
        if not waited:
            raise HTTPException(status_code=504, detail="Amazon token exchange timed out.")
            
        if state.get("error"):
            raise HTTPException(status_code=500, detail=state["error"])
            
        return {"status": "success", "profile": profile}
        
        # --- DOWNLOAD ENDPOINTS ---
    @api.get("/api/system/browse-directory")
    def browse_directory():
        import tkinter as tk
        from tkinter import filedialog
        
        # Create a temporary hidden Tkinter root so we can use the native dialog
        temp_root = tk.Tk()
        temp_root.withdraw()
        
        # Force the window to pop up in front of the browser
        temp_root.attributes('-topmost', True) 
        
        # Open the system folder picker
        folder_path = filedialog.askdirectory(
            parent=temp_root, 
            title="Select TomeBox Download Location"
        )
        
        temp_root.destroy()
        
        return {"path": folder_path}
    @api.get("/api/downloads/queue")
    def get_download_queue():
        # Read the state directly from our new tracker in the manager
        queue_list = [{"asin": task["asin"], "title": task["title"]} for task in tomebox.download_manager.queue]
        
        return {
            "is_processing": tomebox.download_manager.is_processing,
            "active": getattr(tomebox.download_manager, 'web_state', {"active_asin": None, "progress": 0, "status": "Idle"}),
            "queue": queue_list
        }
    @api.post("/api/downloads/queue")
    async def queue_downloads(request: Request):
        from fastapi.responses import JSONResponse
        data = await request.json()
        asins = data.get("asins", [])
        
        # Check if the user has set a download directory; NO DEFAULT FALLBACK
        save_dir = tomebox.settings.get("download_dir")
        if not save_dir:
            # Return the specific error the frontend is waiting for
            return JSONResponse(status_code=400, content={"detail": "DOWNLOAD_DIR_NOT_SET"})
            
        items_to_queue = []
        for item in getattr(tomebox.library_manager, 'cloud_items', []):
            if item.get("asin") in asins:
                items_to_queue.append(item)
                
        if items_to_queue:
            tomebox.download_manager.queue_batch(items_to_queue, save_dir)
            return {"status": "success", "queued": len(items_to_queue)}
        raise HTTPException(status_code=404, detail="Items not found in cloud library")


    # NEW ENDPOINT: Save the directory
    @api.post("/api/settings/download-dir")
    async def set_download_dir(request: Request):
        data = await request.json()
        path = data.get("path", "").strip()
        
        if not path:
            raise HTTPException(status_code=400, detail="Path cannot be empty.")
            
        try:
            # Create the directory if it doesn't exist
            os.makedirs(path, exist_ok=True)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Cannot create or access directory: {e}")
            
        # Save to database
        tomebox.settings["download_dir"] = path
        tomebox.db.save_settings(tomebox.settings)
        return {"status": "success", "path": path}

    @api.delete("/api/downloads/{asin}")
    def cancel_download(asin: str):
        tomebox.download_manager.cancel_download(asin)
        return {"status": "cancelled", "asin": asin}

    # --- CONVERSION ENDPOINTS ---

    @api.post("/api/conversions/queue")
    async def queue_conversions(request: Request):
        data = await request.json()
        paths = data.get("paths", [])
        
        valid_paths = [p for p in paths if os.path.exists(p)]
        if valid_paths:
            tomebox.conversion_manager.convert_batch(valid_paths)
            return {"status": "success", "queued": len(valid_paths)}
        raise HTTPException(status_code=400, detail="No valid files provided")

    @api.get("/api/conversions/queue")
    def get_conversion_queue():
        return {
            "active": tomebox._web_task_state["conversions"],
            # Since convert_batch doesn't expose a queue list natively, we return a simple boolean
            "is_processing": getattr(tomebox.conversion_manager, 'current_process', None) is not None
        }
    return api
