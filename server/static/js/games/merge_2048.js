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
            this.canvas.removeEventListener('pointerdown', this.downHandler);
            this.canvas.removeEventListener('pointerup', this.upHandler);
            window.removeEventListener('resize', this.resizeHandler);
            this.canvas = null;
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
