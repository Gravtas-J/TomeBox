if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
        navigator.serviceWorker.register('/static/sw.js').catch(err => {
            console.error('ServiceWorker registration failed: ', err);
        });
    });
}

// Support BOTH Desktop and Mobile element IDs
const audio = document.getElementById('audio-player') || document.getElementById('main-audio');
const playBtn = document.getElementById('btn-play-pause') || document.getElementById('play-pause-btn');
const progressFill = document.getElementById('seek-progress') || document.getElementById('progress-fill');
const playerBar = document.getElementById('player-bar');
const speedBtn = document.getElementById('btn-speed') || document.getElementById('speed-btn');

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
        const profRes = await fetch(`/api/profiles`);
        const profiles = await profRes.json();
        
        // Set the current profile FIRST
        currentProfile = profiles[0] || "Main";
        
        const profSelect = document.getElementById('profile-selector');
        
        // SAFETY CHECK: Only update the HTML if the element actually exists
        if (profSelect) {
            profSelect.innerHTML = '';
            profiles.forEach(p => { 
                profSelect.innerHTML += `<option value="${p}" ${p===currentProfile?'selected':''}>${p}</option>`; 
            });
        }
    } catch (e) { 
        console.error("Profile fetch failed", e); 
    }
    
    await loadLibrary();
    await cueLastPlayedBook();
}

async function loadLibrary() {
    try {
        // FIXED: Backtick syntax was broken here
        const response = await fetch(`/api/library`);
        rawLibraryData = await response.json();
        window.currentLibraryData = rawLibraryData;
        renderGrid();
    } catch (e) { console.error("Failed to load library", e); }
    if (window.updateLibraryCountDisplay) window.updateLibraryCountDisplay();
}

function renderGrid() {
    const grid = document.getElementById('library-grid');
    const shelfFilter = document.getElementById('shelf-filter');
    
    grid.innerHTML = '';
    allBooks = [];
    let uniqueShelves = new Set();

    for (const [path, data] of Object.entries(rawLibraryData)) {
        // Skip non-audio local files but always include cloud-only items
        // Skip non-audio local files but always include cloud-only items
        const isCloudOnly = data.download_status === 'cloud_only';

        // NEW: Allow raw AAX/AAXC files to be displayed so we can convert them!
        const validFormats = ['M4B', 'MP3', 'AAXC', 'AAX'];
        if (!isCloudOnly && !validFormats.includes(data.format)) continue;

        let authorStr = data.authors || 'Unknown Author';
        const titleStr = data.title || "Unknown Title";
        const asin = data.asin || "Unknown";
        const bookShelves = data.shelves || [];
        bookShelves.forEach(s => uniqueShelves.add(s));
        
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

        // Status badge — Downloaded (green) or Cloud (blue)
        const badge = isCloudOnly
            ? '<div class="status-badge cloud-only">Cloud</div>'
            : '<div class="status-badge downloaded">Downloaded</div>';

        const card = document.createElement('div');
        card.className = 'book-card';
        
        // --- NEW: Dynamic Actions & Progress Bar ---
        const actionButton = isCloudOnly 
            ? `<button class="action-btn-primary btn-small" onclick="if(window.queueSingleDownload) window.queueSingleDownload('${asin}'); event.stopPropagation();">⬇️ Download</button>`
            : ``; 
            
        const cardProgressBar = `
            <div class="card-progress-track">
                <div id="progress-bar-${asin}" class="card-progress-fill"></div>
                <div id="progress-text-${asin}" class="card-progress-text"></div>
            </div>
        `;

        // Handle click events (Download vs Play)
        card.onclick = () => {
            if (isCloudOnly) {
                if (window.queueSingleDownload) {
                    window.queueSingleDownload(asin);
                } else {
                    console.log(`Cloud-only book clicked: ${titleStr} (Downloads not supported on this view)`);
                }
            } else {
                startPlayback(path, titleStr, authorStr, resumePos, asin);
            }
        };
        
        // Attach the right-click context menu if we are on Desktop
        if (typeof attachContextMenu === 'function') {
            attachContextMenu(card, data);
        }
        
        card.innerHTML = `
            ${badge}
            ${coverHtml}
            <p class="book-title">${titleStr}</p>
            <p class="book-author">${authorStr}</p>
            ${timePill}
            <div class="card-actions" style="margin-top: 10px; text-align: center;">${actionButton}</div>
            ${cardProgressBar}
        `;
        grid.appendChild(card);
        
        let seriesStr = "";
        if (data.series) {
            if (Array.isArray(data.series)) {
                seriesStr = data.series.map(s => s.title || s).join(', ');
            } else {
                seriesStr = data.series;
            }
        }

        allBooks.push({ 
            path: path, 
            element: card, 
            searchString: `${titleStr} ${authorStr} ${seriesStr}`.toLowerCase(), 
            shelves: bookShelves,
            // NEW: Data needed for sorting
            title: titleStr.toLowerCase(),
            author: authorStr.toLowerCase(),
            series: seriesStr.toLowerCase(),
            status: isCloudOnly ? 'cloud' : 'downloaded'
        });
    }
    
    // Populate the shelf filter dropdown
    if (shelfFilter) {
        const currentValue = shelfFilter.value;
        shelfFilter.innerHTML = '<option value="all">All Shelves</option>';
        for (const shelf of [...uniqueShelves].sort()) {
            const option = document.createElement('option');
            option.value = shelf;
            option.textContent = shelf;
            shelfFilter.appendChild(option);
        }
        if ([...uniqueShelves].includes(currentValue)) {
            shelfFilter.value = currentValue;
        }
    }
    filterLibrary();
}

async function cueLastPlayedBook() {
    try {
        const res = await fetch(`/api/last_played/${currentProfile}`);
        const data = await res.json();
        if (data.path && rawLibraryData[data.path]) {
            const book = rawLibraryData[data.path];
            let authorStr = book.authors || 'Unknown Author';
            const titleStr = book.title || "Unknown Title";
            const asin = book.asin || "Unknown";
            
            let resumePos = 0;
            if (book.progress && book.progress[currentProfile] !== undefined) {
                resumePos = book.progress[currentProfile];
            } else if (book.last_position) {
                resumePos = book.last_position;
            }
            
            currentPath = data.path;
            currentAsin = asin;
            
            // SAFE FALLBACKS: Works on both desktop and mobile UI
            const titleEl = document.getElementById('player-title') || document.getElementById('now-playing-title');
            if (titleEl) titleEl.innerText = titleStr;
            
            const authorEl = document.getElementById('player-author') || document.getElementById('now-playing-author');
            if (authorEl) authorEl.innerText = authorStr;
            
            const coverImg = document.getElementById('player-cover');
            if (coverImg) {
                if (asin && asin !== "Unknown") {
                    coverImg.src = `/api/cover/${asin}`;
                    coverImg.style.display = 'block';
                } else {
                    coverImg.style.display = 'none';
                }
            }
            
            audio.src = `/api/stream?path=${encodeURIComponent(data.path)}`;
            audio.playbackRate = currentSpeed; 
            
            audio.onloadedmetadata = () => {
                audio.currentTime = resumePos;
            };
            
            if (playBtn) playBtn.innerText = '▶';
            if (playerBar) playerBar.classList.add('active');
            setSleepOff(); 

            try {
                const chapRes = await fetch(`/api/chapters?path=${encodeURIComponent(data.path)}`);
                currentChapters = await chapRes.json();
            } catch(e) { currentChapters = []; }

            if ('mediaSession' in navigator) {
                const artworkUrl = asin !== "Unknown" ? [{ src: `/api/cover/${asin}`, sizes: '500x500', type: 'image/jpeg' }] : [];
                navigator.mediaSession.metadata = new MediaMetadata({ title: titleStr, artist: authorStr, album: 'TomeBox', artwork: artworkUrl });
                navigator.mediaSession.setActionHandler('seekbackward', () => skipAudio(-15));
                navigator.mediaSession.setActionHandler('seekforward', () => skipAudio(15));
                navigator.mediaSession.setActionHandler('previoustrack', () => skipChapter(-1));
                navigator.mediaSession.setActionHandler('nexttrack', () => skipChapter(1));
            }
        }
    } catch (e) { console.error("Failed to cue last played", e); }
}
async function changeProfile() {
    currentProfile = document.getElementById('profile-selector').value;
    await loadLibrary(); 
    
    audio.pause();
    playBtn.innerText = '▶';
    currentPath = null;
    audio.src = '';
    playerBar.classList.remove('active');
    
    await cueLastPlayedBook();
}

function filterLibrary() {
    const query = document.getElementById('search-box').value.toLowerCase();
    const selectedShelf = document.getElementById('shelf-filter').value;
    const sortMethod = document.getElementById('sort-filter') ? document.getElementById('sort-filter').value : 'title_asc';
    
    let visibleBooks = [];

    // 1. Filter: Hide or Show the cards based on search and shelf
    allBooks.forEach(book => {
        const matchesSearch = book.searchString.includes(query);
        const matchesShelf = selectedShelf === 'all' || book.shelves.includes(selectedShelf);
        
        if (matchesSearch && matchesShelf) {
            book.element.style.display = 'flex';
            visibleBooks.push(book);
        } else {
            book.element.style.display = 'none';
        }
    });

    // 2. Sort: Order the visible array based on the dropdown selection
    visibleBooks.sort((a, b) => {
        switch (sortMethod) {
            case 'title_asc':
                return a.title.localeCompare(b.title);
            case 'title_desc':
                return b.title.localeCompare(a.title);
            case 'author_asc':
                // Sort by author, then fall back to title if author is the same
                return a.author.localeCompare(b.author) || a.title.localeCompare(b.title);
            case 'series_asc':
                // Group books without a series at the bottom
                if (a.series && !b.series) return -1;
                if (!a.series && b.series) return 1;
                // Sort by series name, then by title (which handles Book 1, Book 2 etc.)
                return a.series.localeCompare(b.series) || a.title.localeCompare(b.title);
            case 'status':
                // Put downloaded (local) books first, then cloud, then alphabetize
                if (a.status !== b.status) {
                    return a.status === 'downloaded' ? -1 : 1;
                }
                return a.title.localeCompare(b.title);
            default:
                return 0;
        }
    });

    // 3. Render: Re-append elements to the grid container
    // (Appending an existing DOM node moves it to the bottom, effectively reordering the grid)
    const grid = document.getElementById('library-grid');
    if (grid) {
        visibleBooks.forEach(book => {
            grid.appendChild(book.element);
        });
    }
}

async function startPlayback(filePath, title, author, fallbackPosition, asin) {
    saveProgressToServer(); 
    
    let latestPosition = fallbackPosition;
    if (rawLibraryData[filePath]) {
        if (rawLibraryData[filePath].progress && rawLibraryData[filePath].progress[currentProfile] !== undefined) {
            latestPosition = rawLibraryData[filePath].progress[currentProfile];
        } else if (rawLibraryData[filePath].last_position) {
            latestPosition = rawLibraryData[filePath].last_position;
        }
    }

    currentPath = filePath;
    currentAsin = asin;
    
    // SAFE FALLBACKS: Works on both desktop and mobile UI
    const titleEl = document.getElementById('player-title') || document.getElementById('now-playing-title');
    if (titleEl) titleEl.innerText = title;
    
    const authorEl = document.getElementById('player-author') || document.getElementById('now-playing-author');
    if (authorEl) authorEl.innerText = author;
    
    const coverImg = document.getElementById('player-cover');
    if (coverImg) {
        if (asin && asin !== "Unknown") {
            coverImg.src = `/api/cover/${asin}`;
            coverImg.style.display = 'block';
        } else {
            coverImg.style.display = 'none';
        }
    }

    // AWAIT CHAPTERS FIRST: Prevent race condition before audio plays
    try {
        const res = await fetch(`/api/chapters?path=${encodeURIComponent(filePath)}`);
        currentChapters = await res.json();
    } catch(e) { 
        currentChapters = []; 
    }
    
    // Set fallback title if the file genuinely has no chapters
    if (!currentChapters || currentChapters.length === 0) {
        const chapTitleEl = document.getElementById('player-chapter-title');
        if (chapTitleEl) chapTitleEl.textContent = title;
    }
    
    audio.src = `/api/stream?path=${encodeURIComponent(filePath)}`;
    audio.playbackRate = currentSpeed; 
    
    audio.onloadedmetadata = () => {
        audio.currentTime = latestPosition;
    };
    
    audio.play().then(() => {
        if (playBtn) playBtn.innerText = '⏸';
        if (playerBar) playerBar.classList.add('active');
    }).catch(err => console.error("Audio play failed:", err));
    
    setSleepOff(); 

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

setInterval(() => {
    if (!audio.paused && currentPath) {
        const pos = audio.currentTime;
        
        if (rawLibraryData[currentPath]) {
            if (!rawLibraryData[currentPath].progress) rawLibraryData[currentPath].progress = {};
            rawLibraryData[currentPath].progress[currentProfile] = pos;
        }

        // FIXED: Added token to the progress sync
        fetch(`/api/progress`, {
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

function changeSpeed() {
    speedIndex = (speedIndex + 1) % speeds.length;
    currentSpeed = speeds[speedIndex];
    audio.playbackRate = currentSpeed;
    speedBtn.innerText = currentSpeed.toFixed(1) + 'x';
}

// ==========================================
// PLAYBACK & CHAPTER TRACKING
// ==========================================
let activeChapterIndex = 0; 

audio.addEventListener('timeupdate', () => {
    if (!audio.duration) return;
    
    const currentTime = audio.currentTime;
    let chStart = 0;
    let chEnd = audio.duration;
    
    // Fallback to the main book title if the file has no chapters
    const titleEl = document.getElementById('player-title') || document.getElementById('now-playing-title');
    let chTitle = titleEl ? titleEl.innerText : "Playing";

    // 1. Find which chapter we are currently in
    if (typeof currentChapters !== 'undefined' && currentChapters && currentChapters.length > 0) {
        let activeIdx = 0;
        for (let i = 0; i < currentChapters.length; i++) {
            if (currentTime >= currentChapters[i].start) {
                activeIdx = i;
            } else {
                break;
            }
        }
        
        activeChapterIndex = activeIdx;
        const currentCh = currentChapters[activeIdx];
        chStart = currentCh.start;
        chTitle = currentCh.title;
        
        if (activeIdx + 1 < currentChapters.length) {
            chEnd = currentChapters[activeIdx + 1].start;
        }
    }

    // 2. Calculate relative times
    const chapterDuration = chEnd - chStart;
    const timeIntoChapter = currentTime - chStart;
    
    let progressPercent = 0;
    if (chapterDuration > 0) {
        progressPercent = (timeIntoChapter / chapterDuration) * 100;
    }

    // 3. Update the UI securely (with Dual ID fallbacks for the progress bar)
    const progressEl = document.getElementById('seek-progress') || document.getElementById('progress-fill');
    if (progressEl) progressEl.style.width = `${progressPercent}%`;
    
    const chapterTitleEl = document.getElementById('player-chapter-title');
    if (chapterTitleEl) chapterTitleEl.textContent = chTitle;
    
    const timeEl = document.getElementById('player-time');
    if (timeEl) timeEl.textContent = `${formatTime(timeIntoChapter)} / ${formatTime(chapterDuration)}`;
    
    // 4. Handle Sleep Timer safely
    if (typeof sleepMode !== 'undefined' && sleepMode === 'chapter' && sleepTargetTime !== null) {
        if (audio.currentTime >= sleepTargetTime - 1) {
            audio.pause();
            if (playBtn) playBtn.innerText = '▶';
            if (sleepTargetTime < audio.duration) { audio.currentTime = sleepTargetTime; }
            if (typeof setSleepOff === 'function') setSleepOff();
        }
    }
});

audio.addEventListener('ended', () => { 
    document.getElementById('btn-play-pause').innerText = '▶'; 
    const progressEl = document.getElementById('seek-progress');
    if (progressEl) progressEl.style.width = '0%'; 
});

window.seekAudio = function(e) {
    if (!audio.duration) return;

    const container = document.getElementById('seek-bar-container');
    const clickX = e.clientX - container.getBoundingClientRect().left;
    const width = container.clientWidth;
    const clickPercent = Math.max(0, Math.min(1, clickX / width)); // Locks between 0 and 100%

    let chStart = 0;
    let chEnd = audio.duration;
    
    if (typeof currentChapters !== 'undefined' && currentChapters && currentChapters.length > 0) {
        chStart = currentChapters[activeChapterIndex].start;
        if (activeChapterIndex + 1 < currentChapters.length) {
            chEnd = currentChapters[activeChapterIndex + 1].start;
        }
    }

    const chapterDuration = chEnd - chStart;
    audio.currentTime = chStart + (chapterDuration * clickPercent);
};
function saveProgressToServer() {
    if (currentPath && audio.currentTime > 0) {
        if (rawLibraryData[currentPath]) {
            if (!rawLibraryData[currentPath].progress) rawLibraryData[currentPath].progress = {};
            rawLibraryData[currentPath].progress[currentProfile] = audio.currentTime;
        }

        // FIXED: Added token to the progress sync
        fetch(`/api/progress`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: currentPath, position: audio.currentTime, profile: currentProfile }),
            keepalive: true
        }).catch(() => {});
    }
}

audio.addEventListener('pause', saveProgressToServer);
document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') saveProgressToServer();
});

window.addEventListener('beforeunload', saveProgressToServer);
window.addEventListener('pagehide', saveProgressToServer);
// ==========================================
// BOOKMARKS SYSTEM
// ==========================================

window.openBookmarksModal = async function() {
    document.getElementById('bookmarks-modal').style.display = 'flex';
    await window.loadBookmarks();
};

window.closeBookmarksModal = function(e) { 
    if (e && e.target.id !== 'bookmarks-modal') return;
    document.getElementById('bookmarks-modal').style.display = 'none'; 
};

window.loadBookmarks = async function() {
    const listEl = document.getElementById('bookmarks-list');
    if (!currentPath) {
        listEl.innerHTML = '<p style="color: #aaa; text-align: center;">No active book playing.</p>';
        return;
    }

    try {
        // FIX 1: Added cache: 'no-store' so the browser is forced to get the fresh list!
        const res = await fetch(`/api/library/bookmarks?path=${encodeURIComponent(currentPath)}`, {
            cache: 'no-store' 
        });
        
        if (!res.ok) {
            const err = await res.json();
            listEl.innerHTML = `<p style="color: #ff6b6b; text-align: center;">Error: ${err.detail}</p>`;
            return;
        }
        
        const data = await res.json();
        
        if (!data.bookmarks || data.bookmarks.length === 0) {
            listEl.innerHTML = '<p style="color: #aaa; text-align: center;">No bookmarks yet.</p>';
            return;
        }

        listEl.innerHTML = '';
        data.bookmarks.forEach((bm, idx) => {
            const row = document.createElement('div');
            row.className = 'profile-row'; 
            // Quick text escaping to prevent HTML injection errors
            const safeNote = bm.note ? bm.note.replace(/</g, "&lt;").replace(/>/g, "&gt;") : 'Bookmark';
            
            row.innerHTML = `
                <div class="profile-row-info" style="cursor: pointer;" onclick="playBookmark(${bm.time})">
                    <div class="profile-row-name">${safeNote}</div>
                    <div class="profile-row-status">⏱️ ${formatTime(bm.time)}</div>
                </div>
                <button class="action-btn-secondary action-btn-danger" onclick="deleteBookmark(${idx})">🗑️</button>
            `;
            listEl.appendChild(row);
        });
    } catch (e) {
        listEl.innerHTML = '<p style="color: #ff6b6b; text-align: center;">Failed to load bookmarks.</p>';
    }
};

window.addBookmark = async function() {
    // FIX 2: Explicitly check for undefined so bookmarking at 0:00 works
    if (!currentPath || audio.currentTime === undefined) {
        return alert("No book is currently playing.");
    }

    const time = audio.currentTime;
    const note = prompt(`Add a note for this bookmark at ${formatTime(time)}:`, "Awesome moment");
    
    if (note === null) return; 

    try {
        const res = await fetch('/api/library/bookmarks', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: currentPath, time: time, note: note.trim() })
        });

        // FIX 3: Actually show you an error popup if the backend rejects the save
        if (res.ok) {
            await window.loadBookmarks();
        } else {
            const err = await res.json();
            alert(`Failed to save bookmark: ${err.detail}`);
        }
    } catch (e) {
        alert(`Network error saving bookmark: ${e.message}`);
    }
};

window.deleteBookmark = async function(index) {
    if (!confirm("Delete this bookmark?")) return;
    try {
        const res = await fetch('/api/library/bookmarks', {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: currentPath, index: index })
        });

        if (res.ok) await window.loadBookmarks();
    } catch (e) {
        console.error("Failed to delete bookmark", e);
    }
};

window.playBookmark = function(time) {
    audio.currentTime = time;
    if (audio.paused) togglePlay();
    window.closeBookmarksModal();
};
// ==========================================
// MOBILE UI: NAVIGATION & ROUTING
// ==========================================

window.toggleSidebar = function() {
    const sidebar = document.getElementById('mobile-sidebar');
    const overlay = document.getElementById('sidebar-overlay');
    
    if (sidebar.classList.contains('open')) {
        // Close
        sidebar.classList.remove('open');
        overlay.style.display = 'none';
        document.body.style.overflow = ''; // Restore main page scrolling
    } else {
        // Open
        sidebar.classList.add('open');
        overlay.style.display = 'block';
        document.body.style.overflow = 'hidden'; // Lock main page scrolling
    }
};

window.togglePlayerBar = function() {
    const playerBar = document.getElementById('player-bar');
    const mainContainer = document.querySelector('main');
    
    if (playerBar.classList.contains('hidden')) {
        // Show
        playerBar.classList.remove('hidden');
        mainContainer.style.paddingBottom = 'calc(var(--player-height) + 20px)';
    } else {
        // Hide
        playerBar.classList.add('hidden');
        mainContainer.style.paddingBottom = '20px';
    }
};

window.handleRouting = function() {
    const hash = window.location.hash || '#/library';
    const views = [document.getElementById('view-library'), document.getElementById('view-games')];
    const navItems = document.querySelectorAll('.sidebar .nav-item');
    
    // Hide all views and reset nav highlights
    views.forEach(v => { if(v) v.style.display = 'none'; });
    navItems.forEach(n => n.classList.remove('active'));

    if (hash === '#/games') {
        const gv = document.getElementById('view-games');
        if (gv) gv.style.display = 'block';
        const nav = document.querySelector('.nav-item[data-view="games"]');
        if (nav) nav.classList.add('active');
    } else {
        // Default to Library
        const lv = document.getElementById('view-library');
        if (lv) lv.style.display = 'block';
        const nav = document.querySelector('.nav-item[data-view="library"]');
        if (nav) nav.classList.add('active');
        
        // Trigger data load if grid is empty
        if (typeof allBooks !== 'undefined' && allBooks.length === 0 && typeof loadLibrary === 'function') {
            loadLibrary();
        }
    }
};

// Listen for back/forward navigation
window.addEventListener('hashchange', window.handleRouting);

// Trigger on initial page load
window.addEventListener('DOMContentLoaded', () => {
    window.handleRouting();
    
    // Android address bar fix (forces a tiny scroll to hide the UI bar)
    setTimeout(() => { window.scrollTo(0, 1); }, 100);
});

window.toggleSearch = function() {
    const searchContainer = document.getElementById('search-container');
    const isOpen = searchContainer.classList.toggle('open');
    
    // Auto-focus the search box when opened so the keyboard pops up immediately
    if (isOpen) {
        setTimeout(() => {
            document.getElementById('search-box').focus();
        }, 300); // Wait for the slide animation to finish
    } else {
        // Remove focus so the mobile keyboard hides
        document.getElementById('search-box').blur();
    }
};
initializeApp();