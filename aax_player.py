import subprocess
import json
import threading
import os
import webbrowser
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
import traceback
import requests
import io
from PIL import Image, ImageTk
import csv
# import sv_ttk
try:
    import audible
    from tkinterdnd2 import DND_FILES, TkinterDnD
    from wakepy import keep
except ImportError:
    messagebox.showerror("Missing Dependency", "Please run: pip install audible requests pillow tkinterdnd2 wakepy")
    exit()
import pystray
from pystray import MenuItem as item
import sys
import socket

class AAXManagerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("TomeBox")
        self.root.geometry("1550x850")
        self.root.drop_target_register(DND_FILES)
        self.root.dnd_bind('<<Drop>>', self.on_file_drop)

        self.enforce_single_instance()
        
        # 1. Setup Base Paths FIRST
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.local_db_path = os.path.join(self.base_dir, "library.json")
        self.last_db_mtime = 0
        self.log_file_path = os.path.join(self.base_dir, "aax_manager.log")
        self.covers_dir = os.path.join(self.base_dir, "covers")
        os.makedirs(self.covers_dir, exist_ok=True)
        self.settings_path = os.path.join(self.base_dir, "settings.json")
        
        # 2. Load Settings BEFORE applying any variables
        self.settings = self.load_settings()
        
        # 3. Apply Settings
        self.active_profile = self.settings.get("active_profile", "Main")
        self.minimize_to_tray_var = tk.BooleanVar(value=self.settings.get("minimize_to_tray", True))
        self.default_download_dir = self.settings.get("download_dir", "")
        
        # 4. Setup Profile-Specific Paths
        self.auth_save_path = os.path.join(self.base_dir, f"auth_{self.active_profile}.json")
        self.cloud_cache_path = os.path.join(self.base_dir, f"cloud_{self.active_profile}.json")
        
        # 5. Load Memory
        self.auth_object = None
        self.local_library = self.load_local_db()
        self.cloud_items = self.load_cloud_cache()

        self.file_path = ""
        self.auth_bytes = tk.StringVar(value="")
        self.locale = tk.StringVar(value="us")
        self.chapters = []
        self.current_chapter_idx = 0
        self.player_process = None
        
        self.debug_mode = tk.BooleanVar(value=False)
        self.dl_progress_var = tk.DoubleVar()
        self.dl_status_var = tk.StringVar(value="Idle")

        self.root.after(100, self.check_dependencies)
        
        try:
            icon_path = os.path.join(self.base_dir, "tomebox.png")
            if os.path.exists(icon_path):
                icon_img = tk.PhotoImage(file=icon_path)
                self.root.iconphoto(True, icon_img) # "True" applies it to all future dialog windows too
        except Exception as e:
            self.write_log(f"Could not load app icon: {e}")
        self.build_context_menu()
        self.setup_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.handle_window_close)
        self.setup_tray_icon()
        self.root.after(500, self.auto_load_auth)
        self.root.after(900000, self.run_background_sync)
        threading.Thread(target=self.cleanup_orphaned_files, daemon=True).start()
        threading.Thread(target=self.db_monitor_worker, daemon=True).start()

        if "stats" not in self.settings:
            self.settings["stats"] = {
                "seconds_listened": 0, 
                "books_finished": 0, 
                "books_downloaded": 0, 
                "unlocked_achievements": []
            }
        
        self.session_listen_buffer = 0.0
        
        self.achievements = {
            "first_dl": {"title": "System Integration Complete", "desc": "Download your first audiobook.", "type": "books_downloaded", "threshold": 1},
            "hoarder_1": {"title": "Spatial Expansion", "desc": "Download 10 audiobooks.", "type": "books_downloaded", "threshold": 10},
            "first_finish": {"title": "Core Consumed", "desc": "Finish an audiobook.", "type": "books_finished", "threshold": 1},
            "finish_5": {"title": "Path Advancement", "desc": "Finish 5 audiobooks.", "type": "books_finished", "threshold": 5},
            "listen_10h": {"title": "Mana Cultivator", "desc": "Listen for 10 total hours.", "type": "seconds_listened", "threshold": 36000},
            "listen_50h": {"title": "Dao of the Tome", "desc": "Listen for 50 total hours.", "type": "seconds_listened", "threshold": 180000}
        }
    
    def get_local_ip(self):
        import socket
        try:
            # We don't actually send data, just forcing the OS to route to an external IP
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1" # Fallback to localhost if disconnected from Wi-Fi
    def _get_mobile_html(self):
        return """
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
            <title>TomeBox</title>
            <style>
                :root { --bg: #121212; --card: #1e1e1e; --text: #e0e0e0; --accent: #bb86fc; }
                body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background-color: var(--bg); color: var(--text); margin: 0; padding: 0; padding-bottom: 120px; }
                header { background-color: var(--card); padding: 15px 20px; text-align: center; font-size: 1.2rem; font-weight: bold; border-bottom: 1px solid #333; position: sticky; top: 0; z-index: 10; display: flex; flex-direction: column; gap: 10px; }
                
                .header-controls { display: flex; flex-direction: column; gap: 10px; width: 100%; }
                #search-box { width: 100%; padding: 10px; border-radius: 8px; border: 1px solid #444; background-color: #222; color: white; font-size: 1rem; outline: none; box-sizing: border-box; }
                
                .filter-row { display: flex; gap: 10px; width: 100%; }
                .ui-select { flex: 1; padding: 10px; border-radius: 8px; border: 1px solid #444; background-color: #222; color: white; font-size: 0.9rem; outline: none; }
                #search-box:focus, .ui-select:focus { border-color: var(--accent); }

                #library-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 15px; padding: 15px; }
                .book-card { background-color: var(--card); border-radius: 8px; padding: 10px; cursor: pointer; transition: transform 0.1s; display: flex; flex-direction: column; align-items: center; text-align: center; }
                .book-card:active { transform: scale(0.98); }
                .cover-image { width: 100%; aspect-ratio: 1; object-fit: cover; border-radius: 4px; margin-bottom: 10px; background-color: #333; box-shadow: 0 4px 8px rgba(0,0,0,0.3); }
                .cover-placeholder { width: 100%; aspect-ratio: 1; background-color: #333; border-radius: 4px; margin-bottom: 10px; display: flex; align-items: center; justify-content: center; font-size: 2rem; color: #555; }
                .book-title { font-weight: bold; font-size: 0.9rem; margin: 0 0 5px 0; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
                .book-author { font-size: 0.8rem; color: #aaa; margin: 0; }
                .progress-pill { font-size: 0.7rem; color: var(--accent); background: #333; padding: 2px 6px; border-radius: 10px; margin-top: 5px; display: inline-block; }

                #player-bar { position: fixed; bottom: 0; left: 0; right: 0; background-color: var(--card); border-top: 1px solid #333; padding: 10px 15px 20px 15px; display: flex; flex-direction: column; gap: 10px; transform: translateY(100%); transition: transform 0.3s ease-out; z-index: 20; box-shadow: 0 -4px 10px rgba(0,0,0,0.5); }
                #player-bar.active { transform: translateY(0); }
                
                .player-top-row { display: flex; justify-content: space-between; align-items: center; width: 100%; }
                .player-info { flex-grow: 1; overflow: hidden; text-align: center; }
                #now-playing-title { font-weight: bold; font-size: 0.95rem; margin: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
                #now-playing-author { font-size: 0.8rem; color: #aaa; margin: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
                
                .player-bottom-row { display: flex; justify-content: space-between; align-items: center; width: 100%; }
                .main-controls { display: flex; align-items: center; gap: 15px; justify-content: center; flex-grow: 1; }
                .side-tools { display: flex; gap: 10px; min-width: 60px; justify-content: flex-end; }
                
                button { background: none; border: none; color: var(--text); cursor: pointer; padding: 5px; outline: none; display: flex; align-items: center; justify-content: center; }
                .play-btn { color: var(--accent); font-size: 2.5rem; width: 50px; height: 50px; }
                .skip-btn { font-size: 1.5rem; color: #ccc; }
                .chapter-btn { font-size: 1.2rem; color: #888; }
                .speed-btn { font-size: 1rem; font-weight: bold; color: var(--accent); background: #333; border-radius: 4px; padding: 5px 10px; min-width: 50px; }
                .tool-btn { font-size: 1.4rem; color: #aaa; transition: color 0.2s; }
                
                #progress-container { position: absolute; top: -2px; left: 0; right: 0; height: 6px; background-color: #444; cursor: pointer; }
                #progress-fill { height: 100%; background-color: var(--accent); width: 0%; pointer-events: none; }

                .modal-overlay { position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.8); z-index: 100; display: none; align-items: center; justify-content: center; backdrop-filter: blur(5px); }
                .modal-content { background: var(--card); width: 90%; max-height: 80vh; border-radius: 12px; display: flex; flex-direction: column; overflow: hidden; border: 1px solid #444; }
                .modal-header { padding: 15px; border-bottom: 1px solid #333; display: flex; justify-content: space-between; align-items: center; font-weight: bold; font-size: 1.2rem; }
                .modal-body { overflow-y: auto; flex-grow: 1; }
                .close-btn { color: #aaa; font-size: 1.5rem; }
                .list-item { padding: 15px; border-bottom: 1px solid #333; color: #ccc; display: flex; justify-content: space-between; align-items: center; font-size: 1rem; }
                .list-item:active { background-color: #2a2a2a; }
                .list-item.active { color: var(--accent); font-weight: bold; }
                .custom-sleep-row { cursor: default; }
                .custom-sleep-row:active { background-color: transparent; }
                .sleep-input-group { display: flex; align-items: center; gap: 10px; }
                .sleep-input { width: 50px; background: #222; color: white; border: 1px solid #444; border-radius: 6px; padding: 5px; text-align: center; font-size: 1rem; outline: none; }
                .sleep-input:focus { border-color: var(--accent); }
                .sleep-set-btn { background: var(--accent); color: var(--bg); font-weight: bold; border-radius: 6px; padding: 5px 15px; font-size: 0.9rem; }
            </style>
        </head>
        <body>
            <header>
                <div>TomeBox Library</div>
                <div class="header-controls">
                    <input type="text" id="search-box" placeholder="Search titles or authors..." onkeyup="filterLibrary()">
                    <div class="filter-row">
                        <select id="shelf-filter" class="ui-select" onchange="filterLibrary()">
                            <option value="all">All Shelves</option>
                        </select>
                        <select id="profile-selector" class="ui-select" onchange="changeProfile()"></select>
                    </div>
                </div>
            </header>
            
            <main id="library-grid"></main>

            <div id="player-bar">
                <div id="progress-container" onclick="seekAudio(event)">
                    <div id="progress-fill"></div>
                </div>
                <div class="player-top-row">
                    <div class="player-info">
                        <p id="now-playing-title">Select a book</p>
                        <p id="now-playing-author">...</p>
                    </div>
                </div>
                <div class="player-bottom-row">
                    <button class="speed-btn" id="speed-btn" onclick="toggleSpeed()">1.0x</button>
                    <div class="main-controls">
                        <button class="chapter-btn" onclick="skipChapter(-1)">⏮</button>
                        <button class="skip-btn" onclick="skipAudio(-15)">↺</button>
                        <button class="play-btn" id="play-pause-btn" onclick="togglePlay()">▶</button>
                        <button class="skip-btn" onclick="skipAudio(15)">↻</button>
                        <button class="chapter-btn" onclick="skipChapter(1)">⏭</button>
                    </div>
                    <div class="side-tools">
                        <button class="tool-btn" id="sleep-btn-ui" onclick="openSleepMenu()">🌙</button>
                        <button class="tool-btn" onclick="openChapterMenu()">📑</button>
                    </div>
                </div>
                <audio id="main-audio"></audio>
            </div>

            <div class="modal-overlay" id="sleep-modal" onclick="closeModals(event)">
                <div class="modal-content" onclick="event.stopPropagation()">
                    <div class="modal-header">
                        <span>Sleep Timer</span>
                        <button class="close-btn" onclick="closeModals()">✕</button>
                    </div>
                    <div class="modal-body">
                        <div class="list-item" onclick="setSleepTimer(15)">15 Minutes</div>
                        <div class="list-item" onclick="setSleepTimer(30)">30 Minutes</div>
                        <div class="list-item" onclick="setSleepTimer(60)">60 Minutes</div>
                        <div class="list-item" onclick="setSleepChapter(1)">End of Current Chapter</div>
                        <div class="list-item custom-sleep-row">
                            <div class="sleep-input-group">
                                <span>After</span>
                                <input type="number" id="custom-chapter-input" class="sleep-input" min="1" max="99" value="2">
                                <span>Chapters</span>
                            </div>
                            <button class="sleep-set-btn" onclick="setCustomSleepChapter()">Set</button>
                        </div>
                        <div class="list-item" onclick="setSleepOff()" style="color: #ff6b6b;">Turn Off Timer</div>
                    </div>
                </div>
            </div>

            <div class="modal-overlay" id="chapter-modal" onclick="closeModals(event)">
                <div class="modal-content" onclick="event.stopPropagation()">
                    <div class="modal-header">
                        <span>Chapters</span>
                        <button class="close-btn" onclick="closeModals()">✕</button>
                    </div>
                    <div class="modal-body" id="chapter-list"></div>
                </div>
            </div>

            <script>
                const audio = document.getElementById('main-audio');
                const playBtn = document.getElementById('play-pause-btn');
                const progressFill = document.getElementById('progress-fill');
                const playerBar = document.getElementById('player-bar');
                const speedBtn = document.getElementById('speed-btn');

                let allBooks = []; 
                let currentPath = null;
                let currentChapters = [];
                let sleepMode = null; 
                let sleepTimeout = null;
                let sleepTargetTime = null;
                
                let currentProfile = "Main";
                let rawLibraryData = {};

                async function initializeApp() {
                    try {
                        const profRes = await fetch('/api/profiles');
                        const profiles = await profRes.json();
                        const profSelect = document.getElementById('profile-selector');
                        profSelect.innerHTML = '';
                        profiles.forEach(p => { profSelect.innerHTML += `<option value="${p}">${p}</option>`; });
                        currentProfile = profiles[0] || "Main";
                    } catch (e) { console.error("Profile fetch failed", e); }
                    loadLibrary();
                }

                async function loadLibrary() {
                    try {
                        const response = await fetch('/api/library');
                        rawLibraryData = await response.json();
                        renderGrid();
                    } catch (e) { console.error("Failed to load library", e); }
                }

                function renderGrid() {
                    const grid = document.getElementById('library-grid');
                    const shelfFilter = document.getElementById('shelf-filter');
                    
                    grid.innerHTML = '';
                    allBooks = [];
                    let uniqueShelves = new Set();

                    for (const [path, data] of Object.entries(rawLibraryData)) {
                        if (data.format !== 'M4B' && data.format !== 'MP3') continue;

                        let authorStr = data.authors || 'Unknown Author';
                        const titleStr = data.title || "Unknown Title";
                        const asin = data.asin || "Unknown";
                        const bookShelves = data.shelves || [];
                        bookShelves.forEach(s => uniqueShelves.add(s));
                        
                        // Get profile-specific timestamp
                        let resumePos = 0;
                        if (data.progress && data.progress[currentProfile] !== undefined) {
                            resumePos = data.progress[currentProfile];
                        } else if (data.last_position) {
                            resumePos = data.last_position;
                        }
                        
                        let timePill = "";
                        if (resumePos > 60) {
                            const hrs = Math.floor(resumePos / 3600);
                            const mins = Math.floor((resumePos % 3600) / 60);
                            timePill = hrs > 0 ? `<span class="progress-pill">${hrs}h ${mins}m</span>` : `<span class="progress-pill">${mins}m</span>`;
                        }

                        const coverHtml = asin !== "Unknown" 
                            ? `<img src="/api/cover/${asin}" class="cover-image" onerror="this.outerHTML='<div class=\\'cover-placeholder\\'>📖</div>'"/>`
                            : `<div class="cover-placeholder">📖</div>`;

                        const card = document.createElement('div');
                        card.className = 'book-card';
                        card.onclick = () => startPlayback(path, titleStr, authorStr, resumePos, asin);
                        
                        card.innerHTML = `
                            ${coverHtml}
                            <p class="book-title">${titleStr}</p>
                            <p class="book-author">${authorStr}</p>
                            ${timePill}
                        `;
                        grid.appendChild(card);
                        
                        allBooks.push({ path: path, element: card, searchString: `${titleStr} ${authorStr}`.toLowerCase(), shelves: bookShelves });
                    }

                    const currentShelfSelection = shelfFilter.value;
                    shelfFilter.innerHTML = '<option value="all">All Shelves</option>';
                    Array.from(uniqueShelves).sort().forEach(shelf => {
                        const selected = shelf === currentShelfSelection ? "selected" : "";
                        shelfFilter.innerHTML += `<option value="${shelf}" ${selected}>${shelf}</option>`;
                    });
                    
                    filterLibrary();
                }

                // FIX: Changing profiles now forces a fresh database pull so timestamps never get stale
                async function changeProfile() {
                    currentProfile = document.getElementById('profile-selector').value;
                    await loadLibrary(); 
                    
                    if (currentPath && rawLibraryData[currentPath]) {
                        const data = rawLibraryData[currentPath];
                        let newPos = 0;
                        if (data.progress && data.progress[currentProfile] !== undefined) {
                            newPos = data.progress[currentProfile];
                        } else if (data.last_position) {
                            newPos = data.last_position;
                        }
                        
                        // Move the audio player to the new profile's position
                        if (Math.abs(audio.currentTime - newPos) > 2) {
                            audio.currentTime = newPos;
                        }
                    }
                }

                function filterLibrary() {
                    const query = document.getElementById('search-box').value.toLowerCase();
                    const selectedShelf = document.getElementById('shelf-filter').value;
                    
                    allBooks.forEach(book => {
                        const matchesSearch = book.searchString.includes(query);
                        const matchesShelf = selectedShelf === 'all' || book.shelves.includes(selectedShelf);
                        book.element.style.display = (matchesSearch && matchesShelf) ? 'flex' : 'none';
                    });
                }

                async function startPlayback(filePath, title, author, startPosition, asin) {
                    currentPath = filePath;
                    document.getElementById('now-playing-title').innerText = title;
                    document.getElementById('now-playing-author').innerText = author;
                    
                    audio.src = `/api/stream?path=${encodeURIComponent(filePath)}`;
                    audio.playbackRate = currentSpeed; 
                    audio.currentTime = startPosition;
                    audio.play().catch(err => console.error("Audio play failed:", err));
                    
                    playerBar.classList.add('active');
                    playBtn.innerText = '⏸';
                    setSleepOff(); 

                    try {
                        const res = await fetch(`/api/chapters?path=${encodeURIComponent(filePath)}`);
                        currentChapters = await res.json();
                    } catch(e) { currentChapters = []; }

                    if ('mediaSession' in navigator) {
                        const artworkUrl = asin !== "Unknown" ? [{ src: `/api/cover/${asin}`, sizes: '500x500', type: 'image/jpeg' }] : [];
                        navigator.mediaSession.metadata = new MediaMetadata({ title: title, artist: author, album: 'TomeBox', artwork: artworkUrl });
                        navigator.mediaSession.setActionHandler('seekbackward', () => skipAudio(-15));
                        navigator.mediaSession.setActionHandler('seekforward', () => skipAudio(15));
                        navigator.mediaSession.setActionHandler('previoustrack', () => skipChapter(-1));
                        navigator.mediaSession.setActionHandler('nexttrack', () => skipChapter(1));
                    }
                }

                function closeModals(e) { document.getElementById('sleep-modal').style.display = 'none'; document.getElementById('chapter-modal').style.display = 'none'; }
                function openSleepMenu() { document.getElementById('sleep-modal').style.display = 'flex'; }
                
                function formatTime(sec) {
                    const h = Math.floor(sec / 3600);
                    const m = Math.floor((sec % 3600) / 60);
                    const s = Math.floor(sec % 60);
                    if (h > 0) return `${h}:${m.toString().padStart(2,'0')}:${s.toString().padStart(2,'0')}`;
                    return `${m}:${s.toString().padStart(2,'0')}`;
                }

                function openChapterMenu() {
                    if (!currentChapters.length) return alert("No chapters found for this audiobook.");
                    const list = document.getElementById('chapter-list');
                    list.innerHTML = '';
                    const now = audio.currentTime;
                    
                    let activeIdx = currentChapters.findIndex(c => c.start > now) - 1;
                    if (activeIdx < 0 && now >= currentChapters[0].start) activeIdx = currentChapters.length - 1;
                    if (activeIdx === -2) activeIdx = currentChapters.length - 1;

                    currentChapters.forEach((ch, idx) => {
                        const div = document.createElement('div');
                        div.className = 'list-item' + (idx === activeIdx ? ' active' : '');
                        div.innerHTML = `<span>${ch.title}</span> <span>${formatTime(ch.start)}</span>`;
                        div.onclick = () => { audio.currentTime = ch.start; if(audio.paused) togglePlay(); closeModals(); };
                        list.appendChild(div);
                    });
                    document.getElementById('chapter-modal').style.display = 'flex';
                }

                function setSleepTimer(mins) {
                    clearTimeout(sleepTimeout);
                    sleepMode = 'time';
                    sleepTargetTime = null;
                    const ms = mins * 60000;
                    sleepTimeout = setTimeout(() => { audio.pause(); playBtn.innerText = '▶'; setSleepOff(); }, ms);
                    document.getElementById('sleep-btn-ui').style.color = 'var(--accent)';
                    closeModals();
                }
                
                function setCustomSleepChapter() {
                    const inputElem = document.getElementById('custom-chapter-input');
                    let count = parseInt(inputElem.value, 10);
                    if (isNaN(count) || count < 1) { count = 1; inputElem.value = 1; }
                    setSleepChapter(count);
                }

                function setSleepChapter(chapterCount) {
                    clearTimeout(sleepTimeout);
                    if (!currentChapters.length) { alert("No chapter data available for this book."); return; }
                    
                    sleepMode = 'chapter';
                    const now = audio.currentTime;
                    let currentIdx = currentChapters.findIndex(c => c.start > now) - 1;
                    if (currentIdx < 0 && now >= currentChapters[0].start) currentIdx = currentChapters.length - 1;
                    if (currentIdx === -2) currentIdx = 0; 

                    let targetIdx = currentIdx + chapterCount;
                    if (targetIdx < currentChapters.length) { sleepTargetTime = currentChapters[targetIdx].start; } 
                    else { sleepTargetTime = audio.duration; }

                    document.getElementById('sleep-btn-ui').style.color = 'var(--accent)';
                    closeModals();
                }

                function setSleepOff() {
                    clearTimeout(sleepTimeout);
                    sleepMode = null;
                    sleepTargetTime = null;
                    document.getElementById('sleep-btn-ui').style.color = '#aaa';
                    closeModals();
                }

                function skipChapter(direction) {
                    if (!currentChapters.length) { skipAudio(direction * 180); return; }
                    const now = audio.currentTime;
                    if (direction === 1) { 
                        const nextCh = currentChapters.find(c => c.start > now + 2);
                        if (nextCh) audio.currentTime = nextCh.start;
                    } else { 
                        const prevCh = [...currentChapters].reverse().find(c => c.start < now - 3);
                        if (prevCh) audio.currentTime = prevCh.start;
                        else audio.currentTime = 0;
                    }
                }

                // FIX: Update local JS memory instantly so the purple pills stay accurate
                setInterval(() => {
                    if (!audio.paused && currentPath) {
                        const pos = audio.currentTime;
                        
                        if (rawLibraryData[currentPath]) {
                            if (!rawLibraryData[currentPath].progress) rawLibraryData[currentPath].progress = {};
                            rawLibraryData[currentPath].progress[currentProfile] = pos;
                        }

                        fetch('/api/progress', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ path: currentPath, position: pos, profile: currentProfile })
                        }).catch(() => {});
                    }
                }, 10000);

                function togglePlay() {
                    if (!audio.src) return;
                    if (audio.paused) { audio.play(); playBtn.innerText = '⏸'; } 
                    else { audio.pause(); playBtn.innerText = '▶'; }
                }

                function skipAudio(seconds) {
                    if (audio.src && audio.duration) {
                        audio.currentTime = Math.min(Math.max(audio.currentTime + seconds, 0), audio.duration);
                    }
                }

                const speeds = [1.0, 1.25, 1.5, 1.75, 2.0];
                let speedIndex = 0;
                let currentSpeed = 1.0;

                function toggleSpeed() {
                    speedIndex = (speedIndex + 1) % speeds.length;
                    currentSpeed = speeds[speedIndex];
                    audio.playbackRate = currentSpeed;
                    speedBtn.innerText = currentSpeed.toFixed(1) + 'x';
                }

                audio.addEventListener('timeupdate', () => {
                    if (audio.duration) { progressFill.style.width = `${(audio.currentTime / audio.duration) * 100}%`; }
                    
                    if (sleepMode === 'chapter' && sleepTargetTime !== null) {
                        if (audio.currentTime >= sleepTargetTime - 1) {
                            audio.pause();
                            playBtn.innerText = '▶';
                            if (sleepTargetTime < audio.duration) { audio.currentTime = sleepTargetTime; }
                            setSleepOff();
                        }
                    }
                });

                audio.addEventListener('ended', () => { playBtn.innerText = '▶'; progressFill.style.width = '0%'; });
                function seekAudio(e) { if (!audio.duration) return; const rect = e.target.getBoundingClientRect(); audio.currentTime = (e.clientX - rect.left) / rect.width * audio.duration; }

                initializeApp();
            </script>
        </body>
        </html>
        """
    
    def toggle_web_server(self):
        if hasattr(self, 'web_server') and self.web_server is not None:
            self.write_log("Stopping companion server...")
            self.web_server.should_exit = True
            self.web_server = None
            self.file_menu.entryconfigure("Disable Web Server", label="Enable Web Server")
            messagebox.showinfo("Server Stopped", "The companion server has been safely disabled.")
        else:
            try:
                from fastapi import FastAPI, Request, HTTPException, status
                import uvicorn
                import threading
                from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse
                import os
                import subprocess, json
                
                import sys
                import asyncio
                if sys.platform == 'win32':
                    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

                api = FastAPI()

                @api.get("/", response_class=HTMLResponse)
                def web_interface():
                    return HTMLResponse(content=self._get_mobile_html())

                @api.get("/api/profiles")
                def get_profiles():
                    profs = self.settings.get("profiles")
                    if not profs or not isinstance(profs, list):
                        return ["Main"]
                    return profs

                @api.get("/api/library")
                def get_web_library():
                    enriched_lib = {}
                    shelves_db = self.settings.get("shelves_db", {})
                    
                    master_metadata = {}
                    for f in os.listdir(self.base_dir):
                        if f.startswith("cloud_") and f.endswith(".json") or f == "cloud_cache.json":
                            try:
                                with open(os.path.join(self.base_dir, f), "r") as file:
                                    for item in json.load(file):
                                        if item.get("asin"): master_metadata[item["asin"]] = item
                                        if item.get("title"): master_metadata[item["title"]] = item
                            except Exception:
                                pass

                    for item in getattr(self, 'cloud_items', []):
                        if item.get("asin"): master_metadata[item["asin"]] = item
                        if item.get("title"): master_metadata[item["title"]] = item

                    for path, data in self.local_library.items():
                        item_copy = dict(data)
                        asin = item_copy.get("asin")
                        item_copy["shelves"] = shelves_db.get(asin, [])
                        
                        if "progress" not in item_copy:
                            item_copy["progress"] = {}
                            
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
                    cover_path = os.path.join(getattr(self, 'covers_dir', self.base_dir), f"{asin}.jpg")
                    if os.path.exists(cover_path):
                        return FileResponse(cover_path)
                    raise HTTPException(status_code=404, detail="Cover not found")

                @api.post("/api/progress")
                async def update_progress(request: Request):
                    try:
                        data = await request.json()
                        path = data.get("path")
                        position = data.get("position")
                        profile = data.get("profile", "Main")

                        if path and path in self.local_library:
                            if "progress" not in self.local_library[path]:
                                self.local_library[path]["progress"] = {}
                                
                            self.local_library[path]["progress"][profile] = position
                            self.local_library[path]["last_position"] = position
                                
                            self.root.after(0, self.save_local_db)
                    except Exception:
                        pass
                    return {"status": "success"}

                @api.get("/api/chapters")
                def get_chapters(path: str):
                    import subprocess, json
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
                    except Exception as e:
                        return []

                @api.get("/api/stream")
                def stream_audio(path: str, request: Request):
                    if not path or not os.path.exists(path):
                        raise HTTPException(status_code=404, detail="Audio file not found.")

                    file_size = os.path.getsize(path)
                    range_header = request.headers.get("Range")

                    if not range_header:
                        headers = {"Accept-Ranges": "bytes", "Content-Length": str(file_size), "Content-Type": "audio/mp4"}
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

                    headers = {"Content-Range": f"bytes {start_byte}-{end_byte}/{file_size}", "Accept-Ranges": "bytes", "Content-Length": str(chunk_size), "Content-Type": "audio/mp4"}
                    return StreamingResponse(chunk_generator(), status_code=206, headers=headers)

                config = uvicorn.Config(api, host="0.0.0.0", port=8000, log_config=None)
                self.web_server = uvicorn.Server(config)
                threading.Thread(target=self.web_server.run, daemon=True).start()
                
                self.file_menu.entryconfigure("Enable Web Server", label="Disable Web Server")
                local_ip = self.get_local_ip()
                self.write_log(f"Server started on http://{local_ip}:8000")
                messagebox.showinfo("Server Active", f"Companion Server is now running!\n\nAccess it at:\nhttp://{local_ip}:8000")
                
            except ImportError:
                messagebox.showerror("Missing Libraries", "Please install the required server packages first:\n\npip install fastapi uvicorn")
            except Exception as e:
                self.write_log(f"Failed to start server: {e}")
                messagebox.showerror("Server Error", f"Could not start the server.\n\n{e}")

    def cleanup_orphaned_files(self):
        save_dir = self.settings.get("download_dir", "")
        if not save_dir or not os.path.exists(save_dir):
            return

        self.write_log("Running startup scan for orphaned/partial files...")
        cleaned_count = 0

        try:
            for filename in os.listdir(save_dir):
                filepath = os.path.join(save_dir, filename)
                
                # Skip directories
                if not os.path.isfile(filepath):
                    continue

                # Target 1: Explicitly temporary/partial files
                if filename.endswith(".part") or "_temp." in filename:
                    try:
                        os.remove(filepath)
                        self.write_log(f"Deleted partial file: {filename}")
                        cleaned_count += 1
                    except OSError:
                        pass
                    continue

                # Target 2: Corrupted 0-byte media files
                if filename.lower().endswith(('.aax', '.aaxc', '.m4b', '.mp3')):
                    try:
                        if os.path.getsize(filepath) == 0:
                            os.remove(filepath)
                            self.write_log(f"Deleted empty 0-byte file: {filename}")
                            cleaned_count += 1
                    except OSError:
                        pass

            if cleaned_count > 0:
                self.write_log(f"Cleanup complete. Removed {cleaned_count} orphaned files.")
                
        except Exception as e:
            self.write_log(f"Failed to run orphaned file cleanup: {e}")

    def toggle_system_sleep(self, prevent_sleep=True):
        if os.name != 'nt':
            return # Only implemented for Windows

        try:
            import ctypes
            # 0x80000000 = ES_CONTINUOUS, 0x00000001 = ES_SYSTEM_REQUIRED
            if prevent_sleep:
                self.write_log("Applying sleep prevention for active background task.")
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
            else:
                self.write_log("Releasing system sleep prevention.")
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        except Exception as e:
            self.write_log(f"Failed to toggle sleep state: {e}")

    def has_enough_disk_space(self, target_dir, required_bytes):
        import shutil
        try:
            # If the directory doesn't exist yet, check the drive it belongs to
            check_dir = target_dir
            while not os.path.exists(check_dir) and os.path.dirname(check_dir) != check_dir:
                check_dir = os.path.dirname(check_dir)
                
            total, used, free = shutil.disk_usage(check_dir)
            return free > required_bytes
        except Exception as e:
            self.write_log(f"Disk space check failed: {e}")
            return True # Fail open so we don't accidentally block valid operations

    def add_stat(self, stat_name, amount=1):
        stats = self.settings.get("stats", {})
        stats[stat_name] = stats.get(stat_name, 0) + amount
        self.settings["stats"] = stats
        self.save_settings()
        self.check_achievements()

    def on_file_drop(self, event):
        # Tkinter safely parses the dropped string into a tuple of file paths
        files = self.root.tk.splitlist(event.data)
        
        # Start a background thread so FFprobe doesn't freeze the app if you drop 50 files
        threading.Thread(target=self.process_dropped_files_worker, args=(files,), daemon=True).start()

    def process_dropped_files_worker(self, files):
        valid_exts = [".aax", ".aaxc", ".m4b", ".mp3"]
        added_count = 0
        
        for filepath in files:
            if not os.path.exists(filepath): continue
            
            ext = os.path.splitext(filepath)[1].lower()
            if ext not in valid_exts: continue
            
            filename = os.path.basename(filepath)
            title = filename
            authors = "Unknown Author"
            format_clean = ext.replace(".", "").upper()
            
            self.root.after(0, lambda f=filename: self.dl_status_var.set(f"Importing: {f}"))
            
            if format_clean in ["M4B", "MP3"]:
                try:
                    cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", filepath]
                    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                    data = json.loads(res.stdout)
                    tags = data.get("format", {}).get("tags", {})

                    if "title" in tags: title = tags["title"]
                    if "artist" in tags: authors = tags["artist"]
                    elif "album_artist" in tags: authors = tags["album_artist"]
                except Exception as e:
                    self.write_log(f"Failed to read tags for {filename}: {e}")

            self.local_library[filepath] = {
                "title": title, 
                "format": format_clean, 
                "path": filepath, 
                "authors": authors,
                "owner": self.active_profile
            }
            added_count += 1
            
        if added_count > 0:
            self.save_local_db()
            self.root.after(0, self.refresh_library_ui)
            self.root.after(0, lambda c=added_count: self.dl_status_var.set(f"Successfully imported {c} files."))
        else:
            self.root.after(0, lambda: self.dl_status_var.set("No valid audiobooks found in drop."))
            
        self.root.after(4000, lambda: self.dl_status_var.set("Idle"))

    def check_achievements(self):
        stats = self.settings.get("stats", {})
        unlocked = stats.get("unlocked_achievements", [])
        
        for ach_id, data in self.achievements.items():
            if ach_id not in unlocked:
                current_val = stats.get(data["type"], 0)
                if current_val >= data["threshold"]:
                    unlocked.append(ach_id)
                    self.settings["stats"]["unlocked_achievements"] = unlocked
                    self.save_settings()
                    self.show_achievement_toast(data["title"], data["desc"])

    def show_achievement_toast(self, title, desc):
        toast = tk.Toplevel(self.root)
        toast.wm_overrideredirect(True)
        toast.attributes('-topmost', True)
        
        # Pull colors dynamically to match the current theme
        style = ttk.Style()
        bg_color = style.lookup("TFrame", "background") or "#2b2b2b"
        fg_color = style.lookup("TLabel", "foreground") or "#f0f0f0"
        accent_color = "#f39c12" # Legendary gold
        
        toast.configure(bg=accent_color)
        
        inner = tk.Frame(toast, bg=bg_color, highlightthickness=0)
        inner.pack(fill="both", expand=True, padx=2, pady=2) # 2px padding creates the gold border
        
        tk.Label(inner, text="🏆 Achievement Unlocked!", font=("Segoe UI", 9, "bold"), bg=bg_color, fg=accent_color).pack(anchor="w", padx=15, pady=(10, 0))
        tk.Label(inner, text=title, font=("Segoe UI", 11, "bold"), bg=bg_color, fg=fg_color).pack(anchor="w", padx=15)
        tk.Label(inner, text=desc, font=("Segoe UI", 9), bg=bg_color, fg=fg_color).pack(anchor="w", padx=15, pady=(0, 10))
        
        # Force render to get accurate dimensions
        toast.update_idletasks()
        w = toast.winfo_width()
        h = toast.winfo_height()
        
        # Position in the bottom right corner of the primary monitor
        x = self.root.winfo_screenwidth() - w - 20
        y = self.root.winfo_screenheight() - h - 60
        toast.geometry(f"+{x}+{y}")
        
        self.root.after(5000, toast.destroy)

    def enforce_single_instance(self):
        self.lock_port = 43128 # Unique port just for TomeBox
        self.lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        
        try:
            # Try to claim the port
            self.lock_socket.bind(('127.0.0.1', self.lock_port))
            self.lock_socket.listen(1)
            
            # Success! We are the first instance. Start listening for wake requests.
            threading.Thread(target=self.instance_listener_worker, daemon=True).start()
            
        except socket.error:
            # Port is already in use! Another TomeBox is running.
            self.write_log("Another instance detected. Sending wake signal...")
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.connect(('127.0.0.1', self.lock_port))
                s.sendall(b"WAKEUP")
                s.close()
            except Exception:
                pass
            
            # Kill this duplicate instance immediately
            sys.exit(0)

    def instance_listener_worker(self):
        while True:
            try:
                conn, addr = self.lock_socket.accept()
                data = conn.recv(1024)
                if data == b"WAKEUP":
                    self.write_log("Wake signal received. Bringing window to front.")
                    self.root.after(0, self.bring_to_front)
                conn.close()
            except Exception as e:
                if self.debug_mode.get():
                    self.write_log(f"Socket listener error: {e}")
                break

    def bring_to_front(self):
        # 1. Un-hide it if it was minimized to the system tray
        self.root.deiconify()
        
        # 2. Lift it above other windows
        self.root.lift()
        
        # 3. Force it to the absolute top, then release the lock so the user can click other things again
        self.root.attributes('-topmost', True)
        self.root.after_idle(self.root.attributes, '-topmost', False)

    def setup_tray_icon(self):
        try:
            icon_path = os.path.join(self.base_dir, "tomebox.png")
            if not os.path.exists(icon_path):
                return
                
            image = Image.open(icon_path)
            
            menu = pystray.Menu(
                item('Show TomeBox', self.show_window_from_tray, default=True),
                item('Quit', self.quit_from_tray)
            )
            
            self.tray_icon = pystray.Icon("TomeBox", image, "TomeBox", menu)
            
            # Run the tray icon loop in a background thread so it doesn't block Tkinter
            threading.Thread(target=self.tray_icon.run, daemon=True).start()
        except Exception as e:
            self.write_log(f"Failed to initialize system tray: {e}")

    def hide_window_to_tray(self):
        # Withdraw hides the window from the taskbar and screen
        self.root.withdraw()
        
    def show_window_from_tray(self, icon, item):
        # Must be passed back to the main Tkinter thread using .after
        self.root.after(0, self.root.deiconify)
        
    def quit_from_tray(self, icon, item):
        icon.stop()
        self.root.after(0, self.on_closing)

    def open_support_link(self):
        import webbrowser
        self.write_log("Opening Buy Me a Coffee link...")
        webbrowser.open("https://buymeacoffee.com/ProblematicSyntax")

    def add_new_profile(self):
        new_name = simpledialog.askstring("New Profile", "Enter a name for the new profile:")
        if new_name and new_name not in self.profiles_list:
            self.profiles_list.append(new_name)
            self.settings["profiles"] = self.profiles_list
            self.profile_combo.config(values=self.profiles_list)
            self.profile_combo.set(new_name)
            self.switch_profile()

    def switch_profile(self, event=None):
        selected = self.profile_combo.get()
        self.active_profile = selected
        self.settings["active_profile"] = selected
        self.save_settings()
        
        # Update paths
        self.auth_save_path = os.path.join(self.base_dir, f"auth_{self.active_profile}.json")
        self.cloud_cache_path = os.path.join(self.base_dir, f"cloud_{self.active_profile}.json")
        
        # Clear current session
        self.auth_object = None
        self.auth_bytes.set("")
        self.cloud_items = self.load_cloud_cache()
        
        # Try to load the new profile's auth file
        self.auto_load_auth()
        self.refresh_library_ui()

    def check_dependencies(self):
        import shutil
        import webbrowser
        
        ffmpeg_installed = shutil.which("ffmpeg") is not None
        ffplay_installed = shutil.which("ffplay") is not None
        
        if not ffmpeg_installed or not ffplay_installed:
            self.write_log("WARNING: FFmpeg or FFplay not found in system PATH.")
            
            msg = (
                "FFmpeg is missing from your system.\n\n"
                "TomeBox requires FFmpeg to play, convert, and split audiobooks. "
                "Without it, you will only be able to download files.\n\n"
                "Would you like to open the official FFmpeg download page now?"
            )
            
            # askyesno returns True if they click Yes, False if No
            user_wants_link = messagebox.askyesno("Missing Dependency: FFmpeg", msg)
            
            if user_wants_link:
                self.write_log("Opening FFmpeg download page in browser...")
                webbrowser.open("https://ffmpeg.org/download.html")

    def run_background_sync(self):
        threading.Thread(target=self.silent_sync_worker, daemon=True).start()
        # Schedule the next check in 15 minutes (900000 milliseconds)
        self.root.after(900000, self.run_background_sync)
    
    def db_monitor_worker(self):
        import time
        import os
        import json
        
        while True:
            ui_needs_refresh = False
            
            # 1. Check if any actual audio files were deleted from the hard drive
            # We copy the keys() to a list so we can safely delete from the dictionary during the loop
            missing_paths = [path for path in list(self.local_library.keys()) if not os.path.exists(path)]
            
            if missing_paths:
                for path in missing_paths:
                    del self.local_library[path]
                    
                self.write_log(f"Detected {len(missing_paths)} deleted files. Updating library...")
                
                # Save the cleaned DB without triggering the JSON file monitor below
                with open(self.local_db_path, "w") as f:
                    json.dump(self.local_library, f, indent=4)
                
                if os.path.exists(self.local_db_path):
                    self.last_db_mtime = os.path.getmtime(self.local_db_path)
                    
                ui_needs_refresh = True

            # 2. Check if the library.json file was edited externally
            if os.path.exists(self.local_db_path):
                try:
                    current_mtime = os.path.getmtime(self.local_db_path)
                    
                    if self.last_db_mtime == 0:
                        self.last_db_mtime = current_mtime
                    elif current_mtime > self.last_db_mtime:
                        self.write_log("External DB change detected. Syncing local library...")
                        self.last_db_mtime = current_mtime
                        self.local_library = self.load_local_db()
                        ui_needs_refresh = True
                except Exception as e:
                    self.write_log(f"DB Monitor Error: {e}")
            
            # Redraw the screen if either of the above checks triggered a change
            if ui_needs_refresh:
                self.root.after(0, self.refresh_library_ui)
                
            time.sleep(2)

    def build_menu_bar(self):
        self.root.config(menu="")
        self.menu_frame = ttk.Frame(self.root)
        self.menu_frame.pack(side=tk.TOP, fill="x")

        self.file_menubutton = ttk.Menubutton(self.menu_frame, text="File")
        self.file_menubutton.pack(side=tk.LEFT, padx=5, pady=2)

        self.file_menu = tk.Menu(self.file_menubutton, tearoff=0, relief="flat")
        self.file_menubutton.config(menu=self.file_menu)
        
        self.file_menu.add_command(label="Set Download Folder", command=self.set_download_folder)
        self.file_menu.add_command(label="Authentication & Profiles", command=self.open_auth_window)
        self.file_menu.add_separator()

        self.file_menu.add_checkbutton(
            label="Minimize to Tray on Close", 
            variable=self.minimize_to_tray_var, 
            command=self.save_tray_setting
        )
        self.file_menu.add_separator()

        # Appearance Sub-Menu
        self.appearance_menu = tk.Menu(self.file_menu, tearoff=0, relief="flat")
        self.file_menu.add_cascade(label="Appearance", menu=self.appearance_menu)
        
        # if not hasattr(self, 'ui_mode_var'):
        #     self.ui_mode_var = tk.StringVar(value=self.settings.get("ui_mode", "modern"))
            
        # self.appearance_menu.add_radiobutton(label="Classic Engine (ttk)", variable=self.ui_mode_var, value="classic", command=self.on_ui_mode_change)
        # self.appearance_menu.add_radiobutton(label="Modern Engine (sv_ttk)", variable=self.ui_mode_var, value="modern", command=self.on_ui_mode_change)

        # self.appearance_menu.add_separator()

        # Classic Palettes Sub-Menu
        # self.palette_menu = tk.Menu(self.appearance_menu, tearoff=0, relief="flat")
        # self.appearance_menu.add_cascade(label="Colour Palettes", menu=self.palette_menu)
        
        self.palette_var = tk.StringVar(value=self.settings.get("classic_palette", "light"))
        
        self.appearance_menu.add_radiobutton(label="Light Default", variable=self.palette_var, value="light", command=lambda: self.apply_classic_palette("light"))
        self.appearance_menu.add_radiobutton(label="Dark Charcoal", variable=self.palette_var, value="dark", command=lambda: self.apply_classic_palette("dark"))
        self.appearance_menu.add_radiobutton(label="Terminal Green", variable=self.palette_var, value="terminal", command=lambda: self.apply_classic_palette("terminal"))
        self.appearance_menu.add_separator()
        self.appearance_menu.add_radiobutton(label="Solarized Dark", variable=self.palette_var, value="solarized_dark", command=lambda: self.apply_classic_palette("solarized_dark"))
        self.appearance_menu.add_radiobutton(label="Solarized Light", variable=self.palette_var, value="solarized_light", command=lambda: self.apply_classic_palette("solarized_light"))
        self.appearance_menu.add_separator()
        self.appearance_menu.add_radiobutton(label="Dracula", variable=self.palette_var, value="dracula", command=lambda: self.apply_classic_palette("dracula"))
        self.appearance_menu.add_radiobutton(label="Nordic Slate", variable=self.palette_var, value="nord", command=lambda: self.apply_classic_palette("nord"))
        self.appearance_menu.add_radiobutton(label="Cyberpunk", variable=self.palette_var, value="cyberpunk", command=lambda: self.apply_classic_palette("cyberpunk"))

        # self.appearance_menu.add_separator()
        # self.appearance_menu.add_command(label="Toggle Light / Dark Mode", command=self.toggle_custom_colors)

        self.file_menu.add_separator()

        # Export Sub-Menu
        self.export_menu = tk.Menu(self.file_menu, tearoff=0, relief="flat")
        self.file_menu.add_cascade(label="Export Library", menu=self.export_menu)
        self.export_menu.add_command(label="Export to CSV", command=self.export_csv_worker)
        self.export_menu.add_command(label="Export to HTML Page", command=self.export_html_worker)

        self.file_menu.add_separator()
        self.file_menu.add_command(label="Exit", command=self.on_closing)

        self.help_menubutton = ttk.Menubutton(self.menu_frame, text="Donate")
        self.help_menubutton.pack(side=tk.LEFT, padx=5, pady=2)

        self.help_menu = tk.Menu(self.help_menubutton, tearoff=0, relief="flat")
        self.help_menubutton.config(menu=self.help_menu)
        
        self.help_menu.add_command(label="Support the Developer ☕", command=self.open_support_link)

        #Achievement menu
        self.file_menu.add_command(label="My Achievements", command=self.open_achievements_window)
        self.file_menu.add_separator()
        self.file_menu.add_command(label="Enable Web Server", command=self.toggle_web_server)
        self.file_menu.add_separator()

    def build_info_components(self, parent):
        self.cover_frame = ttk.Frame(parent)
        self.cover_frame.pack(fill="x", padx=5, pady=10)
        
        self.cover_label = ttk.Label(self.cover_frame, text="No Cover Art")
        self.cover_label.pack(pady=5)
        
        self.author_label = ttk.Label(self.cover_frame, text="", font=("Segoe UI", 10, "italic"))
        self.author_label.pack(pady=2)
        
        self.current_cover_photo = None

    def open_auth_window(self):
        # Prevent opening multiple auth windows
        if hasattr(self, 'auth_window') and self.auth_window.winfo_exists():
            self.auth_window.lift()
            self.auth_window.focus_set()
            return

        self.auth_window = tk.Toplevel(self.root)
        self.auth_window.title("Authentication & Profiles")
        self.auth_window.geometry("380x320")
        self.auth_window.resizable(False, False)
        self.auth_window.transient(self.root) 
        
        # Apply current theme background
        style = ttk.Style()
        bg_color = style.lookup("TFrame", "background") or "#f0f0f0"
        self.auth_window.configure(bg=bg_color)
        
        main_frame = ttk.Frame(self.auth_window, padding=10)
        main_frame.pack(fill="both", expand=True)

        auth_frame = ttk.LabelFrame(main_frame, text="Audible Authentication", padding=10)
        auth_frame.pack(fill="x", pady=5)

        reg_frame = ttk.Frame(auth_frame)
        reg_frame.pack(fill="x", pady=5)
        ttk.Label(reg_frame, text="Region:").pack(side=tk.LEFT, padx=5)
        
        reg_combo = ttk.Combobox(reg_frame, textvariable=self.locale, values=["us", "uk", "au", "ca", "de", "fr", "jp"], state="readonly", width=5)
        reg_combo.pack(side=tk.LEFT)

        btn_frame = ttk.Frame(auth_frame)
        btn_frame.pack(fill="x", pady=5)
        self.browser_login_btn = ttk.Button(btn_frame, text="Browser Login", command=self.start_browser_login_thread)
        self.browser_login_btn.pack(side=tk.LEFT, expand=True, fill="x", padx=2)
        self.auth_file_btn = ttk.Button(btn_frame, text="Load .json", command=self.load_auth_file_prompt)
        self.auth_file_btn.pack(side=tk.LEFT, expand=True, fill="x", padx=2)

        profile_frame = ttk.Frame(auth_frame)
        profile_frame.pack(fill="x", pady=5)
        
        ttk.Label(profile_frame, text="Profile:").pack(side=tk.LEFT, padx=5)
        
        self.profiles_list = getattr(self, 'profiles_list', self.settings.get("profiles", ["Main"]))
        self.profile_combo = ttk.Combobox(profile_frame, values=self.profiles_list, state="readonly", width=15)
        self.profile_combo.set(self.active_profile)
        self.profile_combo.pack(side=tk.LEFT, padx=5)
        
        ttk.Button(profile_frame, text="New", width=5, command=self.add_new_profile).pack(side=tk.LEFT)
        self.profile_combo.bind("<<ComboboxSelected>>", self.switch_profile)

        bytes_frame = ttk.LabelFrame(main_frame, text="Decryption Bytes", padding=10)
        bytes_frame.pack(fill="x", pady=10)
        ttk.Entry(bytes_frame, textvariable=self.auth_bytes, justify="center").pack(fill="x", pady=5)
        
        ttk.Button(main_frame, text="Close", command=self.auth_window.destroy).pack(pady=(10, 0))

    def build_library_components(self, parent):
        self.main_paned = ttk.PanedWindow(parent, orient=tk.VERTICAL)
        self.main_paned.pack(fill="both", expand=True, padx=5, pady=5)

        lib_frame = ttk.LabelFrame(self.main_paned, text="", padding=10)
        self.main_paned.add(lib_frame, weight=1)

        self.queue_frame = ttk.LabelFrame(self.main_paned, text="Active Downloads", padding=10)
        
        queue_controls = ttk.Frame(self.queue_frame)
        queue_controls.pack(fill="x", pady=(0, 5))
        ttk.Button(queue_controls, text="Cancel All Downloads", command=self.cancel_all_downloads).pack(side=tk.RIGHT)

        # sv_ttk background color applied to the canvas
        self.queue_canvas = tk.Canvas(self.queue_frame, height=120, bg="#1c1c1c", highlightthickness=0)
        queue_scroll = ttk.Scrollbar(self.queue_frame, orient="vertical", command=self.queue_canvas.yview)
        

        # sv_ttk background color applied to the inner frame
        self.queue_inner = tk.Frame(self.queue_canvas, bg="#1c1c1c")

        self.queue_inner.bind("<Configure>", lambda e: self.queue_canvas.configure(scrollregion=self.queue_canvas.bbox("all")))
        self.queue_canvas.create_window((0, 0), window=self.queue_inner, anchor="nw")
        self.queue_canvas.configure(yscrollcommand=queue_scroll.set)

        self.queue_canvas.pack(side="left", fill="both", expand=True)
        queue_scroll.pack(side="right", fill="y")

        self.active_downloads = {}

        filter_frame = ttk.Frame(lib_frame)
        filter_frame.pack(fill="x", pady=(0, 5))

        ttk.Label(filter_frame, text="Search:").pack(side=tk.LEFT, padx=(0, 5))
        
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *args: self.refresh_library_ui()) 
        search_entry = ttk.Entry(filter_frame, textvariable=self.search_var, width=35)
        search_entry.pack(side=tk.LEFT, padx=(0, 20))

        ttk.Label(filter_frame, text="Filter:").pack(side=tk.LEFT, padx=(0, 5))
        
        self.filter_var = tk.StringVar(value="All")
        filter_combo = ttk.Combobox(filter_frame, textvariable=self.filter_var, values=["All", "Downloaded", "Cloud Only"], state="readonly", width=15)
        filter_combo.pack(side=tk.LEFT)
        filter_combo.bind("<<ComboboxSelected>>", lambda e: self.refresh_library_ui())

        ttk.Label(filter_frame, text="Shelf:").pack(side=tk.LEFT, padx=(10, 5))
        self.shelf_filter_var = tk.StringVar(value="All Shelves")
        self.shelf_combo = ttk.Combobox(filter_frame, textvariable=self.shelf_filter_var, state="readonly", width=15)
        self.shelf_combo.pack(side=tk.LEFT)
        self.shelf_combo.bind("<<ComboboxSelected>>", lambda e: self.refresh_library_ui())

        self.view_btn = ttk.Button(filter_frame, text="Grid View", command=self.toggle_library_view)
        self.view_btn.pack(side=tk.RIGHT, padx=5)

        self.toggle_queue_btn = ttk.Button(filter_frame, text="Show/Hide Queue", command=self.toggle_queue_visibility)
        self.toggle_queue_btn.pack(side=tk.RIGHT, padx=5)

        self.dl_all_btn = ttk.Button(filter_frame, text="Download Missing", command=self.start_download_all)
        self.dl_all_btn.pack(side=tk.RIGHT, padx=(5, 5))

        tree_frame = ttk.Frame(lib_frame)
        tree_frame.pack(fill="both", expand=True, pady=5)

        scroll = ttk.Scrollbar(tree_frame)
        scroll.pack(side=tk.RIGHT, fill="y")

        self.library_tree = ttk.Treeview(tree_frame, columns=("Title", "Author", "Series", "Duration", "ASIN", "Status"), show="headings", yscrollcommand=scroll.set)
        scroll.config(command=self.library_tree.yview)
        self.library_tree.bind("<<TreeviewSelect>>", self.on_item_select)
        
        self.current_view_mode = "list"
        self.grid_images_ref = [] 
        
        
        self.grid_canvas = tk.Canvas(tree_frame, bg="#1c1c1c", highlightthickness=0)
        self.grid_inner = tk.Frame(self.grid_canvas, bg="#1c1c1c")
        self.grid_window_id = self.grid_canvas.create_window((0, 0), window=self.grid_inner, anchor="nw")
        
        
        self.grid_canvas.configure(yscrollcommand=scroll.set)
        self.grid_inner.bind("<Configure>", lambda e: self.grid_canvas.configure(scrollregion=self.grid_canvas.bbox("all")))
        
        
        self.grid_canvas.bind("<Configure>", self.on_canvas_resize)
        self.root.bind_all("<MouseWheel>", self._on_grid_scroll)  
        self.root.bind_all("<Button-4>", self._on_grid_scroll)    
        self.root.bind_all("<Button-5>", self._on_grid_scroll)    
        self.root.bind_all("<Button-3>", self.show_context_menu)

        self.empty_state_frame = tk.Frame(tree_frame)
        self.empty_state_img_label = ttk.Label(self.empty_state_frame)
        self.empty_state_img_label.pack(pady=(80, 20))
        

        empty_text = (
            "Your library is completely empty.\n\n"
            "To get started:\n"
            "1. Navigate to 'File -> Authentication & Profiles' to link your Audible account.\n"
            "2. Download your library or drag and drop .aax or .m4b files directly into this window to import local media."
        )
        ttk.Label(self.empty_state_frame, text=empty_text, justify="center", font=("Segoe UI", 12)).pack()

        for col in self.library_tree["columns"]:
            self.library_tree.heading(col, text=col, command=lambda _col=col: self.sort_treeview(self.library_tree, _col, False))
            
        self.library_tree.column("Title", width=250)
        self.library_tree.column("Author", width=120)
        self.library_tree.column("Series", width=120)
        self.library_tree.column("Duration", width=70)
        self.library_tree.column("ASIN", width=90)
        self.library_tree.column("Status", width=110)
        self.library_tree.pack(side=tk.LEFT, fill="both", expand=True)
        
        self.library_tree.bind("<Double-1>", self.master_play)

        btn_frame = ttk.Frame(lib_frame)
        btn_frame.pack(fill="x", pady=2)
        ttk.Button(btn_frame, text="Refresh Cloud", command=self.fetch_cloud_library).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Download Selected", command=lambda: self.handle_action_on_selected("download")).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Convert Selected", command=lambda: self.handle_action_on_selected("convert")).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Convert All", command=self.start_convert_all_thread).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Manage Shelves", command=self.manage_shelves_prompt).pack(side=tk.LEFT, padx=5)

        local_btn_frame = ttk.Frame(lib_frame)
        local_btn_frame.pack(fill="x", pady=2)
        ttk.Button(local_btn_frame, text="Add Local File", command=self.add_local_file).pack(side=tk.LEFT, padx=5)
        ttk.Button(local_btn_frame, text="Remove from List", command=self.remove_local_file).pack(side=tk.LEFT, padx=5)
        ttk.Button(local_btn_frame, text="Scrape Metadata", command=lambda: self.handle_action_on_selected("scrape")).pack(side=tk.LEFT, padx=5)

        dl_prog_frame = ttk.Frame(lib_frame)
        dl_prog_frame.pack(fill="x", padx=5)
        
        self.dl_status_var = tk.StringVar(value="Idle")
        self.dl_progress_var = tk.DoubleVar()
        ttk.Label(dl_prog_frame, textvariable=self.dl_status_var).pack(side=tk.TOP, anchor="w")
        ttk.Progressbar(dl_prog_frame, variable=self.dl_progress_var, maximum=100).pack(side=tk.TOP, fill="x")

        self.refresh_library_ui()

    def build_player_components(self, parent):
        play_frame = ttk.LabelFrame(parent, text="Playback", padding=10)
        play_frame.pack(fill="x", expand=True, padx=5, pady=5)

        self.is_playing = False
        self.is_paused = False
        self.chapter_duration = 0
        self.current_play_time = 0

        top_row = ttk.Frame(play_frame)
        top_row.pack(fill="x", pady=2)
        
        self.info_label = ttk.Label(top_row, text="", justify="left")
        self.info_label.pack(side=tk.LEFT, padx=5)
        
        self.time_label = ttk.Label(top_row, text="00:00 / 00:00")
        self.time_label.pack(side=tk.RIGHT, padx=5)

        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(play_frame, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(fill="x", padx=5, pady=5)

        controls_frame = ttk.Frame(play_frame)
        controls_frame.pack(pady=5)

        ttk.Button(controls_frame, text="<< Prev Chapter", width=14, command=self.prev_chapter).pack(side=tk.LEFT, padx=2)
        ttk.Button(controls_frame, text="-30s", width=5, command=lambda: self.seek_audio(-30)).pack(side=tk.LEFT, padx=2)
        ttk.Button(controls_frame, text="Play", width=8, command=self.master_play).pack(side=tk.LEFT, padx=2)
        ttk.Button(controls_frame, text="Pause", width=8, command=self.pause_audio).pack(side=tk.LEFT, padx=2)
        ttk.Button(controls_frame, text="+30s", width=5, command=lambda: self.seek_audio(30)).pack(side=tk.LEFT, padx=2)
        ttk.Button(controls_frame, text="Next Chapter >>", width=14, command=self.next_chapter).pack(side=tk.LEFT, padx=2)
        ttk.Button(controls_frame, text="🔖 Bookmark", width=12, command=self.add_bookmark).pack(side=tk.LEFT, padx=(10, 2))
        ttk.Button(controls_frame, text="📑 Chapters", command=self.open_chapter_window).pack(side=tk.LEFT, padx=(15, 2))

        self.playback_speed = tk.StringVar(value="1.0x")
        speed_options = ["0.8x", "1.0x", "1.1x", "1.25x", "1.5x", "1.75x", "2.0x", "2.5x", "3.0x"]
        
        speed_menu = ttk.Combobox(controls_frame, textvariable=self.playback_speed, values=speed_options, state="readonly", width=5)
        speed_menu.bind("<<ComboboxSelected>>", self.on_speed_change)
        speed_menu.pack(side=tk.LEFT, padx=10)

        self.volume_var = tk.DoubleVar(value=100.0)
        vol_frame = ttk.Frame(controls_frame)
        vol_frame.pack(side=tk.LEFT, padx=5)
        ttk.Label(vol_frame, text="Vol:").pack(side=tk.LEFT)
        self.vol_slider = ttk.Scale(vol_frame, from_=0, to=100, orient=tk.HORIZONTAL, variable=self.volume_var, command=self.on_volume_change, length=80)
        self.vol_slider.pack(side=tk.LEFT)

        timer_frame = ttk.Frame(controls_frame)
        timer_frame.pack(side=tk.LEFT, padx=15)
        
        self.timer_btn = ttk.Button(timer_frame, text="Sleep: Off", command=self.open_sleep_menu, width=16)
        self.timer_btn.pack(side=tk.LEFT)
        
        self.timer_countdown_var = tk.StringVar(value="")
        ttk.Label(timer_frame, textvariable=self.timer_countdown_var, width=5).pack(side=tk.LEFT)

        self.voice_boost_var = tk.BooleanVar(value=False)
        self.skip_silence_var = tk.BooleanVar(value=False)
        
        filters_frame = ttk.Frame(play_frame)
        filters_frame.pack(fill="x", pady=(5, 0))
        
        ttk.Label(filters_frame, text="Filters:").pack(side=tk.LEFT, padx=(5, 10))
        
        ttk.Checkbutton(
            filters_frame, text="Voice Boost (Compressor)", 
            variable=self.voice_boost_var, command=self.on_filter_change
        ).pack(side=tk.LEFT, padx=5)
        
        ttk.Checkbutton(
            filters_frame, text="Skip Silence", 
            variable=self.skip_silence_var, command=self.on_filter_change
        ).pack(side=tk.LEFT, padx=5)

    def build_context_menu(self):
        self.context_menu = tk.Menu(self.root, tearoff=0)
        
        # Playback Controls
        self.context_menu.add_command(label="▶ Play", command=self.master_play)
        # self.context_menu.add_command(label="⏸ Pause", command=self.pause_audio)
        self.context_menu.add_separator()
        
        # Chapter Navigation
        # self.context_menu.add_command(label="⏮ Prev Chapter", command=self.prev_chapter)
        # self.context_menu.add_command(label="⏭ Next Chapter", command=self.next_chapter)
        # self.context_menu.add_separator()
        
        # # Seeking
        # self.context_menu.add_command(label="⏪ -30s", command=lambda: self.seek_audio(-30))
        # self.context_menu.add_command(label="⏩ +30s", command=lambda: self.seek_audio(30))
        # self.context_menu.add_separator()

        # File Operations 
        self.context_menu.add_command(label="⬇️ Download", command=lambda: self.handle_action_on_selected("download"))
        self.context_menu.add_command(label="🔄 Convert", command=lambda: self.handle_action_on_selected("convert"))
        self.context_menu.add_command(label="🔍 Scrape Metadata", command=lambda: self.handle_action_on_selected("scrape"))
        # self.context_menu.add_separator()

        # Tools
        # self.context_menu.add_command(label="🔖 Bookmark", command=self.add_bookmark)
        # self.context_menu.add_command(label="📑 Chapters", command=self.open_chapter_window)

    def build_bookmarks_components(self, parent):
        self.bm_frame = ttk.LabelFrame(parent, text="Bookmarks & Notes", padding=10)
        self.bm_frame.pack(fill="both", expand=True, padx=5, pady=5)

        scroll = ttk.Scrollbar(self.bm_frame)
        scroll.pack(side=tk.RIGHT, fill="y")

        self.bm_tree = ttk.Treeview(self.bm_frame, columns=("Time", "Note"), show="headings", yscrollcommand=scroll.set, height=5)
        self.bm_tree.heading("Time", text="Time")
        self.bm_tree.heading("Note", text="Note")
        
        self.bm_tree.column("Time", width=140, anchor="w", stretch=False)
        self.bm_tree.column("Note", width=150, anchor="w")
        self.bm_tree.pack(fill="both", expand=True)

        scroll.config(command=self.bm_tree.yview)

        # Double click to jump to the bookmark
        self.bm_tree.bind("<Double-1>", self.jump_to_bookmark)
        
        btn_frame = ttk.Frame(self.bm_frame)
        btn_frame.pack(fill="x", pady=(5, 0))
        ttk.Button(btn_frame, text="Delete Selected", command=self.delete_bookmark).pack(side=tk.RIGHT)

    def show_context_menu(self, event):
        # If we are in the list view, select the item under the cursor first
        if getattr(self, 'current_view_mode', 'list') == "list":
            item = self.library_tree.identify_row(event.y)
            if item:
                self.library_tree.selection_set(item)
                self.library_tree.focus(item)
                self.on_item_select() # Update the side panel preview

        # Pop the menu at the exact screen coordinates of the mouse click
        try:
            self.context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.context_menu.grab_release()

    def open_chapter_window(self):
        # Ensure a book is loaded and has chapter data
        if not hasattr(self, 'chapters') or not self.chapters:
            messagebox.showinfo("Chapters", "No chapter data available. Please load an audiobook first.")
            return

        # Prevent spam-opening multiple windows
        if hasattr(self, 'chapter_win') and self.chapter_win.winfo_exists():
            self.chapter_win.lift()
            self.chapter_win.focus_set()
            return

        self.chapter_win = tk.Toplevel(self.root)
        self.chapter_win.title("Select Chapter")
        self.chapter_win.geometry("450x500")
        self.chapter_win.transient(self.root) # Keeps it floating above the main window
        
        # Match theme colors
        style = ttk.Style()
        bg_color = style.lookup("TFrame", "background") or "#f0f0f0"
        self.chapter_win.configure(bg=bg_color)
        
        main_frame = ttk.Frame(self.chapter_win, padding=10)
        main_frame.pack(fill="both", expand=True)
        
        ttk.Label(main_frame, text="Table of Contents", font=("Segoe UI", 14, "bold")).pack(pady=(0, 10))

        # Build the Treeview
        columns = ("Index", "Title", "Start Time")
        tree = ttk.Treeview(main_frame, columns=columns, show="headings", selectmode="browse")
        
        tree.heading("Index", text="#")
        tree.column("Index", width=40, anchor="center")
        
        tree.heading("Title", text="Chapter Title")
        tree.column("Title", width=250, anchor="w")
        
        tree.heading("Start Time", text="Start Time")
        tree.column("Start Time", width=100, anchor="center")

        # Scrollbar
        scrollbar = ttk.Scrollbar(main_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        
        tree.pack(side=tk.LEFT, fill="both", expand=True)
        scrollbar.pack(side=tk.RIGHT, fill="y")

        # Populate Data
        for i, chap in enumerate(self.chapters):
            # Handle standard ffprobe chapter format dictionaries
            start_sec = float(chap.get('start_time', 0))
            
            # Format to HH:MM:SS
            h, m = divmod(start_sec, 3600)
            m, s = divmod(m, 60)
            time_str = f"{int(h):02d}:{int(m):02d}:{int(s):02d}"

            # Fallback for titles if metadata is missing
            title = chap.get('tags', {}).get('title', f"Chapter {i+1}")

            tree.insert("", "end", values=(i+1, title, time_str))

        # Double-click binding
        tree.bind("<Double-1>", lambda e: self.on_chapter_select(tree))

    def on_chapter_select(self, tree):
        selected = tree.focus()
        if not selected:
            return
            
        item = tree.item(selected)
        # The index in the Treeview is 1-based, so subtract 1 for the 0-based list
        target_idx = int(item['values'][0]) - 1 

        if 0 <= target_idx < len(self.chapters):
            # Close the window
            self.chapter_win.destroy()
            
            # Stop current playback
            if hasattr(self, 'stop_audio'):
                self.stop_audio()
                
            # Update internal state
            self.current_chapter_index = target_idx
            self.current_play_time = float(self.chapters[target_idx].get('start_time', 0))
            
            # Restart playback from the new position
            # (Use whatever your primary play method is named)
            self.play_chapter()

    def start_convert_all_thread(self):
        to_convert = [path for path, data in self.local_library.items() if data.get("format", "").upper() in ["AAX", "AAXC"]]
        
        if not to_convert:
            messagebox.showinfo("Convert All", "No AAX or AAXC files found to convert.")
            return
        required_bytes = sum(os.path.getsize(p) for p in to_convert if os.path.exists(p))
        if not self.has_enough_disk_space(self.base_dir, required_bytes + (500 * 1024 * 1024)): # Add 500MB padding
            required_gb = required_bytes / (1024**3)
            messagebox.showerror(
                "Insufficient Storage", 
                f"Batch conversion requires at least {required_gb:.2f} GB of free space on your drive.\n\n"
                "Please free up space and try again."
            )
            return
        if not messagebox.askyesno("Convert All", f"Found {len(to_convert)} files to convert.\nThis will process sequentially in the background. Proceed?"):
            return
            
        threading.Thread(target=self.convert_all_worker, args=(to_convert,), daemon=True).start()

    def on_item_select(self, event=None):
        if getattr(self, 'current_view_mode', 'list') == "list":
            selected = self.library_tree.focus()
            if not selected: return
            item = self.library_tree.item(selected)
            title = item['values'][0]
            authors = item['values'][1]
            asin = item['values'][4]
        else:
            if not getattr(self, '_selected_grid_item', None): return
            item = self._selected_grid_item
            title = item['values'][0]
            authors = item['values'][1]
            asin = item['values'][4]

        if hasattr(self, 'author_label'):
            self.author_label.config(text=authors)
        
        cover_path = None
        covers_dir = getattr(self, 'covers_dir', self.base_dir)
        
        if asin and asin != "Unknown":
            test_path = os.path.join(covers_dir, f"{asin}.jpg")
            if os.path.exists(test_path):
                cover_path = test_path
                
        if not cover_path:
            for p, d in getattr(self, 'local_library', {}).items():
                if d.get("title") == title:
                    test_local = os.path.splitext(p)[0] + "_cover.jpg"
                    if os.path.exists(test_local):
                        cover_path = test_local
                    break

        if cover_path and hasattr(self, 'cover_label'):
            try:
                from PIL import Image, ImageTk
                img = Image.open(cover_path)
                img.thumbnail((400, 400), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                self.cover_label.config(image=photo, text="")
                self.current_cover_photo = photo 
            except Exception:
                self.cover_label.config(image="", text=title)
        elif hasattr(self, 'cover_label'):
            self.cover_label.config(image="", text=title)

    def convert_all_worker(self, file_list):
        total = len(file_list)
        
        try:
            with keep.running():
                for idx, filepath in enumerate(file_list, 1):
                    if not os.path.exists(filepath):
                        continue
                        
                    data = self.local_library.get(filepath, {})
                    title = data.get("title", "Unknown")
                    
                    self.root.after(0, lambda i=idx, t=title: self.dl_status_var.set(f"Converting {i}/{total}: {t}"))
                    
                    base_name, _ = os.path.splitext(filepath)
                    out_path = f"{base_name}.m4b"
                    
                    drm_flags = self.get_drm_flags(filepath)
                    
                    cmd = ["ffmpeg", "-y"] + drm_flags + ["-i", filepath, "-c", "copy", out_path]
                    
                    try:
                        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                        
                        if res.returncode == 0:
                            self.local_library[out_path] = data
                            self.local_library[out_path]["format"] = "M4B"
                            self.local_library[out_path]["path"] = out_path
                            
                            if os.path.exists(filepath):
                                os.remove(filepath)
                            del self.local_library[filepath]
                            self.save_local_db()
                            
                            self.root.after(0, self.refresh_library_ui)
                        else:
                            self.write_log(f"Batch Convert Error on {title}: {res.stderr}")
                            
                    except Exception as e:
                        self.write_log(f"Batch Convert Exception on {title}: {e}")
        finally:
            self.root.after(0, lambda: self.dl_status_var.set("Idle"))
            self.root.after(0, lambda: messagebox.showinfo("Convert All", "Batch conversion complete!"))

    def manage_shelves_prompt(self):
        if getattr(self, 'current_view_mode', 'list') == "list":
            selected = self.library_tree.focus()
            if not selected:
                messagebox.showwarning("Selection Required", "Please select an audiobook to tag.")
                return
            item = self.library_tree.item(selected)
        else:
            if not hasattr(self, '_selected_grid_item') or not self._selected_grid_item:
                messagebox.showwarning("Selection Required", "Please select an audiobook to tag.")
                return
            item = self._selected_grid_item

        title = item['values'][0]
        asin = item['values'][4]

        if not asin or asin == "Unknown":
            messagebox.showerror("Error", "Cannot tag an orphaned file without an ASIN. Please scrape its metadata first.")
            return

        if "shelves_db" not in self.settings:
            self.settings["shelves_db"] = {}

        current_shelves = self.settings["shelves_db"].get(asin, [])
        current_shelves_str = ", ".join(current_shelves)

        new_shelves_str = simpledialog.askstring(
            "Manage Shelves", 
            f"Enter custom shelves for:\n{title}\n\n(Separate multiple tags with commas)", 
            initialvalue=current_shelves_str
        )

        if new_shelves_str is not None:
            tags = [t.strip() for t in new_shelves_str.split(",") if t.strip()]
            self.settings["shelves_db"][asin] = tags
            self.save_settings()
            
            self.refresh_library_ui()


    def open_achievements_window(self):
        if hasattr(self, 'ach_window') and self.ach_window.winfo_exists():
            self.ach_window.lift()
            self.ach_window.focus_set()
            return

        self.ach_window = tk.Toplevel(self.root)
        self.ach_window.title("My Achievements")
        self.ach_window.geometry("450x600")
        self.ach_window.transient(self.root)

        style = ttk.Style()
        bg_color = style.lookup("TFrame", "background") or "#f0f0f0"
        fg_color = style.lookup("TLabel", "foreground") or "#000000"
        self.ach_window.configure(bg=bg_color)
        
        main_frame = ttk.Frame(self.ach_window, padding=10)
        main_frame.pack(fill="both", expand=True)
        
        ttk.Label(main_frame, text="TomeBox Achievements", font=("Segoe UI", 16, "bold")).pack(pady=(0, 15))

        canvas = tk.Canvas(main_frame, bg=bg_color, highlightthickness=0)
        scrollbar = ttk.Scrollbar(main_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = tk.Frame(canvas, bg=bg_color)
        
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        
        canvas_window = canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")

        canvas.bind(
            "<Configure>",
            lambda e: canvas.itemconfig(canvas_window, width=e.width)
        )
        
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        stats = self.settings.get("stats", {})
        unlocked = stats.get("unlocked_achievements", [])

        for ach_id, data in getattr(self, 'achievements', {}).items():
            is_unlocked = ach_id in unlocked

            border_color = "#4a90e2" if is_unlocked else "#555555"
            status_icon = "🏆" if is_unlocked else "🔒"
            text_color = fg_color if is_unlocked else "#888888"
            
            card = tk.Frame(scrollable_frame, bg=bg_color, highlightbackground=border_color, highlightthickness=1)
            card.pack(fill="x", pady=5, padx=5)
            
            header_frame = tk.Frame(card, bg=bg_color)
            header_frame.pack(fill="x", padx=10, pady=(10, 0))
            
            tk.Label(header_frame, text=status_icon, font=("Segoe UI", 16), bg=bg_color).pack(side=tk.LEFT, padx=(0, 10))
            tk.Label(header_frame, text=data["title"], font=("Segoe UI", 12, "bold"), fg=text_color, bg=bg_color).pack(side=tk.LEFT)
            
            tk.Label(card, text=data["desc"], font=("Segoe UI", 9), fg=text_color, bg=bg_color).pack(anchor="w", padx=45, pady=(0, 5))

            current_val = stats.get(data["type"], 0)
            threshold = data["threshold"]
            
            if data["type"] == "seconds_listened":
                curr_h = int(current_val // 3600)
                thresh_h = int(threshold // 3600)
                prog_text = f"Progress: {curr_h}h / {thresh_h}h"
                percent = min(100, (current_val / threshold) * 100) if threshold > 0 else 0
            else:
                prog_text = f"Progress: {int(current_val)} / {threshold}"
                percent = min(100, (current_val / threshold) * 100) if threshold > 0 else 0
                
            if is_unlocked:
                prog_text = "Completed!"
                percent = 100

            bottom_frame = tk.Frame(card, bg=bg_color)
            bottom_frame.pack(fill="x", padx=10, pady=(0, 10))
            
            tk.Label(bottom_frame, text=prog_text, font=("Segoe UI", 8, "italic"), fg=text_color, bg=bg_color).pack(side=tk.RIGHT)

            bar_bg = "#333333" if is_unlocked else "#d3d3d3"
            bar_canvas = tk.Canvas(bottom_frame, height=6, bg=bar_bg, highlightthickness=0)
            bar_canvas.pack(side=tk.LEFT, fill="x", expand=True, padx=(35, 10))
            
            if percent > 0:
                bar_canvas.update_idletasks()
                bar_canvas.bind("<Configure>", lambda e, p=percent, c=bar_canvas, b=border_color: c.create_rectangle(0, 0, e.width * (p/100), e.height, fill=b, outline=""))
                
    def save_tray_setting(self):
        self.settings["minimize_to_tray"] = self.minimize_to_tray_var.get()
        self.save_settings()

    def on_filter_change(self):

        if getattr(self, 'is_playing', False):
            self.pause_audio()
            self.is_paused = False
            self.resume_playback()

    def handle_window_close(self):
        if self.minimize_to_tray_var.get():
            self.hide_window_to_tray()
        else:
            if hasattr(self, 'tray_icon') and self.tray_icon:
                self.tray_icon.stop()
            self.on_closing()

    def silent_sync_worker(self):
        if not getattr(self, 'auth_object', None):
            return

        try:
            self.write_log("Background sync: Polling Audible API...")
            client = audible.Client(auth=self.auth_object)
            response = client.get("1.0/library", response_groups="product_desc,product_attrs,series,contributors", num_results=1000)
            new_items = response.get("items", [])

            if len(new_items) != len(self.cloud_items):
                self.write_log(f"Background sync: Detected library change. Old: {len(self.cloud_items)}, New: {len(new_items)}")
                self.cloud_items = new_items
                self.save_cloud_cache()
                self.root.after(0, self.refresh_library_ui)
            else:
                self.write_log("Background sync: No changes detected.")

        except Exception as e:
            self.write_log(f"Background sync failed silently: {e}")
    
    def on_closing(self):
        self.save_playback_state()
        if self.player_process:
            self.player_process.terminate()
        self.root.destroy()

    def save_playback_state(self):
        if getattr(self, 'file_path', None) and self.file_path in self.local_library:
            chap_idx = getattr(self, 'current_chapter_idx', 0)
            rel_time = getattr(self, 'current_play_time', 0.0)
            
            self.local_library[self.file_path]["last_chapter"] = chap_idx
            self.local_library[self.file_path]["last_time"] = rel_time
            
            if hasattr(self, 'chapters') and self.chapters and chap_idx < len(self.chapters):
                abs_time = float(self.chapters[chap_idx].get("start_time", 0)) + rel_time
                self.local_library[self.file_path]["last_position"] = abs_time
                
            self.save_local_db()


    def fetch_metadata_worker(self, filepath):
        local_data = self.local_library.get(filepath, {})
        title = local_data.get("title", "")
        asin = local_data.get("asin")

        authors = ""
        for item in getattr(self, 'cloud_items', []):
            if item.get("title") == title or item.get("asin") == asin:
                asin = item.get("asin")
                raw_authors = item.get("authors", [])
                authors = ", ".join([a.get("name", "") for a in raw_authors if isinstance(a, dict)])
                break
        
        if not asin:
            self.root.after(0, lambda: self.cover_label.config(image="", text="Metadata Unavailable"))
            self.root.after(0, lambda: self.author_label.config(text=authors))
            return

        cover_path = os.path.join(getattr(self, 'covers_dir', self.base_dir), f"{asin}.jpg")

        if os.path.exists(cover_path):
            try:
                img = Image.open(cover_path)
                img.thumbnail((400, 400))
                photo = ImageTk.PhotoImage(img)
                
                def update_ui_local():
                    self.current_cover_photo = photo
                    self.cover_label.config(image=photo, text="")
                    self.author_label.config(text=authors)
                
                self.root.after(0, update_ui_local)
                return 
            except Exception as e:
                self.write_log(f"Failed to load local cover cache, falling back to API: {e}")

        if not getattr(self, 'auth_object', None):
            return
            
        try:
            client = audible.Client(auth=self.auth_object)
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
                    
                img = Image.open(io.BytesIO(img_data))
                img.thumbnail((250, 250))
                photo = ImageTk.PhotoImage(img)
                
                def update_ui_api():
                    self.current_cover_photo = photo
                    self.cover_label.config(image=photo, text="")
                    self.author_label.config(text=authors)
                
                self.root.after(0, update_ui_api)
            else:
                self.root.after(0, lambda: self.cover_label.config(image="", text="No Cover Art Found"))
                self.root.after(0, lambda: self.author_label.config(text=authors))
                
        except Exception as e:
            self.write_log(f"Metadata Fetch Error: {e}")
            self.root.after(0, lambda: self.cover_label.config(image="", text="Failed to load metadata"))
            
    def load_settings(self):
        if os.path.exists(self.settings_path):
            with open(self.settings_path, "r") as f:
                return json.load(f)
        return {}
    
    def save_settings(self):
        with open(self.settings_path, "w") as f:
            json.dump(self.settings, f, indent=4)

    def load_local_db(self):
        if os.path.exists(self.local_db_path):
            with open(self.local_db_path, "r") as f:
                raw_db = json.load(f)

            cleaned_db = {path: data for path, data in raw_db.items() if os.path.exists(path)}
            return cleaned_db
        return {}

    def save_local_db(self):
        with open(self.local_db_path, "w") as f:
            json.dump(self.local_library, f, indent=4)

        self.last_db_mtime = os.path.getmtime(self.local_db_path)
    
    def load_cloud_cache(self):
        if os.path.exists(self.cloud_cache_path):
            try:
                with open(self.cloud_cache_path, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return []

    def save_cloud_cache(self):
        try:
            with open(self.cloud_cache_path, "w") as f:
                json.dump(self.cloud_items, f, indent=4)
        except Exception as e:
            self.write_log(f"Failed to save cloud cache: {e}")

    def set_download_folder(self):
        directory = filedialog.askdirectory(title="Select Default Download Folder")
        if directory:
            self.default_download_dir = directory
            self.settings["download_dir"] = directory
            self.save_settings()
            messagebox.showinfo("Folder Saved", f"Default download folder updated to:\n{directory}")

    def download_title_prompt(self):
        selected = self.cloud_tree.focus()
        if not selected:
            messagebox.showwarning("Selection Required", "Select a title from the Cloud Library first.")
            return

        item = self.cloud_tree.item(selected)
        title = item['values'][0]
        asin = item['values'][3]

        if not asin or asin == "Unknown":
            messagebox.showerror("Data Error", "This item does not have a valid ASIN.")
            return

        save_dir = self.default_download_dir
        if not save_dir:
            save_dir = filedialog.askdirectory(title=f"Select Download Folder for '{title}'")
            if not save_dir:
                return

        self.write_log(f"Starting download process for ASIN: {asin}")
        threading.Thread(target=self.download_single_worker, args=(asin, title, save_dir), daemon=True).start()

    def download_single_worker(self, asin, title, save_dir):
        try:
            self.execute_download(asin, title, save_dir)
            self.root.after(0, lambda: messagebox.showinfo("Success", f"Finished downloading:\n{title}"))
        except Exception as e:
            import traceback
            error_trace = traceback.format_exc()
            self.write_log(f"DOWNLOAD ERROR:\n{error_trace}")
            error_msg = str(e) 
            self.root.after(0, lambda err=error_msg: messagebox.showerror("Download Error", f"Failed to download.\n\n{err}\n\nCheck log for details."))
        finally:
            self.root.after(0, lambda: self.dl_status_var.set("Idle"))
            self.root.after(0, lambda: self.dl_progress_var.set(0))

    def download_queue_worker(self, items, save_dir):
        try:
            # 1. Use the new cross-platform wakepy context manager
            with keep.running():
                for item in items:
                    title = item[0]
                    asin = item[3]
                    
                    # 2. Check if the user hit the "Cancel All" button
                    if asin in getattr(self, 'active_downloads', {}) and self.active_downloads[asin].get("cancel_flag"):
                        self.root.after(0, lambda a=asin: self.active_downloads[a]["status_var"].set("Canceled"))
                        continue
                    
                    safe_title = "".join([c for c in title if c.isalpha() or c.isdigit() or c==' ']).rstrip()
                    if os.path.exists(os.path.join(save_dir, f"{safe_title}.aaxc")) or os.path.exists(os.path.join(save_dir, f"{safe_title}.aax")):
                        self.write_log(f"Skipping {title}, file already exists.")
                        # Optionally update the UI so it doesn't get stuck saying "Waiting..."
                        if asin in getattr(self, 'active_downloads', {}):
                            self.root.after(0, lambda a=asin: self.active_downloads[a]["status_var"].set("Skipped"))
                        continue

                    # 3. Isolate each download so one failure doesn't kill the whole queue
                    try:
                        self.download_worker(asin, title, save_dir, is_queue=True)
                    except Exception as e:
                        self.write_log(f"Failed to queue download for {title}: {e}")
                        if asin in getattr(self, 'active_downloads', {}):
                            self.root.after(0, lambda a=asin: self.active_downloads[a]["status_var"].set("Failed"))
                        
        finally:
            self.root.after(0, lambda: self.dl_status_var.set("All downloads completed."))
            self.root.after(0, lambda: self.dl_progress_var.set(0))
            self.root.after(0, lambda: messagebox.showinfo("Download Queue Finished", "Finished processing all titles."))
    
    def execute_download(self, asin, title, save_dir):
        self.root.after(0, lambda: self.dl_status_var.set(f"Downloading: {title}"))
        self.root.after(0, lambda: self.dl_progress_var.set(0))
        
        client = audible.Client(auth=self.auth_object)
        self.write_log(f"Requesting DRM license and download URL from Audible for: {title}...")
        
        resp = client.post(
            f"1.0/content/{asin}/licenserequest",
            body={"drm_type": "Adrm", "consumption_type": "Download"}
        )

        content_license = resp.get("content_license", {})
        content_metadata = content_license.get("content_metadata", {})
        content_url = content_metadata.get("content_url", {}).get("offline_url")
        
        if not content_url:
            raise Exception("Could not find 'offline_url' in the payload.")
        
        offline_key = content_metadata.get("content_key", {}).get("offline_key")
        audible_key, audible_iv = None, None
        
        if offline_key:
            import rsa
            import base64
            priv_pem = getattr(self.auth_object, "rsa_private_key", None) or getattr(self.auth_object, "_rsa_private_key", None)
            if priv_pem:
                priv_key = rsa.PrivateKey.load_pkcs1(priv_pem.encode('utf-8'))
                decrypted = rsa.decrypt(base64.b64decode(offline_key), priv_key)
                audible_key = decrypted[:16].hex()
                audible_iv = decrypted[16:].hex()

        safe_title = "".join([c for c in title if c.isalpha() or c.isdigit() or c==' ']).rstrip()
        ext = ".aaxc" if audible_key else ".aax"
        filepath = os.path.join(save_dir, f"{safe_title}{ext}")
        
        self.write_log(f"Downloading file to: {filepath}")
        
        headers = {"User-Agent": "Audible/6.6.1 (iPhone; iOS 15.5; Scale/3.00)"}
        import urllib.request
        req = urllib.request.Request(content_url, headers=headers)
        
        with urllib.request.urlopen(req) as response, open(filepath, 'wb') as out_file:
            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0
            
            while True:
                chunk = response.read(32768)
                if not chunk: break
                out_file.write(chunk)
                if total_size > 0:
                    downloaded += len(chunk)
                    percent = (downloaded / total_size) * 100
                    self.root.after(0, self.dl_progress_var.set, percent)
        
        self.local_library[filepath] = {
            "title": title, 
            "format": "AAXC" if audible_key else "AAX", 
            "path": filepath,
            "audible_key": audible_key,
            "audible_iv": audible_iv
        }
        self.save_local_db()
        self.root.after(0, self.refresh_local_ui)

    

    # def toggle_custom_colors(self):
    #     if self.settings.get("ui_mode", "modern") == "classic":
    #         messagebox.showinfo(
    #             "Engine Restriction", 
    #             "Light / Dark mode toggling is a feature of the Modern Engine (sv_ttk).\n\n"
    #             "To use this feature, please select the Modern Engine from the Appearance menu and restart TomeBox."
    #         )
    #         return

    #     import sv_ttk

    #     current_theme = sv_ttk.get_theme()
        
    #     if current_theme == "dark":
    #         sv_ttk.set_theme("light")
    #         bg_color = "#f3f3f3" 
    #     else:
    #         sv_ttk.set_theme("dark")
    #         bg_color = "#1c1c1c" 

    #     if hasattr(self, 'queue_canvas'):
    #         self.queue_canvas.config(bg=bg_color)
    #         self.queue_inner.config(bg=bg_color)

    #         for data in getattr(self, 'active_downloads', {}).values():
    #             if "frame" in data:
    #                 data["frame"].config(bg=bg_color)

    def apply_classic_palette(self, palette_name):
        style = ttk.Style()
        style.theme_use("clam")

        palettes = {
            "light": {"bg": "#f0f0f0", "fg": "#000000", "entry": "#ffffff", "select": "#0078D7", "btn": "#e1e1e1", "border": "#cccccc"},
            "dark": {"bg": "#2b2b2b", "fg": "#e0e0e0", "entry": "#1e1e1e", "select": "#4a90e2", "btn": "#3c3c3c", "border": "#555555"},
            "terminal": {"bg": "#0c0c0c", "fg": "#00ff00", "entry": "#000000", "select": "#005500", "btn": "#1a1a1a", "border": "#004400"},
            "solarized_dark": {"bg": "#002b36", "fg": "#839496", "entry": "#073642", "select": "#cb4b16", "btn": "#073642", "border": "#586e75"},
            "solarized_light": {"bg": "#fdf6e3", "fg": "#657b83", "entry": "#eee8d5", "select": "#268bd2", "btn": "#eee8d5", "border": "#93a1a1"},
            "dracula": {"bg": "#282a36", "fg": "#f8f8f2", "entry": "#44475a", "select": "#bd93f9", "btn": "#44475a", "border": "#6272a4"},
            "cyberpunk": {"bg": "#0a0a2a", "fg": "#00ffcc", "entry": "#161638", "select": "#ff00ff", "btn": "#20204a", "border": "#00ffff"},
            "nord": {"bg": "#2e3440", "fg": "#d8dee9", "entry": "#3b4252", "select": "#5e81ac", "btn": "#434c5e", "border": "#4c566a"}
        }
        
        colors = palettes.get(palette_name, palettes["light"])
        
        self.root.configure(bg=colors["bg"])
        
        def paint_structural_frames(widget):
            if type(widget) in (tk.Frame, tk.Tk):
                try:
                    widget.configure(bg=colors["bg"])
                except tk.TclError:
                    pass
            for child in widget.winfo_children():
                paint_structural_frames(child)
                
        paint_structural_frames(self.root)
        
        style.configure(".", background=colors["bg"], foreground=colors["fg"], bordercolor=colors["border"], lightcolor=colors["bg"], darkcolor=colors["bg"])
        style.configure("TFrame", background=colors["bg"])
        
        style.configure("TButton", background=colors["btn"], borderwidth=1, bordercolor=colors["border"])
        style.map("TButton", background=[("active", colors["select"])])
        
        style.configure("TMenubutton", background=colors["bg"], foreground=colors["fg"], borderwidth=0, arrowcolor=colors["bg"])
        style.map("TMenubutton", background=[("active", colors["select"])], foreground=[("active", "#ffffff")])
        
        if hasattr(self, 'file_menu'):
            menu_list = [self.file_menu, self.appearance_menu, self.export_menu, self.help_menu]
            for m in menu_list:
                m.config(
                    bg=colors["entry"], 
                    fg=colors["fg"], 
                    activebackground=colors["select"], 
                    activeforeground="#ffffff",
                    activeborderwidth=0,
                    borderwidth=1
                )

        style.configure("TCombobox", fieldbackground=colors["entry"], background=colors["btn"], arrowcolor=colors["fg"], foreground=colors["fg"])
        style.map("TCombobox", 
                  fieldbackground=[("readonly", colors["entry"])], 
                  selectbackground=[("readonly", colors["select"])], 
                  selectforeground=[("readonly", "#ffffff")])
                  
        self.root.option_add('*TCombobox*Listbox.background', colors["entry"])
        self.root.option_add('*TCombobox*Listbox.foreground', colors["fg"])
        self.root.option_add('*TCombobox*Listbox.selectBackground', colors["select"])
        self.root.option_add('*TCombobox*Listbox.selectForeground', "#ffffff")
        
        def repaint_combobox_dropdowns(widget):
            if isinstance(widget, ttk.Combobox):
                try:
                    popdown = widget.tk.eval(f'ttk::combobox::PopdownWindow {widget._w}')
                    widget.tk.call(f'{popdown}.f.l', 'configure',
                                   '-background', colors["entry"],
                                   '-foreground', colors["fg"],
                                   '-selectbackground', colors["select"],
                                   '-selectforeground', "#ffffff")
                except tk.TclError:
                    pass
            for child in widget.winfo_children():
                repaint_combobox_dropdowns(child)
                
        repaint_combobox_dropdowns(self.root)

        style.configure("TEntry", fieldbackground=colors["entry"], foreground=colors["fg"])
        
        style.configure("Treeview", background=colors["entry"], foreground=colors["fg"], fieldbackground=colors["entry"], bordercolor=colors["border"])
        style.map("Treeview", background=[("selected", colors["select"])], foreground=[("selected", "#ffffff")])
        
        style.configure("Treeview.Heading", background=colors["btn"], foreground=colors["fg"], bordercolor=colors["border"])
        style.map("Treeview.Heading", background=[("active", colors["select"])], foreground=[("active", "#ffffff")])
        
        style.configure("TLabelframe", bordercolor=colors["border"])
        style.configure("TLabelframe.Label", background=colors["bg"], foreground=colors["fg"])
        
        style.configure("TProgressbar", background=colors["select"], troughcolor=colors["entry"], bordercolor=colors["border"])
        
        style.configure("Vertical.TScrollbar", background=colors["btn"], troughcolor=colors["bg"], arrowcolor=colors["fg"], bordercolor=colors["border"])
        style.configure("Horizontal.TScrollbar", background=colors["btn"], troughcolor=colors["bg"], arrowcolor=colors["fg"], bordercolor=colors["border"])
        
        style.configure("Sash", background=colors["border"], sashthickness=4)
        
        if hasattr(self, 'queue_canvas'):
            self.queue_canvas.config(bg=colors["bg"], highlightthickness=0)
            self.queue_inner.config(bg=colors["bg"])
            for data in getattr(self, 'active_downloads', {}).values():
                if "frame" in data:
                    data["frame"].config(bg=colors["bg"])
                    
        self.settings["classic_palette"] = palette_name
        self.save_settings()

    # def on_ui_mode_change(self, *args):
    #     new_mode = self.ui_mode_var.get()
    #     self.settings["ui_mode"] = new_mode
    #     self.save_settings()
            
    #     messagebox.showinfo("Restart Required", f"UI engine set to '{new_mode}'.\n\nPlease restart TomeBox to apply the changes.")

    def download_all_prompt(self):
        save_dir = getattr(self, 'default_download_dir', '')
        if not save_dir:
            save_dir = filedialog.askdirectory(title="Select Download Folder for All Titles")
            if not save_dir: return
            self.default_download_dir = save_dir
            self.settings["download_dir"] = save_dir
            self.save_settings()
            self.lbl_download_dir.config(text=save_dir)

        items_to_download = []
        for child in self.cloud_tree.get_children():
            values = self.cloud_tree.item(child)['values']
            if values[3] and values[3] != "Unknown":
                items_to_download.append(values)

        if not items_to_download:
            return

        threading.Thread(target=self.download_queue_worker, args=(items_to_download, save_dir), daemon=True).start()

    

    def toggle_library_view(self):
        scroll_bar = None
        for child in self.library_tree.master.winfo_children():
            if isinstance(child, ttk.Scrollbar) and str(child.cget("orient")) == "vertical":
                scroll_bar = child
                break

        if self.current_view_mode == "list":
            self.current_view_mode = "grid"
            self.view_btn.config(text="List View")
            self.library_tree.pack_forget()
            
            if self.cloud_items or self.local_library:
                self.grid_canvas.pack(side=tk.LEFT, fill="both", expand=True)
            
            if scroll_bar:
                scroll_bar.config(command=self.grid_canvas.yview)
                self.grid_canvas.config(yscrollcommand=scroll_bar.set)
        else:
            self.current_view_mode = "list"
            self.view_btn.config(text="Grid View")
            self.grid_canvas.pack_forget()
            
            if self.cloud_items or self.local_library:
                self.library_tree.pack(side=tk.LEFT, fill="both", expand=True)
            
            if scroll_bar:
                scroll_bar.config(command=self.library_tree.yview)
                self.library_tree.config(yscrollcommand=scroll_bar.set)
            
        self.refresh_library_ui()

    def on_canvas_resize(self, event):

        if hasattr(self, 'grid_window_id'):
            self.grid_canvas.itemconfig(self.grid_window_id, width=event.width)
        if getattr(self, '_last_canvas_width', None) == event.width:
            return
        self._last_canvas_width = event.width
        if hasattr(self, '_resize_timer'):
            self.root.after_cancel(self._resize_timer)
        self._resize_timer = self.root.after(200, self.draw_grid_view)

    def draw_grid_view(self):
        if getattr(self, 'current_view_mode', 'list') != "grid": return
        
        for widget in self.grid_inner.winfo_children():
            widget.destroy()

        if not hasattr(self, 'cover_cache'):
            self.cover_cache = {}

        style = ttk.Style()
        default_bg = style.lookup("TFrame", "background") or "#f0f0f0"
        default_fg = style.lookup("TLabel", "foreground") or "#000000"
        select_bg = "#4a90e2" 

        self.grid_canvas.config(bg=default_bg)
        self.grid_inner.config(bg=default_bg)
        
        canvas_width = self.grid_canvas.winfo_width()
        cols = max(1, canvas_width // 190)

        for i in range(20): 
            self.grid_inner.columnconfigure(i, weight=0)
        for i in range(cols):
            self.grid_inner.columnconfigure(i, weight=1)
        
        for idx, row_data in enumerate(getattr(self, '_current_filtered_data', [])):
            title, authors, series_str, duration_str, asin, status = row_data

            outer_card = tk.Frame(self.grid_inner, bg=default_bg)
            outer_card.grid(row=idx // cols, column=idx % cols, padx=5, pady=5)

            card = tk.Frame(outer_card, bg=default_bg, width=170, height=240, bd=0, highlightthickness=0)
            card.pack_propagate(False) 
            card.pack(padx=2, pady=2) 
            img_obj = None
            if asin in self.cover_cache:
                img_obj = self.cover_cache[asin]
            else:
                cover_path = os.path.join(getattr(self, 'covers_dir', self.base_dir), f"{asin}.jpg")
                if os.path.exists(cover_path):
                    try:
                        img = Image.open(cover_path)
                        img.thumbnail((150, 150))
                        img_obj = ImageTk.PhotoImage(img)
                        self.cover_cache[asin] = img_obj 
                    except: pass
                
            img_label = tk.Label(card, image=img_obj, text="No Cover" if not img_obj else "", bg=default_bg, fg=default_fg, bd=0, highlightthickness=0, takefocus=0)
            img_label.pack(pady=(5, 0))
            
            display_title = title[:45] + "..." if len(title) > 45 else title
            text_label = tk.Label(card, text=display_title, bg=default_bg, fg=default_fg, font=("Segoe UI", 9), wraplength=150, justify="center", bd=0, highlightthickness=0, takefocus=0)
            text_label.pack(pady=(5, 0))
            
            def on_card_click(e, oc=outer_card, t=title, a=asin, s=status):

                if hasattr(self, '_last_selected_card_frame') and self._last_selected_card_frame.winfo_exists():
                    self._last_selected_card_frame.config(bg=default_bg)
                
                oc.config(bg=select_bg)
                
                self._last_selected_card_frame = oc 
                self._selected_grid_item = {'values': [t, "", "", "", a, s]}
                self.on_item_select()
            def on_card_double_click(e, oc=outer_card, t=title, a=asin, s=status):
                on_card_click(e, oc, t, a, s)
                self.master_play()

            outer_card.bind("<Button-1>", on_card_click)
            outer_card.bind("<Double-1>", on_card_double_click)
            card.bind("<Button-1>", on_card_click)
            card.bind("<Double-1>", on_card_double_click)
            img_label.bind("<Button-1>", on_card_click)
            img_label.bind("<Double-1>", on_card_double_click)
            text_label.bind("<Button-1>", on_card_click)
            text_label.bind("<Double-1>", on_card_double_click)

        self.grid_inner.update_idletasks()
        self.grid_canvas.configure(scrollregion=self.grid_canvas.bbox("all"))

    def toggle_queue_visibility(self):
        current_panes = self.main_paned.panes()
        queue_str = str(self.queue_frame)
        
        if queue_str in current_panes:
            self.main_paned.forget(self.queue_frame)
        else:
            self.main_paned.add(self.queue_frame, weight=0)

    def cancel_all_downloads(self):
        if not getattr(self, 'active_downloads', None):
            return

        if messagebox.askyesno("Cancel All", "Cancel all active and pending downloads?"):
            for asin, data in self.active_downloads.items():
                current_status = data["status_var"].get()
                if not data["cancel_flag"] and current_status not in ["Complete", "Failed", "Canceled"]:
                    data["cancel_flag"] = True
                    data["status_var"].set("Canceling...")
            
            self.write_log("User initiated Cancel All Downloads.")

            self.dl_status_var.set("Downloads Canceled")
            self.dl_progress_var.set(0)
            self.root.after(3000, lambda: self.dl_status_var.set("Idle"))
            self.root.after(3000, lambda: self.toggle_queue_drawer(False))

    def toggle_queue_drawer(self, show=True):
        current_panes = self.main_paned.panes()
        queue_str = str(self.queue_frame)
        
        if show and queue_str not in current_panes:
            self.main_paned.add(self.queue_frame, weight=0)
        elif not show and queue_str in current_panes:
            self.main_paned.forget(self.queue_frame)

    def add_queue_ui_row(self, asin, title):
        row_frame = tk.Frame(self.queue_inner, bg="#1c1c1c")
        row_frame.pack(fill="x", pady=2, padx=5)

        title_lbl = ttk.Label(row_frame, text=title[:40] + ("..." if len(title) > 40 else ""), width=35, anchor="w")
        title_lbl.pack(side=tk.LEFT, padx=(0, 10))

        prog_var = tk.DoubleVar()
        prog_bar = ttk.Progressbar(row_frame, variable=prog_var, maximum=100, length=200)
        prog_bar.pack(side=tk.LEFT, fill="x", expand=True, padx=(0, 10))

        status_var = tk.StringVar(value="Waiting...")
        status_lbl = ttk.Label(row_frame, textvariable=status_var, width=15, anchor="w")
        status_lbl.pack(side=tk.LEFT, padx=(0, 10))

        cancel_btn = ttk.Button(row_frame, text="✕", command=lambda a=asin: self.cancel_download(a))
        cancel_btn.pack(side=tk.RIGHT)

        self.active_downloads[asin] = {
            "frame": row_frame,
            "prog_var": prog_var,
            "status_var": status_var,
            "cancel_flag": False
        }
        
    def cancel_download(self, asin):
        if asin in self.active_downloads:
            self.active_downloads[asin]["cancel_flag"] = True
            self.active_downloads[asin]["status_var"].set("Canceling...")

    def start_download_all(self):
        local_titles = {data["title"] for path, data in self.local_library.items()}
        missing_items = [item for item in getattr(self, 'cloud_items', []) if item.get("title") not in local_titles]

        if not missing_items:
            messagebox.showinfo("Up to Date", "Your local library already has all cloud items downloaded.")
            return
        save_dir = getattr(self, 'default_download_dir', self.base_dir)
        estimated_bytes_per_book = 500 * 1024 * 1024 # 500 MB
        total_required_bytes = len(missing_items) * estimated_bytes_per_book
        
        if not self.has_enough_disk_space(save_dir, total_required_bytes):
            required_gb = total_required_bytes / (1024**3)
            messagebox.showerror(
                "Insufficient Storage", 
                f"Downloading {len(missing_items)} books requires approximately {required_gb:.2f} GB of free space in your target folder.\n\n"
                "Please change your download directory or free up space."
            )
            return
        if messagebox.askyesno("Download All", f"Found {len(missing_items)} missing audiobooks.\n\nDo you want to batch download them all now? This may take a while depending on your internet connection."):
            self.dl_all_btn.config(state=tk.DISABLED)
            threading.Thread(target=self.download_all_worker, args=(missing_items,), daemon=True).start()

    def download_all_worker(self, missing_items):
        total = len(missing_items)
        
        save_dir = getattr(self, 'default_download_dir', "")
        if not save_dir:
            save_dir = getattr(self, 'base_dir', os.getcwd())

        self.root.after(0, lambda: self.toggle_queue_drawer(True))

        for item in missing_items:
            asin = item.get("asin")
            title = item.get("title", "Unknown")
            self.root.after(0, self.add_queue_ui_row, asin, title)

        try:
            with keep.running():
                for idx, item in enumerate(missing_items):
                    title = item.get("title", "Unknown")
                    asin = item.get("asin")

                    if asin in getattr(self, 'active_downloads', {}) and self.active_downloads[asin].get("cancel_flag"):
                        self.root.after(0, lambda a=asin: self.active_downloads[a]["status_var"].set("Canceled"))
                        continue
                    
                    self.root.after(0, lambda i=idx+1, t=total, name=title: self.dl_status_var.set(f"Batch Downloading ({i}/{t}): {name}..."))
                    
                    if asin in getattr(self, 'active_downloads', {}):
                        self.root.after(0, lambda a=asin: self.active_downloads[a]["status_var"].set("Starting..."))
                    
                    try:
                        self.download_worker(asin, title, save_dir, is_queue=True)
                    except Exception as e:
                        self.write_log(f"Failed to batch download {title}: {e}")
                        if asin in getattr(self, 'active_downloads', {}):
                            self.root.after(0, lambda a=asin: self.active_downloads[a]["status_var"].set("Failed"))
        finally:
            self.root.after(0, lambda: self.dl_status_var.set("Batch Download Complete"))
            self.root.after(0, lambda: self.dl_progress_var.set(0))
            self.root.after(0, self.refresh_library_ui)
            if hasattr(self, 'dl_all_btn'):
                self.root.after(0, lambda: self.dl_all_btn.config(state=tk.NORMAL))
            
            self.root.after(5000, lambda: self.toggle_queue_drawer(False))
            self.root.after(5000, lambda: self.dl_status_var.set("Idle"))

    def refresh_library_ui(self, *args):
        for row in self.library_tree.get_children():
            self.library_tree.delete(row)

        search_query = self.search_var.get().lower()
        current_filter = self.filter_var.get()
        current_shelf = getattr(self, 'shelf_filter_var', tk.StringVar(value="All Shelves")).get()

        local_titles = {data["title"]: data for path, data in self.local_library.items()}
        cloud_titles = []
        rows_to_insert = []

        all_unique_shelves = set()
        shelves_db = self.settings.get("shelves_db", {})

        # --- FIXED: Master Metadata Dictionary ---
        master_metadata = {}
        for f in os.listdir(self.base_dir):
            if f.startswith("cloud_") and f.endswith(".json") or f == "cloud_cache.json":
                try:
                    with open(os.path.join(self.base_dir, f), "r") as file:
                        for item in json.load(file):
                            if item.get("title"):
                                master_metadata[item["title"]] = item
                except Exception:
                    pass

        for item in getattr(self, 'cloud_items', []):
            if item.get("title"):
                master_metadata[item["title"]] = item
        # -----------------------------------------

        for item in getattr(self, 'cloud_items', []):
            title = item.get("title", "Unknown")
            cloud_titles.append(title)
            
            raw_authors = item.get("authors") or []
            authors = ", ".join([a.get("name", "") for a in raw_authors if isinstance(a, dict)])
            
            raw_series = item.get("series") or []
            series_list = []
            for s in raw_series:
                if isinstance(s, dict) and s.get("title"):
                    series_list.append(f"{s.get('title')} (Bk {s.get('sequence', '')})")
            series_str = ", ".join(series_list)
            
            duration_min = item.get("runtime_length_min", 0)
            hours, mins = divmod(duration_min, 60)
            duration_str = f"{hours}h {mins}m"
            
            asin = item.get("asin", "Unknown")
            
            local_data = local_titles.get(title)
            status = f"Downloaded ({local_data['format']})" if local_data else "Cloud Only"
            
            rows_to_insert.append((title, authors, series_str, duration_str, asin, status))
            all_unique_shelves.update(shelves_db.get(asin, []))

        for path, data in self.local_library.items():
            if data["title"] not in cloud_titles:
                title = data["title"]
                asin = data.get("asin", "Unknown")
                meta = master_metadata.get(title, {})

                # Extract rich metadata safely
                if meta.get("authors"):
                    raw_authors = meta.get("authors")
                    loc_authors = ", ".join([a.get("name", "") for a in raw_authors if isinstance(a, dict)])
                else:
                    loc_authors = data.get("authors", "Local File")

                if meta.get("series"):
                    raw_series = meta.get("series")
                    series_list = [f"{s.get('title')} (Bk {s.get('sequence', '')})" for s in raw_series if isinstance(s, dict) and s.get("title")]
                    loc_series = ", ".join(series_list)
                else:
                    loc_series = data.get("series", "N/A")

                duration_min = meta.get("runtime_length_min") or data.get("duration_min", 0)
                if duration_min > 0:
                    hours, mins = divmod(duration_min, 60)
                    loc_duration = f"{hours}h {mins}m"
                else:
                    loc_duration = "N/A"

                if asin == "Unknown" and meta.get("asin"):
                    asin = meta.get("asin")

                rows_to_insert.append((title, loc_authors, loc_series, loc_duration, asin, f"Downloaded ({data['format']})"))
                all_unique_shelves.update(shelves_db.get(asin, []))

        shelf_list = ["All Shelves"] + sorted(list(all_unique_shelves))
        if hasattr(self, 'shelf_combo'):
            self.shelf_combo.config(values=shelf_list)
            if current_shelf not in shelf_list:
                self.shelf_filter_var.set("All Shelves")
                current_shelf = "All Shelves"

        filtered_rows = []
        for row in rows_to_insert:
            title, authors, series_str, duration_str, asin, status = row

            if current_filter == "Downloaded" and "Downloaded" not in status:
                continue
            if current_filter == "Cloud Only" and status != "Cloud Only":
                continue

            if current_shelf != "All Shelves":
                book_shelves = shelves_db.get(asin, [])
                if current_shelf not in book_shelves:
                    continue

            if search_query:
                search_target = f"{title} {authors} {series_str}".lower()
                if search_query not in search_target:
                    continue

            filtered_rows.append(row)

        self._current_filtered_data = filtered_rows

        is_completely_empty = (not getattr(self, 'cloud_items', [])) and (not self.local_library)

        if is_completely_empty:
            self.library_tree.pack_forget()
            self.grid_canvas.pack_forget()
            if hasattr(self, 'empty_state_frame'):
                self.empty_state_frame.pack(fill="both", expand=True)
        else:
            if hasattr(self, 'empty_state_frame'):
                self.empty_state_frame.pack_forget()
                
            if self.current_view_mode == "list":
                self.grid_canvas.pack_forget()
                self.library_tree.pack(side=tk.LEFT, fill="both", expand=True)
                for row in filtered_rows:
                    self.library_tree.insert("", "end", values=row)
            else:
                self.library_tree.pack_forget()
                self.grid_canvas.pack(side=tk.LEFT, fill="both", expand=True)
                self.draw_grid_view()
    

    def handle_action_on_selected(self, action_type):
        if self.current_view_mode == "list":
            selected = self.library_tree.focus()
            if not selected:
                messagebox.showwarning("Selection Required", "Select a title first.")
                return
            item = self.library_tree.item(selected)
        else:
            if not hasattr(self, '_selected_grid_item'):
                messagebox.showwarning("Selection Required", "Select a title first.")
                return
            item = self._selected_grid_item

        title = item['values'][0]
        asin = item['values'][4]

        local_path = None
        for path, data in self.local_library.items():
            if data["title"] == title:
                local_path = path
                break

        if local_path:
            if not os.path.exists(local_path):
                messagebox.showerror("File Missing", "The file was deleted or moved. Please remove it from the list and re-download.")
                return
                
            if action_type == "scrape":
                self.start_scrape_thread(local_path)
                return
                
            self.load_specific_file(local_path)
            if action_type == "play":
                self.play_chapter()
            elif action_type == "convert":
                self.start_convert_thread()
        else:
            if action_type == "download" or messagebox.askyesno("Download Required", f"'{title}' is not downloaded.\n\nDownload it now?"):
                if not asin or asin == "Unknown":
                    messagebox.showerror("Error", "Cannot download a file without an ASIN.")
                    return

                save_dir = getattr(self, 'default_download_dir', '')
                if not save_dir:
                    save_dir = filedialog.askdirectory(title=f"Select Download Folder for '{title}'")
                    if not save_dir:
                        return

                self.write_log(f"Queuing download for {title}. Post-action: {action_type}")
                threading.Thread(target=self.download_worker, args=(asin, title, save_dir, False, action_type), daemon=True).start()

    def start_scrape_thread(self, filepath):
        if not getattr(self, 'auth_object', None):
            messagebox.showwarning("Not Logged In", "An Audible login is required to search the catalog for ASINs.")
            return
        
        data = self.local_library.get(filepath, {})
        current_title = data.get("title", os.path.basename(filepath))
        
        query = simpledialog.askstring("Search Catalog", "Enter book title or author to search:", initialvalue=current_title)
        if not query: return
        
        self.dl_status_var.set("Searching catalog...")
        threading.Thread(target=self.scrape_search_worker, args=(filepath, query), daemon=True).start()

    def scrape_search_worker(self, filepath, query):
        try:
            client = audible.Client(auth=self.auth_object)
            resp = client.get("1.0/catalog/products", title=query, num_results=5, response_groups="product_desc,product_attrs,contributors")
            products = resp.get("products", [])
            
            if not products:
                self.root.after(0, lambda: messagebox.showinfo("No Results", "No matches found for that title."))
                return
                
            self.root.after(0, lambda: self.show_scrape_results(filepath, products))
        except Exception as e:
            self.write_log(f"Scrape search error: {e}")
            self.root.after(0, lambda: messagebox.showerror("Search Failed", str(e)))
        finally:
            self.root.after(0, lambda: self.dl_status_var.set("Idle"))

    def show_scrape_results(self, filepath, products):
        popup = tk.Toplevel(self.root)
        popup.title("Select Correct Book")
        popup.geometry("600x300")
        popup.transient(self.root)
        
        style = ttk.Style()
        bg_color = style.lookup("TFrame", "background") or "#f0f0f0"
        popup.configure(bg=bg_color)
        
        listbox = tk.Listbox(popup, width=80, height=12)
        listbox.pack(padx=10, pady=10, fill="both", expand=True)
        
        for p in products:
            title = p.get("title", "")
            raw_authors = p.get("authors", [])
            authors = ", ".join([a.get("name", "") for a in raw_authors])
            listbox.insert(tk.END, f"{title} | {authors} ({p.get('asin')})")
            
        def on_select():
            sel = listbox.curselection()
            if not sel: return
            selected_asin = products[sel[0]].get("asin")
            popup.destroy()
            self.dl_status_var.set("Fetching Audnexus data...")
            threading.Thread(target=self.apply_scraped_metadata, args=(filepath, selected_asin), daemon=True).start()
            
        ttk.Button(popup, text="Apply Metadata", command=on_select).pack(pady=(0, 10))

    def apply_scraped_metadata(self, filepath, asin):
        try:
            client = audible.Client(auth=self.auth_object)
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

            cover_path = os.path.join(getattr(self, 'covers_dir', self.base_dir), f"{asin}.jpg")
            images = product.get("product_images", {})
            img_url = images.get("500") or images.get("252")
            
            if img_url:
                img_resp = requests.get(img_url, timeout=10)
                if img_resp.status_code == 200:
                    with open(cover_path, "wb") as f:
                        f.write(img_resp.content)

            data = self.local_library.get(filepath, {})
            data["title"] = title
            data["authors"] = authors
            data["series"] = series_str      # NEW
            data["duration_min"] = duration_min # NEW
            data["asin"] = asin
            self.local_library[filepath] = data
            self.save_local_db()

            ext = data.get("format", "").upper()
            if ext in ["M4B", "MP3"]:
                self.root.after(0, lambda: self.dl_status_var.set("Embedding tags..."))
                
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
                
                res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                if res.returncode == 0:
                    import shutil
                    shutil.move(temp_path, filepath)
                else:
                    if os.path.exists(temp_path): os.remove(temp_path)
                    self.write_log(f"FFmpeg Embed Error: {res.stderr}")
                    raise Exception("FFmpeg failed to embed metadata. Check log for details.")

            self.root.after(0, lambda: messagebox.showinfo("Success", "Metadata scraped and applied!"))
            self.root.after(0, self.refresh_library_ui)

            if getattr(self, 'file_path', "") == filepath:
                self.root.after(0, lambda: self.load_specific_file(filepath))
                
        except Exception as e:
            self.write_log(f"Scrape Error: {e}")
            self.root.after(0, lambda err=str(e): messagebox.showerror("Scrape Failed", err))
        finally:
            self.root.after(0, lambda: self.dl_status_var.set("Idle"))

    def sort_treeview(self, tree, col, descending):
        data = [(tree.set(child, col), child) for child in tree.get_children('')]
        
        def sort_key(item):
            val = item[0]
            if "h " in val and "m" in val:
                try:
                    parts = val.split("h ")
                    h = int(parts[0])
                    m = int(parts[1].replace("m", ""))
                    return h * 60 + m
                except ValueError:
                    pass
            return val.lower()

        data.sort(key=sort_key, reverse=descending)
        
        for index, (val, child) in enumerate(data):
            tree.move(child, '', index)
            
        tree.heading(col, command=lambda _col=col: self.sort_treeview(tree, _col, not descending))

    def setup_ui(self):
        self.build_menu_bar() # NEW

        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        main_vbox = tk.Frame(self.root)
        main_vbox.pack(fill="both", expand=True, padx=10, pady=10)
        main_vbox.rowconfigure(0, weight=1)
        main_vbox.columnconfigure(0, weight=1)

        top_split = ttk.PanedWindow(main_vbox, orient=tk.HORIZONTAL)
        top_split.grid(row=0, column=0, sticky="nsew", pady=(0, 10))

        left_panel = tk.Frame(top_split)
        right_panel = tk.Frame(top_split)

        top_split.add(left_panel, weight=3)
        top_split.add(right_panel, weight=1)

        bottom_panel = tk.Frame(main_vbox)
        bottom_panel.grid(row=1, column=0, sticky="ew")

        self.build_library_components(left_panel)
        self.build_info_components(right_panel)
        self.build_bookmarks_components(right_panel)
        self.build_player_components(bottom_panel)

    def export_csv_worker(self):
        output_file = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV File", "*.csv")],
            title="Export Library to CSV"
        )
        if not output_file:
            return

        try:
            with open(output_file, mode='w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(["Title", "Author(s)", "Series", "Duration (mins)", "ASIN", "Status", "Local Path"])

                local_titles = {data["title"]: data for path, data in self.local_library.items()}
                cloud_titles = []

                for item in self.cloud_items:
                    title = item.get("title", "Unknown")
                    cloud_titles.append(title)
                    
                    raw_authors = item.get("authors") or []
                    authors = ", ".join([a.get("name", "") for a in raw_authors if isinstance(a, dict)])
                    
                    raw_series = item.get("series") or []
                    series_list = []
                    for s in raw_series:
                        if isinstance(s, dict):
                            s_title = s.get("title", "")
                            s_seq = s.get("sequence", "")
                            if s_title and s_seq:
                                series_list.append(f"{s_title} (Bk {s_seq})")
                            elif s_title:
                                series_list.append(s_title)
                    series_str = ", ".join(series_list)

                    duration = item.get("runtime_length_min", 0)
                    asin = item.get("asin", "Unknown")

                    local_data = local_titles.get(title)
                    status = f"Downloaded ({local_data['format']})" if local_data else "Cloud Only"
                    local_path = local_data['path'] if local_data else ""

                    writer.writerow([title, authors, series_str, duration, asin, status, local_path])

                for path, data in self.local_library.items():
                    if data["title"] not in cloud_titles:
                        writer.writerow([data["title"], "Local File", "N/A", "N/A", data.get("asin", "Unknown"), f"Downloaded ({data['format']})", path])

            messagebox.showinfo("Export Successful", f"Library successfully exported to:\n{output_file}")
        except Exception as e:
            messagebox.showerror("Export Failed", f"Failed to write CSV:\n{e}")

    def export_html_worker(self):
        output_file = filedialog.asksaveasfilename(
            defaultextension=".html",
            filetypes=[("HTML Document", "*.html")],
            title="Export Library to HTML"
        )
        if not output_file:
            return

        try:
            html_content = """
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="utf-8">
                <title>My TomeBox Library</title>
                <style>
                    body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #1e1e1e; color: #f0f0f0; margin: 0; padding: 20px; }
                    h1 { text-align: center; color: #ffffff; }
                    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: 20px; padding: 20px 0; }
                    .card { background: #2d2d2d; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); overflow: hidden; display: flex; flex-direction: column; }
                    .cover-art { width: 100%; height: 250px; object-fit: cover; background-color: #3d3d3d; display: flex; align-items: center; justify-content: center; color: #aaaaaa; }
                    .card-content { padding: 15px; flex-grow: 1; display: flex; flex-direction: column; }
                    .title { font-size: 1.1em; font-weight: bold; margin: 0 0 5px 0; color: #ffffff; }
                    .author { color: #cccccc; font-size: 0.9em; margin: 0 0 10px 0; font-style: italic; }
                    .series { font-size: 0.85em; color: #f39c12; margin-bottom: 10px; }
                    .status { margin-top: auto; font-size: 0.85em; padding: 5px; border-radius: 4px; text-align: center; font-weight: bold; }
                    .status.downloaded { background-color: #2e5a36; color: #a3e4b3; }
                    .status.cloud { background-color: #4a4a4a; color: #cccccc; }
                </style>
            </head>
            <body>
                <h1>My TomeBox Library</h1>
                <div class="grid">
            """

            local_titles = {data["title"]: data for path, data in self.local_library.items()}
            cloud_titles = []

            for item in self.cloud_items:
                title = item.get("title", "Unknown")
                cloud_titles.append(title)
                
                raw_authors = item.get("authors") or []
                authors = ", ".join([a.get("name", "") for a in raw_authors if isinstance(a, dict)])
                
                raw_series = item.get("series") or []
                series_list = []
                for s in raw_series:
                    if isinstance(s, dict) and s.get("title"):
                        series_list.append(f"{s.get('title')} (Bk {s.get('sequence', '')})")
                series_str = ", ".join(series_list)

                images = item.get("product_images", {})
                img_url = images.get("500") or images.get("252") or ""
                
                local_data = local_titles.get(title)
                is_downloaded = bool(local_data)
                status_class = "downloaded" if is_downloaded else "cloud"
                status_text = f"Downloaded ({local_data['format']})" if is_downloaded else "Cloud Only"

                img_tag = f'<img src="{img_url}" class="cover-art" alt="Cover">' if img_url else '<div class="cover-art">No Cover Art</div>'

                html_content += f"""
                    <div class="card">
                        {img_tag}
                        <div class="card-content">
                            <h3 class="title">{title}</h3>
                            <p class="author">{authors}</p>
                            <p class="series">{series_str}</p>
                            <div class="status {status_class}">{status_text}</div>
                        </div>
                    </div>
                """

            for path, data in self.local_library.items():
                if data["title"] not in cloud_titles:
                    html_content += f"""
                        <div class="card">
                            <div class="cover-art">Local File</div>
                            <div class="card-content">
                                <h3 class="title">{data["title"]}</h3>
                                <p class="author">Local File</p>
                                <div class="status downloaded">Downloaded ({data['format']})</div>
                            </div>
                        </div>
                    """

            html_content += """
                </div>
            </body>
            </html>
            """

            with open(output_file, "w", encoding="utf-8") as f:
                f.write(html_content)

            import webbrowser
            webbrowser.open(output_file)

        except Exception as e:
            messagebox.showerror("Export Failed", f"Failed to generate HTML:\n{e}")

    def write_log(self, message):
            import datetime
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log_entry = f"[{timestamp}] {message}\n"
            try:
                with open(self.log_file_path, "a", encoding="utf-8") as f:
                    f.write(log_entry)
            except Exception:
                pass

    def auto_load_auth(self):
        self.write_log("DEBUG: auto_load_auth fired from startup timer.")
        self.write_log(f"DEBUG: Looking for auth file at: {self.auth_save_path}")
        
        if os.path.exists(self.auth_save_path):
            self.write_log("DEBUG: Auth file found! Attempting to load...")
            try:
                self.auth_object = audible.Authenticator.from_file(self.auth_save_path)
                activation_bytes = self.auth_object.get_activation_bytes()
                self.auth_bytes.set(activation_bytes)
                self.write_log(f"Session loaded automatically. Activation Bytes: {activation_bytes}")
                
                self.write_log("DEBUG: Sending trigger to fetch_cloud_library now...")
                self.fetch_cloud_library()
                self.write_log("DEBUG: Returned from fetch_cloud_library trigger.")
                
            except Exception as e:
                self.write_log(f"DEBUG EXCEPTION in auto_load_auth: {e}")
                self.write_log(f"Failed to load saved session. You may need to log in again. Error: {e}")
        else:
            self.write_log("DEBUG: Auth file does not exist. Halting auto-load sequence.")
            self.write_log("No saved session found. Please log in.")

    def load_auth_file_prompt(self):
        filepath = filedialog.askopenfilename(filetypes=[("JSON Auth File", "*.json")], title="Select Audible Auth File")
        if not filepath: return

        self.write_log(f"Loading auth from external file: {filepath}")
        try:
            self.auth_object = audible.Authenticator.from_file(filepath)
            activation_bytes = self.auth_object.get_activation_bytes()
            
            self.auth_bytes.set(activation_bytes)
            self.write_log(f"Activation Bytes Received: {activation_bytes}")
            self.write_log("Auth file loaded successfully.")
            
            self.auth_object.to_file(self.auth_save_path)
            
            messagebox.showinfo("Success", "Auth file loaded! You can now fetch your library.")
            self.fetch_cloud_library()
        except Exception as e:
            self.write_log(f"ERROR: {traceback.format_exc()}")
            messagebox.showerror("Error", "Could not load auth file. Check the log.")

    def start_browser_login_thread(self):
        if hasattr(self, 'browser_login_btn') and self.browser_login_btn.winfo_exists():
            self.browser_login_btn.config(text="Connecting...", state=tk.DISABLED)
        threading.Thread(target=self.browser_login_worker, args=(self.locale.get(),), daemon=True).start()

    def browser_login_worker(self, locale):
        self.write_log(f"Starting external browser login for region: {locale}")
        
        def custom_login_callback(login_url):
            self.write_log("Opening default web browser...")
            webbrowser.open(login_url)
            
            result = [None]
            event = threading.Event()
            
            def ask_user_for_url():
                msg = (
                    "1. Your web browser should have opened.\n"
                    "2. Log in to Amazon / Audible.\n"
                    "3. Once logged in, you will land on a blank or 'Page Not Found' error page.\n\n"
                    "4. Copy the ENTIRE URL from your browser's address bar and paste it below:"
                )
                res = simpledialog.askstring("Audible Login Authorization", msg, parent=self.root)
                result[0] = res
                event.set()
                
            self.root.after(0, ask_user_for_url)
            event.wait()
            
            if not result[0]:
                raise Exception("Authentication cancelled by user.")
                
            return result[0].strip()

        try:
            self.write_log("Waiting for user to complete browser login and paste URL...")
            self.auth_object = audible.Authenticator.from_login_external(
                locale=locale, 
                login_url_callback=custom_login_callback
            )
            
            self.write_log("Authentication successful! Retrieving activation bytes...")
            activation_bytes = self.auth_object.get_activation_bytes()
            
            self.root.after(0, self.auth_bytes.set, activation_bytes)
            self.write_log(f"Activation Bytes Received: {activation_bytes}")
            
            self.auth_object.to_file(self.auth_save_path)
            self.write_log(f"Session saved locally to {self.auth_save_path}")

            self.root.after(0, lambda: messagebox.showinfo("Success", "Connected to Audible!"))
            self.root.after(0, self.fetch_cloud_library)
            
        except Exception as e:
            error_trace = traceback.format_exc()
            self.write_log("ERROR DURING LOGIN:")
            self.write_log(error_trace)
            self.root.after(0, lambda: messagebox.showerror("Login Failed", str(e)))
            
        finally:
            self.write_log("Login thread terminated.")
            def restore_btn():
                if hasattr(self, 'browser_login_btn') and self.browser_login_btn.winfo_exists():
                    self.browser_login_btn.config(text="Login via Browser", state=tk.NORMAL)
            self.root.after(0, restore_btn)

    def fetch_cloud_library(self):
        self.write_log("DEBUG: fetch_cloud_library method started executing.")
        
        if not self.auth_object:
            self.write_log("DEBUG: fetch_cloud_library aborted - self.auth_object is missing or None.")
            messagebox.showwarning("Not Logged In", "Please login via the Settings tab first.")
            return

        self.write_log("DEBUG: self.auth_object verified. Launching fetch_library_worker thread...")
        
        self.dl_status_var.set("Fetching data from Amazon... Please wait.")
        
        threading.Thread(target=self.fetch_library_worker, daemon=True).start()

    def fetch_library_worker(self):
        try:
            self.write_log("Querying Audible Library API...")
            client = audible.Client(auth=self.auth_object)

            response = client.get("1.0/library", response_groups="product_desc,product_attrs,series,contributors,media", num_results=1000)
            
            self.cloud_items = response.get("items", [])
            self.write_log(f"Successfully retrieved {len(self.cloud_items)} library items.")
            
            self.save_cloud_cache()

            self.root.after(0, self.refresh_library_ui)
            self.root.after(0, lambda: self.dl_status_var.set("Idle"))

            threading.Thread(target=self.background_cover_downloader, daemon=True).start()
            
        except Exception as e:
            import traceback
            self.write_log(f"ERROR FETCHING LIBRARY:\n{traceback.format_exc()}")
            self.root.after(0, lambda: messagebox.showerror("Library Error", "Failed to fetch cloud library."))
            self.root.after(0, lambda: self.dl_status_var.set("Idle"))

    def background_cover_downloader(self):
        self.write_log("Starting background cover sync...")
        covers_downloaded = 0
        
        for item in getattr(self, 'cloud_items', []):
            asin = item.get("asin")
            if not asin: continue
                
            cover_path = os.path.join(getattr(self, 'covers_dir', self.base_dir), f"{asin}.jpg")
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
                except Exception as e:
                    pass
                    
        if covers_downloaded > 0:
            self.write_log(f"Downloaded {covers_downloaded} new covers.")

            if getattr(self, 'current_view_mode', 'list') == 'grid':
                self.root.after(0, self.refresh_library_ui)

    def update_cloud_ui(self, items):
        for row in self.cloud_tree.get_children():
            self.cloud_tree.delete(row)

        for item in items:
            try:
                asin = item.get("asin", "Unknown")
                title = item.get("title") or "Unknown"
                
                raw_authors = item.get("authors") or []
                authors = ", ".join([a.get("name", "") for a in raw_authors if isinstance(a, dict)])
                
                duration_min = item.get("runtime_length_min") or 0
                hours, mins = divmod(duration_min, 60)
                duration_str = f"{hours}h {mins}m"
                
                self.cloud_tree.insert("", "end", values=(title, authors, duration_str, asin))
            except Exception as e:
                if self.debug_mode.get():
                    self.write_log(f"DEBUG - Failed to parse UI for item: {e}")

    def download_title_prompt(self):
        selected = self.cloud_tree.focus()
        if not selected:
            messagebox.showwarning("Selection Required", "Select a title from the Cloud Library first.")
            return

        item = self.cloud_tree.item(selected)
        title = item['values'][0]
        asin = item['values'][3]

        if not asin or asin == "Unknown":
            messagebox.showerror("Data Error", "This item does not have a valid ASIN.")
            return

        save_dir = getattr(self, 'default_download_dir', '')
        if not save_dir:
            save_dir = filedialog.askdirectory(title=f"Select Download Folder for '{title}'")
            if not save_dir:
                return

        self.write_log(f"Starting download process for ASIN: {asin}")
        threading.Thread(target=self.download_worker, args=(asin, title, save_dir), daemon=True).start()

    def download_worker(self, asin, title, save_dir, is_queue=False, post_action=None):
        filepath = None 
        try:
            self.root.after(0, lambda: self.dl_status_var.set(f"Downloading: {title}"))
            self.root.after(0, lambda: self.dl_progress_var.set(0))

            from audible.aescipher import decrypt_voucher_from_licenserequest
            client = audible.Client(auth=self.auth_object)
            
            self.write_log(f"Requesting AAXC license and download link for ASIN: {asin}")

            body = {
                "drm_type": "Adrm", 
                "consumption_type": "Download"
            }
            lic_resp = client.post(
                f"1.0/content/{asin}/licenserequest",
                body=body
            )

            def find_url(d):
                if isinstance(d, dict):
                    if "offline_url" in d: return d["offline_url"]
                    for k, v in d.items():
                        res = find_url(v)
                        if res: return res
                elif isinstance(d, list):
                    for item in d:
                        res = find_url(item)
                        if res: return res
                return None
            
            download_link = find_url(lic_resp)
            
            if not download_link:
                raise Exception("Could not find the offline download URL in the API response.")

            self.write_log("Decrypting AAXC voucher...")
            decrypted_voucher = decrypt_voucher_from_licenserequest(self.auth_object, lic_resp)

            def find_key_iv(d):
                k, i = None, None
                if isinstance(d, dict):
                    if "key" in d and "iv" in d: return d["key"], d["iv"]
                    for val in d.values():
                        k, i = find_key_iv(val)
                        if k and i: return k, i
                elif isinstance(d, list):
                    for val in d:
                        k, i = find_key_iv(val)
                        if k and i: return k, i
                return k, i
            
            a_key, a_iv = find_key_iv(decrypted_voucher)
            
            if not a_key or not a_iv:
                raise Exception("Decrypted voucher did not contain 'key' and 'iv'.")

            self.write_log(f"Extracted AAXC Key: {a_key}")

            safe_title = "".join([c for c in title if c.isalpha() or c.isdigit() or c==' ']).rstrip()
            safe_title = "".join([c for c in title if c.isalpha() or c.isdigit() or c==' ']).rstrip()
            filepath = os.path.join(save_dir, f"{safe_title}.aaxc")
            temp_filepath = f"{filepath}.part"
            
            self.write_log(f"Downloading AAXC file to: {temp_filepath}")
            
            headers = {"User-Agent": "Audible/6.6.1 (iPhone; iOS 15.5; Scale/3.00)"}
            import urllib.request
            req = urllib.request.Request(download_link, headers=headers)
            
            with urllib.request.urlopen(req) as response, open(temp_filepath, 'wb') as out_file:
                total_size = int(response.headers.get('content-length', 0))
                downloaded = 0
                last_log_percent = 0
                last_ui_percent = -1 
                
                while True:
                    if is_queue and asin in getattr(self, 'active_downloads', {}):
                        if self.active_downloads[asin].get("cancel_flag"):
                            raise Exception("Download canceled by user.")

                    chunk = response.read(32768)
                    if not chunk: break
                    out_file.write(chunk)
                    
                    if total_size > 0:
                        downloaded += len(chunk)
                        percent_float = (downloaded / total_size) * 100
                        percent_int = int(percent_float)

                        if percent_int > last_ui_percent:
                            self.root.after(0, self.dl_progress_var.set, percent_float)
                            
                            if is_queue and asin in getattr(self, 'active_downloads', {}):
                                self.root.after(0, self.active_downloads[asin]["prog_var"].set, percent_float)
                                self.root.after(0, self.active_downloads[asin]["status_var"].set, f"{percent_int}%")
                                
                            last_ui_percent = percent_int

                        if percent_int >= last_log_percent + 10:
                            self.write_log(f"Download Progress: {percent_int}%")
                            last_log_percent = percent_int
            
            # Stream complete, finalize the file
            os.replace(temp_filepath, filepath)

            if is_queue and asin in getattr(self, 'active_downloads', {}):
                self.root.after(0, self.active_downloads[asin]["status_var"].set, "Complete")
            
            self.write_log(f"Download complete: {safe_title}.aaxc")
            self.add_stat("books_downloaded", 1)
            self.local_library[filepath] = {
                "title": title, 
                "format": "AAXC", 
                "path": filepath,
                "audible_key": a_key,
                "audible_iv": a_iv,
                "asin": asin  
            }
            self.save_local_db()
            self.root.after(0, self.refresh_library_ui)

            if post_action == "play" or post_action == "convert":
                self.root.after(0, lambda: self.load_specific_file(filepath))
                if post_action == "play":
                    self.root.after(500, self.play_chapter)
                elif post_action == "convert":
                    self.root.after(500, self.start_convert_thread)
            elif not is_queue:
                self.root.after(0, lambda: messagebox.showinfo("Success", f"Finished downloading:\n{title}"))
            
        except Exception as e:
            import traceback
            error_trace = traceback.format_exc()
            error_msg = str(e)
            
            # Clean up the .part file on failure/cancellation
            if os.path.exists(temp_filepath):
                try:
                    os.remove(temp_filepath)
                    self.write_log(f"Cleaned up partial file: {temp_filepath}")
                except OSError as cleanup_error:
                    self.write_log(f"Failed to clean up partial file: {cleanup_error}")
            else:
                self.write_log(f"DOWNLOAD ERROR:\n{error_trace}")

            if not is_queue:
                self.root.after(0, lambda err=error_msg: messagebox.showerror("Download Error", f"Failed to download.\n\n{err}\n\nCheck log for details."))
                
        finally:
            if not is_queue:
                self.root.after(0, lambda: self.dl_status_var.set("Idle"))
                self.root.after(0, lambda: self.dl_progress_var.set(0))
        
    def seek_audio(self, offset):
        if not self.file_path or not self.chapters:
            return

        if not self.is_playing and not self.is_paused:
            return

        new_time = self.current_play_time + offset
        
        if new_time < 0:
            new_time = 0
        elif new_time >= self.chapter_duration:
            self.next_chapter()
            return
            
        self.current_play_time = new_time
        
        if self.is_playing:
            self.is_playing = False
            if self.player_process:
                self.player_process.terminate()
                self.player_process = None
            self.resume_playback()
            
        elif self.is_paused:
            curr_str = self.format_time(self.current_play_time)
            dur_str = self.format_time(self.chapter_duration)
            self.time_label.config(text=f"{curr_str} / {dur_str}")
            percent = (self.current_play_time / self.chapter_duration) * 100 if self.chapter_duration > 0 else 0
            self.progress_var.set(percent)
        
    def get_drm_flags(self, filepath):
        data = self.local_library.get(filepath, {})
        a_key = data.get("audible_key")
        a_iv = data.get("audible_iv")

        if a_key and a_iv:
            return ["-audible_key", a_key, "-audible_iv", a_iv]

        owner = data.get("owner", self.active_profile)
        
        if owner == self.active_profile and self.auth_bytes.get().strip():
            return ["-activation_bytes", self.auth_bytes.get().strip()]
            
        owner_auth_path = os.path.join(self.base_dir, f"auth_{owner}.json")
        if os.path.exists(owner_auth_path):
            try:
                temp_auth = audible.Authenticator.from_file(owner_auth_path)
                dynamic_bytes = temp_auth.get_activation_bytes()
                if dynamic_bytes:
                    return ["-activation_bytes", dynamic_bytes]
            except Exception as e:
                self.write_log(f"Failed to dynamically load auth for {owner}: {e}")
        
        return ["-activation_bytes", self.auth_bytes.get().strip()]
            
    def add_local_file(self):
        filepath = filedialog.askopenfilename(filetypes=[("Audiobooks", "*.aax *.m4b *.mp3")])
        if not filepath: return
        
        filename = os.path.basename(filepath)
        ext = filename.split(".")[-1].upper()
        
        title = filename
        authors = "Unknown Author"
        
        if ext in ["M4B", "MP3"]:
            try:
                cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", filepath]
                res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                data = json.loads(res.stdout)
                tags = data.get("format", {}).get("tags", {})

                if "title" in tags: 
                    title = tags["title"]
                if "artist" in tags: 
                    authors = tags["artist"]
                elif "album_artist" in tags: 
                    authors = tags["album_artist"]
                    
            except Exception as e:
                self.write_log(f"Failed to read tags for {filename}: {e}")

        self.local_library[filepath] = {
            "title": title, 
            "format": ext, 
            "path": filepath, 
            "authors": authors,
            "owner": self.active_profile
        }
        self.save_local_db()
        self.refresh_library_ui()

    

    def remove_local_file(self):
        selected = self.library_tree.focus()
        if not selected: 
            return
        
        item = self.library_tree.item(selected)
        title = item['values'][0]
        
        local_path = None
        for path, data in self.local_library.items():
            if data["title"] == title:
                local_path = path
                break
        
        if local_path and local_path in self.local_library:
            if messagebox.askyesno("Remove File", f"Remove '{title}' from your local library list?\n\n(This only removes it from the list, it does not delete the actual file from your hard drive.)"):
                del self.local_library[local_path]
                self.save_local_db()
                self.refresh_library_ui()
        else:
            messagebox.showinfo("Cloud Only", "This title is not currently in your downloaded local library.")

    def open_sleep_menu(self):
        if hasattr(self, 'sleep_menu_popup') and self.sleep_menu_popup.winfo_exists():
            self.sleep_menu_popup.destroy()
            return

        self.sleep_menu_popup = tk.Toplevel(self.root)
        self.sleep_menu_popup.wm_overrideredirect(True)
        
        style = ttk.Style()
        bg_color = style.lookup("TFrame", "background") or "#f0f0f0"
        self.sleep_menu_popup.config(bg=bg_color, highlightbackground="#4a90e2", highlightthickness=1)

        x = self.timer_btn.winfo_rootx()
        y = self.timer_btn.winfo_rooty() + self.timer_btn.winfo_height() + 2
        self.sleep_menu_popup.geometry(f"+{x}+{y}")

        inner = tk.Frame(self.sleep_menu_popup, bg=bg_color, padx=5, pady=5)
        inner.pack(fill="both", expand=True)

        # 3. Add Presets
        ttk.Button(inner, text="Turn Off Timer", command=lambda: self.set_sleep_timer("off")).pack(fill="x", pady=(0,5))
        ttk.Button(inner, text="15 Minutes", command=lambda: self.set_sleep_timer("time", 15)).pack(fill="x", pady=1)
        ttk.Button(inner, text="30 Minutes", command=lambda: self.set_sleep_timer("time", 30)).pack(fill="x", pady=1)
        ttk.Button(inner, text="End of Chapter", command=lambda: self.set_sleep_timer("chapters", 1)).pack(fill="x", pady=1)

        ttk.Separator(inner, orient="horizontal").pack(fill="x", pady=5)

        custom_time_frame = ttk.Frame(inner)
        custom_time_frame.pack(fill="x", pady=2)
        ttk.Label(custom_time_frame, text="Mins:").pack(side=tk.LEFT)
        min_var = tk.StringVar(value="60")
        ttk.Entry(custom_time_frame, textvariable=min_var, width=5).pack(side=tk.LEFT, padx=(5, 2))
        ttk.Button(custom_time_frame, text="Set", width=4, command=lambda: self.set_sleep_timer("time", min_var.get())).pack(side=tk.LEFT)

        custom_chap_frame = ttk.Frame(inner)
        custom_chap_frame.pack(fill="x", pady=2)
        ttk.Label(custom_chap_frame, text="Chaps:").pack(side=tk.LEFT)
        chap_var = tk.StringVar(value="2")
        ttk.Entry(custom_chap_frame, textvariable=chap_var, width=5).pack(side=tk.LEFT, padx=(1, 2))
        ttk.Button(custom_chap_frame, text="Set", width=4, command=lambda: self.set_sleep_timer("chapters", chap_var.get())).pack(side=tk.LEFT)

        self.sleep_menu_popup.update_idletasks()
        popup_height = self.sleep_menu_popup.winfo_reqheight()

        x = self.timer_btn.winfo_rootx()
        y = self.timer_btn.winfo_rooty()

        self.sleep_menu_popup.geometry(f"+{x}+{y - popup_height - 2}")

        def on_focus_out(event):

            if self.sleep_menu_popup.focus_get() is None or not str(self.sleep_menu_popup.focus_get()).startswith(str(self.sleep_menu_popup)):
                self.sleep_menu_popup.destroy()
                
        self.sleep_menu_popup.bind("<FocusOut>", on_focus_out)
        self.sleep_menu_popup.focus_set()

    def set_sleep_timer(self, mode, value=0):

        if hasattr(self, '_sleep_timer_id'):
            self.root.after_cancel(self._sleep_timer_id)
            
        if hasattr(self, 'sleep_menu_popup') and self.sleep_menu_popup.winfo_exists():
            self.sleep_menu_popup.destroy()

        try:
            val = int(value)
        except ValueError:
            return

        if mode == "off" or val <= 0:
            self.sleep_mode = None
            self.timer_btn.config(text="Sleep: Off")
            return
            
        self.sleep_mode = mode
        
        if mode == "time":
            self.sleep_timer_seconds = val * 60
            self.timer_btn.config(text=f"Sleep: {self.format_time(self.sleep_timer_seconds)}")
            self.sleep_timer_tick()
            
        elif mode == "chapters":
            self.sleep_chapters_remaining = val
            text = "End of Chapter" if val == 1 else f"Sleep: {val} ch"
            self.timer_btn.config(text=text)

    def sleep_timer_tick(self):
        if getattr(self, 'sleep_mode', None) != "time":
            return
            
        if self.sleep_timer_seconds <= 0:
            self.sleep_mode = None
            self.timer_btn.config(text="Sleep: Off")
            
            if getattr(self, 'is_playing', False):
                self.write_log("Sleep timer (minutes) finished. Pausing playback.")
                self.pause_audio()
            return
            
        self.sleep_timer_seconds -= 1
        self.timer_btn.config(text=f"Sleep: {self.format_time(self.sleep_timer_seconds)}")
        
        self._sleep_timer_id = self.root.after(1000, self.sleep_timer_tick)

    def on_volume_change(self, event=None):
        if os.name == 'nt':
            try:
                from pycaw.pycaw import AudioUtilities, ISimpleAudioVolume
                
                vol_float = float(self.volume_var.get()) / 100.0
                sessions = AudioUtilities.GetAllSessions()
                for session in sessions:
                    if session.Process and session.Process.name() == "ffplay.exe":
                        volume = session._ctl.QueryInterface(ISimpleAudioVolume)
                        volume.SetMasterVolume(vol_float, None)
                        
            except ImportError:
                self.write_log("Volume control failed: pycaw or comtypes not installed.")
            except Exception as e:
                if self.debug_mode.get():
                    self.write_log(f"Volume change error: {e}")
        else:
            if self.is_playing:
                self.pause_audio()
                self.is_paused = False
                self.resume_playback()
                
    def on_speed_change(self, selected_speed):
        if self.is_playing:
            self.pause_audio()
            self.is_paused = False
            self.resume_playback()
    
    def on_sleep_timer_set(self, event=None):
        val = self.sleep_time_var.get()

        if hasattr(self, '_sleep_timer_id'):
            self.root.after_cancel(self._sleep_timer_id)
            
        if val == "Off":
            self.sleep_timer_active = False
            self.timer_countdown_var.set("")
            return
            
        mins = int(val.replace("m", ""))
        self.sleep_timer_seconds = mins * 60
        self.sleep_timer_active = True

        self.timer_countdown_var.set(self.format_time(self.sleep_timer_seconds))
        
        self.sleep_timer_tick()

        
    def _on_grid_scroll(self, event):
        if getattr(self, 'current_view_mode', 'list') != "grid":
            return

        if str(self.grid_canvas) not in str(event.widget):
            return

        num = getattr(event, 'num', 0)
        delta = getattr(event, 'delta', 0)

        if num == 4 or delta > 0:
            self.grid_canvas.yview_scroll(-1, "units")
        # Scroll Down
        elif num == 5 or delta < 0:
            self.grid_canvas.yview_scroll(1, "units")

    def master_play(self, event=None):
        if getattr(self, 'current_view_mode', 'list') == "list":
            selected = self.library_tree.focus()
            if not selected:
                if self.file_path:
                    self.play_chapter()
                else:
                    messagebox.showwarning("Selection Required", "Please select an audiobook to play.")
                return
            item = self.library_tree.item(selected)
        else:
            if not hasattr(self, '_selected_grid_item') or not self._selected_grid_item:
                if self.file_path:
                    self.play_chapter()
                else:
                    messagebox.showwarning("Selection Required", "Please select an audiobook to play.")
                return
            item = self._selected_grid_item

        title = item['values'][0]
        status = item['values'][5]  

        if "Downloaded" not in status:
            messagebox.showinfo("Cloud Only", "This title has not been downloaded yet.")
            return

        local_path = None
        for path, data in self.local_library.items():
            if data.get("title") == title:
                local_path = path
                break

        if not local_path or not os.path.exists(local_path):
            messagebox.showerror("File Error", "The audio file could not be found on your disk.")
            return

        if self.file_path == local_path:
            self.play_chapter()
            return

        self.stop_audio()

        threading.Thread(target=self.fetch_metadata_worker, args=(local_path,), daemon=True).start()
        
        self.handle_action_on_selected("play")

    def load_file_prompt(self):
        filepath = filedialog.askopenfilename(filetypes=[("Audiobooks", "*.aax *.m4b")])
        if filepath:
            self.load_specific_file(filepath)

    def load_specific_file(self, filepath):
        self.file_path = filepath
        is_encrypted = filepath.endswith(".aax") or filepath.endswith(".aaxc")
        
        self.dl_status_var.set("Analyzing...")
        self.root.update()
        
        if is_encrypted:
            success, error_msg = self.verify_bytes(self.file_path)
            if not success:
                self.dl_status_var.set("Verification Failed")
                messagebox.showerror("Audio Processing Error", f"Failed to process the file. Reason:\n\n{error_msg}")
                self.file_path = ""
                return

        self.dl_status_var.set(f"Ready: {os.path.basename(self.file_path)}")
        self.chapters = self.extract_chapters(self.file_path)
        
        if self.chapters:
            local_data = self.local_library.get(filepath, {})
            
            # The Web Player tracks absolute time (last_position). 
            # The PC Player tracks chapter index + relative time.
            abs_pos = local_data.get("last_position")
            
            if abs_pos is not None:
                # Translate Web's absolute time to PC's chapter format
                found_chap = 0
                for i, chap in enumerate(self.chapters):
                    start = float(chap.get("start_time", 0))
                    end = float(chap.get("end_time", 0))
                    if start <= abs_pos < end:
                        found_chap = i
                        break
                    # Catch-all if position somehow overshoots the last chapter
                    if i == len(self.chapters) - 1 and abs_pos >= end:
                        found_chap = i
                        
                self.current_chapter_idx = found_chap
                self.current_play_time = max(0.0, abs_pos - float(self.chapters[found_chap].get("start_time", 0)))
            else:
                # Fallback to standard PC tracking if no web data exists
                self.current_chapter_idx = local_data.get("last_chapter", 0)
                self.current_play_time = local_data.get("last_time", 0.0)
            
            if self.current_chapter_idx >= len(self.chapters):
                self.current_chapter_idx = 0
                self.current_play_time = 0.0
                
            self.update_info()
            
            chapter = self.chapters[self.current_chapter_idx]
            self.chapter_duration = float(chapter.get("end_time", 0)) - float(chapter.get("start_time", 0))
            
            curr_str = self.format_time(self.current_play_time)
            dur_str = self.format_time(self.chapter_duration)
            self.time_label.config(text=f"{curr_str} / {dur_str}")
            percent = (self.current_play_time / self.chapter_duration) * 100 if self.chapter_duration > 0 else 0
            self.progress_var.set(percent)

        threading.Thread(target=self.fetch_metadata_worker, args=(filepath,), daemon=True).start()
        self.refresh_bookmarks_ui()

    def add_bookmark(self):
        if not getattr(self, 'file_path', None):
            messagebox.showwarning("No File", "Please load an audiobook first.")
            return

        was_playing = self.is_playing
        if was_playing:
            self.pause_audio()

        current_time = getattr(self, 'current_play_time', 0.0)
        chapter_idx = getattr(self, 'current_chapter_idx', 0)

        abs_time = current_time
        if self.chapters:
            abs_time += float(self.chapters[chapter_idx].get("start_time", 0))

        note = simpledialog.askstring("Add Bookmark", f"Add a note for {self.format_time(current_time)}:")

        if was_playing:
            self.is_paused = False
            self.resume_playback()
            
        if not note: return 

        local_data = self.local_library.get(self.file_path, {})
        if "bookmarks" not in local_data:
            local_data["bookmarks"] = []
            
        local_data["bookmarks"].append({
            "chapter_idx": chapter_idx,
            "time": current_time,
            "abs_time": abs_time,
            "note": note
        })
        
        self.save_local_db()
        self.refresh_bookmarks_ui()

    def refresh_bookmarks_ui(self):
        if not hasattr(self, 'bm_tree'): return
        
        for row in self.bm_tree.get_children():
            self.bm_tree.delete(row)
            
        if not getattr(self, 'file_path', None): return
        
        local_data = self.local_library.get(self.file_path, {})
        bookmarks = local_data.get("bookmarks", [])

        bookmarks.sort(key=lambda x: x.get("abs_time", 0))
        
        for idx, bm in enumerate(bookmarks):
            chap_idx = bm.get("chapter_idx", 0)

            chap_title = f"Chapter {chap_idx + 1}"
            if hasattr(self, 'chapters') and self.chapters and chap_idx < len(self.chapters):
                chap_title = self.chapters[chap_idx].get("tags", {}).get("title", chap_title)
                
            t_str = self.format_time(bm.get("time", 0))
            display_time = f"{chap_title} - {t_str}"

            self.bm_tree.insert("", "end", iid=str(idx), values=(display_time, bm.get("note", "")))

    def jump_to_bookmark(self, event=None):
        selected = self.bm_tree.focus()
        if not selected: return
        
        idx = int(selected)
        bookmarks = self.local_library.get(self.file_path, {}).get("bookmarks", [])
        
        if 0 <= idx < len(bookmarks):
            bm = bookmarks[idx]
            
            self.stop_audio()
            self.current_chapter_idx = bm.get("chapter_idx", 0)
            self.current_play_time = bm.get("time", 0.0)
            
            self.play_chapter()

    def delete_bookmark(self):
        selected = self.bm_tree.focus()
        if not selected: return
        
        idx = int(selected)
        bookmarks = self.local_library.get(self.file_path, {}).get("bookmarks", [])
        
        if 0 <= idx < len(bookmarks):
            del bookmarks[idx]
            self.save_local_db()
            self.refresh_bookmarks_ui()

    def verify_bytes(self, filepath):
        cmd = ["ffmpeg", "-v", "error"]
        cmd.extend(self.get_drm_flags(filepath))
        cmd.extend(["-i", filepath, "-t", "0.1", "-f", "null", "-"])
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            if result.returncode != 0:
                return False, result.stderr if result.stderr else "FFmpeg rejected the file."
            return True, ""
        except FileNotFoundError:
            return False, "FFmpeg is missing!"
        except Exception as e:
            return False, str(e)

    def start_convert_thread(self):
        if not self.chapters:
            messagebox.showinfo("No Chapters Found", "This file does not contain chapter markers. Defaulting to single file conversion.")
            split_choice = False
        else:
            split_choice = messagebox.askyesnocancel(
                "Conversion Options",
                "Do you want to split this audiobook into individual chapters?\n\n"
                "Yes = Split into multiple files (Export only)\n"
                "No = Keep as a single .m4b file\n"
                "Cancel = Abort"
            )

        if split_choice is None:
            return

        if split_choice:
            output_dir = filedialog.askdirectory(title=f"Select Folder to Extract Chapters For: {os.path.basename(self.file_path)}")
            if not output_dir: 
                return
            self.dl_status_var.set("Splitting into chapters... Please wait.")
            threading.Thread(target=self.split_worker, args=(self.file_path, output_dir), daemon=True).start()
        else:
            output_file = filedialog.asksaveasfilename(
                defaultextension=".m4b", 
                filetypes=[("M4B Audiobook", "*.m4b")], 
                initialfile=os.path.basename(self.file_path).replace(".aaxc", ".m4b").replace(".aax", ".m4b")
            )
            if not output_file: 
                return
            self.dl_status_var.set("Converting to .m4b... Please wait.")
            threading.Thread(target=self.convert_worker, args=(self.file_path, output_file), daemon=True).start()

    def convert_worker(self, input_path, output_path):
        total_duration = 0
        if hasattr(self, 'chapters') and self.chapters:
            total_duration = float(self.chapters[-1].get("end_time", 0))
            
        if total_duration == 0:
            try:
                probe_cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", input_path]
                res = subprocess.run(probe_cmd, capture_output=True, text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                total_duration = float(res.stdout.strip())
            except Exception:
                total_duration = 0

        original_data = self.local_library.get(input_path, {})
        title = original_data.get("title", os.path.basename(output_path))
        asin = original_data.get("asin", "")

        authors = ""
        for item in getattr(self, 'cloud_items', []):
            if item.get("asin") == asin:
                raw_authors = item.get("authors", [])
                authors = ", ".join([a.get("name", "") for a in raw_authors if isinstance(a, dict)])
                break

        cover_path = os.path.join(getattr(self, 'covers_dir', self.base_dir), f"{asin}.jpg")

        # Define the temporary part file
        base, ext = os.path.splitext(output_path)
        temp_out_path = f"{base}_temp{ext}"

        cmd = ["ffmpeg", "-y"]
        if input_path.endswith(".aax") or input_path.endswith(".aaxc"):
            cmd.extend(self.get_drm_flags(input_path))
            
        cmd.extend(["-i", input_path])

        if asin and os.path.exists(cover_path):
            cmd.extend([
                "-i", cover_path, 
                "-map", "0:a", 
                "-map", "1:v", 
                "-c:v", "mjpeg", 
                "-disposition:v", "attached_pic"
            ])

        cmd.extend([
            "-c:a", "copy",
            "-metadata", f"title={title}",
            "-metadata", f"album={title}",
            "-metadata", "genre=Audiobook"
        ])
        
        if authors:
            cmd.extend([
                "-metadata", f"artist={authors}",
                "-metadata", f"album_artist={authors}"
            ])

        # Target the .part file instead of the final .m4b
        cmd.extend(["-progress", "pipe:1", temp_out_path])
        
        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, 
                universal_newlines=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
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
                                    self.root.after(0, self.dl_progress_var.set, percent)
                                    last_percent = percent
                    except ValueError:
                        pass

            process.wait()
            
            if process.returncode != 0:
                raise Exception(f"FFmpeg process failed with exit code {process.returncode}.")
            
            # Atomic rename upon absolute success
            os.replace(temp_out_path, output_path)
            
            self.local_library[output_path] = {
                "title": title, 
                "format": "M4B", 
                "path": output_path,
                "asin": asin
            }
            self.save_local_db()
            
            self.root.after(0, lambda: messagebox.showinfo("Success", "File converted with embedded metadata."))
            self.root.after(0, self.refresh_library_ui)
            
        except Exception as e:
            # Clean up the immediate failure, fallback to startup cleaner if this misses
            if os.path.exists(temp_out_path):
                try:
                    os.remove(temp_out_path)
                except OSError:
                    pass
            self.write_log(f"Conversion Error: {e}")
            self.root.after(0, lambda err=str(e): messagebox.showerror("Conversion Failed", err))
        finally:
            self.root.after(0, lambda: self.dl_status_var.set(f"Ready: {os.path.basename(input_path)}"))
            self.root.after(0, lambda: self.dl_progress_var.set(0))

    def split_worker(self, input_path, output_dir):
        try:
            base_flags = []
            if input_path.endswith(".aax") or input_path.endswith(".aaxc"):
                base_flags = self.get_drm_flags(input_path)

            total_chaps = len(self.chapters)
            
            original_data = self.local_library.get(input_path, {})
            book_title = original_data.get("title", os.path.splitext(os.path.basename(input_path))[0])
            safe_book_title = "".join([c for c in book_title if c.isalnum() or c in [' ', '-', '_']]).rstrip()
            
            target_dir = os.path.join(output_dir, safe_book_title)
            os.makedirs(target_dir, exist_ok=True)
            
            for idx, chapter in enumerate(self.chapters):
                self.root.after(0, lambda p=((idx + 1) / total_chaps) * 100: self.dl_progress_var.set(p))
                
                chap_title = chapter.get("tags", {}).get("title", f"Chapter {idx + 1}")
                safe_chap_title = "".join([c for c in chap_title if c.isalnum() or c in [' ', '-', '_']]).rstrip()
                
                out_name = f"{idx + 1:03d} - {safe_chap_title}.m4b"
                out_path = os.path.join(target_dir, out_name)

                start = chapter.get("start_time", 0)
                end = chapter.get("end_time", 0)

                cmd = ["ffmpeg", "-y"]
                cmd.extend(base_flags)
                cmd.extend(["-i", input_path, "-ss", str(start), "-to", str(end), "-c", "copy", out_path])
                
                subprocess.run(cmd, check=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)

            self.root.after(0, lambda: self.dl_progress_var.set(0))
            self.root.after(0, lambda: messagebox.showinfo("Success", f"Audiobook successfully split into {total_chaps} files.\n\nFiles were saved to:\n{target_dir}"))
            
        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror("Split Failed", str(e)))
        finally:
            self.root.after(0, lambda: self.dl_status_var.set(f"Ready: {os.path.basename(input_path)}"))
            self.root.after(0, lambda: self.dl_progress_var.set(0))

    def extract_chapters(self, filepath):
        cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_chapters", filepath]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", check=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            data = json.loads(result.stdout)
            return data.get("chapters", [])
        except Exception:
            return []

    def play_chapter(self):
        if not self.file_path or not self.chapters: return
        
        if self.is_paused:
            self.is_paused = False
            self.resume_playback()
            return
            
        self.stop_audio()
        
        chapter = self.chapters[self.current_chapter_idx]
        start_time = float(chapter.get("start_time", 0))
        end_time = float(chapter.get("end_time", 0))
        
        self.chapter_duration = end_time - start_time
        self.update_info()
        self.resume_playback()

    def pause_audio(self):
        if self.is_playing and self.player_process:
            self.is_playing = False
            self.is_paused = True
            self.player_process.terminate()
            self.player_process = None
            
            self.current_play_time = max(0, self.current_play_time - 1.5)
            
            curr_str = self.format_time(self.current_play_time)
            dur_str = self.format_time(self.chapter_duration)
            self.time_label.config(text=f"{curr_str} / {dur_str}")
            
            self.save_playback_state()

    def resume_playback(self):
        chapter = self.chapters[self.current_chapter_idx]
        base_start_time = float(chapter.get("start_time", 0))
        
        actual_start_time = base_start_time + self.current_play_time
        remaining_duration = self.chapter_duration - self.current_play_time
        
        cmd = [
            "ffplay", "-nodisp", "-autoexit", "-loglevel", "error", 
            "-ss", str(actual_start_time), "-t", str(remaining_duration)
        ]
        
        if os.name != 'nt':
            vol_int = int(self.volume_var.get())
            cmd.extend(["-volume", str(vol_int)])
        
        audio_filters = []
        
        speed_val = float(self.playback_speed.get().replace("x", ""))
        if speed_val != 1.0:
            audio_filters.append(f"atempo={speed_val}")
            
        if getattr(self, 'voice_boost_var', None) and self.voice_boost_var.get():
            audio_filters.append("acompressor=threshold=-15dB:ratio=3:makeup=5dB")

        if getattr(self, 'skip_silence_var', None) and self.skip_silence_var.get():

            audio_filters.append("silenceremove=stop_periods=-1:stop_duration=0.5:stop_threshold=-40dB")

        if audio_filters:
            cmd.extend(["-af", ",".join(audio_filters)])
        
        if self.file_path.endswith(".aax") or self.file_path.endswith(".aaxc"):
            cmd.extend(self.get_drm_flags(self.file_path))
            
        cmd.append(self.file_path)

        if self.debug_mode.get():
            self.write_log(f"Starting player: {' '.join(cmd)}")

        self.player_process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        )
        
        if os.name == 'nt':
            self.root.after(500, self.on_volume_change)
            
        import time
        import time
        self._last_tick_time = time.time()
        self.is_playing = True
        
        active_proc = self.player_process
        threading.Thread(target=self.monitor_player_output, args=(active_proc,), daemon=True).start()
        self.update_playback_progress(active_proc)

    def format_time(self, seconds):
        m, s = divmod(int(seconds), 60)
        h, m = divmod(m, 60)
        if h > 0: return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    def update_playback_progress(self, active_proc):
        if not self.is_playing or self.player_process != active_proc or active_proc.poll() is not None:
            return
        
        import time
        now = time.time()
        delta = now - getattr(self, '_last_tick_time', now)
        self._last_tick_time = now
        
        speed_val = float(self.playback_speed.get().replace("x", ""))
        self.current_play_time += (delta * speed_val)

        real_time_delta = delta * speed_val
        self.session_listen_buffer += real_time_delta
        if self.session_listen_buffer >= 60.0:
            self.add_stat("seconds_listened", self.session_listen_buffer)
            self.session_listen_buffer = 0.0
        
        if self.current_play_time > self.chapter_duration:
            self.current_play_time = self.chapter_duration
            
        percent = (self.current_play_time / self.chapter_duration) * 100 if self.chapter_duration > 0 else 0
        self.progress_var.set(percent)
        
        curr_str = self.format_time(self.current_play_time)
        dur_str = self.format_time(self.chapter_duration)
        self.time_label.config(text=f"{curr_str} / {dur_str}")

        # Save to database every 10 seconds so the web server stays updated
        if not hasattr(self, '_last_disk_save_time'):
            self._last_disk_save_time = now
            
        if now - self._last_disk_save_time > 10:
            self.save_playback_state()
            self._last_disk_save_time = now

        self.root.after(500, self.update_playback_progress, active_proc)

    def monitor_player_output(self, proc):
        if not proc: return
        
        for line in proc.stderr:
            if line.strip():
                self.write_log(f"[PLAYER ERROR]: {line.strip()}")
        
        proc.wait()

        if self.player_process == proc and self.is_playing:
            if proc.returncode == 0:
                self.root.after(0, self.next_chapter)
            else:
                self.write_log(f"[CRITICAL]: Player crashed with code {proc.returncode}.")
                self.root.after(0, self.stop_audio)

    def next_chapter(self):
        self.save_playback_state()

        if self.current_chapter_idx < len(self.chapters) - 1:
            self.current_chapter_idx += 1
            self.current_play_time = 0

            if getattr(self, 'sleep_mode', None) == "chapters":
                self.sleep_chapters_remaining -= 1
                if self.sleep_chapters_remaining <= 0:
                    self.sleep_mode = None
                    self.timer_btn.config(text="Sleep: Off")
                    self.write_log("Sleep timer (chapters) finished. Pausing playback.")
                    self.is_paused = True 

                    chapter = self.chapters[self.current_chapter_idx]
                    start_time = float(chapter.get("start_time", 0))
                    end_time = float(chapter.get("end_time", 0))
                    self.chapter_duration = end_time - start_time
                    self.update_info()
                    
                    curr_str = self.format_time(self.current_play_time)
                    dur_str = self.format_time(self.chapter_duration)
                    self.time_label.config(text=f"{curr_str} / {dur_str}")
                    self.progress_var.set(0)
                    
                    if self.player_process:
                        self.player_process.terminate()
                        self.player_process = None
                        
                    return 
                else:
                    self.timer_btn.config(text=f"Sleep: {self.sleep_chapters_remaining} ch")

            self.is_paused = False
            self.play_chapter()
        else:
            self.stop_audio()
            self.add_stat("books_finished", 1)
            self.info_label.config(text="Finished Book")

    def prev_chapter(self):
        self.save_playback_state()
        if self.current_chapter_idx > 0:
            self.current_chapter_idx -= 1
            self.current_play_time = 0
            self.is_paused = False
            self.play_chapter()
        else:
            self.current_play_time = 0
            self.is_paused = False
            self.play_chapter()

    def stop_audio(self):
        self.is_playing = False
        self.is_paused = False
        if self.player_process:
            self.player_process.terminate()
            self.player_process = None
            
        self.save_playback_state()

    def update_info(self):
        if self.chapters:
            title = self.chapters[self.current_chapter_idx].get("tags", {}).get("title", f"Chapter {self.current_chapter_idx + 1}")
            self.info_label.config(text=f"Playing:\n{title}")

if __name__ == "__main__":
    root = TkinterDnD.Tk()
    app = AAXManagerApp(root)
    
    # current_engine = app.settings.get("ui_mode", "modern")
    
    # if current_engine == "modern":
    #     import sv_ttk
    #     sv_ttk.set_theme("dark") 
    # else:
    #     saved_palette = app.settings.get("classic_palette", "light")
    #     app.apply_classic_palette(saved_palette)
    saved_palette = app.settings.get("classic_palette", "dark")
    app.apply_classic_palette(saved_palette)
    root.mainloop()
