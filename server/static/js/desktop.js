// ========================================
// TomeBox Desktop Client
// ========================================

class TomeBoxDesktop {
    constructor() {
        this.state = {
            activeTaskAsins: new Set(),
            pendingDownloadAsins: [],
            pendingLoginProfile: null,
            pendingLoginLocale: null,
            currentContextItem: null,
            queuePollingInterval: null,
            devicesViewLoaded: false,
            sidebarStateKey: 'tomebox_sidebar_collapsed'
        };

        this.dom = {
            shell: document.getElementById('app-shell'),
            sidebarToggle: document.getElementById('sidebar-toggle'),
            qrContainer: document.getElementById('qr-container'),
            pairingUrl: document.getElementById('pairing-url'),
            profileSelector: document.getElementById('profile-selector'),
            libraryStatus: document.getElementById('library-status'),
            libraryGrid: document.getElementById('library-grid')
        };

        this.routes = {
            '#/library': 'library',
            '#/devices': 'devices',
            '#/pairing': 'devices',
            '#/account': 'account'
        };

        this.init();
    }

    async init() {
        this.bindStaticUI();
        this.initSidebar();
        this.initRouting();
        this.bindGlobalEvents();
        
        await this.loadActiveProfile();
        this.checkFirstRun();
        this.startQueuePolling();

        document.addEventListener('tomebox:libraryLoaded', () => this.updateLibraryCountDisplay());
    }

    // ==========================================
    // UI BINDING ENGINE
    // ==========================================
    bindStaticUI() {
        const bind = (id, event, handler) => {
            const el = document.getElementById(id);
            if (el) el.addEventListener(event, handler.bind(this));
        };

        // Header & Search
        bind('search-box', 'keyup', () => { if (window.TomeBoxApp) window.TomeBoxApp.filterLibrary(); });
        bind('shelf-filter', 'change', () => { if (window.TomeBoxApp) window.TomeBoxApp.filterLibrary(); });
        bind('sort-filter', 'change', () => { if (window.TomeBoxApp) window.TomeBoxApp.filterLibrary(); });
        bind('btn-action-menu-toggle', 'click', this.toggleActionMenu);
        bind('refresh-library-btn', 'click', this.refreshLibrary);
        bind('btn-download-all', 'click', this.downloadAllMissing);
        bind('btn-add-file', 'click', this.addLocalFile);
        bind('btn-import-folder', 'click', this.importFolder);
        bind('btn-cancel-import', 'click', this.cancelImport);
        bind('profile-selector', 'change', this.handleProfileSelect);

        // Avatar Routing
        document.querySelectorAll('.profile-icon, .profile-label').forEach(el => {
            el.addEventListener('click', () => {
                window.location.hash = '#/account';
                this.loadActiveProfile();
            });
        });

        // Account Tab
        bind('btn-new-profile', 'click', this.openCreateProfileModal);

        // Event Delegation for dynamic Profile List
        const profList = document.getElementById('profiles-list');
        if (profList) {
            profList.addEventListener('click', (e) => {
                const btn = e.target.closest('button');
                if (!btn) return;
                const action = btn.dataset.action;
                const name = btn.dataset.profile;
                if (action === 'signin') this.openLoginModal(name);
                if (action === 'switch') this.switchProfile(name);
                if (action === 'delete') this.deleteProfile(name);
            });
        }

        // Modals (Background & Cancel clicks)
        document.querySelectorAll('.modal-overlay').forEach(modal => {
            modal.addEventListener('click', (e) => { if (e.target === modal) modal.style.display = 'none'; });
            modal.querySelector('.close-btn')?.addEventListener('click', () => modal.style.display = 'none');
            modal.querySelectorAll('.action-btn-secondary:not(.close-btn)').forEach(btn => {
                btn.addEventListener('click', () => modal.style.display = 'none');
            });
        });

        // Specific Modal Buttons
        bind('btn-create-profile-submit', 'click', this.submitCreateProfile);
        bind('btn-login-continue', 'click', this.startLoginFlow);
        bind('btn-login-complete', 'click', this.completeLoginFlow);
        bind('btn-execute-metadata', 'click', this.executeMetadataSearch);
        bind('btn-browse-dir', 'click', this.browseForDirectory);
        bind('btn-save-dir', 'click', this.submitDownloadDir);

        const metaInput = document.getElementById('metadata-search-input');
        if (metaInput) metaInput.addEventListener('keypress', (e) => { if (e.key === 'Enter') this.executeMetadataSearch(); });

        // Event Delegation for Metadata Search Results
        const metaResults = document.getElementById('metadata-search-results');
        if (metaResults) {
            metaResults.addEventListener('click', (e) => {
                const btn = e.target.closest('button');
                if (btn && btn.dataset.asin) this.applyMetadata(btn.dataset.asin);
            });
        }

        // Context Menu
        ['download', 'match', 'shelf', 'scrape', 'convert', 'cancel', 'remove'].forEach(action => {
            bind(`ctx-${action}`, 'click', () => this.ctxAction(action));
        });
    }

    // ==========================================
    // ROUTING & SIDEBAR
    // ==========================================
    initSidebar() {
        if (!this.dom.sidebarToggle || !this.dom.shell) return;
        if (localStorage.getItem(this.state.sidebarStateKey) === 'true') this.dom.shell.classList.add('sidebar-collapsed');
        
        this.dom.sidebarToggle.addEventListener('click', () => {
            this.dom.shell.classList.toggle('sidebar-collapsed');
            localStorage.setItem(this.state.sidebarStateKey, this.dom.shell.classList.contains('sidebar-collapsed'));
        });
    }

    initRouting() {
        window.addEventListener('hashchange', () => this.handleRoute());
        this.handleRoute();
    }

    handleRoute() {
        const hash = window.location.hash || '#/library';
        const viewName = this.routes[hash] || 'library';
        this.showView(viewName);
    }

    showView(viewName) {
        document.querySelectorAll('.nav-item').forEach(item => {
            if (item.dataset.view === viewName || (viewName === 'pairing' && item.dataset.view === 'devices')) {
                item.classList.add('active');
            } else { item.classList.remove('active'); }
        });
        
        document.querySelectorAll('.view').forEach(view => view.classList.remove('active'));
        let target = document.getElementById(`view-${viewName}`);
        if (!target && viewName === 'pairing') target = document.getElementById('view-devices');
        if (target) target.classList.add('active');
        
        if (viewName === 'pairing' || viewName === 'devices') this.loadDevicesView();
        else if (viewName === 'account') this.loadProfilesView();
    }

    async checkFirstRun() {
        try {
            const response = await fetch('/api/library', { cache: 'no-store' });
            if (!response.ok) return;
            const data = await response.json();
            if (Object.keys(data).length === 0) {
                window.location.hash = '#/account';
                setTimeout(() => {
                    const list = document.getElementById('profiles-list');
                    if (list && !document.getElementById('first-run-banner')) {
                        const banner = document.createElement('div');
                        banner.id = 'first-run-banner';
                        banner.style.cssText = 'background: rgba(187,134,252,0.1); border-left: 3px solid var(--accent); padding: 15px; margin-bottom: 15px; border-radius: 6px;';
                        banner.innerHTML = '<strong>Welcome to TomeBox!</strong> Your library is empty. Sign in to your Audible account or import local files to get started.';
                        list.parentElement.insertBefore(banner, list.parentElement.firstChild);
                    }
                }, 200);
            }
        } catch (error) {}
    }

    // ==========================================
    // PAIRING VIEW
    // ==========================================
    async loadDevicesView() {
        if (this.state.devicesViewLoaded || !this.dom.qrContainer || !this.dom.pairingUrl) return;
        try {
            const response = await fetch('/api/pairing-info');
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const data = await response.json();
            const pairingUrl = data.pairing_url;
            this.dom.pairingUrl.textContent = pairingUrl;
            
            await this.loadQRLibrary();
            this.dom.qrContainer.innerHTML = '';
            new QRCode(this.dom.qrContainer, {
                text: pairingUrl, width: 240, height: 240, colorDark: '#000000', colorLight: '#ffffff', correctLevel: QRCode.CorrectLevel.H
            });
            
            this.dom.pairingUrl.addEventListener('click', () => {
                navigator.clipboard.writeText(pairingUrl).then(() => {
                    const original = this.dom.pairingUrl.textContent;
                    this.dom.pairingUrl.textContent = 'Copied to clipboard!';
                    setTimeout(() => this.dom.pairingUrl.textContent = original, 1500);
                });
            });
            this.state.devicesViewLoaded = true;
        } catch (error) { this.dom.qrContainer.innerHTML = `<p style="color: #ff6b6b;">Failed to load pairing info: ${error.message}</p>`; }
    }

    loadQRLibrary() {
        return new Promise((resolve, reject) => {
            if (window.QRCode) return resolve();
            const script = document.createElement('script');
            script.src = 'https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js';
            script.onload = resolve;
            script.onerror = () => reject(new Error('Failed to load QR library'));
            document.head.appendChild(script);
        });
    }

    // ==========================================
    // LIBRARY CORE
    // ==========================================
    async refreshLibrary() {
        const btn = document.getElementById('refresh-library-btn');
        if (!btn || btn.disabled) return;
        btn.disabled = true;
        btn.classList.add('refreshing');
        this.setStatus('Refreshing library from Audible...');
        
        try {
            const response = await fetch('/api/library/refresh', { method: 'POST' });
            if (!response.ok) throw new Error((await response.json()).detail || `HTTP ${response.status}`);
            const data = await response.json();
            this.setStatus(`Library refreshed — ${data.items_count} items in cloud`, 'success', 4000);
            if (window.TomeBoxApp) await window.TomeBoxApp.loadLibrary();
        } catch (error) { this.setStatus(`Refresh failed: ${error.message}`, 'error'); } 
        finally { btn.disabled = false; btn.classList.remove('refreshing'); }
    }

    updateLibraryCountDisplay() {
        const displayEl = document.getElementById('library-count-display');
        const libData = window.TomeBoxApp ? window.TomeBoxApp.state.rawLibraryData : null;
        if (!displayEl || !libData) return;

        const libraryItems = Object.values(libData);
        const totalBooks = libraryItems.length;
        const formats = {};
        
        libraryItems.forEach(item => {
            let fmt = "UNKNOWN";
            if (item.is_playlist) fmt = "PLAYLIST";
            else if (item.format) fmt = item.format.toUpperCase();
            else if (item.download_status === 'cloud_only') fmt = "CLOUD ONLY";
            formats[fmt] = (formats[fmt] || 0) + 1;
        });

        displayEl.textContent = `Books found: ${totalBooks}`;
        if (totalBooks > 0) {
            const tooltipLines = Object.entries(formats)
                .sort((a, b) => b[1] - a[1])
                .map(([fmt, count]) => `• ${fmt}: ${count}`);
            displayEl.title = "Format Breakdown:\n" + tooltipLines.join('\n');
        } else { displayEl.title = "Library is empty."; }
    }

    setStatus(message, type = '', clearAfterMs = null) {
        if (!this.dom.libraryStatus) return;
        this.dom.libraryStatus.textContent = message;
        this.dom.libraryStatus.className = `library-status ${type}`;
        if (clearAfterMs) {
            setTimeout(() => {
                if (this.dom.libraryStatus.textContent === message) {
                    this.dom.libraryStatus.textContent = '';
                    this.dom.libraryStatus.className = 'library-status';
                }
            }, clearAfterMs);
        }
    }

    // ==========================================
    // ACCOUNT & PROFILES
    // ==========================================
    async loadActiveProfile() {
        try {
            const response = await fetch('/api/profiles/active');
            if (!response.ok) return;
            const data = await response.json();
            if (!this.dom.profileSelector) return;
            
            this.dom.profileSelector.innerHTML = '';
            for (const profileName of data.available) {
                const option = document.createElement('option');
                option.value = profileName;
                option.textContent = profileName;
                if (profileName === data.active) option.selected = true;
                this.dom.profileSelector.appendChild(option);
            }

            const divider = document.createElement('option');
            divider.disabled = true;
            divider.textContent = "──────────";
            this.dom.profileSelector.appendChild(divider);
            
            const manageOption = document.createElement('option');
            manageOption.value = "_manage_";
            manageOption.textContent = "⚙️ Manage Profiles & Auth...";
            this.dom.profileSelector.appendChild(manageOption);
        } catch (error) {}
    }

    handleProfileSelect() {
        const val = this.dom.profileSelector.value;
        if (val === '_manage_') {
            window.location.hash = '#/account';
            this.loadActiveProfile(); 
            return;
        }
        if (val) this.switchProfile(val);
    }

    async loadProfilesView() {
        const list = document.getElementById('profiles-list');
        if (!list) return;
        list.innerHTML = '<p style="color: #888;">Loading profiles...</p>';
        
        try {
            const response = await fetch('/api/profiles/list');
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const data = await response.json();
            list.innerHTML = '';
            
            for (const profile of data.profiles) {
                const row = document.createElement('div');
                row.className = 'profile-row' + (profile.is_active ? ' active' : '');
                const statusClass = profile.is_authenticated ? 'authenticated' : 'not-authenticated';
                const statusText = profile.is_authenticated ? 'Signed in' : 'Not signed in';
                const actions = [];
                const safeName = this.escapeHtml(profile.name);
                
                if (!profile.is_authenticated) actions.push(`<button class="action-btn-secondary" data-action="signin" data-profile="${safeName}">Sign In</button>`);
                if (!profile.is_active) actions.push(`<button class="action-btn-secondary" data-action="switch" data-profile="${safeName}">Switch To</button>`);
                if (data.profiles.length > 1) actions.push(`<button class="action-btn-secondary action-btn-danger" data-action="delete" data-profile="${safeName}">Delete</button>`);
                
                row.innerHTML = `
                    <div class="profile-row-icon">👤</div>
                    <div class="profile-row-info">
                        <div class="profile-row-name">${safeName}${profile.is_active ? ' <span style="color: var(--accent); font-size: 0.8em;">(Active)</span>' : ''}</div>
                        <div class="profile-row-status ${statusClass}">${statusText}</div>
                    </div>
                    <div class="profile-row-actions">${actions.join('')}</div>
                `;
                list.appendChild(row);
            }
        } catch (error) { list.innerHTML = `<p style="color: #ff6b6b;">Failed to load profiles: ${error.message}</p>`; }
    }

    openCreateProfileModal() {
        document.getElementById('new-profile-name').value = '';
        document.getElementById('create-profile-modal').style.display = 'flex';
        setTimeout(() => document.getElementById('new-profile-name').focus(), 100);
    }

    async submitCreateProfile() {
        const name = document.getElementById('new-profile-name').value.trim();
        if (!name) return;
        try {
            const response = await fetch('/api/profiles/create', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({name: name}) });
            if (!response.ok) throw new Error((await response.json()).detail || 'Failed to create profile');
            
            document.getElementById('create-profile-modal').style.display = 'none';
            await this.loadProfilesView();
            await this.loadActiveProfile();
            
            if (confirm(`Profile "${name}" created. Would you like to sign in now?`)) this.openLoginModal(name);
        } catch (error) { alert(error.message); }
    }

    async switchProfile(name) {
        try {
            const response = await fetch(`/api/profiles/${encodeURIComponent(name)}/activate`, { method: 'POST' });
            if (!response.ok) throw new Error((await response.json()).detail || 'Failed to switch profile');
            await this.loadProfilesView();
            await this.loadActiveProfile();
            if (window.TomeBoxApp) await window.TomeBoxApp.loadLibrary();
        } catch (error) { alert(`Failed to switch profile: ${error.message}`); }
    }

    async deleteProfile(name) {
        if (!confirm(`Delete profile "${name}"? This will remove the saved authentication. Local files won't be affected.`)) return;
        try {
            const response = await fetch(`/api/profiles/${encodeURIComponent(name)}`, { method: 'DELETE' });
            if (!response.ok) throw new Error((await response.json()).detail || 'Failed to delete profile');
            await this.loadProfilesView();
            await this.loadActiveProfile();
        } catch (error) { alert(`Failed to delete profile: ${error.message}`); }
    }

    // ==========================================
    // LOGIN FLOW
    // ==========================================
    openLoginModal(profileName) {
        this.state.pendingLoginProfile = profileName;
        document.getElementById('login-modal-title').textContent = `Sign in: ${profileName}`;
        document.getElementById('login-step-1').style.display = 'block';
        document.getElementById('login-step-2').style.display = 'none';
        document.getElementById('callback-url-input').value = '';
        document.getElementById('login-error').style.display = 'none';
        document.getElementById('login-modal').style.display = 'flex';
    }

    async startLoginFlow() {
        this.state.pendingLoginLocale = document.getElementById('login-locale').value;
        try {
            const response = await fetch('/api/auth/login-start', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ locale: this.state.pendingLoginLocale, profile: this.state.pendingLoginProfile })
            });
            if (!response.ok) throw new Error((await response.json()).detail || `HTTP ${response.status}`);
            
            const data = await response.json();
            document.getElementById('login-step-1').style.display = 'none';
            document.getElementById('login-step-2').style.display = 'block';
            
            document.getElementById('login-iframe').style.display = 'none';
            document.getElementById('login-fallback').style.display = 'block';
            document.getElementById('login-external-link').href = data.auth_url;

            const loginWindow = window.open(data.auth_url, '_blank', 'width=600,height=800');
            if (loginWindow) {
                const linkText = document.querySelector('#login-fallback p');
                if (linkText) linkText.textContent = 'Audible login opened in a new window. Sign in there, then copy the URL from the address bar after the redirect fails.';
            }
        } catch (error) {
            alert(`Failed to start login: ${error.message}`);
            document.getElementById('login-modal').style.display = 'none';
        }
    }

    async completeLoginFlow() {
        const callbackUrl = document.getElementById('callback-url-input').value.trim();
        const errorEl = document.getElementById('login-error');
        if (!callbackUrl) { errorEl.textContent = 'Please paste the URL from your browser'; errorEl.style.display = 'block'; return; }
        errorEl.style.display = 'none';
        
        try {
            const response = await fetch('/api/auth/login-complete', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ profile: this.state.pendingLoginProfile, callback_url: callbackUrl })
            });
            if (!response.ok) throw new Error((await response.json()).detail || `HTTP ${response.status}`);
            
            const profileName = this.state.pendingLoginProfile;
            document.getElementById('login-modal').style.display = 'none';
            await this.loadProfilesView();
            await this.loadActiveProfile();
            if (window.TomeBoxApp) await window.TomeBoxApp.loadLibrary();
            alert(`Successfully signed in to ${profileName}`);
        } catch (error) { errorEl.textContent = error.message; errorEl.style.display = 'block'; }
    }

    // ==========================================
    // CONTEXT MENU & QUEUE
    // ==========================================
    bindGlobalEvents() {
        document.addEventListener('click', () => {
            const menu = document.getElementById('context-menu');
            if(menu) menu.style.display = 'none';
        });
        document.addEventListener('contextmenu', (e) => {
            if (e.target.closest('input, textarea, [contenteditable]')) return;
            if (e.target.closest('#view-library') || e.target.closest('#sidebar')) e.preventDefault();
        }, { capture: true });
        document.addEventListener('mousedown', (e) => {
            if (e.target.closest('input, textarea, [contenteditable]')) return;
            if (e.button === 2 || (e.button === 0 && e.ctrlKey)) {
                if (e.target.closest('#view-library') || e.target.closest('#sidebar')) e.preventDefault();
            }
        }, { capture: true });
    }

    attachContextMenu(cardElement, itemData) {
        cardElement.addEventListener('contextmenu', (e) => {
            e.preventDefault(); e.stopPropagation();
            this.state.currentContextItem = itemData;
            window.getSelection().removeAllRanges();

            const menu = document.getElementById('context-menu');
            menu.style.display = 'block';
            menu.style.left = `${e.pageX}px`;
            menu.style.top = `${e.pageY}px`;

            const isCloudOnly = itemData.download_status === 'cloud_only';
            const isAax = itemData.format === 'AAXC' || itemData.format === 'AAX';
            const isDownloadingOrQueued = this.state.activeTaskAsins.has(itemData.asin);
            const isDownloaded = !isCloudOnly && !isDownloadingOrQueued;

            document.getElementById('ctx-download').style.display = (isCloudOnly && !isDownloadingOrQueued) ? 'block' : 'none';
            document.getElementById('ctx-match').style.display = (isCloudOnly && !isDownloadingOrQueued) ? 'block' : 'none';
            document.getElementById('ctx-shelf').style.display = isDownloaded ? 'block' : 'none';
            document.getElementById('ctx-scrape').style.display = isDownloaded ? 'block' : 'none';
            document.getElementById('ctx-remove').style.display = isDownloaded ? 'block' : 'none';
            document.getElementById('ctx-convert').style.display = (isDownloaded && isAax) ? 'block' : 'none';
            document.getElementById('ctx-cancel').style.display = isDownloadingOrQueued ? 'block' : 'none';
        });
    }

    async ctxAction(action) {
        if (!this.state.currentContextItem) return;
        document.getElementById('context-menu').style.display = 'none';
        const item = this.state.currentContextItem;
        
        try {
            if (action === 'download') this.queueSingleDownload(item.asin);
            else if (action === 'convert') await fetch('/api/conversions/queue', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ paths: [item.path] }) });
            else if (action === 'cancel') await fetch(`/api/downloads/${item.asin}`, { method: 'DELETE' });
            else if (action === 'match') {
                const res = await fetch('/api/system/browse-file');
                const data = await res.json();
                if (data.path) {
                    await fetch('/api/library/match', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ asin: item.asin, path: data.path }) });
                    if (window.TomeBoxApp) await window.TomeBoxApp.loadLibrary();
                }
            } else if (action === 'scrape') {
                document.getElementById('metadata-search-input').value = item.title;
                document.getElementById('metadata-search-results').innerHTML = '';
                document.getElementById('metadata-search-modal').style.display = 'flex';
            } else if (action === 'remove') {
                if (confirm(`Remove "${item.title}" from local library database? (The file on your drive will not be deleted)`)) {
                    await fetch('/api/library/remove', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: item.path }) });
                    if (window.TomeBoxApp) await window.TomeBoxApp.loadLibrary();
                }
            } else if (action === 'shelf') {
                const shelfName = prompt(`Add "${item.title}" to which shelf?`);
                if (shelfName && shelfName.trim() !== '') {
                    const response = await fetch('/api/library/shelf', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ asin: item.asin, shelf: shelfName.trim() }) });
                    if (response.ok && window.TomeBoxApp) await window.TomeBoxApp.loadLibrary();
                    else alert(`Failed to add to shelf: ${(await response.json()).detail}`);
                }
            }
        } catch (error) {}
    }

    startQueuePolling() {
        if (this.state.queuePollingInterval) clearInterval(this.state.queuePollingInterval);
        this.state.queuePollingInterval = setInterval(() => this.pollQueues(), 2000);
    }

    async pollQueues() {
        try {
            const res = await fetch('/api/downloads/queue');
            if (!res.ok) return;
            const data = await res.json();
            
            this.state.activeTaskAsins.clear();
            if (data.is_processing && data.active && data.active.active_asin) this.state.activeTaskAsins.add(data.active.active_asin);
            if (data.queue && data.queue.length > 0) data.queue.forEach(task => this.state.activeTaskAsins.add(task.asin));
            
            if (data.is_processing && data.active.active_asin) {
                this._wasProcessingQueue = true; 
                const libProgressBar = document.getElementById(`progress-bar-${data.active.active_asin}`);
                const libProgressText = document.getElementById(`progress-text-${data.active.active_asin}`);
                if (libProgressBar) libProgressBar.style.width = `${data.active.progress}%`;
                if (libProgressText) {
                    let statusMsg = data.active.status;
                    if (statusMsg.includes("Downloading") && data.active.progress > 0) statusMsg = `Downloading... ${Math.floor(data.active.progress)}%`;
                    libProgressText.textContent = statusMsg;
                }
            } else {
                if (this._wasProcessingQueue) {
                    this._wasProcessingQueue = false; 
                    if (window.TomeBoxApp) await window.TomeBoxApp.loadLibrary();
                }
            }
            if (data.queue && data.queue.length > 0) {
                data.queue.forEach(task => {
                    const pendingText = document.getElementById(`progress-text-${task.asin}`);
                    if (pendingText && (!data.active || data.active.active_asin !== task.asin)) pendingText.textContent = "Queued...";
                });
            }
        } catch (error) {} 
    }

    // ==========================================
    // DOWNLOADS & DIRECTORIES
    // ==========================================
    async attemptQueueDownloads(asins) {
        try {
            const response = await fetch('/api/downloads/queue', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ asins: asins }) });
            if (!response.ok) {
                const err = await response.json();
                if (err.detail === 'DOWNLOAD_DIR_NOT_SET') {
                    this.state.pendingDownloadAsins = asins;
                    document.getElementById('download-dir-error').style.display = 'none';
                    document.getElementById('download-dir-modal').style.display = 'flex';
                    return;
                }
                throw new Error(err.detail || "Failed to queue downloads");
            }
        } catch (error) { alert(error.message); }
    }

    queueSingleDownload(asin) { this.attemptQueueDownloads([asin]); }

    downloadAllMissing() {
        const libData = window.TomeBoxApp ? window.TomeBoxApp.state.rawLibraryData : null;
        if (!libData) return;
        const cloudAsins = Object.values(libData).filter(item => item.download_status === 'cloud_only').map(item => item.asin);
        if (cloudAsins.length === 0) return alert("All books are already downloaded!");
        if (confirm(`Queue ${cloudAsins.length} missing books for download?`)) this.attemptQueueDownloads(cloudAsins);
    }

    async browseForDirectory() {
        const inputEl = document.getElementById('download-dir-input');
        inputEl.placeholder = "Waiting for system dialog...";
        try {
            const response = await fetch('/api/system/browse-directory');
            if (!response.ok) throw new Error('Failed to open system dialog');
            const data = await response.json();
            if (data.path) inputEl.value = data.path;
        } catch (error) {} 
        finally { inputEl.placeholder = "e.g., C:\\Audiobooks or /Users/name/Audiobooks"; }
    }

    async submitDownloadDir() {
        const path = document.getElementById('download-dir-input').value.trim();
        const errorEl = document.getElementById('download-dir-error');
        if (!path) return;
        try {
            const response = await fetch('/api/settings/download-dir', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ path: path }) });
            if (!response.ok) throw new Error((await response.json()).detail || 'Invalid path');
            document.getElementById('download-dir-modal').style.display = 'none';
            if (this.state.pendingDownloadAsins.length > 0) {
                this.attemptQueueDownloads(this.state.pendingDownloadAsins);
                this.state.pendingDownloadAsins = [];
            }
        } catch (error) { errorEl.textContent = error.message; errorEl.style.display = 'block'; }
    }

    // ==========================================
    // METADATA SCRAPING
    // ==========================================
    async executeMetadataSearch() {
        const query = document.getElementById('metadata-search-input').value.trim();
        const resultsContainer = document.getElementById('metadata-search-results');
        if (!query) return;
        resultsContainer.innerHTML = '<p style="color: #aaa; text-align: center;">Searching Audible catalog...</p>';

        try {
            const res = await fetch('/api/library/search', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({query: query}) });
            if (!res.ok) throw new Error("Search failed");
            const data = await res.json();
            resultsContainer.innerHTML = '';
            if (!data.results || data.results.length === 0) {
                resultsContainer.innerHTML = '<p style="color: #ff6b6b;">No results found.</p>';
                return;
            }
            data.results.forEach(item => {
                const authors = item.authors ? item.authors.map(a => a.name).join(', ') : 'Unknown Author';
                const div = document.createElement('div');
                div.className = 'profile-row';
                div.innerHTML = `
                    <div class="profile-row-info">
                        <div class="profile-row-name">${this.escapeHtml(item.title)}</div>
                        <div class="profile-row-status">${this.escapeHtml(authors)} | ASIN: ${item.asin}</div>
                    </div>
                    <button class="action-btn-secondary" data-asin="${item.asin}">Apply</button>
                `;
                resultsContainer.appendChild(div);
            });
        } catch (error) { resultsContainer.innerHTML = `<p style="color: #ff6b6b;">${error.message}</p>`; }
    }

    async applyMetadata(asin) {
        document.getElementById('metadata-search-modal').style.display = 'none';
        this.setStatus('Downloading covers & embedding metadata...');
        try {
            const res = await fetch('/api/library/scrape', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: this.state.currentContextItem.path, asin: asin }) });
            if (!res.ok) throw new Error((await res.json()).detail);
            setTimeout(async () => {
                if (window.TomeBoxApp) await window.TomeBoxApp.loadLibrary();
                this.setStatus('Metadata embedded!', 'success', 3000);
            }, 8000); 
        } catch (err) { alert(`Scrape failed: ${err.message}`); this.setStatus(''); }
    }

    // ==========================================
    // IMPORTS & LOCAL FILES
    // ==========================================
    async addLocalFile() {
        try {
            const response = await fetch('/api/system/browse-file');
            if (!response.ok) throw new Error('Failed to open file dialog');
            const data = await response.json();
            if (data.path) await this.processImport(data.path);
        } catch (error) {}
    }

    async importFolder() {
        try {
            const response = await fetch('/api/system/browse-directory');
            if (!response.ok) throw new Error('Failed to open folder dialog');
            const data = await response.json();
            if (data.path && confirm("Auto-Merge Warning:\n\nTomeBox will scan this folder. If multiple audio files belonging to the same book are found, they will be automatically merged into a new .m4b file on your hard drive.\n\nDo you wish to continue?")) {
                await this.processImport(data.path);
            }
        } catch (error) {}
    }

    async cancelImport() {
        const cancelBtn = document.getElementById('btn-cancel-import');
        if (cancelBtn) cancelBtn.style.display = 'none';
        try {
            await fetch('/api/library/import', { method: 'DELETE' });
            this.setStatus('Cancelling active task...', 'error', 3000);
        } catch (error) {}
    }

    async processImport(path) {
        const cancelBtn = document.getElementById('btn-cancel-import');
        this.setStatus('Initializing import...');
        if (cancelBtn) cancelBtn.style.display = 'inline-block';

        try {
            const response = await fetch('/api/library/import', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: path }) });
            if (!response.ok) throw new Error((await response.json()).detail || 'Import failed');

            const pollInterval = setInterval(async () => {
                try {
                    const res = await fetch('/api/system/status');
                    if (res.ok) {
                        const data = await res.json();
                        if (data.task) this.setStatus(data.task);
                        else {
                            clearInterval(pollInterval);
                            this.setStatus('Task completed.', 'success', 3000);
                            if (cancelBtn) cancelBtn.style.display = 'none';
                            if (window.TomeBoxApp) await window.TomeBoxApp.loadLibrary();
                            window.location.hash = '#/library';
                        }
                    }
                } catch (e) {} 
            }, 1000);
        } catch (error) {
            this.setStatus(`Error: ${error.message}`, 'error');
            if (cancelBtn) cancelBtn.style.display = 'none';
        }
    }

    toggleActionMenu() {
        const menu = document.getElementById('action-menu');
        if (menu) menu.classList.toggle('collapsed');
    }

    escapeHtml(unsafe) {
        if (unsafe == null) return "";
        const div = document.createElement('div');
        div.textContent = unsafe;
        return div.innerHTML;
    }
}

document.addEventListener('DOMContentLoaded', () => {
    // We strictly expose one API point for cross-class communication (mobile_client triggering desktop downloads)
    window.DesktopApp = new TomeBoxDesktop();
});