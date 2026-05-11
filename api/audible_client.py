import os
import requests
try:
    import audible
    from audible.aescipher import decrypt_voucher_from_licenserequest
    from httpx import HTTPStatusError, RequestError
except ImportError:
    raise ImportError(
        "CRITICAL: The 'audible' package is missing. "
        "Please install it by running: pip install audible"
    )
import time


class RateLimitError(Exception): pass
class APIUnavailableError(Exception): pass

def find_url_in_response(d):
    """Recursively hunts for an offline_url anywhere in a nested API response."""
    if isinstance(d, dict):
        if "offline_url" in d:
            return d["offline_url"]
        for v in d.values():
            res = find_url_in_response(v)
            if res:
                return res
    elif isinstance(d, list):
        for item in d:
            res = find_url_in_response(item)
            if res:
                return res
    return None


def find_key_iv_in_voucher(d):
    """Recursively hunts for a (key, iv) pair anywhere in a decrypted voucher."""
    if isinstance(d, dict):
        if "key" in d and "iv" in d:
            return d["key"], d["iv"]
        for val in d.values():
            k, i = find_key_iv_in_voucher(val)
            if k and i:
                return k, i
    elif isinstance(d, list):
        for val in d:
            k, i = find_key_iv_in_voucher(val)
            if k and i:
                return k, i
    return None, None

class AudibleClient:
    def __init__(self, logger=None):
        self.auth = None
        self.logger = logger or print
    def _request_with_backoff(self, request_func, *args, max_retries=3, base_delay=2, **kwargs):
        """Executes an API call with exponential backoff for 429 errors."""
        for attempt in range(max_retries):
            try:
                return request_func(*args, **kwargs)
            except Exception as e:
                # Safely extract status code regardless of the HTTP library used
                status_code = getattr(getattr(e, 'response', None), 'status_code', None)
                err_str = str(e).lower()
                
                # 1. Terminal Failure: 401 Unauthorized
                if status_code == 401:
                    if hasattr(self, 'logger') and self.logger and self.logger != print:
                        self.logger("Terminal Error: 401 Unauthorized. Session expired.")
                    raise APIUnavailableError("Session expired. Please log in again.") from e
                
                # 2. Recoverable Failure: 429 Too Many Requests
                if status_code == 429 or "429" in err_str or "too many requests" in err_str:
                    if attempt == max_retries - 1:
                        raise RateLimitError("Audible API rate limit reached (HTTP 429).")
                    
                    if hasattr(self, 'logger') and self.logger and self.logger != print:
                        self.logger(f"Rate limited. Retrying in {base_delay * (2 ** attempt)}s...")
                    time.sleep(base_delay * (2 ** attempt))
                    continue
                
                # 3. Terminal Failure: 500-level Server Errors
                if status_code in (403, 500, 502, 503, 504) or any(code in err_str for code in ["403", "500", "502", "503", "504"]):
                    raise APIUnavailableError(f"Audible API unavailable (HTTP {status_code or 'Error'}).") from e
                
                # 4. Unhandled Exceptions
                raise e

    def is_authenticated(self):
        return self.auth is not None

    def load_auth_from_file(self, filepath):
        if os.path.exists(filepath):
            self.auth = audible.Authenticator.from_file(filepath)
            return True
        return False

    def save_auth_to_file(self, filepath):
        if self.auth:
            self.auth.to_file(filepath)

    def login_with_browser(self, locale, url_callback):
        self.auth = audible.Authenticator.from_login_external(
            locale=locale,
            login_url_callback=url_callback
        )
        return self.is_authenticated()

    def get_activation_bytes(self):
        if self.auth:
            try:
                # 1. First attempt
                return self.auth.get_activation_bytes()
            except ValueError as e:
                # 2. Trap the specific Audible backend propagation bug
                if "data wrong" in str(e):
                    print("[API] Audible server delay: Retrying activation bytes in 3 seconds...")
                    time.sleep(3)
                    try:
                        # 3. Second attempt
                        return self.auth.get_activation_bytes()
                    except ValueError:
                        print("[API] Failed to fetch activation bytes. .aaxc downloads will still work.")
                        return ""
                else:
                    # If it's a different ValueError, raise it normally
                    raise e
        return ""
    
    def fetch_library(self):
        if not self.auth:
            raise Exception("Not authenticated")
        client = audible.Client(auth=self.auth)
        
        all_items = []
        current_page = 1
        page_size = 1000 
        
        while True:
            resp = self._request_with_backoff(
                client.get, 
                "1.0/library", 
                response_groups="product_desc,product_attrs,series,contributors,media", 
                num_results=page_size,
                page=current_page
            )
            
            items = resp.get("items", [])
            if not items:
                break
                
            all_items.extend(items)
            
            if len(items) < page_size:
                break
                
            current_page += 1
            
        return all_items

    def _handle_api_error(self, e):
        """Standardizes exception handling across Audible API calls."""
        if isinstance(e, HTTPStatusError):
            status = e.response.status_code
            if status == 429:
                raise RateLimitError("Audible API rate limit reached (HTTP 429).")
            elif status in (403, 500, 502, 503, 504):
                raise APIUnavailableError(f"Audible API unavailable (HTTP {status}).")
            raise e
            
        err_str = str(e).lower()
        if "429" in err_str or "too many requests" in err_str:
            raise RateLimitError("Audible API rate limit reached (HTTP 429).")
        elif any(code in err_str for code in ["403", "500", "502", "503", "504"]):
            raise APIUnavailableError(f"Audible API unavailable: {e}")
        raise e

    def fetch_product_metadata(self, asin, detailed=False):
        if not self.auth:
            raise Exception("Not authenticated")
        client = audible.Client(auth=self.auth)
        rg = "product_desc,product_attrs,contributors,media,series" if detailed else "media,product_attrs"
        
        resp = self._request_with_backoff(client.get, f"1.0/catalog/products/{asin}", response_groups=rg)
        return resp.get("product", {})

    def search_catalog(self, query, num_results=5):
        if not self.auth:
            raise Exception("Not authenticated")
        client = audible.Client(auth=self.auth)
        
        resp = self._request_with_backoff(
            client.get, 
            "1.0/catalog/products", 
            keywords=query, 
            num_results=num_results, 
            response_groups="product_desc,product_attrs,contributors,media"
        )
        return resp.get("products", [])

    def get_download_license(self, asin):
        clean_asin = str(asin).zfill(10)
        if not self.auth:
            raise Exception("Not authenticated")
        client = audible.Client(auth=self.auth)
        body = {"drm_type": "Adrm", "consumption_type": "Download"}
        resp = client.post(f"1.0/content/{clean_asin}/licenserequest", body=body)
        
        download_url = find_url_in_response(resp)
        if not download_url:
            raise Exception("Could not find the offline download URL in the API response.")

        a_key, a_iv = None, None
        
        content_metadata = resp.get("content_license", {}).get("content_metadata", {})
        offline_key = content_metadata.get("content_key", {}).get("offline_key")
        
        if offline_key:
            import rsa
            import base64
            priv_pem = getattr(self.auth, "rsa_private_key", None) or getattr(self.auth, "_rsa_private_key", None)
            if priv_pem:
                priv_key = rsa.PrivateKey.load_pkcs1(priv_pem.encode('utf-8'))
                decrypted = rsa.decrypt(base64.b64decode(offline_key), priv_key)
                a_key = decrypted[:16].hex()
                a_iv = decrypted[16:].hex()
        else:
            decrypted_voucher = decrypt_voucher_from_licenserequest(self.auth, resp)
            a_key, a_iv = find_key_iv_in_voucher(decrypted_voucher)

        return download_url, a_key, a_iv
    
    def get_drm_flags(self, filepath, local_data, active_profile, auth_bytes, data_dir, logger=None):
        a_key = local_data.get("audible_key")
        a_iv = local_data.get("audible_iv")
        if a_key and a_iv:
            return ["-audible_key", a_key, "-audible_iv", a_iv]

        owner = local_data.get("owner", active_profile)
        if owner == active_profile and auth_bytes:
            return ["-activation_bytes", auth_bytes]
            
        owner_auth_path = os.path.join(data_dir, f"auth_{owner}.json")
        if os.path.exists(owner_auth_path):
            try:
                temp_auth = audible.Authenticator.from_file(owner_auth_path)
                dynamic_bytes = temp_auth.get_activation_bytes()
                if dynamic_bytes:
                    return ["-activation_bytes", dynamic_bytes]
            except Exception as e:
                if logger: logger.warning(f"Failed to dynamically load auth for {owner}: {e}")
        
        return ["-activation_bytes", auth_bytes] if auth_bytes else []