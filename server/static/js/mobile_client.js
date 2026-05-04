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
        if (typeof exitGame === 'function') {
            exitGame(); 
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



window.handleRouting = function() {
    const hash = window.location.hash || '#/library';
    const views = [document.getElementById('view-library'), document.getElementById('view-games')];
    const navItems = document.querySelectorAll('.sidebar .nav-item');
    
    views.forEach(v => { if(v) v.style.display = 'none'; });
    navItems.forEach(n => n.classList.remove('active'));

    if (hash === '#/games') {
        const gv = document.getElementById('view-games');
        if (gv) gv.style.display = 'block';
        const nav = document.querySelector('.nav-item[data-view="games"]');
        if (nav) nav.classList.add('active');
        
        // Start the physics engine
        BrainrotEngine.start();
    } else {
        const lv = document.getElementById('view-library');
        if (lv) lv.style.display = 'block';
        const nav = document.querySelector('.nav-item[data-view="library"]');
        if (nav) nav.classList.add('active');
        
        // Kill the physics engine to save battery
        BrainrotEngine.stop();
        
        if (typeof allBooks !== 'undefined' && allBooks.length === 0 && typeof loadLibrary === 'function') {
            loadLibrary();
        }
    }
};
initializeApp();


// ==========================================
// BRAINROT MODE: GAME MANAGER
// ==========================================

let activeGameEngine = null;

window.launchGame = function(gameId) {
    // 1. Swap UI
    document.getElementById('games-menu').style.display = 'none';
    document.getElementById('active-game-container').style.display = 'block';
    
    const canvas = document.getElementById('game-canvas');
    
    // 2. Route to the correct engine (We will build these next)
    console.log("Launching:", gameId);
    
    if (gameId === 'particles') {
        activeGameEngine = BubblePopEngine;
        activeGameEngine.start();
    } else if (gameId === '2048') {
        activeGameEngine = MergeEngine;
        activeGameEngine.start();
    } else if (gameId === 'breakout') {
        activeGameEngine = BreakoutEngine;
        activeGameEngine.start();
    } else if (gameId === 'invaders') {
        activeGameEngine = SpaceInvadersEngine; // <-- NEW
        activeGameEngine.start();
    }
};

window.exitGame = function() {
    // 1. Stop the active game loop
    if (activeGameEngine && typeof activeGameEngine.stop === 'function') {
        activeGameEngine.stop();
    }
    activeGameEngine = null;
    
    // 2. Clear the canvas visually
    const canvas = document.getElementById('game-canvas');
    if (canvas) {
        const ctx = canvas.getContext('2d');
        ctx.clearRect(0, 0, canvas.width, canvas.height);
    }
    
    // 3. Swap UI back to menu
    document.getElementById('active-game-container').style.display = 'none';
    document.getElementById('games-menu').style.display = 'block';
};

// ==========================================
// BRAINROT MODE: BUBBLE POP
// ==========================================

const BubblePopEngine = {
    canvas: null,
    ctx: null,
    bubbles: [],
    particles: [], // For the "pop" explosion effect
    animationId: null,
    isActive: false,
    
    // Game State
    gameState: 'ready', // ready, playing, gameover
    score: 0,
    totalBubbles: 25,
    timeLeft: 30, // 30 seconds to pop them all
    lastFrameTime: 0,
    
    colors: ['#bb86fc', '#03dac6', '#cf6679', '#f1c40f', '#e74c3c'],

    init: function() {
        this.canvas = document.getElementById('game-canvas');
        if (!this.canvas) return;
        this.ctx = this.canvas.getContext('2d');
        
        // Handle touch/click
        this.pointerHandler = (e) => {
            e.preventDefault();
            this.handleInput(e.clientX, e.clientY);
        };
        this.canvas.addEventListener('pointerdown', this.pointerHandler);
        window.addEventListener('resize', this.resizeHandler);
    },

    resizeHandler: () => {
        if (BubblePopEngine.isActive) BubblePopEngine.resize();
    },

    resize: function() {
        const dpr = window.devicePixelRatio || 1;
        const rect = this.canvas.parentElement.getBoundingClientRect();
        this.canvas.width = rect.width * dpr;
        this.canvas.height = rect.height * dpr;
        this.ctx.scale(dpr, dpr);
    },

    start: function() {
        if (this.isActive) return;
        if (!this.canvas) this.init();
        
        this.isActive = true;
        this.resize();
        this.resetGame();
        
        this.lastFrameTime = performance.now();
        this.update(this.lastFrameTime);
    },

    stop: function() {
        this.isActive = false;
        if (this.animationId) cancelAnimationFrame(this.animationId);
        if (this.canvas) {
            this.canvas.removeEventListener('pointerdown', this.pointerHandler);
            window.removeEventListener('resize', this.resizeHandler);
            this.canvas = null; // <-- ADD THIS LINE
        }
    },

    resetGame: function() {
        this.gameState = 'playing';
        this.score = 0;
        this.timeLeft = 30;
        this.bubbles = [];
        this.particles = [];
        
        const rect = this.canvas.getBoundingClientRect();
        
        // Spawn initial bubbles
        for (let i = 0; i < this.totalBubbles; i++) {
            this.bubbles.push(this.createBubble(rect.width, rect.height));
        }
    },

    createBubble: function(width, height) {
        const radius = Math.random() * 20 + 20; // 20px to 40px
        return {
            x: Math.random() * (width - radius * 2) + radius,
            y: Math.random() * (height - radius * 2) + radius,
            vx: (Math.random() - 0.5) * 2,
            vy: (Math.random() - 0.5) * 2,
            radius: radius,
            baseRadius: radius,
            color: this.colors[Math.floor(Math.random() * this.colors.length)],
            wobbleSpeed: Math.random() * 0.1 + 0.05,
            wobbleTime: Math.random() * Math.PI * 2
        };
    },

    createPopParticles: function(x, y, color) {
        for (let i = 0; i < 8; i++) {
            const angle = Math.random() * Math.PI * 2;
            const speed = Math.random() * 4 + 2;
            this.particles.push({
                x: x,
                y: y,
                vx: Math.cos(angle) * speed,
                vy: Math.sin(angle) * speed,
                radius: Math.random() * 3 + 1,
                color: color,
                life: 1.0
            });
        }
    },

    handleInput: function(clientX, clientY) {
        if (this.gameState === 'gameover') {
            this.resetGame();
            return;
        }

        const rect = this.canvas.getBoundingClientRect();
        const tapX = clientX - rect.left;
        const tapY = clientY - rect.top;

        // Check collisions (reverse loop to hit top bubbles first)
        for (let i = this.bubbles.length - 1; i >= 0; i--) {
            const b = this.bubbles[i];
            const dist = Math.hypot(tapX - b.x, tapY - b.y);
            
            // Forgiving hitbox (add 10px to radius for fat fingers)
            if (dist < b.radius + 10) {
                this.createPopParticles(b.x, b.y, b.color);
                this.bubbles.splice(i, 1);
                this.score++;
                
                if (this.bubbles.length === 0) {
                    this.gameState = 'gameover';
                }
                break; // Only pop one bubble per tap
            }
        }
    },

    update: function(timestamp) {
        if (!this.isActive) return;
        
        const dt = (timestamp - this.lastFrameTime) / 1000;
        this.lastFrameTime = timestamp;

        // Update Timer
        if (this.gameState === 'playing') {
            this.timeLeft -= dt;
            if (this.timeLeft <= 0) {
                this.timeLeft = 0;
                this.gameState = 'gameover';
            }
        }

        const rect = this.canvas.getBoundingClientRect();
        const width = rect.width;
        const height = rect.height;

        // Clear Canvas
        this.ctx.fillStyle = '#121212';
        this.ctx.fillRect(0, 0, width, height);

        // Update & Draw Bubbles
        for (let b of this.bubbles) {
            b.x += b.vx;
            b.y += b.vy;
            b.wobbleTime += b.wobbleSpeed;
            b.radius = b.baseRadius + Math.sin(b.wobbleTime) * 2; // Breathing effect

            // Bounce off walls
            if (b.x - b.radius < 0 || b.x + b.radius > width) b.vx *= -1;
            if (b.y - b.radius < 0 || b.y + b.radius > height) b.vy *= -1;
            
            // Keep inside bounds
            b.x = Math.max(b.radius, Math.min(width - b.radius, b.x));
            b.y = Math.max(b.radius, Math.min(height - b.radius, b.y));

            // Draw Bubble
            this.ctx.beginPath();
            this.ctx.arc(b.x, b.y, b.radius, 0, Math.PI * 2);
            this.ctx.fillStyle = b.color + '88'; // Transparent fill
            this.ctx.fill();
            this.ctx.lineWidth = 2;
            this.ctx.strokeStyle = b.color;
            this.ctx.stroke();
            
            // Draw shine highlight
            this.ctx.beginPath();
            this.ctx.arc(b.x - b.radius * 0.3, b.y - b.radius * 0.3, b.radius * 0.2, 0, Math.PI * 2);
            this.ctx.fillStyle = 'rgba(255,255,255,0.4)';
            this.ctx.fill();
        }

        // Update & Draw Particles
        for (let i = this.particles.length - 1; i >= 0; i--) {
            let p = this.particles[i];
            p.x += p.vx;
            p.y += p.vy;
            p.life -= 0.03; // Fade out

            if (p.life <= 0) {
                this.particles.splice(i, 1);
                continue;
            }

            this.ctx.beginPath();
            this.ctx.arc(p.x, p.y, p.radius, 0, Math.PI * 2);
            this.ctx.fillStyle = p.color;
            this.ctx.globalAlpha = p.life;
            this.ctx.fill();
            this.ctx.globalAlpha = 1.0;
        }

        // Draw UI (Time & Score)
        this.ctx.fillStyle = 'rgba(255, 255, 255, 0.2)';
        this.ctx.font = 'bold 40px sans-serif';
        this.ctx.textAlign = 'center';
        
        // Only show timer if there's actually a challenge
        if (this.gameState === 'playing') {
            this.ctx.fillText(`${Math.ceil(this.timeLeft)}s`, width / 2, height / 2);
        } else if (this.gameState === 'gameover') {
            this.ctx.fillStyle = '#bb86fc';
            if (this.bubbles.length === 0) {
                this.ctx.fillText("CLEARED!", width / 2, height / 2 - 20);
            } else {
                this.ctx.fillText("TIME UP", width / 2, height / 2 - 20);
            }
            this.ctx.fillStyle = '#aaa';
            this.ctx.font = '20px sans-serif';
            this.ctx.fillText(`Score: ${this.score} / ${this.totalBubbles}`, width / 2, height / 2 + 20);
            this.ctx.fillText("Tap to Restart", width / 2, height / 2 + 60);
        }

        this.animationId = requestAnimationFrame((ts) => this.update(ts));
    }
};

// ==========================================
// BRAINROT MODE: MERGE 2048
// ==========================================

const MergeEngine = {
    canvas: null,
    ctx: null,
    animationId: null,
    isActive: false,
    
    grid: [],
    score: 0,
    gameState: 'playing', // playing, gameover, won
    
    // Swipe tracking
    startX: 0,
    startY: 0,
    
    // Theme colors tailored to TomeBox
    colors: {
        empty: 'rgba(255, 255, 255, 0.05)',
        2: '#333333', 4: '#444444', 8: '#cf6679', 
        16: '#e67e22', 32: '#e74c3c', 64: '#f39c12', 
        128: '#f1c40f', 256: '#03dac6', 512: '#1abc9c', 
        1024: '#2ecc71', 2048: '#bb86fc', super: '#8e44ad'
    },

    init: function() {
        this.canvas = document.getElementById('game-canvas');
        if (!this.canvas) return;
        this.ctx = this.canvas.getContext('2d');
        
        // Touch Handlers
        this.downHandler = (e) => {
            this.startX = e.clientX;
            this.startY = e.clientY;
        };
        
        this.upHandler = (e) => {
            if (this.gameState !== 'playing') {
                this.resetGame();
                return;
            }
            
            const dx = e.clientX - this.startX;
            const dy = e.clientY - this.startY;
            
            // Require a minimum swipe distance of 30px to prevent accidental nudges
            if (Math.abs(dx) > 30 || Math.abs(dy) > 30) {
                if (Math.abs(dx) > Math.abs(dy)) {
                    this.move(dx > 0 ? 'right' : 'left');
                } else {
                    this.move(dy > 0 ? 'down' : 'up');
                }
            }
        };

        this.canvas.addEventListener('pointerdown', this.downHandler);
        this.canvas.addEventListener('pointerup', this.upHandler);
        window.addEventListener('resize', this.resizeHandler);
    },

    resizeHandler: () => {
        if (MergeEngine.isActive) MergeEngine.resize();
    },

    resize: function() {
        const dpr = window.devicePixelRatio || 1;
        const rect = this.canvas.parentElement.getBoundingClientRect();
        this.canvas.width = rect.width * dpr;
        this.canvas.height = rect.height * dpr;
        this.ctx.scale(dpr, dpr);
    },

    start: function() {
        if (this.isActive) return;
        if (!this.canvas) this.init();
        
        this.isActive = true;
        this.resize();
        this.resetGame();
        this.update();
    },

    stop: function() {
        this.isActive = false;
        if (this.animationId) cancelAnimationFrame(this.animationId);
        if (this.canvas) {
            this.canvas.removeEventListener('pointerdown', this.pointerHandler);
            window.removeEventListener('resize', this.resizeHandler);
            this.canvas = null; // <-- ADD THIS LINE
        }
    },

    resetGame: function() {
        this.score = 0;
        this.gameState = 'playing';
        this.grid = [
            [0, 0, 0, 0],
            [0, 0, 0, 0],
            [0, 0, 0, 0],
            [0, 0, 0, 0]
        ];
        this.spawnTile();
        this.spawnTile();
    },

    spawnTile: function() {
        let emptySpots = [];
        for (let r = 0; r < 4; r++) {
            for (let c = 0; c < 4; c++) {
                if (this.grid[r][c] === 0) emptySpots.push({r, c});
            }
        }
        if (emptySpots.length > 0) {
            let spot = emptySpots[Math.floor(Math.random() * emptySpots.length)];
            this.grid[spot.r][spot.c] = Math.random() < 0.9 ? 2 : 4;
        }
    },

    move: function(direction) {
        let moved = false;
        let newGrid = JSON.parse(JSON.stringify(this.grid)); // Deep copy

        const slide = (row) => {
            let arr = row.filter(val => val);
            let merged = [];
            while (arr.length > 0) {
                if (arr.length >= 2 && arr[0] === arr[1]) {
                    let newVal = arr[0] * 2;
                    merged.push(newVal);
                    this.score += newVal;
                    if (newVal === 2048) this.gameState = 'won';
                    arr.shift();
                    arr.shift();
                } else {
                    merged.push(arr.shift());
                }
            }
            while (merged.length < 4) merged.push(0);
            return merged;
        };

        if (direction === 'left' || direction === 'right') {
            for (let r = 0; r < 4; r++) {
                let row = newGrid[r];
                if (direction === 'right') row.reverse();
                row = slide(row);
                if (direction === 'right') row.reverse();
                newGrid[r] = row;
            }
        } else if (direction === 'up' || direction === 'down') {
            for (let c = 0; c < 4; c++) {
                let col = [newGrid[0][c], newGrid[1][c], newGrid[2][c], newGrid[3][c]];
                if (direction === 'down') col.reverse();
                col = slide(col);
                if (direction === 'down') col.reverse();
                for (let r = 0; r < 4; r++) newGrid[r][c] = col[r];
            }
        }

        // Check if board changed
        if (JSON.stringify(this.grid) !== JSON.stringify(newGrid)) {
            this.grid = newGrid;
            this.spawnTile();
            this.checkGameOver();
        }
    },

    checkGameOver: function() {
        // Any empty spots?
        for (let r = 0; r < 4; r++) {
            for (let c = 0; c < 4; c++) {
                if (this.grid[r][c] === 0) return;
            }
        }
        // Any valid merges left?
        for (let r = 0; r < 4; r++) {
            for (let c = 0; c < 4; c++) {
                let val = this.grid[r][c];
                if (r < 3 && val === this.grid[r+1][c]) return;
                if (c < 3 && val === this.grid[r][c+1]) return;
            }
        }
        this.gameState = 'gameover';
    },

    update: function() {
        if (!this.isActive) return;

        const rect = this.canvas.getBoundingClientRect();
        const width = rect.width;
        const height = rect.height;

        // Clear Canvas
        this.ctx.fillStyle = '#121212';
        this.ctx.fillRect(0, 0, width, height);

        // Draw Score Header
        this.ctx.fillStyle = '#aaa';
        this.ctx.font = '20px sans-serif';
        this.ctx.textAlign = 'center';
        this.ctx.fillText(`Score: ${this.score}`, width / 2, 60);

        // Calculate Grid Geometry (Centered Square)
        const padding = 15;
        const boardSize = Math.min(width, height - 120) - (padding * 2);
        const tileSize = (boardSize - (padding * 3)) / 4;
        
        const offsetX = (width - boardSize) / 2;
        const offsetY = (height - boardSize) / 2 + 30;

        // Draw Board Background
        this.ctx.fillStyle = '#1e1e1e';
        this.roundRect(this.ctx, offsetX - 10, offsetY - 10, boardSize + 20, boardSize + 20, 10);
        this.ctx.fill();

        // Draw Tiles
        for (let r = 0; r < 4; r++) {
            for (let c = 0; c < 4; c++) {
                let val = this.grid[r][c];
                let tx = offsetX + c * (tileSize + padding);
                let ty = offsetY + r * (tileSize + padding);

                // Draw Tile Background
                this.ctx.fillStyle = val === 0 ? this.colors.empty : (this.colors[val] || this.colors.super);
                this.roundRect(this.ctx, tx, ty, tileSize, tileSize, 8);
                this.ctx.fill();

                // Draw Number
                if (val !== 0) {
                    this.ctx.fillStyle = val <= 4 ? '#ffffff' : '#121212';
                    
                    // Scale font based on number size
                    let fontSize = tileSize * 0.4;
                    if (val > 100) fontSize = tileSize * 0.3;
                    if (val > 1000) fontSize = tileSize * 0.25;
                    
                    this.ctx.font = `bold ${fontSize}px sans-serif`;
                    this.ctx.textBaseline = 'middle';
                    this.ctx.fillText(val, tx + tileSize / 2, ty + tileSize / 2);
                }
            }
        }

        // Draw Overlays
        if (this.gameState === 'gameover' || this.gameState === 'won') {
            this.ctx.fillStyle = 'rgba(0,0,0,0.7)';
            this.roundRect(this.ctx, offsetX - 10, offsetY - 10, boardSize + 20, boardSize + 20, 10);
            this.ctx.fill();
            
            this.ctx.fillStyle = this.gameState === 'won' ? '#03dac6' : '#cf6679';
            this.ctx.font = 'bold 36px sans-serif';
            this.ctx.fillText(this.gameState === 'won' ? 'You Win!' : 'Game Over', width / 2, offsetY + boardSize / 2 - 20);
            
            this.ctx.fillStyle = '#aaa';
            this.ctx.font = '20px sans-serif';
            this.ctx.fillText("Tap to Restart", width / 2, offsetY + boardSize / 2 + 20);
        }

        this.animationId = requestAnimationFrame(() => this.update());
    },

    // Helper function to draw rounded rectangles on HTML5 Canvas
    roundRect: function(ctx, x, y, width, height, radius) {
        ctx.beginPath();
        ctx.moveTo(x + radius, y);
        ctx.lineTo(x + width - radius, y);
        ctx.quadraticCurveTo(x + width, y, x + width, y + radius);
        ctx.lineTo(x + width, y + height - radius);
        ctx.quadraticCurveTo(x + width, y + height, x + width - radius, y + height);
        ctx.lineTo(x + radius, y + height);
        ctx.quadraticCurveTo(x, y + height, x, y + height - radius);
        ctx.lineTo(x, y + radius);
        ctx.quadraticCurveTo(x, y, x + radius, y);
        ctx.closePath();
    }
};

// ==========================================
// BRAINROT MODE: BRICK BREAKER
// ==========================================

const BreakoutEngine = {
    canvas: null,
    ctx: null,
    animationId: null,
    isActive: false,
    lastFrameTime: 0,
    
    // Game State
    gameState: 'ready', // ready, playing, gameover, won
    score: 0,
    lives: 3,
    
    // Entities
    paddle: { x: 0, y: 0, width: 80, height: 12 },
    ball: { x: 0, y: 0, dx: 0, dy: 0, radius: 6, speed: 6 },
    bricks: [],
    
    // Config
    brickRowCount: 5,
    brickColumnCount: 6,
    brickPadding: 8,
    brickOffsetTop: 60,
    
    colors: ['#bb86fc', '#cf6679', '#f39c12', '#03dac6', '#4a90e2'],

    init: function() {
        this.canvas = document.getElementById('game-canvas');
        if (!this.canvas) return;
        this.ctx = this.canvas.getContext('2d');
        
        // Touch/Mouse Tracking
        this.moveHandler = (e) => {
            if (this.gameState === 'playing' || this.gameState === 'ready') {
                const rect = this.canvas.getBoundingClientRect();
                const relativeX = e.clientX - rect.left;
                
                // Keep paddle within bounds
                this.paddle.x = Math.max(0, Math.min(relativeX - this.paddle.width / 2, rect.width - this.paddle.width));
                
                // If waiting to start, move the ball with the paddle
                if (this.gameState === 'ready') {
                    this.ball.x = this.paddle.x + this.paddle.width / 2;
                }
            }
        };

        this.downHandler = (e) => {
            if (this.gameState === 'ready') {
                this.gameState = 'playing';
                // Launch ball up and slightly in a random direction
                this.ball.dx = (Math.random() > 0.5 ? 1 : -1) * (Math.random() * 2 + 2);
                this.ball.dy = -this.ball.speed;
            } else if (this.gameState === 'gameover' || this.gameState === 'won') {
                this.resetGame();
            }
        };

        this.canvas.addEventListener('pointermove', this.moveHandler);
        this.canvas.addEventListener('pointerdown', this.downHandler);
        window.addEventListener('resize', this.resizeHandler);
    },

    resizeHandler: () => {
        if (BreakoutEngine.isActive) BreakoutEngine.resize();
    },

    resize: function() {
        const dpr = window.devicePixelRatio || 1;
        const rect = this.canvas.parentElement.getBoundingClientRect();
        this.canvas.width = rect.width * dpr;
        this.canvas.height = rect.height * dpr;
        this.ctx.scale(dpr, dpr);
        
        // Paddle clearance from bottom
        this.paddle.y = rect.height - 100;
        
        // Rebuild bricks if not playing
        if (this.gameState === 'ready') {
            this.paddle.x = (rect.width - this.paddle.width) / 2;
            this.ball.x = rect.width / 2;
            this.ball.y = this.paddle.y - this.ball.radius;
            this.buildBricks(rect.width);
        }
    },

    start: function() {
        if (this.isActive) return;
        if (!this.canvas) this.init();
        
        this.isActive = true;
        this.lastFrameTime = 0; // Reset timer
        this.resetGame();
        this.resize();
        this.update(performance.now());
    },

    stop: function() {
        this.isActive = false;
        if (this.animationId) cancelAnimationFrame(this.animationId);
        if (this.canvas) {
            this.canvas.removeEventListener('pointerdown', this.pointerHandler);
            window.removeEventListener('resize', this.resizeHandler);
            this.canvas = null; // <-- ADD THIS LINE
        }
    },

    buildBricks: function(canvasWidth) {
        this.bricks = [];
        const totalPadding = this.brickPadding * (this.brickColumnCount + 1);
        const brickWidth = (canvasWidth - totalPadding) / this.brickColumnCount;
        const brickHeight = 20;

        for (let c = 0; c < this.brickColumnCount; c++) {
            this.bricks[c] = [];
            for (let r = 0; r < this.brickRowCount; r++) {
                let brickX = (c * (brickWidth + this.brickPadding)) + this.brickPadding;
                let brickY = (r * (brickHeight + this.brickPadding)) + this.brickOffsetTop;
                this.bricks[c][r] = { 
                    x: brickX, 
                    y: brickY, 
                    w: brickWidth, 
                    h: brickHeight, 
                    status: 1, 
                    color: this.colors[r % this.colors.length] 
                };
            }
        }
    },

    resetGame: function() {
        this.score = 0;
        this.lives = 3;
        this.ball.speed = 6;
        this.resetTurn();
        
        const rect = this.canvas.getBoundingClientRect();
        this.buildBricks(rect.width);
    },

    resetTurn: function() {
        this.gameState = 'ready';
        const rect = this.canvas.getBoundingClientRect();
        this.paddle.y = rect.height - 100;
        this.paddle.x = (rect.width - this.paddle.width) / 2;
        this.ball.x = rect.width / 2;
        this.ball.y = this.paddle.y - this.ball.radius;
        this.ball.dx = 0;
        this.ball.dy = 0;
    },

    collisionDetection: function(timeScale) {
        const rect = this.canvas.getBoundingClientRect();
        
        // Predict next position based on timeScale
        let nextX = this.ball.x + this.ball.dx * timeScale;
        let nextY = this.ball.y + this.ball.dy * timeScale;

        // Brick collisions
        let activeBricks = 0;
        for (let c = 0; c < this.brickColumnCount; c++) {
            for (let r = 0; r < this.brickRowCount; r++) {
                let b = this.bricks[c][r];
                if (b.status === 1) {
                    activeBricks++;
                    if (nextX > b.x && nextX < b.x + b.w && 
                        nextY > b.y && nextY < b.y + b.h) {
                        
                        this.ball.dy = -this.ball.dy;
                        b.status = 0;
                        this.score += 10;
                        
                        if (this.score % 50 === 0) {
                            this.ball.speed += 0.5;
                            const magnitude = Math.hypot(this.ball.dx, this.ball.dy);
                            this.ball.dx = (this.ball.dx / magnitude) * this.ball.speed;
                            this.ball.dy = (this.ball.dy / magnitude) * this.ball.speed;
                        }
                    }
                }
            }
        }
        
        if (activeBricks === 0) this.gameState = 'won';

        // Wall collisions
        if (nextX > rect.width - this.ball.radius || nextX < this.ball.radius) {
            this.ball.dx = -this.ball.dx;
        }
        
        if (nextY < this.ball.radius) {
            this.ball.dy = -this.ball.dy;
        } else if (nextY > rect.height - this.ball.radius) {
            this.lives--;
            if (this.lives === 0) {
                this.gameState = 'gameover';
            } else {
                this.resetTurn();
            }
        }

        // Paddle Collision
        if (this.ball.dy > 0 && 
            nextY + this.ball.radius >= this.paddle.y && 
            nextY - this.ball.radius <= this.paddle.y + this.paddle.height &&
            nextX >= this.paddle.x && 
            nextX <= this.paddle.x + this.paddle.width) {
            
            let hitPoint = (nextX - (this.paddle.x + this.paddle.width / 2)) / (this.paddle.width / 2);
            let bounceAngle = hitPoint * (Math.PI / 3);
            
            this.ball.dx = this.ball.speed * Math.sin(bounceAngle);
            this.ball.dy = -this.ball.speed * Math.cos(bounceAngle);
            
            // Push ball out of paddle to prevent getting stuck
            this.ball.y = this.paddle.y - this.ball.radius;
        }
    },

    update: function(timestamp) {
        if (!this.isActive) return;

        // --- TIME SCALING LOGIC ---
        if (!this.lastFrameTime) this.lastFrameTime = timestamp;
        let dt = (timestamp - this.lastFrameTime) / 1000;
        this.lastFrameTime = timestamp;

        // Cap dt to prevent massive jumps if the browser tab was inactive
        if (dt > 0.1) dt = 0.016; 
        
        // Standardize around 60fps (16.6ms) so the base speed values still work perfectly
        const timeScale = dt / 0.01666;

        const rect = this.canvas.getBoundingClientRect();
        const width = rect.width;
        const height = rect.height;

        // Clear Canvas
        this.ctx.fillStyle = '#121212';
        this.ctx.fillRect(0, 0, width, height);

        // Physics
        if (this.gameState === 'playing') {
            this.collisionDetection(timeScale);
            
            // Make sure the ball wasn't killed during collision check
            if (this.gameState === 'playing') { 
                this.ball.x += this.ball.dx * timeScale;
                this.ball.y += this.ball.dy * timeScale;
            }
        }

        // Draw Bricks
        for (let c = 0; c < this.brickColumnCount; c++) {
            for (let r = 0; r < this.brickRowCount; r++) {
                if (this.bricks[c][r].status === 1) {
                    const b = this.bricks[c][r];
                    this.ctx.fillStyle = b.color;
                    this.ctx.beginPath();
                    this.ctx.roundRect ? this.ctx.roundRect(b.x, b.y, b.w, b.h, 4) : this.ctx.rect(b.x, b.y, b.w, b.h);
                    this.ctx.fill();
                }
            }
        }

        // Draw Paddle
        this.ctx.fillStyle = '#bb86fc';
        this.ctx.beginPath();
        this.ctx.roundRect ? this.ctx.roundRect(this.paddle.x, this.paddle.y, this.paddle.width, this.paddle.height, 6) : this.ctx.rect(this.paddle.x, this.paddle.y, this.paddle.width, this.paddle.height);
        this.ctx.fill();

        // Draw Ball
        this.ctx.beginPath();
        this.ctx.arc(this.ball.x, this.ball.y, this.ball.radius, 0, Math.PI * 2);
        this.ctx.fillStyle = '#ffffff';
        this.ctx.fill();

        // Draw UI
        this.ctx.fillStyle = '#aaa';
        this.ctx.font = '16px sans-serif';
        this.ctx.textAlign = 'left';
        this.ctx.fillText(`Score: ${this.score}`, 15, 35);
        this.ctx.textAlign = 'center';
        this.ctx.fillText(`Lives: ${this.lives}`, width / 2, 35);

        // Draw State Overlays
        this.ctx.textAlign = 'center';
        if (this.gameState === 'ready') {
            this.ctx.fillStyle = '#ffffff';
            this.ctx.font = 'bold 24px sans-serif';
            this.ctx.fillText('Tap to Launch', width / 2, height / 2);
        } else if (this.gameState === 'gameover' || this.gameState === 'won') {
            this.ctx.fillStyle = 'rgba(0,0,0,0.7)';
            this.ctx.fillRect(0, 0, width, height);
            
            this.ctx.fillStyle = this.gameState === 'won' ? '#03dac6' : '#cf6679';
            this.ctx.font = 'bold 36px sans-serif';
            this.ctx.fillText(this.gameState === 'won' ? 'CLEARED!' : 'GAME OVER', width / 2, height / 2 - 20);
            
            this.ctx.fillStyle = '#aaa';
            this.ctx.font = '20px sans-serif';
            this.ctx.fillText(`Final Score: ${this.score}`, width / 2, height / 2 + 20);
            this.ctx.fillText("Tap to Restart", width / 2, height / 2 + 60);
        }

        this.animationId = requestAnimationFrame((ts) => this.update(ts));
    }
};
// ==========================================
// BRAINROT MODE: SPACE INVADERS
// ==========================================

const SpaceInvadersEngine = {
    canvas: null,
    ctx: null,
    animationId: null,
    isActive: false,
    lastFrameTime: 0,
    
    // Game State
    gameState: 'ready', // ready, playing, gameover, victory
    score: 0,
    lives: 3,
    level: 1,
    timeAlive: 0, 
    
    // Entities
    player: { x: 0, y: 0, width: 30, height: 20, speed: 0, lastFire: 0, fireRate: 300 },
    bullets: [],
    aliens: [],
    barricades: [],
    particles: [],
    stars: [],
    
    // Fleet Config
    fleet: { dx: 2, dy: 15, direction: 1, speedMultiplier: 1 },
    
    // Theme Colors
    colors: {
        player: '#03dac6',
        shield: '#03dac6',
        alienTop: '#cf6679',
        alienMid: '#f39c12',
        alienBot: '#bb86fc',
        bulletPlayer: '#ffffff',
        bulletAlien: '#ff6b6b'
    },

    // Matrix: [HP, Speed Mult, Fire Prob]
    getLevelStats: function() {
        const stats = [
            { hp: 1, spd: 1.0, fire: 0.02 }, // Level 1 (Base)
            { hp: 2, spd: 1.0, fire: 0.02 }, // Level 2 (+Armor)
            { hp: 2, spd: 1.3, fire: 0.02 }, // Level 3 (+Speed)
            { hp: 2, spd: 1.3, fire: 0.04 }, // Level 4 (+Fire Rate)
            { hp: 3, spd: 1.3, fire: 0.04 }, // Level 5 (+Armor)
            { hp: 3, spd: 1.6, fire: 0.04 }, // Level 6 (+Speed)
            { hp: 3, spd: 1.6, fire: 0.06 }, // Level 7 (+Fire Rate)
            { hp: 4, spd: 1.6, fire: 0.06 }, // Level 8 (+Armor)
            { hp: 4, spd: 1.9, fire: 0.06 }, // Level 9 (+Speed)
            { hp: 4, spd: 1.9, fire: 0.08 }  // Level 10 (Boss Wave)
        ];
        return stats[Math.min(this.level - 1, 9)];
    },

    init: function() {
        this.canvas = document.getElementById('game-canvas');
        if (!this.canvas) return;
        this.ctx = this.canvas.getContext('2d');
        
        this.touchX = null;
        
        this.downHandler = (e) => {
            e.preventDefault();
            if (this.gameState === 'ready') {
                this.gameState = 'playing';
            } else if (this.gameState === 'gameover' || this.gameState === 'victory') {
                this.resetGame();
            }
            this.touchX = e.clientX;
        };

        this.moveHandler = (e) => {
            e.preventDefault();
            if ((this.gameState === 'playing' || this.gameState === 'ready') && this.touchX !== null) {
                const dx = e.clientX - this.touchX;
                const rect = this.canvas.getBoundingClientRect();
                
                this.player.x += dx;
                this.player.x = Math.max(0, Math.min(this.player.x, rect.width - this.player.width));
                this.touchX = e.clientX;
            }
        };

        this.upHandler = (e) => {
            e.preventDefault();
            this.touchX = null;
        };

        this.canvas.addEventListener('pointerdown', this.downHandler);
        this.canvas.addEventListener('pointermove', this.moveHandler);
        this.canvas.addEventListener('pointerup', this.upHandler);
        this.canvas.addEventListener('pointercancel', this.upHandler);
        window.addEventListener('resize', this.resizeHandler);
    },

    resizeHandler: () => {
        if (SpaceInvadersEngine.isActive) SpaceInvadersEngine.resize();
    },

    resize: function() {
        const dpr = window.devicePixelRatio || 1;
        const rect = this.canvas.parentElement.getBoundingClientRect();
        this.canvas.width = rect.width * dpr;
        this.canvas.height = rect.height * dpr;
        this.ctx.scale(dpr, dpr);
        
        this.player.y = rect.height - 70;
        
        if (this.gameState === 'ready') {
            this.player.x = (rect.width - this.player.width) / 2;
            this.buildFleet(rect.width);
            this.buildBarricades(rect.width);
            this.generateStars(rect.width, rect.height);
        }
    },

    start: function() {
        if (this.isActive) return;
        if (!this.canvas) this.init();
        
        this.isActive = true;
        this.lastFrameTime = 0;
        this.timeAlive = 0;
        this.resetGame();
        this.resize();
        this.update(performance.now());
    },

    stop: function() {
        this.isActive = false;
        if (this.animationId) cancelAnimationFrame(this.animationId);
        if (this.canvas) {
            this.canvas.removeEventListener('pointerdown', this.downHandler);
            this.canvas.removeEventListener('pointermove', this.moveHandler);
            this.canvas.removeEventListener('pointerup', this.upHandler);
            this.canvas.removeEventListener('pointercancel', this.upHandler);
            window.removeEventListener('resize', this.resizeHandler);
            this.canvas = null;
        }
    },

    generateStars: function(width, height) {
        this.stars = [];
        for (let i = 0; i < 50; i++) {
            this.stars.push({
                x: Math.random() * width,
                y: Math.random() * height,
                speed: Math.random() * 2 + 0.5,
                size: Math.random() * 2
            });
        }
    },

    buildFleet: function(canvasWidth) {
        this.aliens = [];
        this.bullets = [];
        
        const cols = 6;
        const rows = 4;
        const padding = 15;
        const w = 25;
        const h = 20;
        
        const fleetWidth = (cols * w) + ((cols - 1) * padding);
        const offsetX = (canvasWidth - fleetWidth) / 2;
        const offsetY = 60;
        
        const stats = this.getLevelStats();

        for (let r = 0; r < rows; r++) {
            let color = this.colors.alienBot;
            let pts = 10;
            if (r === 0) { color = this.colors.alienTop; pts = 30; }
            else if (r === 1) { color = this.colors.alienMid; pts = 20; }

            for (let c = 0; c < cols; c++) {
                this.aliens.push({
                    x: offsetX + c * (w + padding),
                    y: offsetY + r * (h + padding),
                    width: w,
                    height: h,
                    color: color,
                    points: pts,
                    hp: stats.hp
                });
            }
        }
        
        this.fleet.speedMultiplier = stats.spd;
        this.fleet.direction = 1; 
    },

    buildBarricades: function(canvasWidth) {
        this.barricades = [];
        const numShields = 3;
        const shieldWidth = 50;
        const shieldHeight = 35;
        const blockSize = 5; 
        
        const spacing = canvasWidth / (numShields + 1);
        const shieldY = this.player.y - 70;

        for (let i = 0; i < numShields; i++) {
            let startX = spacing * (i + 1) - (shieldWidth / 2);
            
            for (let bx = 0; bx < shieldWidth; bx += blockSize) {
                for (let by = 0; by < shieldHeight; by += blockSize) {
                    if (by < blockSize && (bx < blockSize || bx >= shieldWidth - blockSize)) continue;
                    if (by > shieldHeight - blockSize * 3 && bx > blockSize * 2 && bx < shieldWidth - blockSize * 2) continue;
                    
                    this.barricades.push({
                        x: startX + bx,
                        y: shieldY + by,
                        w: blockSize,
                        h: blockSize,
                        color: this.colors.shield
                    });
                }
            }
        }
    },

    resetGame: function() {
        this.score = 0;
        this.lives = 3;
        this.level = 1;
        this.particles = [];
        this.gameState = 'ready';
        
        const rect = this.canvas.getBoundingClientRect();
        this.player.y = rect.height - 70;
        this.player.x = (rect.width - this.player.width) / 2;
        
        this.buildFleet(rect.width);
        this.buildBarricades(rect.width);
    },

    createExplosion: function(x, y, color, count = 10) {
        for (let i = 0; i < count; i++) {
            const angle = Math.random() * Math.PI * 2;
            const speed = Math.random() * 3 + 1;
            this.particles.push({
                x: x,
                y: y,
                vx: Math.cos(angle) * speed,
                vy: Math.sin(angle) * speed,
                radius: Math.random() * 2 + 1,
                color: color,
                life: 1.0
            });
        }
    },

    update: function(timestamp) {
        if (!this.isActive) return;

        try {
            // Absolute safety checks on timestamp to prevent Canvas crashes
            const validTimestamp = timestamp || performance.now();
            if (!this.lastFrameTime) this.lastFrameTime = validTimestamp;
            
            let dt = (validTimestamp - this.lastFrameTime) / 1000;
            this.lastFrameTime = validTimestamp;
            
            // Protect against NaN or extreme lag spikes
            if (isNaN(dt) || dt < 0) dt = 0.016;
            if (dt > 0.1) dt = 0.016; 
            
            const timeScale = dt / 0.01666;
            this.timeAlive += dt;

            const rect = this.canvas.getBoundingClientRect();
            const width = rect.width || this.canvas.width;
            const height = rect.height || this.canvas.height;

            // Clear Canvas
            this.ctx.fillStyle = '#121212';
            this.ctx.fillRect(0, 0, width, height);

            // Parallax Stars
            this.ctx.fillStyle = 'rgba(255, 255, 255, 0.5)';
            for (let s of this.stars) {
                if (this.gameState === 'playing') {
                    s.y += s.speed * timeScale;
                    if (s.y > height) {
                        s.y = 0;
                        s.x = Math.random() * width;
                    }
                }
                this.ctx.fillRect(s.x, s.y, s.size, s.size);
            }

            const currentStats = this.getLevelStats();

            if (this.gameState === 'playing') {
                // Player Auto-Fire
                if (validTimestamp - this.player.lastFire > this.player.fireRate) {
                    this.bullets.push({
                        x: this.player.x + this.player.width / 2 - 2,
                        y: this.player.y - 10,
                        width: 4, height: 12,
                        dy: -8,
                        isPlayer: true,
                        color: this.colors.bulletPlayer
                    });
                    this.player.lastFire = validTimestamp;
                }

                // Alien Random Fire
                if (this.aliens.length > 0 && Math.random() < currentStats.fire * timeScale) {
                    const randomAlien = this.aliens[Math.floor(Math.random() * this.aliens.length)];
                    this.bullets.push({
                        x: randomAlien.x + randomAlien.width / 2 - 2,
                        y: randomAlien.y + randomAlien.height,
                        width: 4, height: 12,
                        dy: 4 + (this.level * 0.5),
                        isPlayer: false,
                        color: this.colors.bulletAlien
                    });
                }

                // Fleet Movement Logic
                let hitEdge = false;
                let minX = width, maxX = 0, maxY = 0;
                
                for (let a of this.aliens) {
                    if (a.x < minX) minX = a.x;
                    if (a.x + a.width > maxX) maxX = a.x + a.width;
                    if (a.y + a.height > maxY) maxY = a.y + a.height;
                }

                if (maxX >= width - 15 && this.fleet.direction === 1) hitEdge = true;
                if (minX <= 15 && this.fleet.direction === -1) hitEdge = true;

                if (hitEdge) {
                    this.fleet.direction *= -1;
                    for (let a of this.aliens) {
                        a.y += this.fleet.dy;
                        a.x += this.fleet.dx * this.fleet.direction * this.fleet.speedMultiplier * timeScale;
                    }
                } else {
                    for (let a of this.aliens) {
                        a.x += this.fleet.dx * this.fleet.direction * this.fleet.speedMultiplier * timeScale;
                    }
                }

                // Aliens destroying barricades on descent
                for (let a of this.aliens) {
                    for (let j = this.barricades.length - 1; j >= 0; j--) {
                        let bar = this.barricades[j];
                        if (a.x < bar.x + bar.w && a.x + a.width > bar.x &&
                            a.y < bar.y + bar.h && a.y + a.height > bar.y) {
                            this.barricades.splice(j, 1);
                        }
                    }
                }

                // Game Over if aliens reach player
                if (maxY >= this.player.y) {
                    this.gameState = 'gameover';
                    this.createExplosion(this.player.x + 15, this.player.y + 10, this.colors.player, 20);
                }

                // Bullet Logic & Collisions
                for (let i = this.bullets.length - 1; i >= 0; i--) {
                    let b = this.bullets[i];
                    b.y += b.dy * timeScale;
                    let hitSomething = false;

                    if (b.y < 0 || b.y > height) {
                        this.bullets.splice(i, 1);
                        continue;
                    }

                    // Check Barricade Collisions
                    for (let j = this.barricades.length - 1; j >= 0; j--) {
                        let bar = this.barricades[j];
                        if (b.x < bar.x + bar.w && b.x + b.width > bar.x &&
                            b.y < bar.y + bar.h && b.y + b.height > bar.y) {
                            
                            this.createExplosion(bar.x + 2, bar.y + 2, bar.color, 3);
                            this.barricades.splice(j, 1);
                            this.bullets.splice(i, 1);
                            hitSomething = true;
                            break;
                        }
                    }
                    if (hitSomething) continue;

                    // Player bullets hitting aliens
                    if (b.isPlayer) {
                        for (let j = this.aliens.length - 1; j >= 0; j--) {
                            let a = this.aliens[j];
                            if (b.x < a.x + a.width && b.x + b.width > a.x &&
                                b.y < a.y + a.height && b.y + b.height > a.y) {
                                
                                a.hp -= 1;
                                if (a.hp <= 0) {
                                    this.createExplosion(a.x + a.width/2, a.y + a.height/2, a.color);
                                    this.score += a.points;
                                    this.aliens.splice(j, 1);
                                } else {
                                    this.createExplosion(b.x + b.width/2, b.y, '#ffffff', 4);
                                }
                                
                                this.bullets.splice(i, 1);
                                hitSomething = true;
                                break;
                            }
                        }
                        if (hitSomething) {
                            if (this.aliens.length === 0) {
                                this.level++;
                                if (this.level > 10) {
                                    this.gameState = 'victory';
                                } else {
                                    this.buildFleet(width);
                                    this.buildBarricades(width); 
                                }
                            }
                            continue;
                        }
                    } 
                    // Alien bullets hitting player
                    else {
                        if (b.x < this.player.x + this.player.width && b.x + b.width > this.player.x &&
                            b.y < this.player.y + this.player.height && b.y + b.height > this.player.y) {
                            
                            this.createExplosion(this.player.x + 15, this.player.y + 10, this.colors.player, 15);
                            this.bullets.splice(i, 1);
                            this.lives--;
                            
                            if (this.lives <= 0) {
                                this.gameState = 'gameover';
                            }
                        }
                    }
                }
            }

            // Draw Barricades
            for (let bar of this.barricades) {
                this.ctx.fillStyle = bar.color;
                this.ctx.fillRect(bar.x, bar.y, bar.w, bar.h);
            }

            // Draw Bullets
            for (let b of this.bullets) {
                this.ctx.fillStyle = b.color;
                this.ctx.fillRect(b.x, b.y, b.width, b.height);
            }

            // Draw Aliens
            for (let a of this.aliens) {
                this.ctx.fillStyle = a.color;
                this.ctx.fillRect(a.x, a.y, a.width, a.height);
                this.ctx.fillStyle = '#121212';
                this.ctx.fillRect(a.x + 5, a.y + 5, 4, 4);
                this.ctx.fillRect(a.x + a.width - 9, a.y + 5, 4, 4);
                
                if (a.hp > 1) {
                    this.ctx.fillStyle = 'rgba(255, 255, 255, 0.7)';
                    for(let h = 1; h < a.hp; h++) {
                        this.ctx.fillRect(a.x + 2, a.y - (3 * h), a.width - 4, 2);
                    }
                }
            }

            // Draw Player
            if (this.gameState !== 'gameover' || this.lives > 0) {
                this.ctx.fillStyle = this.colors.player;
                this.ctx.fillRect(this.player.x, this.player.y + 10, this.player.width, this.player.height - 10);
                this.ctx.fillRect(this.player.x + 12, this.player.y, 6, 10);
            }

            // Update & Draw Particles (Clamping globalAlpha prevents Canvas API crashes)
            for (let i = this.particles.length - 1; i >= 0; i--) {
                let p = this.particles[i];
                p.x += p.vx * timeScale;
                p.y += p.vy * timeScale;
                p.life -= 0.05 * timeScale;

                if (p.life <= 0) {
                    this.particles.splice(i, 1);
                    continue;
                }

                this.ctx.beginPath();
                this.ctx.arc(p.x, p.y, p.radius, 0, Math.PI * 2);
                this.ctx.fillStyle = p.color;
                this.ctx.globalAlpha = Math.max(0, Math.min(1, p.life || 0));
                this.ctx.fill();
                this.ctx.globalAlpha = 1.0;
            }

            // Draw UI
            this.ctx.fillStyle = '#aaa';
            this.ctx.font = '16px sans-serif';
            this.ctx.textAlign = 'left';
            this.ctx.fillText(`Score: ${this.score}`, 15, 35);
            this.ctx.textAlign = 'right';
            this.ctx.fillText(`Lives: ${this.lives}`, width - 15, 35);
            this.ctx.textAlign = 'center';
            this.ctx.fillText(`Level ${this.level}/10`, width / 2, 35);

            // Draw State Overlays (Clamping globalAlpha for safe text pulsing)
            if (this.gameState === 'ready') {
                const pulse = 0.65 + Math.sin(this.timeAlive * 4) * 0.35;
                
                this.ctx.globalAlpha = Math.max(0, Math.min(1, pulse || 1));
                this.ctx.fillStyle = '#ffffff';
                this.ctx.font = 'bold 24px sans-serif';
                this.ctx.fillText('Tap to Start', width / 2, height / 2);
                
                this.ctx.font = '16px sans-serif';
                this.ctx.fillStyle = '#aaaaaa';
                this.ctx.fillText('Drag to move. Ship auto-fires.', width / 2, height / 2 + 30);
                this.ctx.globalAlpha = 1.0;
                
            } else if (this.gameState === 'gameover') {
                this.ctx.fillStyle = 'rgba(0,0,0,0.7)';
                this.ctx.fillRect(0, 0, width, height);
                
                this.ctx.fillStyle = '#cf6679';
                this.ctx.font = 'bold 36px sans-serif';
                this.ctx.fillText('GAME OVER', width / 2, height / 2 - 20);
                
                this.ctx.fillStyle = '#aaa';
                this.ctx.font = '20px sans-serif';
                this.ctx.fillText(`Final Score: ${this.score}`, width / 2, height / 2 + 20);
                this.ctx.fillText("Tap to Restart", width / 2, height / 2 + 60);
                
            } else if (this.gameState === 'victory') {
                this.ctx.fillStyle = 'rgba(0,0,0,0.85)';
                this.ctx.fillRect(0, 0, width, height);
                
                this.ctx.fillStyle = '#bb86fc';
                this.ctx.font = 'bold 36px sans-serif';
                this.ctx.fillText('YOU WIN!', width / 2, height / 2 - 20);
                
                this.ctx.fillStyle = '#aaa';
                this.ctx.font = '20px sans-serif';
                this.ctx.fillText(`Earth is Safe. Score: ${this.score}`, width / 2, height / 2 + 20);
                this.ctx.fillText("Tap to Replay", width / 2, height / 2 + 60);
            }

            this.animationId = requestAnimationFrame((ts) => this.update(ts));
            
        } catch (error) {
            console.error("Space Invaders Engine Error:", error);
            // Attempt to keep the loop alive even if a frame fails
            this.animationId = requestAnimationFrame((ts) => this.update(ts));
        }
    }
};