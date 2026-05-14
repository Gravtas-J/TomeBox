import os
import threading
import traceback
import webbrowser
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog

class AuthController:
    def __init__(self, app):
        self.app = app

    def auto_load_auth(self):
        self.app.logger.info("DEBUG: auto_load_auth fired from startup timer.")
        if self.app.api_client.load_auth_from_file(self.app.auth_save_path):
            activation_bytes = self.app.api_client.get_activation_bytes()
            self.app.ui_state.auth_bytes.set(activation_bytes)
            self.app.logger.info(f"Session loaded automatically. Activation Bytes: {activation_bytes}")
            
            # Reset filters before fetch so UI shows everything when worker completes
            self.app.ui_state.filter.set("All")
            self.app.ui_state.shelf_filter.set("All Shelves")
            self.app.ui_state.search.set("")
            
            self.app.fetch_cloud_library()
        else:
            self.app.logger.info("No saved session found. Please log in.")

    def load_auth_file_prompt(self):
        filepath = filedialog.askopenfilename(filetypes=[("JSON Auth File", "*.json")], title="Select Audible Auth File")
        if not filepath: return

        self.app.logger.info(f"Loading auth from external file: {filepath}")
        try:
            if self.app.api_client.load_auth_from_file(filepath):
                activation_bytes = self.app.api_client.get_activation_bytes()
                self.app.ui_state.auth_bytes.set(activation_bytes)
                self.app.logger.info(f"Activation Bytes Received: {activation_bytes}")
                self.app.api_client.save_auth_to_file(self.app.auth_save_path)
                
                messagebox.showinfo("Success", "Auth file loaded! You can now fetch your library.")
                self.app.fetch_cloud_library()
        except Exception as e:
            self.app.logger.error(f"ERROR: {traceback.format_exc()}")
            messagebox.showerror("Error", "Could not load auth file. Check the log.")

    def start_browser_login_thread(self):
        if hasattr(self.app, 'browser_login_btn') and self.app.browser_login_btn and self.app.browser_login_btn.winfo_exists():
            self.app.browser_login_btn.config(text="Connecting...", state=tk.DISABLED)
        self.app.thread_pool.submit(self.browser_login_worker, self.app.ui_state.locale.get())

    def browser_login_worker(self, locale):
        self.app.logger.info(f"Starting external browser login for region: {locale}")
        
        def custom_login_callback(login_url):
            self.app.logger.info("Opening default web browser...")
            webbrowser.open(login_url)
            
            result = [None]
            event = threading.Event()
            
            def ask_user_for_url():
                msg = (
                    "1. Your web browser should have opened.\n"
                    "2. Log in to Amazon / Audible.\n"
                    "3. Once logged in, you will land on a blank or 'Page Not Found' error page.\n\n"
                    "4. Copy the ENTIRE URL from your browser's address bar and paste it below:"
                )
                res = simpledialog.askstring("Audible Login Authorization", msg, parent=self.app.root)
                result[0] = res
                event.set()
                
            self.app.root.after(0, ask_user_for_url)
            event.wait()
            
            if not result[0]:
                raise Exception("Authentication cancelled by user.")
                
            return result[0].strip()

        try:
            self.app.logger.info("Waiting for user to complete browser login and paste URL...")
            if self.app.api_client.login_with_browser(locale, custom_login_callback):
                activation_bytes = self.app.api_client.get_activation_bytes()
                
                self.app.root.after(0, self.app.ui_state.auth_bytes.set, activation_bytes)
                self.app.logger.info(f"Activation Bytes Received: {activation_bytes}")
                
                self.app.api_client.save_auth_to_file(self.app.auth_save_path)
                self.app.logger.info(f"Session saved locally to {self.app.auth_save_path}")

                self.app.root.after(0, lambda: messagebox.showinfo("Success", "Connected to Audible!"))
                self.app.ui_state.filter.set("All")
                self.app.ui_state.shelf_filter.set("All Shelves")
                self.app.ui_state.search.set("")
                self.app.root.after(0, self.app.fetch_cloud_library)
                
        except Exception as e:
            error_trace = traceback.format_exc()
            self.app.logger.error("ERROR DURING LOGIN:")
            self.app.logger.error(error_trace)
            self.app.root.after(0, lambda: messagebox.showerror("Login Failed", str(e)))
            
        finally:
            self.app.logger.info("Login thread terminated.")
            def restore_btn():
                if hasattr(self.app, 'browser_login_btn') and self.app.browser_login_btn and self.app.browser_login_btn.winfo_exists():
                    self.app.browser_login_btn.config(text="Login via Browser", state=tk.NORMAL)
            self.app.root.after(0, restore_btn)

    def add_new_profile(self):
        new_name = simpledialog.askstring("New Profile", "Enter a name for the new profile:")
        if new_name and new_name not in self.app.profiles_list:
            self.app.profiles_list.append(new_name)
            self.app.settings["profiles"] = self.app.profiles_list
            self.app.profile_combo.config(values=self.app.profiles_list)
            self.app.profile_combo.set(new_name)
            self.switch_profile()

    def switch_profile(self, event=None):
        selected = self.app.profile_combo.get()
        self.app.active_profile = selected
        self.app.settings["active_profile"] = selected
        self.app.db.save_settings(self.app.settings)
        
        # Need to dynamically fetch path via app.db to stay aligned with the new structure
        self.app.auth_save_path = self.app.db.get_auth_path(self.app.active_profile)
        self.app.cloud_cache_path = self.app.db.get_cloud_cache_path(self.app.active_profile)
        
        # Clear current session
        self.app.api_client.auth = None
        self.app.ui_state.auth_bytes.set("")
        
        # We need to reach into library_manager to load the correct cache
        self.app.library_manager.cloud_items = self.app.library_manager.load_cloud_cache(self.app.active_profile)
        
        # Try to load the new profile's auth file
        self.auto_load_auth()
        self.app.refresh_library_ui()