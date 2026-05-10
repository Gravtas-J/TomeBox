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

let pairingViewLoaded = false;

async function loadPairingView() {
    if (pairingViewLoaded) return;
    
    const qrContainer = document.getElementById('qr-container');
    const urlElement = document.getElementById('pairing-url');
    if (!qrContainer || !urlElement) return;
    
    try {
        const response = await fetch('/api/pairing-info');
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        
        const data = await response.json();
        const pairingUrl = data.pairing_url;
        
        urlElement.textContent = pairingUrl;
        
        // Dynamically load the QR library
        await new Promise((resolve, reject) => {
            if (window.QRCode) { resolve(); return; }
            const script = document.createElement('script');
            script.src = 'https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js';
            script.onload = resolve;
            script.onerror = reject;
            document.head.appendChild(script);
        });
        
        qrContainer.innerHTML = '';
        new QRCode(qrContainer, {
            text: pairingUrl,
            width: 200,
            height: 200,
            colorDark: '#000000',
            colorLight: '#ffffff',
            correctLevel: QRCode.CorrectLevel.H
        });
        
        // Tap to copy functionality
        urlElement.addEventListener('click', () => {
            navigator.clipboard.writeText(pairingUrl).then(() => {
                const original = urlElement.textContent;
                urlElement.textContent = 'Copied to clipboard!';
                setTimeout(() => urlElement.textContent = original, 1500);
            });
        });
        
        pairingViewLoaded = true;
        
    } catch (error) {
        console.error('Failed to load pairing info:', error);
        qrContainer.innerHTML = `<p style="color: #ff6b6b;">Failed to load: ${error.message}</p>`;
    }
}