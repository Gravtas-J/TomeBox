import os
import textwrap
from collections import OrderedDict
from PIL import Image, ImageTk, ImageDraw, ImageFont

class ImageCache:
    """
    LRU Cache for Tkinter PhotoImages. 
    Handles on-the-fly thumbnail generation and dummy card creation.
    """
    def __init__(self, max_size=100):
        self.max_size = max_size
        self.cache = OrderedDict()
        
        # Try to load a nice font, fallback to default if not found
        try:
            # Arial is standard on Windows/Mac, fallback works everywhere else
            self.title_font = ImageFont.truetype("arial.ttf", 20)
            self.author_font = ImageFont.truetype("arial.ttf", 14)
        except IOError:
            self.title_font = ImageFont.load_default()
            self.author_font = ImageFont.load_default()

    def get_thumbnail(self, asin, filepath, title="Unknown Title", author="Unknown Author", size=(200, 200)):
        """
        Retrieves a cached PhotoImage. If it doesn't exist, it loads/resizes the 
        real cover or generates a fallback dummy card.
        """
        cache_key = f"{asin}_{size[0]}x{size[1]}"
        
        # 1. Check cache (and move to end to mark as recently used)
        if cache_key in self.cache:
            self.cache.move_to_end(cache_key)
            return self.cache[cache_key]

        # 2. Generate the PIL Image
        img = None
        if filepath and os.path.exists(filepath):
            try:
                img = Image.open(filepath)
                if img.mode != "RGB":
                    img = img.convert("RGB")
                # Thumbnail modifies the image in place, preserving aspect ratio
                img.thumbnail(size, Image.Resampling.LANCZOS)
                
                # Pad it to make it exactly the requested size (for uniform grid)
                final_img = Image.new("RGB", size, (30, 30, 30))
                offset_x = (size[0] - img.width) // 2
                offset_y = (size[1] - img.height) // 2
                final_img.paste(img, (offset_x, offset_y))
                img = final_img
                
            except Exception as e:
                img = None # Fallback to dummy

        # 3. Generate Dummy Card if no image was found/valid
        if img is None:
            img = self._generate_dummy_card(title, author, size)

        # 4. Convert to Tkinter PhotoImage (MUST be done in main thread)
        photo = ImageTk.PhotoImage(img)

        # 5. Manage LRU Cache size
        self.cache[cache_key] = photo
        if len(self.cache) > self.max_size:
            self.cache.popitem(last=False) # Remove oldest

        return photo

    def _generate_dummy_card(self, title, author, size):
        """Generates a solid color card with text for missing covers."""
        # Create a dark gradient-like background
        img = Image.new("RGB", size, color="#2c3e50")
        draw = ImageDraw.Draw(img)

        # Wrap text to fit
        margin = 15
        max_chars_per_line = int((size[0] - (margin * 2)) / 10) # rough estimate
        
        title_lines = textwrap.wrap(title, width=max_chars_per_line)
        author_lines = textwrap.wrap(author, width=max_chars_per_line)

        # Draw Title
        y_text = margin
        for line in title_lines:
            # Use textbbox to center text
            bbox = draw.textbbox((0, 0), line, font=self.title_font)
            w = bbox[2] - bbox[0]
            draw.text(((size[0] - w) / 2, y_text), line, font=self.title_font, fill="#ecf0f1")
            y_text += (bbox[3] - bbox[1]) + 5

        # Draw Author near the bottom
        y_text = size[1] - margin - 20
        for line in author_lines:
            bbox = draw.textbbox((0, 0), line, font=self.author_font)
            w = bbox[2] - bbox[0]
            draw.text(((size[0] - w) / 2, y_text), line, font=self.author_font, fill="#95a5a6")
            y_text -= (bbox[3] - bbox[1]) + 5

        return img

    def clear(self):
        """Purge the cache (useful on major library reloads or theme changes)."""
        self.cache.clear()