import os
import subprocess

def generate_test_files():
    target_dir = "test_audiobook"
    os.makedirs(target_dir, exist_ok=True)

    # Physical Filename, Track Number, Part Title, Cover Color
    test_files = [
        ("03_the_end.mp3", "3", "Part 3: The Conclusion", "blue"),
        ("01_the_start.mp3", "1", "Part 1: The Beginning", "red"),
        ("02_the_middle.mp3", "2", "Part 2: The Action", "green")
    ]

    # Global book metadata
    author = "Brandon Sanderson"
    narrator = "Michael Kramer"
    album = "The Way of Kings"
    series = "The Stormlight Archive"
    year = "2010"
    comment = "Dummy test file for TomeBox metadata extraction."

    print("Generating files (this may take a few seconds)...")

    for filename, track_num, title, color in test_files:
        filepath = os.path.join(target_dir, filename)
        
        cmd = [
            "ffmpeg", "-y", 
            
            # 1. Generate Audio Stream (Silence)
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", 
            
            # 2. Generate Video Stream (Solid color square, 1 frame)
            "-f", "lavfi", "-i", f"color=c={color}:s=400x400:d=1", 
            
            # Map both streams together
            "-map", "0:a", 
            "-map", "1:v", 
            
            # Encode the image as mjpeg (Required for mp3 cover art)
            "-c:v", "mjpeg",
            "-disposition:v", "attached_pic",
            
            # Audio Length: 65 seconds (Registers as 1m in TomeBox to clear the N/A warning)
            "-t", "65", 
            
            # --- Map to TomeBox variables ---
            "-metadata", f"title={title}",
            "-metadata", f"album={album}",
            "-metadata", f"artist={author}",
            "-metadata", f"album_artist={author}",
            "-metadata", f"composer={narrator}",
            "-metadata", f"date={year}",
            "-metadata", f"show={series}",
            "-metadata", f"comment={comment}",
            "-metadata", f"track={track_num}",
            
            filepath
        ]
        
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"Generated: {filepath} (Color: {color.capitalize()}, Length: 1m 5s)")

    print("Done! Drag the 'test_audiobook' folder into TomeBox.")

if __name__ == "__main__":
    generate_test_files()