from PIL import Image, ImageDraw

def create_tomebox_icon():
    # Create a 256x256 transparent image
    img = Image.new('RGBA', (256, 256), color=(0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # 1. The "Box": Dark rounded background with a subtle border
    draw.rounded_rectangle([16, 16, 240, 240], radius=45, fill="#1c1c1c", outline="#4a90e2", width=6)
    
    # 2. The "Tome": Stylized open book pages
    # Left page
    draw.polygon([(128, 190), (60, 210), (60, 90), (128, 70)], fill="#2b2b2b", outline="#4a90e2", width=4)
    # Right page
    draw.polygon([(128, 190), (196, 210), (196, 90), (128, 70)], fill="#2b2b2b", outline="#4a90e2", width=4)
    
    # 3. The "Player": A bright green play button resting on the book
    draw.polygon([(115, 110), (115, 170), (155, 140)], fill="#00ff00", outline="#a3e4b3", width=2)
    
    # Save as both ICO (for Windows shortcuts) and PNG (for Tkinter/Unix)
    img.save("tomebox.ico", format="ICO", sizes=[(256, 256), (128, 128), (64, 64), (32, 32), (16, 16)])
    img.save("tomebox.png", format="PNG")
    print("Success! Generated 'tomebox.ico' and 'tomebox.png'.")

if __name__ == "__main__":
    create_tomebox_icon()