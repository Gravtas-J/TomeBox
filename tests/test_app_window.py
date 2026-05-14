import pytest
from unittest.mock import MagicMock, patch
import tkinter as tk
import os

# Intercept all the heavy background managers so they don't boot during the test
@pytest.fixture
@patch("ui.app_window.DatabaseManager")
@patch("ui.app_window.AudibleClient")
@patch("ui.app_window.LibraryManager")
@patch("ui.app_window.AudioConverter")
@patch("ui.app_window.AppThreadPool")
@patch("ui.app_window.DownloadManager")
@patch("ui.app_window.MetadataManager")
@patch("ui.app_window.ConversionManager")
@patch("ui.app_window.PlaybackController")
@patch("ui.app_window.SystemManager")
@patch("ui.app_window.StatsManager")
@patch("ui.app_window.ActionRouter")
@patch("ui.app_window.BookmarksPresenter")
@patch("ui.app_window.ImportSession")
@patch("ui.app_window.PaletteController")
@patch("ui.app_window.PlaybackPresenter")
@patch("ui.app_window.AuthController")
@patch("ui.app_window.LibraryPresenter")
@patch("ui.app_window.CloudServerController")
def app_instance(MockCloud, MockLibPres, MockAuth, MockPlayPres, MockPal, MockImp, MockBM, MockAR, MockStats, MockSys, MockPlayCtrl, MockConv, MockMeta, MockDL, MockPool, MockAC, MockLib, MockAud, MockDB):
    from ui.app_window import AAXManagerApp
    
    root = MagicMock()
    # Force Tkinter thread updates to execute instantly
    root.after.side_effect = lambda delay, func, *args: func(*args)
    root.after_idle.side_effect = lambda func, *args: func(*args)
    
    # Inject a dummy search entry so the _focus_search startup timer doesn't crash
    def fake_setup_menu(app):
        app.search_entry = MagicMock()
    
    # Prevent the UI components from crashing trying to draw to the headless mock
    # Prevent the UI components from crashing trying to draw to the headless mock
    with patch("ui.app_window.setup_menu_bar", side_effect=fake_setup_menu), \
         patch("ui.app_window.PlayerBarView"), \
         patch("ui.app_window.setup_library_view"), \
         patch("ui.app_window.setup_sidebar"), \
         patch("ui.app_window.UiState"), \
         patch("ui.app_window.setup_logger"), \
         patch("os.makedirs"): 
         
        app = AAXManagerApp(root, "/mock/base/dir")
        
        # Provide some basic safe default state
        app.settings = {}
        app.queue_ui_elements = {}
        app.ui_state = MagicMock()
        
        return app

def test_clear_sidebar(app_instance):
    """Verifies that the side panel is wiped clean on deselection."""
    app_instance.author_label = MagicMock()
    app_instance.cover_label = MagicMock()
    app_instance.bm_tree = MagicMock()
    app_instance.bm_tree.get_children.return_value = ["row1", "row2"]
    
    app_instance.clear_sidebar()
    
    app_instance.author_label.config.assert_called_with(text="")
    app_instance.cover_label.config.assert_called_with(image="", text="No Cover Art")
    assert app_instance.current_cover_photo is None
    app_instance.bm_tree.delete.assert_any_call("row1")
    app_instance.bm_tree.delete.assert_any_call("row2")

@patch("ui.app_window.filedialog.askdirectory")
def test_set_download_folder(mock_askdir, app_instance):
    """Verifies changing the download folder writes to settings."""
    mock_askdir.return_value = "/new/download/dir"
    
    # Forget the saves that happened automatically during app bootup
    app_instance.db.save_settings.reset_mock()
    
    app_instance.set_download_folder()
    
    assert app_instance.default_download_dir == "/new/download/dir"
    assert app_instance.settings["download_folder"] == "/new/download/dir"
    app_instance.db.save_settings.assert_called_once_with(app_instance.settings)

def test_update_api_health(app_instance):
    """Verifies the health indicator updates and auto-resets after cooldown."""
    app_instance.api_health_var = True
    app_instance.update_api_health("Degraded", is_error=True)
    
    # It should set to Degraded, and then the lambda in .after() should instantly reset it to Online
    app_instance.ui_state.api_health.set.assert_any_call("API: Degraded")
    app_instance.ui_state.api_health.set.assert_any_call("API: Online")

def test_cancel_active_task_global(app_instance):
    """Verifies the nuclear cancel button stops all managers."""
    # Add a dummy task to the queue
    app_instance.queue_ui_elements = {"import_123": {}}
    
    app_instance.cancel_active_task()
    
    app_instance.converter.cancel.assert_called_once()
    app_instance.library_manager.cancel_import.assert_called_once()
    app_instance.download_manager.cancel_all.assert_called_once()
    app_instance.action_router.on_dl_status.assert_called_with("import_123", "Canceling...", is_global=False)

def test_cancel_task_specific(app_instance):
    """Verifies targeted cancellation routes to the right manager based on prefix."""
    app_instance.library_manager.active_task_id = "import_123"
    
    app_instance.cancel_task("import_123")
    app_instance.library_manager.cancel_import.assert_called_with("import_123")
    app_instance.converter.cancel.assert_called_once()
    
    app_instance.cancel_task("dl_456")
    app_instance.download_manager.cancel_download.assert_called_with("dl_456")

@patch("ui.app_window.messagebox.askyesno")
def test_start_convert_all_thread(mock_yesno, app_instance):
    """Verifies Convert All filters for AAX/AAXC and checks disk space."""
    mock_yesno.return_value = True
    
    # Mix of valid and invalid files
    app_instance.library_manager.local_library = {
        "/fake/1.aax": {"format": "AAX"},
        "/fake/2.mp3": {"format": "MP3"},
        "/fake/3.aaxc": {"format": "AAXC"}
    }
    
    # Bypass the OS checks
    app_instance.system_manager.has_enough_disk_space.return_value = True
    
    with patch("os.path.exists", return_value=True), patch("os.path.getsize", return_value=1000):
        app_instance.start_convert_all_thread()
        
    # Should only submit the two AAX files
    app_instance.conversion_manager.convert_batch.assert_called_once_with(["/fake/1.aax", "/fake/3.aaxc"])

def test_bring_to_front(app_instance):
    """Verifies window recovery sequence."""
    app_instance.bring_to_front()
    app_instance.root.deiconify.assert_called_once()
    app_instance.root.lift.assert_called_once()
    app_instance.root.attributes.assert_called_with('-topmost', False)

def test_tray_actions(app_instance):
    """Verifies the system tray hide/show/quit bindings."""
    app_instance.hide_window_to_tray()
    app_instance.root.withdraw.assert_called()

    icon_mock = MagicMock()
    item_mock = MagicMock()
    
    app_instance.show_window_from_tray(icon_mock, item_mock)
    app_instance.root.deiconify.assert_called()
    
    # We mock on_closing so we don't accidentally trigger the os._exit(0) bomb in the test suite
    with patch.object(app_instance, "on_closing"):
        app_instance.quit_from_tray(icon_mock, item_mock)
        icon_mock.stop.assert_called()

def test_handle_window_close_minimize(app_instance):
    """Verifies hitting 'X' minimizes to tray if the setting is on."""
    app_instance.ui_state.minimize_to_tray.get.return_value = True
    app_instance.handle_window_close()
    app_instance.root.withdraw.assert_called()

def test_handle_window_close_quit(app_instance):
    """Verifies hitting 'X' shuts down if the setting is off."""
    app_instance.ui_state.minimize_to_tray.get.return_value = False
    app_instance.tray_icon = MagicMock()
    
    with patch.object(app_instance, "on_closing") as mock_close:
        app_instance.handle_window_close()
        app_instance.tray_icon.stop.assert_called()
        mock_close.assert_called()

@patch("ui.app_window.webbrowser.open")
def test_open_support_link(mock_open, app_instance):
    """Verifies the support link routes correctly."""
    app_instance.open_support_link()
    mock_open.assert_called_with("https://buymeacoffee.com/ProblematicSyntax")

@patch("ui.app_window.tk.Menu")
def test_build_and_show_context_menu(mock_menu, app_instance):
    """Verifies right-click menus are built and displayed at the cursor."""
    app_instance.build_context_menu()
    assert app_instance.context_menu is not None
    
    event = MagicMock()
    event.x_root = 100
    event.y_root = 200
    app_instance.current_view_mode = "grid" 
    
    app_instance.show_context_menu(event)
    app_instance.context_menu.tk_popup.assert_called_with(100, 200)

def test_save_tray_setting(app_instance):
    """Verifies the settings checkbox syncs to the database."""
    app_instance.ui_state.minimize_to_tray.get.return_value = False
    app_instance.save_tray_setting()
    assert app_instance.settings["minimize_to_tray"] is False
    app_instance.db.save_settings.assert_called_with(app_instance.settings)

def test_on_filter_change(app_instance):
    """Verifies that changing library filters safely pauses and resumes playback."""
    app_instance.playback.is_playing = True
    app_instance.on_filter_change()
    app_instance.playback_presenter.pause_audio.assert_called()
    app_instance.playback_presenter.resume_playback.assert_called()

@patch("ui.app_window.webbrowser.open")
def test_open_web_ui(mock_open, app_instance):
    """Verifies clicking 'Open Web UI' turns on the server and opens the browser."""
    app_instance.server_running = False
    app_instance.open_web_ui()
    app_instance.cloud_server_controller.toggle_web_server.assert_called()
    mock_open.assert_called_with("http://127.0.0.1:8000/desktop")

def test_toggle_sidebar_visibility(app_instance):
    """Verifies the sidebar drawer toggles correctly."""
    app_instance.top_split = MagicMock()
    app_instance.right_panel = MagicMock()
    
    # Test 1: Visible -> Hidden
    app_instance.top_split.panes.return_value = [str(app_instance.right_panel)]
    app_instance.toggle_sidebar_visibility()
    app_instance.top_split.forget.assert_called_with(app_instance.right_panel)
    
    # Test 2: Hidden -> Visible
    app_instance.top_split.panes.return_value = []
    app_instance.toggle_sidebar_visibility()
    app_instance.top_split.add.assert_called_with(app_instance.right_panel, weight=1)

@patch("ui.app_window.filedialog.asksaveasfilename")
@patch("ui.app_window.LibraryExporter")
def test_export_csv_worker(mock_exporter, mock_asksave, app_instance):
    """Verifies CSV generation fires."""
    mock_asksave.return_value = "/mock/export.csv"
    app_instance.export_csv_worker()
    mock_exporter.export_csv.assert_called_with("/mock/export.csv", app_instance.library_manager.local_library, app_instance.library_manager.cloud_items)

@patch("ui.app_window.filedialog.asksaveasfilename")
@patch("ui.app_window.LibraryExporter")
@patch("ui.app_window.webbrowser.open")
def test_export_html_worker(mock_open, mock_exporter, mock_asksave, app_instance):
    """Verifies HTML generation fires and opens in the browser."""
    mock_asksave.return_value = "/mock/export.html"
    app_instance.export_html_worker()
    mock_exporter.export_html.assert_called_with("/mock/export.html", app_instance.library_manager.local_library, app_instance.library_manager.cloud_items)
    mock_open.assert_called_with("/mock/export.html")

@patch("ui.app_window.messagebox.showwarning")
def test_manage_shelves_prompt_no_selection(mock_warn, app_instance):
    """Verifies the shelf manager refuses to open if nothing is selected."""
    app_instance.current_view_mode = "grid"
    app_instance._selected_grid_item = None
    app_instance.manage_shelves_prompt()
    mock_warn.assert_called()

@patch("ui.components.dialogs.open_shelf_management_window") # <--- Fixed Patch Target
def test_manage_shelves_prompt_success(mock_open_shelf, app_instance):
    """Verifies the shelf manager parses the ASIN and Title and passes it down."""
    app_instance.current_view_mode = "grid"
    app_instance._selected_grid_item = {'values': ["The Book", "Auth", "Ser", "Dur", "ASIN123"]}
    app_instance.manage_shelves_prompt()
    mock_open_shelf.assert_called_with(app_instance, "The Book", "ASIN123")

def test_on_item_select_list_mode(app_instance):
    """Verifies selecting an item updates the sidebar."""
    app_instance.current_view_mode = "list"
    app_instance.library_tree = MagicMock()
    app_instance.library_tree.focus.return_value = "row_1"
    app_instance.library_tree.item.return_value = {'values': ["Test Title", "Author Name", "Series", "Dur", "ASIN_X"]}
    
    app_instance.library_manager.local_library = {"/fake/file.m4b": {"title": "Test Title"}}
    app_instance.author_label = MagicMock()
    app_instance.cover_label = MagicMock()
    
    app_instance.on_item_select()
    
    app_instance.author_label.config.assert_called_with(text="Author Name")
    assert app_instance._selected_local_path == "/fake/file.m4b"
    app_instance.bookmarks_presenter.refresh_bookmarks_ui.assert_called_once()

@patch("ui.app_window.messagebox.askyesno")
def test_handle_action_download(mock_yesno, app_instance):
    """Verifies right-clicking a cloud item triggers the download queue."""
    mock_yesno.return_value = True
    app_instance.current_view_mode = "grid"
    app_instance._selected_grid_item = {'values': ["Cloud Book", "Auth", "Ser", "Dur", "ASIN123"]}
    app_instance.library_manager.local_library = {} # Not downloaded
    
    with patch.object(app_instance, "ensure_download_folder", return_value="/mock/dl"):
        app_instance.handle_action_on_selected("download")
        
        app_instance.import_session.add_queue_ui_row.assert_called_with("ASIN123", "Cloud Book")
        app_instance.download_manager.queue_download.assert_called_with("ASIN123", "Cloud Book", "/mock/dl", post_action="download")

@patch("os.path.exists")
def test_handle_action_play_local(mock_exists, app_instance):
    """Verifies right-clicking a local item safely triggers playback."""
    mock_exists.return_value = True
    app_instance.current_view_mode = "grid"
    app_instance._selected_grid_item = {'values': ["Local Book", "Auth", "Ser", "Dur", "ASIN123"]}
    app_instance.library_manager.local_library = {"/mock/file.m4b": {"title": "Local Book"}}
    
    app_instance.handle_action_on_selected("play")
    
    app_instance.playback_presenter.load_specific_file.assert_called_with("/mock/file.m4b")
    app_instance.playback_presenter.play_chapter.assert_called_once()

@patch("ui.app_window.messagebox.askyesno")
def test_remove_local_file(mock_yesno, app_instance):
    """Verifies removing an item deletes it from the library list but keeps the file."""
    mock_yesno.return_value = True
    app_instance.library_tree = MagicMock()
    app_instance.library_tree.selection.return_value = ["row_1"]
    app_instance.library_tree.item.return_value = {'values': ["The Book"]}
    app_instance.library_manager.local_library = {"/mock/file.m4b": {"title": "The Book"}}
    
    app_instance.remove_local_file()
    
    assert "/mock/file.m4b" not in app_instance.library_manager.local_library
    app_instance.db.save_local_db.assert_called_once()
    app_instance.library_presenter.refresh_library_ui.assert_called_once()

@patch("ui.app_window.filedialog.askdirectory")
def test_start_convert_thread_split(mock_askdir, app_instance):
    """Verifies clicking Convert on a book with chapters triggers the splitter."""
    mock_askdir.return_value = "/mock/out_folder"
    app_instance.library_manager.local_library = {
        "/mock/file.m4b": {
            "chapters": [{"id": 0, "tags": {"title": "Ch 1"}}, {"id": 1, "tags": {"title": "Ch 2"}}]
        }
    }
    
    app_instance.start_convert_thread(target_path="/mock/file.m4b")
    
    app_instance.conversion_manager.split_book.assert_called_with(
        "/mock/file.m4b", 
        "/mock/out_folder", 
        app_instance.library_manager.local_library["/mock/file.m4b"]["chapters"]
    )