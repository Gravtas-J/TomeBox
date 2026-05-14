import os
import traceback
import httpx
import tkinter as tk
from tkinter import messagebox
from ui.components.dialogs import open_pairing_window

class CloudServerController:
    def __init__(self, app):
        self.app = app

    def fetch_cloud_library(self):
        self.app.logger.info("DEBUG: fetch_cloud_library method started executing.")
        
        if not self.app.api_client.auth:
            self.app.logger.info("DEBUG: fetch_cloud_library aborted - self.app.api_client.auth is missing or None.")
            messagebox.showwarning("Not Logged In", "Please login via the Settings tab first.")
            return

        self.app.logger.info("DEBUG: self.app.api_client.auth verified. Launching fetch_library_worker thread...")
        
        self.app.ui_state.dl_status.set("Fetching data from Amazon... Please wait.")
        
        self.app.thread_pool.submit(self.fetch_library_worker)

    def fetch_library_worker(self):
        try:
            self.app.logger.info("Querying Audible Library API...")
            
            # Delegate entirely to the LibraryManager
            self.app.library_manager.fetch_cloud_library()
            
            self.app.logger.info(f"Successfully retrieved {len(self.app.library_manager.cloud_items)} library items.")

            self.app.root.after(0, self.app.library_presenter.refresh_library_ui)
            self.app.root.after(0, lambda: self.app.action_router.reset_ui_if_idle())

            self.app.metadata_manager.sync_missing_covers(
                on_complete_cb=lambda: self.app.root.after(0, lambda: self.app.library_presenter.refresh_library_ui() if self.app.current_view_mode == 'grid' else None)
            )
            
        except httpx.ConnectError:
            self.app.logger.error("Network offline during library sync.")
            self.app.root.after(0, lambda: messagebox.showerror("Connection Error", "Could not connect to Audible servers. Check your internet connection."))
        except Exception as e:
            if "401" in str(e) or "unauthorized" in str(e).lower() or "Not authenticated" in str(e):
                self.app.logger.error(f"Audible API rejected the request: {e}")
                self.app.root.after(0, lambda: messagebox.showerror("Audible API Error", "Your session may have expired. Please log in again via Settings."))
            else:
                self.app.logger.error(f"Unhandled exception in library worker: {e}\n{traceback.format_exc()}")
                self.app.root.after(0, lambda: messagebox.showerror("Library Error", "An unexpected error occurred while fetching your library."))
        finally:
            self.app.root.after(0, self.app.action_router.reset_ui_if_idle)

    def run_background_sync(self):
        self.app.thread_pool.submit(
            self.app.library_manager.silent_cloud_sync, 
            self.app.logger, 
            lambda msg: self.app.root.after(0, lambda: self.app.ui_state.dl_status.set(msg)), 
            lambda: self.app.root.after(0, self.app.library_presenter.refresh_library_ui)
        )
        # Schedule the next check in 15 minutes (900000 milliseconds)
        self.app.root.after(900000, self.run_background_sync)

    def toggle_web_server(self):
        def on_started():
            self.app.server_running = True
            self.app.root.after(0, lambda: self.app.file_menu.entryconfigure("Enable Web Server", label="Disable Web Server"))
            self.app.root.after(0, lambda: self.app.file_menu.entryconfigure("Show Pairing Info", state=tk.NORMAL))
            self.app.root.after(0, lambda: open_pairing_window(self.app))
            
        def on_stopped():
            self.app.server_running = False
            self.app.root.after(0, lambda: self.app.file_menu.entryconfigure("Disable Web Server", label="Enable Web Server"))
            self.app.root.after(0, lambda: self.app.file_menu.entryconfigure("Show Pairing Info", state=tk.DISABLED))
            
            self.app.root.after(0, lambda: messagebox.showinfo("Server Stopped", "The companion server has been safely disabled."))
            
        def on_error(title, msg):
            self.app.root.after(0, lambda: messagebox.showerror(title, msg))

        self.app.system_manager.toggle_web_server(
            app_instance=self.app,
            on_started_cb=on_started,
            on_stopped_cb=on_stopped,
            on_error_cb=on_error
        )
        
    def add_firewall_rule_prompt(self):
        if os.name != 'nt':
            messagebox.showinfo("Not Applicable", "Firewall management is only automated on Windows.")
            return
            
        if self.app.system_manager._is_firewall_rule_installed():
            messagebox.showinfo("Already Installed", "The 'TomeBox Web Server' firewall rule is already active on your system.")
            return
            
        if messagebox.askyesno(
            "Add Firewall Rule", 
            "This will require Administrator privileges to add the 'TomeBox Web Server' rule to Windows Defender Firewall.\n\n"
            "This allows your mobile device to communicate with the TomeBox companion server over your local Wi-Fi network.\n\n"
            "Do you want to continue?"
        ):
            success = self.app.system_manager._add_firewall_rule()
            if success:
                messagebox.showinfo("Success", "Firewall rule added successfully.")
            else:
                messagebox.showerror("Action Failed", "Failed to add the firewall rule. You may have declined the admin prompt.")

    def remove_firewall_rule_prompt(self):
        if os.name != 'nt':
            messagebox.showinfo("Not Applicable", "Firewall management is only automated on Windows.")
            return
            
        if messagebox.askyesno(
            "Remove Firewall Rule", 
            "This will require Administrator privileges to remove the 'TomeBox Web Server' rule from Windows Defender Firewall.\n\n"
            "If you restart the Web Server later, you will be prompted to approve the rule again.\n\n"
            "Do you want to continue?"
        ):
            success = self.app.system_manager.remove_firewall_rule()
            if success:
                messagebox.showinfo("Success", "Firewall rule removed successfully.")
            else:
                messagebox.showerror("Action Failed", "Failed to remove the firewall rule. You may have declined the admin prompt, or the rule did not exist.")