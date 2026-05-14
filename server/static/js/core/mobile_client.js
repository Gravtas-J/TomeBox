// ==========================================
// TOMEBOX CORE CLIENT
// ==========================================

class TomeBoxClient {
    constructor() {
        this.state = {
            allBooks: [],
            currentPath: null,
            currentAsin: null,
            currentChapters: [],
            currentProfile: "Main",
            rawLibraryData: {},
            speeds: [1.0, 1.25, 1.5, 1.75, 2.0],
            speedIndex: 0,
            activeChapterIndex: 0,
            sleep: { mode: null, timeout: null, targetTime: null },
            pairingViewLoaded: false,
            isPlaylist: false,
            globalDuration: 0
        };

        this.dom = {
            audio: document.getElementById('audio-player') || document.getElementById('main-audio'),
            playBtn: document.getElementById('btn-play-pause') || document.getElementById('play-pause-btn'),
            progressFill: document.getElementById('seek-progress') || document.getElementById('progress-fill'),
            playerBar: document.getElementById('player-bar'),
            speedBtn: document.getElementById('btn-speed') || document.getElementById('speed-btn'),
            sidebar: document.getElementById('mobile-sidebar'),
            overlay: document.getElementById('sidebar-overlay'),
            grid: document.getElementById('library-grid'),
            profSelect: document.getElementById('profile-selector'),
            searchBox: document.getElementById('search-box'),
            shelfFilter: document.getElementById('shelf-filter'),
            sortFilter: document.getElementById('sort-filter')
        };

        this.init();
    }

    async init() {
        this.registerServiceWorker();
        this.bindEvents();
        this.bindAudioEvents();
        this.handleRouting();

        await this.loadProfiles();
        await this.loadLibrary();
        await this.cueLastPlayedBook();
        
        setInterval(() => this.backgroundSync(), 10000);
    }

    registerServiceWorker() {
        if ('serviceWorker' in navigator) {
            window.addEventListener('load', () => {
                navigator.serviceWorker.register('/static/sw.js').catch(err => console.error('SW failed: ', err));
            });
        }
    }

    bindEvents() {
        window.addEventListener('hashchange', () => this.handleRouting());

        const bindClick = (id, handler) => {
            const el = document.getElementById(id);
            if (el) el.addEventListener('click', (e) => handler.call(this, e));
        };

        bindClick('btn-sidebar-toggle', this.toggleSidebar);
        bindClick('sidebar-overlay', this.toggleSidebar);
        bindClick('btn-search-toggle', this.toggleSearch);
        bindClick('btn-play-pause', this.togglePlay);
        bindClick('btn-speed', this.changeSpeed);
        bindClick('btn-sleep', this.openSleepMenu);
        bindClick('btn-chapters', this.openChapterMenu);
        bindClick('btn-bookmarks', this.openBookmarksModal);
        bindClick('btn-player-toggle', this.togglePlayerBar);
        bindClick('btn-seek-back', () => this.seekRelative(-15));
        bindClick('btn-seek-forward', () => this.seekRelative(15));
        bindClick('btn-prev-chap', () => this.skipChapter(-1));
        bindClick('btn-next-chap', () => this.skipChapter(1));
        
        document.querySelectorAll('.modal-bg-close').forEach(modal => {
            modal.addEventListener('click', (e) => { if (e.target === modal) this.closeAllModals(); });
        });
        
        bindClick('btn-sidebar-close', this.toggleSidebar);

        document.querySelectorAll('.sleep-option').forEach(opt => {
            opt.addEventListener('click', (e) => {
                if (e.target.dataset.mins) this.setSleepTimer(parseInt(e.target.dataset.mins));
                if (e.target.dataset.chapter) this.setSleepChapter(parseInt(e.target.dataset.chapter));
            });
        });
        bindClick('btn-custom-sleep', this.setCustomSleepChapter);
        bindClick('btn-sleep-off', this.setSleepOff);
        bindClick('btn-add-bookmark', this.addBookmark);

        document.querySelectorAll('.modal-close').forEach(btn => {
            btn.addEventListener('click', () => this.closeAllModals());
        });

        if (this.dom.profSelect) this.dom.profSelect.addEventListener('change', () => this.changeProfile());
        if (this.dom.searchBox) this.dom.searchBox.addEventListener('input', () => this.filterLibrary());
        if (this.dom.shelfFilter) this.dom.shelfFilter.addEventListener('change', () => this.filterLibrary());
        if (this.dom.sortFilter) this.dom.sortFilter.addEventListener('change', () => this.filterLibrary());

        const seekContainer = document.getElementById('seek-bar-container');
        if (seekContainer) seekContainer.addEventListener('click', (e) => this.seekAudio(e));

        document.querySelectorAll('.game-card').forEach(card => {
            card.addEventListener('click', (e) => {
                const gameId = e.currentTarget.dataset.game;
                if (gameId) this.launchGame(gameId);
            });
        });
        bindClick('btn-exit-game', () => this.exitGame());

        window.addEventListener('beforeunload', () => this.saveProgressToServer());
        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'hidden') this.saveProgressToServer();
        });
    }

    async loadProfiles() {
        try {
            const profRes = await fetch(`/api/profiles`);
            const profiles = await profRes.json();
            this.state.currentProfile = profiles[0] || "Main";
            
            if (this.dom.profSelect) {
                this.dom.profSelect.innerHTML = '';
                profiles.forEach(p => { 
                    this.dom.profSelect.innerHTML += `<option value="${this.escapeHtml(p)}" ${p === this.state.currentProfile ? 'selected' : ''}>${this.escapeHtml(p)}</option>`; 
                });
            }
        } catch (e) { console.error("Profile fetch failed", e); }
    }

    async loadLibrary() {
        try {
            const response = await fetch(`/api/library`);
            this.state.rawLibraryData = await response.json();
            this.renderGrid();
            document.dispatchEvent(new Event('tomebox:libraryLoaded'));
        } catch (e) { console.error("Failed to load library", e); }
    }

    async changeProfile() {
        this.state.currentProfile = this.dom.profSelect.value;
        await this.loadLibrary(); 
        
        this.dom.audio.pause();
        this.dom.playBtn.innerText = '▶';
        this.state.currentPath = null;
        this.dom.audio.src = '';
        if (this.dom.playerBar) this.dom.playerBar.classList.remove('active');
        
        await this.cueLastPlayedBook();
    }

    renderGrid() {
        if (!this.dom.grid) return;
        this.dom.grid.innerHTML = '';
        this.state.allBooks = [];
        let uniqueShelves = new Set();
        // --- ADDED PLAYLIST FORMAT ---
        const validFormats = ['M4B', 'MP3', 'AAXC', 'AAX', 'PLAYLIST'];

        for (const [path, data] of Object.entries(this.state.rawLibraryData)) {
            const isCloudOnly = data.download_status === 'cloud_only';
            if (!isCloudOnly && !validFormats.includes(data.format)) continue;

            let authorStr = data.authors || 'Unknown Author';
            const titleStr = data.title || "Unknown Title";
            const asin = data.asin || "Unknown";
            const safeAsin = this.escapeHtml(asin);
            
            const bookShelves = data.shelves || [];
            bookShelves.forEach(s => uniqueShelves.add(s));
            
            let resumePos = data.progress?.[this.state.currentProfile] || data.last_position || 0;
            let timePill = "";
            if (resumePos > 60) {
                const hrs = Math.floor(resumePos / 3600);
                const mins = Math.floor((resumePos % 3600) / 60);
                timePill = hrs > 0 ? `<span class="progress-pill">${hrs}h ${mins}m</span>` : `<span class="progress-pill">${mins}m</span>`;
            }

            const coverHtml = asin !== "Unknown" 
                ? `<img src="/api/cover/${encodeURIComponent(asin)}" class="cover-image" onerror="this.outerHTML='<div class=\\'cover-placeholder\\'>📖</div>'"/>`
                : `<div class="cover-placeholder">📖</div>`;

            const badge = isCloudOnly ? '<div class="status-badge cloud-only">Cloud</div>' : '<div class="status-badge downloaded">Downloaded</div>';
            const card = document.createElement('div');
            card.className = 'book-card';
            
            card.innerHTML = `
                ${badge}
                ${coverHtml}
                <p class="book-title">${this.escapeHtml(titleStr)}</p>
                <p class="book-author">${this.escapeHtml(authorStr)}</p>
                ${timePill}
                <div class="card-actions" style="margin-top: 10px; text-align: center;"></div>
                <div class="card-progress-track">
                    <div id="progress-bar-${safeAsin}" class="card-progress-fill"></div>
                </div>
            `;

            if (isCloudOnly) {
                const btn = document.createElement('button');
                btn.className = "action-btn-primary btn-small";
                btn.innerText = "⬇️ Download";
                btn.addEventListener('click', (e) => { e.stopPropagation(); if(window.queueSingleDownload) window.queueSingleDownload(asin); });
                card.querySelector('.card-actions').appendChild(btn);
                card.addEventListener('click', () => { if (window.queueSingleDownload) window.queueSingleDownload(asin); });
            } else {
                card.addEventListener('click', () => this.startPlayback(path, titleStr, authorStr, resumePos, safeAsin));
            }
            if (document.body.classList.contains('desktop') && window.DesktopApp) {
                window.DesktopApp.attachContextMenu(card, data);
            }
            this.dom.grid.appendChild(card);
            this.state.allBooks.push({ 
                path: path, element: card, searchString: `${titleStr} ${authorStr}`.toLowerCase(), 
                shelves: bookShelves, title: titleStr.toLowerCase(), author: authorStr.toLowerCase(),
                status: isCloudOnly ? 'cloud' : 'downloaded'
            });
        }
        
        if (this.dom.shelfFilter) {
            const currentValue = this.dom.shelfFilter.value;
            this.dom.shelfFilter.innerHTML = '<option value="all">All Shelves</option>';
            for (const shelf of [...uniqueShelves].sort()) {
                const option = document.createElement('option');
                option.value = shelf;
                option.textContent = shelf;
                this.dom.shelfFilter.appendChild(option);
            }
            if ([...uniqueShelves].includes(currentValue)) this.dom.shelfFilter.value = currentValue;
        }
        this.filterLibrary();
    }

    filterLibrary() {
        if (!this.dom.searchBox || !this.dom.grid) return;
        const query = this.dom.searchBox.value.toLowerCase();
        const selectedShelf = this.dom.shelfFilter ? this.dom.shelfFilter.value : 'all';
        const sortMethod = this.dom.sortFilter ? this.dom.sortFilter.value : 'title_asc';
        
        let visibleBooks = [];
        this.state.allBooks.forEach(book => {
            const matchesSearch = book.searchString.includes(query);
            const matchesShelf = selectedShelf === 'all' || book.shelves.includes(selectedShelf);
            if (matchesSearch && matchesShelf) { book.element.style.display = 'flex'; visibleBooks.push(book); } 
            else { book.element.style.display = 'none'; }
        });

        visibleBooks.sort((a, b) => {
            switch (sortMethod) {
                case 'title_asc': return a.title.localeCompare(b.title);
                case 'title_desc': return b.title.localeCompare(a.title);
                case 'author_asc': return a.author.localeCompare(b.author) || a.title.localeCompare(b.title);
                case 'status': return a.status === 'downloaded' ? -1 : 1;
                default: return 0;
            }
        });
        visibleBooks.forEach(book => this.dom.grid.appendChild(book.element));
    }

    async startPlayback(filePath, title, author, fallbackPosition, asin) {
        this.saveProgressToServer(); 
        
        // --- NEW: Global Playlist Evaluation ---
        const bookData = this.state.rawLibraryData[filePath] || {};
        this.state.isPlaylist = bookData.is_playlist === true;
        this.state.globalDuration = (bookData.duration_min || 0) * 60;
        
        let latestPos = bookData.progress?.[this.state.currentProfile] || fallbackPosition;

        this.state.currentPath = filePath;
        this.state.currentAsin = asin;
        
        const titleEl = document.getElementById('player-title') || document.getElementById('now-playing-title');
        if (titleEl) titleEl.innerText = title;
        
        const authorEl = document.getElementById('player-author') || document.getElementById('now-playing-author');
        if (authorEl) authorEl.innerText = author;

        try {
            const res = await fetch(`/api/chapters?path=${encodeURIComponent(filePath)}`);
            this.state.currentChapters = await res.json();
        } catch(e) { this.state.currentChapters = []; }
        
        this.dom.audio.playbackRate = this.state.speeds[this.state.speedIndex];
        
        // --- NEW: Route via Global Seek ---
        this.seekToGlobalTime(latestPos, true);
        
        this.setSleepOff(); 
        this.setupMediaSession(title, author, asin);
        
        if (this.dom.playerBar) {
            this.dom.playerBar.classList.remove('hidden');
            document.querySelector('main').style.paddingBottom = 'calc(var(--player-height) + 20px)';
        }
    }

    // --- NEW: Master Timeline Router ---
    seekToGlobalTime(targetTime, autoPlay = false) {
        if (this.state.isPlaylist && this.state.currentChapters.length > 0) {
            let targetIdx = 0;
            for (let i = 0; i < this.state.currentChapters.length; i++) {
                if (targetTime >= this.state.currentChapters[i].start) targetIdx = i; else break;
            }
            const targetCh = this.state.currentChapters[targetIdx];
            const physicalPath = targetCh.file_path || this.state.currentPath;
            
            if (targetIdx !== this.state.activeChapterIndex || !this.dom.audio.src.includes(encodeURIComponent(physicalPath))) {
                this.state.activeChapterIndex = targetIdx;
                const wasPlaying = !this.dom.audio.paused || autoPlay;
                
                this.dom.audio.src = `/api/stream?path=${encodeURIComponent(physicalPath)}`;
                
                // FIXED: Fire play() immediately to satisfy strict mobile gesture rules
                if (wasPlaying) {
                    this.dom.audio.play().catch(e => console.log("Buffering...", e));
                    if (this.dom.playBtn) this.dom.playBtn.innerText = '⏸';
                }
                
                this.dom.audio.onloadedmetadata = () => { 
                    this.dom.audio.currentTime = Math.max(0, targetTime - targetCh.start); 
                };
            } else {
                this.dom.audio.currentTime = Math.max(0, targetTime - targetCh.start);
                if (autoPlay) {
                    this.dom.audio.play().catch(() => {});
                    if (this.dom.playBtn) this.dom.playBtn.innerText = '⏸';
                }
            }
        } else {
            // Standard M4B Engine
            if (!this.dom.audio.src || !this.dom.audio.src.includes(encodeURIComponent(this.state.currentPath))) {
                this.dom.audio.src = `/api/stream?path=${encodeURIComponent(this.state.currentPath)}`;
                const wasPlaying = !this.dom.audio.paused || autoPlay;
                
                // FIXED: Fire play() immediately to satisfy strict mobile gesture rules
                if (wasPlaying) {
                    this.dom.audio.play().catch(e => console.log("Buffering...", e));
                    if (this.dom.playBtn) this.dom.playBtn.innerText = '⏸';
                }
                
                this.dom.audio.onloadedmetadata = () => { 
                    this.dom.audio.currentTime = targetTime; 
                };
            } else {
                this.dom.audio.currentTime = targetTime;
                if (autoPlay) {
                    this.dom.audio.play().catch(() => {});
                    if (this.dom.playBtn) this.dom.playBtn.innerText = '⏸';
                }
            }
        }
    }

    async cueLastPlayedBook() {
        try {
            const res = await fetch(`/api/last_played/${this.state.currentProfile}`);
            const data = await res.json();
            if (data.path && this.state.rawLibraryData[data.path]) {
                const book = this.state.rawLibraryData[data.path];
                let resumePos = book.progress?.[this.state.currentProfile] || book.last_position || 0;
                await this.startPlayback(data.path, book.title, book.authors, resumePos, book.asin);
                this.dom.audio.pause();
                if (this.dom.playBtn) this.dom.playBtn.innerText = '▶';
            }
        } catch (e) { console.error("Failed to cue", e); }
    }

    bindAudioEvents() {
        this.dom.audio.addEventListener('timeupdate', () => this.handleTimeUpdate());
        
        // --- NEW: Gapless Playlist Advancing ---
        this.dom.audio.addEventListener('ended', () => { 
            if (this.state.isPlaylist && this.state.activeChapterIndex + 1 < this.state.currentChapters.length) {
                const nextCh = this.state.currentChapters[this.state.activeChapterIndex + 1];
                this.seekToGlobalTime(nextCh.start, true);
            } else {
                this.dom.playBtn.innerText = '▶'; 
                if (this.dom.progressFill) this.dom.progressFill.style.width = '0%'; 
            }
        });
        
        this.dom.audio.addEventListener('pause', () => this.saveProgressToServer());
    }

    handleTimeUpdate() {
        if (!this.dom.audio.duration && !this.state.isPlaylist) return;
        
        let globalTime = this.dom.audio.currentTime;
        let chStart = 0; 
        let chEnd = this.dom.audio.duration || 0; 
        let chTitle = document.getElementById('player-title')?.innerText || "Playing";

        if (this.state.isPlaylist && this.state.currentChapters.length > 0) {
            const currentCh = this.state.currentChapters[this.state.activeChapterIndex];
            globalTime = this.dom.audio.currentTime + currentCh.start;
            chStart = currentCh.start;
            chEnd = currentCh.end || (chStart + this.dom.audio.duration);
            chTitle = currentCh.title;
        } else if (this.state.currentChapters.length > 0) {
            let activeIdx = 0;
            for (let i = 0; i < this.state.currentChapters.length; i++) {
                if (globalTime >= this.state.currentChapters[i].start) activeIdx = i; else break;
            }
            this.state.activeChapterIndex = activeIdx;
            const currentCh = this.state.currentChapters[activeIdx];
            chStart = currentCh.start; 
            chTitle = currentCh.title;
            if (activeIdx + 1 < this.state.currentChapters.length) { 
                chEnd = this.state.currentChapters[activeIdx + 1].start; 
            }
        }

        const chapterDuration = chEnd - chStart;
        const timeIntoChapter = globalTime - chStart;
        const progressPercent = chapterDuration > 0 ? (timeIntoChapter / chapterDuration) * 100 : 0;

        if (this.dom.progressFill) this.dom.progressFill.style.width = `${progressPercent}%`;
        
        const chapterTitleEl = document.getElementById('player-chapter-title');
        if (chapterTitleEl) chapterTitleEl.textContent = chTitle;
        
        const timeEl = document.getElementById('player-time');
        if (timeEl) timeEl.textContent = `${this.formatTime(timeIntoChapter)} / ${this.formatTime(chapterDuration)}`;
        
        if (this.state.sleep.mode === 'chapter' && this.state.sleep.targetTime !== null) {
            if (globalTime >= this.state.sleep.targetTime - 1) {
                this.dom.audio.pause();
                this.dom.playBtn.innerText = '▶';
                this.setSleepOff();
            }
        }
    }

    escapeHtml(unsafe) {
        if (unsafe == null) return "";
        return String(unsafe).replace(/[&<>"']/g, m => ({ '&': "&amp;", '<': "&lt;", '>': "&gt;", '"': "&quot;", "'": "&#039;" })[m]);
    }

    formatTime(sec) {
        const h = Math.floor(sec / 3600); const m = Math.floor((sec % 3600) / 60); const s = Math.floor(sec % 60);
        if (h > 0) return `${h}:${m.toString().padStart(2,'0')}:${s.toString().padStart(2,'0')}`;
        return `${m}:${s.toString().padStart(2,'0')}`;
    }

    backgroundSync() { if (!this.dom.audio.paused && this.state.currentPath) this.saveProgressToServer(); }

    saveProgressToServer() {
        if (this.state.currentPath && this.dom.audio.currentTime > 0) {
            let globalTime = this.dom.audio.currentTime;
            if (this.state.isPlaylist && this.state.currentChapters.length > 0) {
                globalTime += this.state.currentChapters[this.state.activeChapterIndex].start;
            }
            
            if (this.state.rawLibraryData[this.state.currentPath]) {
                if (!this.state.rawLibraryData[this.state.currentPath].progress) this.state.rawLibraryData[this.state.currentPath].progress = {};
                this.state.rawLibraryData[this.state.currentPath].progress[this.state.currentProfile] = globalTime;
            }
            fetch(`/api/progress`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path: this.state.currentPath, position: globalTime, profile: this.state.currentProfile }),
                keepalive: true
            }).catch(() => {});
        }
    }

    setupMediaSession(title, author, asin) {
        if ('mediaSession' in navigator) {
            const artworkUrl = asin !== "Unknown" ? [{ src: `/api/cover/${asin}`, sizes: '500x500', type: 'image/jpeg' }] : [];
            navigator.mediaSession.metadata = new MediaMetadata({ title, artist: author, album: 'TomeBox', artwork: artworkUrl });
            navigator.mediaSession.setActionHandler('seekbackward', () => this.seekRelative(-15));
            navigator.mediaSession.setActionHandler('seekforward', () => this.seekRelative(15));
        }
    }

    togglePlayerBar() {
        if (!this.dom.playerBar) return;
        const isHidden = this.dom.playerBar.classList.toggle('hidden');
        const mainContainer = document.querySelector('main');
        if (mainContainer) mainContainer.style.paddingBottom = isHidden ? '20px' : 'calc(var(--player-height) + 20px)';
    }

    togglePlay() {
        if (!this.dom.audio.src) return;
        if (this.dom.audio.paused) { this.dom.audio.play(); this.dom.playBtn.innerText = '⏸'; } 
        else { this.dom.audio.pause(); this.dom.playBtn.innerText = '▶'; }
    }

    seekRelative(seconds) {
        let globalTime = this.dom.audio.currentTime + (this.state.isPlaylist && this.state.currentChapters.length > 0 ? this.state.currentChapters[this.state.activeChapterIndex].start : 0);
        let maxTime = this.state.isPlaylist ? this.state.globalDuration : this.dom.audio.duration;
        let newGlobalTime = Math.max(0, Math.min(globalTime + seconds, maxTime));
        this.seekToGlobalTime(newGlobalTime, !this.dom.audio.paused);
    }

    skipChapter(direction) {
        if (!this.state.currentChapters.length) { this.seekRelative(direction * 15); return; }
        const now = this.dom.audio.currentTime + (this.state.isPlaylist ? this.state.currentChapters[this.state.activeChapterIndex].start : 0);
        
        if (direction === 1) { 
            const nextCh = this.state.currentChapters.find(c => c.start > now + 2);
            if (nextCh) this.seekToGlobalTime(nextCh.start, !this.dom.audio.paused);
        } else { 
            const prevCh = [...this.state.currentChapters].reverse().find(c => c.start < now - 3);
            if (prevCh) this.seekToGlobalTime(prevCh.start, !this.dom.audio.paused);
            else this.seekToGlobalTime(0, !this.dom.audio.paused); 
        }
    }

    changeSpeed() {
        this.state.speedIndex = (this.state.speedIndex + 1) % this.state.speeds.length;
        const spd = this.state.speeds[this.state.speedIndex];
        this.dom.audio.playbackRate = spd;
        if (this.dom.speedBtn) this.dom.speedBtn.innerText = spd.toFixed(1) + 'x';
    }

    launchGame(gameId) {
        document.getElementById('games-menu').style.display = 'none';
        document.getElementById('active-game-container').style.display = 'block';

        const searchContainer = document.getElementById('search-container');
        if (searchContainer) searchContainer.classList.remove('open');

        if (this.dom.playerBar) {
            this.dom.playerBar.classList.add('hidden');
            const mainContainer = document.querySelector('main');
            if (mainContainer) mainContainer.style.paddingBottom = '20px';
        }

        if (window.activeGameEngine && typeof window.activeGameEngine.stop === 'function') {
            window.activeGameEngine.stop();
        }

        try {
            if (gameId === 'particles') window.activeGameEngine = typeof BubblePopEngine !== 'undefined' ? BubblePopEngine : null;
            else if (gameId === '2048') window.activeGameEngine = typeof MergeEngine !== 'undefined' ? MergeEngine : null;
            else if (gameId === 'breakout') window.activeGameEngine = typeof BreakoutEngine !== 'undefined' ? BreakoutEngine : null;
            else if (gameId === 'invaders') window.activeGameEngine = typeof SpaceInvadersEngine !== 'undefined' ? SpaceInvadersEngine : null;
        } catch(e) { window.activeGameEngine = null; }

        if (window.activeGameEngine && typeof window.activeGameEngine.start === 'function') {
            setTimeout(() => window.activeGameEngine.start(), 10);
        } else {
            this.exitGame();
        }
    }

    exitGame() {
        if (window.activeGameEngine && typeof window.activeGameEngine.stop === 'function') {
            window.activeGameEngine.stop();
        }
        window.activeGameEngine = null;
        
        const canvas = document.getElementById('game-canvas');
        if (canvas) {
            const ctx = canvas.getContext('2d');
            ctx.clearRect(0, 0, canvas.width, canvas.height);
        }
        
        document.getElementById('active-game-container').style.display = 'none';
        document.getElementById('games-menu').style.display = 'grid'; 

        if (this.state.currentPath && this.dom.playerBar) {
            this.dom.playerBar.classList.remove('hidden');
            const mainContainer = document.querySelector('main');
            if (mainContainer) mainContainer.style.paddingBottom = 'calc(var(--player-height) + 20px)';
        }
    }

    openSleepMenu() { document.getElementById('sleep-modal').style.display = 'flex'; }
    
    openChapterMenu() {
        if (!this.state.currentChapters.length) return alert("No chapters found for this audiobook.");
        const list = document.getElementById('chapter-list');
        list.innerHTML = '';
        const now = this.dom.audio.currentTime + (this.state.isPlaylist ? this.state.currentChapters[this.state.activeChapterIndex].start : 0);
        
        let activeIdx = this.state.currentChapters.findIndex(c => c.start > now) - 1;
        if (activeIdx < 0 && now >= this.state.currentChapters[0].start) activeIdx = this.state.currentChapters.length - 1;
        
        this.state.currentChapters.forEach((ch, idx) => {
            const div = document.createElement('div');
            div.className = 'list-item' + (idx === activeIdx ? ' active' : '');
            div.innerHTML = `<span>${this.escapeHtml(ch.title)}</span> <span>${this.formatTime(ch.start)}</span>`;
            div.addEventListener('click', () => { 
                this.seekToGlobalTime(ch.start, !this.dom.audio.paused);
                this.closeAllModals(); 
            });
            list.appendChild(div);
        });
        document.getElementById('chapter-modal').style.display = 'flex';
    }

    setSleepTimer(mins) {
        clearTimeout(this.state.sleep.timeout);
        this.state.sleep.mode = 'time';
        this.state.sleep.targetTime = null;
        this.state.sleep.timeout = setTimeout(() => { 
            this.dom.audio.pause(); 
            if (this.dom.playBtn) this.dom.playBtn.innerText = '▶'; 
            this.setSleepOff(); 
        }, mins * 60000);
        document.getElementById('btn-sleep').style.color = 'var(--accent)';
        this.closeAllModals();
    }

    setSleepChapter(chapterCount) {
        clearTimeout(this.state.sleep.timeout);
        if (!this.state.currentChapters.length) return;
        
        this.state.sleep.mode = 'chapter';
        const now = this.dom.audio.currentTime + (this.state.isPlaylist ? this.state.currentChapters[this.state.activeChapterIndex].start : 0);
        let currentIdx = this.state.currentChapters.findIndex(c => c.start > now) - 1;
        if (currentIdx < 0 && now >= this.state.currentChapters[0].start) currentIdx = this.state.currentChapters.length - 1;
        
        let targetIdx = currentIdx + chapterCount;
        this.state.sleep.targetTime = (targetIdx < this.state.currentChapters.length) 
            ? this.state.currentChapters[targetIdx].start 
            : (this.state.isPlaylist ? this.state.globalDuration : this.dom.audio.duration);

        document.getElementById('btn-sleep').style.color = 'var(--accent)';
        this.closeAllModals();
    }

    setCustomSleepChapter() {
        const inputElem = document.getElementById('custom-chapter-input');
        let count = parseInt(inputElem.value, 10);
        if (isNaN(count) || count < 1) { count = 1; inputElem.value = 1; }
        this.setSleepChapter(count);
    }

    setSleepOff() {
        clearTimeout(this.state.sleep.timeout);
        this.state.sleep = { mode: null, timeout: null, targetTime: null };
        if (document.getElementById('btn-sleep')) document.getElementById('btn-sleep').style.color = '#aaa';
        this.closeAllModals();
    }

    async openBookmarksModal() {
        document.getElementById('bookmarks-modal').style.display = 'flex';
        await this.loadBookmarks();
    }

    async loadBookmarks() {
        const listEl = document.getElementById('bookmarks-list');
        if (!this.state.currentPath) {
            listEl.innerHTML = '<p style="color: #aaa; text-align: center;">No active book playing.</p>';
            return;
        }
        try {
            const res = await fetch(`/api/library/bookmarks?path=${encodeURIComponent(this.state.currentPath)}`, { cache: 'no-store' });
            if (!res.ok) throw new Error("Failed to fetch");
            const data = await res.json();
            
            if (!data.bookmarks || data.bookmarks.length === 0) {
                listEl.innerHTML = '<p style="color: #aaa; text-align: center;">No bookmarks yet.</p>';
                return;
            }
            listEl.innerHTML = '';
            data.bookmarks.forEach((bm, idx) => {
                const row = document.createElement('div');
                row.className = 'profile-row'; 
                row.innerHTML = `
                    <div class="profile-row-info" style="cursor: pointer;">
                        <div class="profile-row-name">${this.escapeHtml(bm.note || 'Bookmark')}</div>
                        <div class="profile-row-status">⏱️ ${this.formatTime(bm.time)}</div>
                    </div>
                    <button class="action-btn-secondary action-btn-danger" data-idx="${idx}">🗑️</button>
                `;
                row.querySelector('.profile-row-info').addEventListener('click', () => {
                    this.seekToGlobalTime(bm.time, true);
                    this.closeAllModals();
                });
                row.querySelector('.action-btn-danger').addEventListener('click', () => this.deleteBookmark(idx));
                listEl.appendChild(row);
            });
        } catch (e) { listEl.innerHTML = '<p style="color: #ff6b6b; text-align: center;">Failed to load bookmarks.</p>'; }
    }

    async addBookmark() {
        if (!this.state.currentPath || this.dom.audio.currentTime === undefined) return alert("No book is currently playing.");
        const globalTime = this.dom.audio.currentTime + (this.state.isPlaylist && this.state.currentChapters.length > 0 ? this.state.currentChapters[this.state.activeChapterIndex].start : 0);
        const note = prompt(`Add a note for this bookmark at ${this.formatTime(globalTime)}:`, "Awesome moment");
        if (note === null) return; 
        try {
            const res = await fetch('/api/library/bookmarks', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path: this.state.currentPath, time: globalTime, note: note.trim() })
            });
            if (res.ok) await this.loadBookmarks();
            else alert("Failed to save bookmark.");
        } catch (e) { alert(`Network error: ${e.message}`); }
    }

    async deleteBookmark(index) {
        if (!confirm("Delete this bookmark?")) return;
        try {
            const res = await fetch('/api/library/bookmarks', {
                method: 'DELETE', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path: this.state.currentPath, index: index })
            });
            if (res.ok) await this.loadBookmarks();
        } catch (e) { console.error("Failed to delete bookmark", e); }
    }

    seekAudio(e) {
        if (!this.dom.audio.duration && !this.state.isPlaylist) return;
        const container = document.getElementById('seek-bar-container');
        const clickPercent = Math.max(0, Math.min(1, (e.clientX - container.getBoundingClientRect().left) / container.clientWidth));
        
        let chStart = 0, chEnd = this.dom.audio.duration; 
        if (this.state.currentChapters.length > 0) {
            const currentCh = this.state.currentChapters[this.state.activeChapterIndex];
            chStart = currentCh.start;
            if (this.state.isPlaylist) {
                chEnd = currentCh.end || (chStart + this.dom.audio.duration);
            } else {
                chEnd = this.state.activeChapterIndex + 1 < this.state.currentChapters.length ? this.state.currentChapters[this.state.activeChapterIndex + 1].start : this.dom.audio.duration;
            }
        }
        
        const targetGlobalTime = chStart + ((chEnd - chStart) * clickPercent);
        this.seekToGlobalTime(targetGlobalTime, !this.dom.audio.paused);
    }

    toggleSidebar() {
        if (!this.dom.sidebar) return;
        const isOpen = this.dom.sidebar.classList.toggle('open');
        this.dom.overlay.style.display = isOpen ? 'block' : 'none';
        document.body.style.overflow = isOpen ? 'hidden' : '';
    }

    toggleSearch() {
        const searchContainer = document.getElementById('search-container');
        if (!searchContainer) return;
        const isOpen = searchContainer.classList.toggle('open');
        if (isOpen) setTimeout(() => this.dom.searchBox.focus(), 300);
        else this.dom.searchBox.blur();
    }

    closeAllModals() { document.querySelectorAll('.modal-overlay').forEach(m => m.style.display = 'none'); }
    
    async loadPairingView() {
        if (this.state.pairingViewLoaded) return;

        const qrContainer = document.getElementById('qr-container');
        const urlElement = document.getElementById('pairing-url');
        if (!qrContainer || !urlElement) return;

        try {
            const res = await fetch('/api/pairing-info'); 
            if (!res.ok) throw new Error(`Server returned ${res.status}`);
            
            const data = await res.json();
            const pairingUrl = data.pairing_url;
            
            urlElement.textContent = pairingUrl;
            
            await new Promise((resolve, reject) => {
                if (window.QRCode) { resolve(); return; }
                const script = document.createElement('script');
                script.src = 'https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js';
                script.onload = resolve;
                script.onerror = reject;
                document.head.appendChild(script);
            });
            
            qrContainer.innerHTML = '';
            new QRCode(qrContainer, { text: pairingUrl, width: 200, height: 200, colorDark: '#000000', colorLight: '#ffffff', correctLevel: QRCode.CorrectLevel.H });
            
            urlElement.addEventListener('click', () => {
                navigator.clipboard.writeText(pairingUrl).then(() => {
                    const original = pairingUrl;
                    urlElement.textContent = 'Copied to clipboard!';
                    setTimeout(() => urlElement.textContent = original, 1500);
                });
            });
            
            this.state.pairingViewLoaded = true;
            
        } catch (error) {
            qrContainer.innerHTML = `<p style="color: #ff6b6b; text-align: center; margin: 0;">Failed to load pairing code.</p>`;
            urlElement.textContent = "Error loading URL";
        }
    }

    handleRouting() {
        if (document.body.classList.contains('desktop')) return;
        const hash = window.location.hash || '#/library';
        
        document.querySelectorAll('.view-container').forEach(v => v.style.display = 'none');
        document.querySelectorAll('.sidebar .nav-item').forEach(n => n.classList.remove('active'));

        const routeMap = { '#/games': 'games', '#/pairing': 'pairing' };
        const viewId = routeMap[hash] || 'library';
        
        const activeView = document.getElementById(`view-${viewId}`);
        const activeNav = document.querySelector(`.nav-item[data-view="${viewId}"]`);
        
        if (activeView) activeView.style.display = 'block';
        if (activeNav) activeNav.classList.add('active');

        if (viewId === 'pairing') {
            this.loadPairingView();
        }
    }
}

document.addEventListener('DOMContentLoaded', () => {
    window.TomeBoxApp = new TomeBoxClient();
});