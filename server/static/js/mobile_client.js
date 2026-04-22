if ('serviceWorker' in navigator) {
                    window.addEventListener('load', () => {
                        navigator.serviceWorker.register('/static/sw.js').catch(err => {
                            console.error('ServiceWorker registration failed: ', err);
                        });
                    });
                }

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
                        const profRes = await fetch(`/api/profiles`);
                        const profiles = await profRes.json();
                        const profSelect = document.getElementById('profile-selector');
                        profSelect.innerHTML = '';
                        profiles.forEach(p => { profSelect.innerHTML += `<option value="${p}">${p}</option>`; });
                        currentProfile = profiles[0] || "Main";
                    } catch (e) { console.error("Profile fetch failed", e); }
                    
                    await loadLibrary();
                    await cueLastPlayedBook();
                }

                async function loadLibrary() {
                    try {
                        // FIXED: Backtick syntax was broken here
                        const response = await fetch(`/api/library`);
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

                        // FIXED: Added token to the cover image fetch so they load properly
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

                async function cueLastPlayedBook() {
                    try {
                        // FIXED: Added token
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
                            document.getElementById('now-playing-title').innerText = titleStr;
                            document.getElementById('now-playing-author').innerText = authorStr;
                            
                            // FIXED: Added &token= because ?path= already exists
                            audio.src = `/api/stream?path=${encodeURIComponent(data.path)}`;
                            audio.playbackRate = currentSpeed; 
                            
                            audio.onloadedmetadata = () => {
                                audio.currentTime = resumePos;
                            };
                            
                            playerBar.classList.add('active');
                            playBtn.innerText = '▶';
                            setSleepOff(); 

                            try {
                                // FIXED: Added &token=
                                const chapRes = await fetch(`/api/chapters?path=${encodeURIComponent(data.path)}`);
                                currentChapters = await chapRes.json();
                            } catch(e) { currentChapters = []; }

                            if ('mediaSession' in navigator) {
                                // FIXED: Added token to MediaSession artwork
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
                    
                    allBooks.forEach(book => {
                        const matchesSearch = book.searchString.includes(query);
                        const matchesShelf = selectedShelf === 'all' || book.shelves.includes(selectedShelf);
                        book.element.style.display = (matchesSearch && matchesShelf) ? 'flex' : 'none';
                    });
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
                    document.getElementById('now-playing-title').innerText = title;
                    document.getElementById('now-playing-author').innerText = author;
                    
                    // FIXED: Added &token= 
                    audio.src = `/api/stream?path=${encodeURIComponent(filePath)}`;
                    audio.playbackRate = currentSpeed; 
                    
                    audio.onloadedmetadata = () => {
                        audio.currentTime = latestPosition;
                        audio.play().catch(err => console.error("Audio play failed:", err));
                    };
                    
                    playerBar.classList.add('active');
                    playBtn.innerText = '⏸';
                    setSleepOff(); 

                    try {
                        // FIXED: Added &token=
                        const res = await fetch(`/api/chapters?path=${encodeURIComponent(filePath)}`);
                        currentChapters = await res.json();
                    } catch(e) { currentChapters = []; }

                    if ('mediaSession' in navigator) {
                        // FIXED: Added token to MediaSession artwork
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

                initializeApp();