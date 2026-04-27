// ========================================
// TomeBox Desktop UI — sidebar, routing, pairing
// ========================================

(function() {
    'use strict';

    // ------------ Sidebar Toggle ------------
    
    const SIDEBAR_STATE_KEY = 'tomebox_sidebar_collapsed';
    
    function initSidebar() {
        const toggle = document.getElementById('sidebar-toggle');
        const shell = document.getElementById('app-shell');
        
        if (!toggle || !shell) return;
        
        // Restore previous state
        if (localStorage.getItem(SIDEBAR_STATE_KEY) === 'true') {
            shell.classList.add('sidebar-collapsed');
        }
        
        toggle.addEventListener('click', () => {
            shell.classList.toggle('sidebar-collapsed');
            localStorage.setItem(
                SIDEBAR_STATE_KEY,
                shell.classList.contains('sidebar-collapsed')
            );
        });
    }
    
    // ------------ Hash-Based Routing ------------
    
    const ROUTES = {
        '#/library': 'library',
        '#/devices': 'devices',
        '#/account': 'account'
    };
    
    function showView(viewName) {
        document.querySelectorAll('.nav-item').forEach(item => {
            if (item.dataset.view === viewName) {
                item.classList.add('active');
            } else {
                item.classList.remove('active');
            }
        });
        
        document.querySelectorAll('.view').forEach(view => {
            view.classList.remove('active');
        });
        
        const target = document.getElementById(`view-${viewName}`);
        if (target) {
            target.classList.add('active');
        }
        
        if (viewName === 'devices') {
            loadDevicesView();
        } else if (viewName === 'account') {
            loadProfilesView();
        }
    }
    async function checkFirstRun() {
        try {
            const response = await fetch('/api/profiles/list');
            if (!response.ok) return;
            
            const data = await response.json();
            const anyAuthenticated = data.profiles.some(p => p.is_authenticated);
            
            if (!anyAuthenticated) {
                // Redirect to account view with a friendly message
                window.location.hash = '#/account';
                
                // Small banner at the top of the account view
                setTimeout(() => {
                    const list = document.getElementById('profiles-list');
                    if (list && !document.getElementById('first-run-banner')) {
                        const banner = document.createElement('div');
                        banner.id = 'first-run-banner';
                        banner.style.cssText = 'background: rgba(187,134,252,0.1); border-left: 3px solid var(--accent); padding: 15px; margin-bottom: 15px; border-radius: 6px;';
                        banner.innerHTML = '<strong>Welcome to TomeBox!</strong> Sign in to your Audible account below to get started.';
                        list.parentElement.insertBefore(banner, list.parentElement.firstChild);
                    }
                }, 200);
            }
        } catch (error) {
            console.error('First run check failed:', error);
        }
    }
    function handleRoute() {
        const hash = window.location.hash || '#/library';
        const viewName = ROUTES[hash] || 'library';
        showView(viewName);
    }
    
    function initRouting() {
        window.addEventListener('hashchange', handleRoute);
        handleRoute();
    }
    
    // ------------ Pairing / Devices View ------------
    
    let devicesViewLoaded = false;
    
    async function loadDevicesView() {
        if (devicesViewLoaded) return;
        
        const qrContainer = document.getElementById('qr-container');
        const urlElement = document.getElementById('pairing-url');
        
        if (!qrContainer || !urlElement) return;
        
        try {
            // Fetch the pairing URL — server's /pairing endpoint returns a full HTML 
            // page, so we need a dedicated JSON endpoint for the data
            const response = await fetch('/api/pairing-info');
            
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            
            const data = await response.json();
            const pairingUrl = data.pairing_url;
            
            urlElement.textContent = pairingUrl;
            
            // Generate QR code client-side using a CDN library
            await loadQRLibrary();
            
            qrContainer.innerHTML = '';
            new QRCode(qrContainer, {
                text: pairingUrl,
                width: 240,
                height: 240,
                colorDark: '#000000',
                colorLight: '#ffffff',
                correctLevel: QRCode.CorrectLevel.H
            });
            
            // Click-to-copy on the URL
            urlElement.addEventListener('click', () => {
                navigator.clipboard.writeText(pairingUrl).then(() => {
                    const original = urlElement.textContent;
                    urlElement.textContent = 'Copied to clipboard!';
                    setTimeout(() => urlElement.textContent = original, 1500);
                });
            });
            
            devicesViewLoaded = true;
            
        } catch (error) {
            console.error('Failed to load pairing info:', error);
            qrContainer.innerHTML = `<p style="color: #ff6b6b;">Failed to load pairing info: ${error.message}</p>`;
        }
    }
    
    function loadQRLibrary() {
        return new Promise((resolve, reject) => {
            if (window.QRCode) {
                resolve();
                return;
            }
            
            const script = document.createElement('script');
            script.src = 'https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js';
            script.onload = resolve;
            script.onerror = () => reject(new Error('Failed to load QR library'));
            document.head.appendChild(script);
        });
    }
    
    // ------------ Initialization ------------
    
    document.addEventListener('DOMContentLoaded', () => {
        initSidebar();
        initRouting();
        loadActiveProfile();
        checkFirstRun();
    });
    // ------------ Library Refresh ------------

    async function refreshLibrary() {
        const btn = document.getElementById('refresh-library-btn');
        const status = document.getElementById('library-status');
        
        if (!btn || btn.disabled) return;
        
        btn.disabled = true;
        btn.classList.add('refreshing');
        status.textContent = 'Refreshing library from Audible...';
        status.className = 'library-status';
        
        try {
            const response = await fetch('/api/library/refresh', { method: 'POST' });
            
            if (!response.ok) {
                const err = await response.json();
                throw new Error(err.detail || `HTTP ${response.status}`);
            }
            
            const data = await response.json();
            status.textContent = `Library refreshed — ${data.items_count} items in cloud`;
            status.classList.add('success');
            
            // Trigger the existing library reload from mobile_client.js
            if (typeof loadLibrary === 'function') {
                await loadLibrary();
            }
            
            // Clear status after a few seconds
            setTimeout(() => {
                status.textContent = '';
                status.className = 'library-status';
            }, 4000);
            
        } catch (error) {
            status.textContent = `Refresh failed: ${error.message}`;
            status.classList.add('error');
        } finally {
            btn.disabled = false;
            btn.classList.remove('refreshing');
        }
    }

    // Expose to global scope for the inline onclick handler
    window.refreshLibrary = refreshLibrary;
    window.loadActiveProfile = loadActiveProfile;

    // ------------ Profile Loading ------------

    async function loadActiveProfile() {
        try {
            const response = await fetch('/api/profiles/active');
            if (!response.ok) return;
            
            const data = await response.json();
            const select = document.getElementById('profile-selector');
            if (!select) return;
            
            select.innerHTML = '';
            for (const profileName of data.available) {
                const option = document.createElement('option');
                option.value = profileName;
                option.textContent = profileName;
                if (profileName === data.active) {
                    option.selected = true;
                }
                select.appendChild(option);
            }
        } catch (error) {
            console.error('Failed to load active profile:', error);
        }
    }
})();

// ============================================================
// Account & Profile Management
// ============================================================

let pendingLoginProfile = null;
let pendingLoginLocale = null;

async function loadProfilesView() {
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
            
            if (!profile.is_authenticated) {
                actions.push(`<button class="action-btn-secondary" onclick="openLoginModal('${escapeHtml(profile.name)}')">Sign In</button>`);
            }
            
            if (!profile.is_active) {
                actions.push(`<button class="action-btn-secondary" onclick="switchProfile('${escapeHtml(profile.name)}')">Switch To</button>`);
            }
            
            if (data.profiles.length > 1) {
                actions.push(`<button class="action-btn-secondary action-btn-danger" onclick="deleteProfile('${escapeHtml(profile.name)}')">Delete</button>`);
            }
            
            row.innerHTML = `
                <div class="profile-row-icon">👤</div>
                <div class="profile-row-info">
                    <div class="profile-row-name">${escapeHtml(profile.name)}${profile.is_active ? ' <span style="color: var(--accent); font-size: 0.8em;">(Active)</span>' : ''}</div>
                    <div class="profile-row-status ${statusClass}">${statusText}</div>
                </div>
                <div class="profile-row-actions">${actions.join('')}</div>
            `;
            
            list.appendChild(row);
        }
    } catch (error) {
        list.innerHTML = `<p style="color: #ff6b6b;">Failed to load profiles: ${error.message}</p>`;
    }
}

function escapeHtml(s) {
    const div = document.createElement('div');
    div.textContent = s;
    return div.innerHTML;
}

// ------------ Create Profile ------------

function openCreateProfileModal() {
    document.getElementById('new-profile-name').value = '';
    document.getElementById('create-profile-modal').style.display = 'flex';
    setTimeout(() => document.getElementById('new-profile-name').focus(), 100);
}

function closeCreateProfileModal(event) {
    if (event && event.target.id !== 'create-profile-modal') return;
    document.getElementById('create-profile-modal').style.display = 'none';
}

async function submitCreateProfile() {
    const name = document.getElementById('new-profile-name').value.trim();
    if (!name) return;
    
    try {
        const response = await fetch('/api/profiles/create', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({name: name})
        });
        
        if (!response.ok) {
            const err = await response.json();
            alert(err.detail || 'Failed to create profile');
            return;
        }
        
        closeCreateProfileModal();
        await loadProfilesView();
        await loadActiveProfile();
        
        // Offer to immediately log in to the new profile
        if (confirm(`Profile "${name}" created. Would you like to sign in now?`)) {
            openLoginModal(name);
        }
    } catch (error) {
        alert(`Failed to create profile: ${error.message}`);
    }
}

// ------------ Switch / Delete ------------

async function switchProfile(name) {
    try {
        const response = await fetch(`/api/profiles/${encodeURIComponent(name)}/activate`, {
            method: 'POST'
        });
        
        if (!response.ok) {
            const err = await response.json();
            alert(err.detail || 'Failed to switch profile');
            return;
        }
        
        await loadProfilesView();
        await loadActiveProfile();
        
        // Reload library since data is now from a different account
        if (typeof loadLibrary === 'function') {
            await loadLibrary();
        }
    } catch (error) {
        alert(`Failed to switch profile: ${error.message}`);
    }
}

async function deleteProfile(name) {
    if (!confirm(`Delete profile "${name}"? This will remove the saved authentication. Local files won't be affected.`)) {
        return;
    }
    
    try {
        const response = await fetch(`/api/profiles/${encodeURIComponent(name)}`, {
            method: 'DELETE'
        });
        
        if (!response.ok) {
            const err = await response.json();
            alert(err.detail || 'Failed to delete profile');
            return;
        }
        
        await loadProfilesView();
        await loadActiveProfile();
    } catch (error) {
        alert(`Failed to delete profile: ${error.message}`);
    }
}

// ------------ Login Flow ------------

function openLoginModal(profileName) {
    pendingLoginProfile = profileName;
    document.getElementById('login-modal-title').textContent = `Sign in: ${profileName}`;
    document.getElementById('login-step-1').style.display = 'block';
    document.getElementById('login-step-2').style.display = 'none';
    document.getElementById('callback-url-input').value = '';
    document.getElementById('login-error').style.display = 'none';
    document.getElementById('login-modal').style.display = 'flex';
}

function closeLoginModal(event) {
    if (event && event.target.id !== 'login-modal') return;
    document.getElementById('login-modal').style.display = 'none';
    pendingLoginProfile = null;
    pendingLoginLocale = null;
}

async function startLoginFlow() {
    pendingLoginLocale = document.getElementById('login-locale').value;
    
    try {
        const response = await fetch('/api/auth/login-start', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                locale: pendingLoginLocale,
                profile: pendingLoginProfile
            })
        });
        
        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.detail || `HTTP ${response.status}`);
        }
        
        const data = await response.json();
        
        document.getElementById('login-step-1').style.display = 'none';
        document.getElementById('login-step-2').style.display = 'block';
        
        // Try iframe approach first
        const iframe = document.getElementById('login-iframe');
        const fallback = document.getElementById('login-fallback');
        const externalLink = document.getElementById('login-external-link');

        externalLink.href = data.auth_url;

        // Audible always blocks iframe embedding via X-Frame-Options.
        // Skip the iframe attempt entirely and go straight to the fallback.
        iframe.style.display = 'none';
        fallback.style.display = 'block';
        const loginWindow = window.open(data.auth_url, '_blank', 'width=600,height=800');
        if (!loginWindow) {
            // Popup blocker fired — user has to click the link
            document.getElementById('login-external-link').href = data.auth_url;
        } else {
            // Hide the link since we opened it for them, just leave the instructions
            const linkText = document.querySelector('#login-fallback p');
            if (linkText) {
                linkText.textContent = 'Audible login opened in a new window. Sign in there, then copy the URL from the address bar after the redirect fails.';
            }
        }
        // Audible's CSP frequently blocks iframes — fall back if nothing happens
        setTimeout(() => {
            if (!iframeWorked) {
                iframe.style.display = 'none';
                fallback.style.display = 'block';
            }
        }, 3000);
        
    } catch (error) {
        alert(`Failed to start login: ${error.message}`);
        closeLoginModal();
    }
}

async function completeLoginFlow() {
    const callbackUrl = document.getElementById('callback-url-input').value.trim();
    const errorEl = document.getElementById('login-error');
    
    if (!callbackUrl) {
        errorEl.textContent = 'Please paste the URL from your browser';
        errorEl.style.display = 'block';
        return;
    }
    
    errorEl.style.display = 'none';
    
    try {
        const response = await fetch('/api/auth/login-complete', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                profile: pendingLoginProfile,
                callback_url: callbackUrl
            })
        });
        
        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.detail || `HTTP ${response.status}`);
        }
        
        const profileName = pendingLoginProfile;
        closeLoginModal();
        await loadProfilesView();
        await loadActiveProfile();
        
        // If this was the active profile, refresh the library
        if (typeof loadLibrary === 'function') {
            await loadLibrary();
        }
        
        alert(`Successfully signed in to ${profileName}`);
    } catch (error) {
        errorEl.textContent = error.message;
        errorEl.style.display = 'block';
    }
}

window.openCreateProfileModal = openCreateProfileModal;
window.closeCreateProfileModal = closeCreateProfileModal;
window.submitCreateProfile = submitCreateProfile;
window.switchProfile = switchProfile;
window.deleteProfile = deleteProfile;
window.openLoginModal = openLoginModal;
window.closeLoginModal = closeLoginModal;
window.startLoginFlow = startLoginFlow;
window.completeLoginFlow = completeLoginFlow;