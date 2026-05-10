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