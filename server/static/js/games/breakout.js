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