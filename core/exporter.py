import csv
import html

class LibraryExporter:
    @staticmethod
    def export_csv(output_file, local_library, cloud_items):
        with open(output_file, mode='w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(["Title", "Author(s)", "Series", "Duration (mins)", "ASIN", "Status", "Local Path"])

            local_titles = {data["title"]: data for path, data in local_library.items()}
            cloud_titles = []

            for item in cloud_items:
                title = item.get("title", "Unknown")
                cloud_titles.append(title)
                
                raw_authors = item.get("authors") or []
                authors = ", ".join([a.get("name", "") for a in raw_authors if isinstance(a, dict)])
                
                raw_series = item.get("series") or []
                series_list = []
                for s in raw_series:
                    if isinstance(s, dict):
                        s_title = s.get("title", "")
                        s_seq = s.get("sequence", "")
                        if s_title and s_seq:
                            series_list.append(f"{s_title} (Bk {s_seq})")
                        elif s_title:
                            series_list.append(s_title)
                series_str = ", ".join(series_list)

                duration = item.get("runtime_length_min", 0)
                asin = item.get("asin", "Unknown")

                local_data = local_titles.get(title)
                status = f"Downloaded ({local_data['format']})" if local_data else "Cloud Only"
                local_path = local_data['path'] if local_data else ""

                writer.writerow([title, authors, series_str, duration, asin, status, local_path])

            for path, data in local_library.items():
                if data["title"] not in cloud_titles:
                    writer.writerow([data["title"], "Local File", "N/A", "N/A", data.get("asin", "Unknown"), f"Downloaded ({data['format']})", path])

    @staticmethod
    def export_html(output_file, local_library, cloud_items):
        html_content = """
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>My TomeBox Library</title>
            <style>
                body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #1e1e1e; color: #f0f0f0; margin: 0; padding: 20px; }
                h1 { text-align: center; color: #ffffff; }
                .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: 20px; padding: 20px 0; }
                .card { background: #2d2d2d; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); overflow: hidden; display: flex; flex-direction: column; }
                .cover-art { width: 100%; height: 250px; object-fit: cover; background-color: #3d3d3d; display: flex; align-items: center; justify-content: center; color: #aaaaaa; }
                .card-content { padding: 15px; flex-grow: 1; display: flex; flex-direction: column; }
                .title { font-size: 1.1em; font-weight: bold; margin: 0 0 5px 0; color: #ffffff; }
                .author { color: #cccccc; font-size: 0.9em; margin: 0 0 10px 0; font-style: italic; }
                .series { font-size: 0.85em; color: #f39c12; margin-bottom: 10px; }
                .status { margin-top: auto; font-size: 0.85em; padding: 5px; border-radius: 4px; text-align: center; font-weight: bold; }
                .status.downloaded { background-color: #2e5a36; color: #a3e4b3; }
                .status.cloud { background-color: #4a4a4a; color: #cccccc; }
            </style>
        </head>
        <body>
            <h1>My TomeBox Library</h1>
            <div class="grid">
        """

        local_titles = {data["title"]: data for path, data in local_library.items()}
        cloud_titles = []

        for item in cloud_items:
            title = item.get("title", "Unknown")
            cloud_titles.append(title)
            
            raw_authors = item.get("authors") or []
            authors = html.escape(", ".join([a.get("name", "") for a in raw_authors if isinstance(a, dict)]))
            
            raw_series = item.get("series") or []
            series_list = []
            for s in raw_series:
                if isinstance(s, dict) and s.get("title"):
                    series_list.append(f"{s.get('title')} (Bk {s.get('sequence', '')})")
            series_str = ", ".join(series_list)

            images = item.get("product_images", {})
            img_url = images.get("500") or images.get("252") or ""
            
            local_data = local_titles.get(title)
            is_downloaded = bool(local_data)
            status_class = "downloaded" if is_downloaded else "cloud"
            status_text = f"Downloaded ({local_data['format']})" if is_downloaded else "Cloud Only"

            img_tag = f'<img src="{img_url}" class="cover-art" alt="Cover">' if img_url else '<div class="cover-art">No Cover Art</div>'

            html_content += f"""
                <div class="card">
                    {img_tag}
                    <div class="card-content">
                        <h3 class="title">{title}</h3>
                        <p class="author">{authors}</p>
                        <p class="series">{series_str}</p>
                        <div class="status {status_class}">{status_text}</div>
                    </div>
                </div>
            """

        for path, data in local_library.items():
            if data["title"] not in cloud_titles:
                html_content += f"""
                    <div class="card">
                        <div class="cover-art">Local File</div>
                        <div class="card-content">
                            <h3 class="title">{data["title"]}</h3>
                            <p class="author">Local File</p>
                            <div class="status downloaded">Downloaded ({data['format']})</div>
                        </div>
                    </div>
                """

        html_content += """
            </div>
        </body>
        </html>
        """

        with open(output_file, "w", encoding="utf-8") as f:
            f.write(html_content)