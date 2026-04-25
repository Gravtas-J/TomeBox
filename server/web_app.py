import os
import json
import subprocess
from fastapi import FastAPI, Request, HTTPException, status, Request
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

def create_server_app(tomebox):
    api = FastAPI()
    static_dir = os.path.join(tomebox.base_dir, "server", "static")
    api.mount("/static", StaticFiles(directory=static_dir), name="static")
    @api.middleware("http")
    async def token_auth_middleware(request: Request, call_next):
        # Allow static files and the new auth endpoint to bypass the check
        if request.url.path in ["/auth", "/favicon.ico", "/pairing"] or request.url.path.startswith("/static"):
            return await call_next(request)

        server_token = tomebox.db.load_settings().get("auth_token")

        # 1. Check for the secure cookie
        client_token = request.cookies.get("tomebox_token")
        
        # 2. Fallback to Authorization header (optional, good for API testing tools like Postman)
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
        html_path = os.path.join(tomebox.base_dir, "server", "mobile_ui.html")
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

    return api

    