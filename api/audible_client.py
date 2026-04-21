import os
import requests
try:
    import audible
    from audible.aescipher import decrypt_voucher_from_licenserequest
except ImportError:
    pass

class AudibleClient:
    def __init__(self):
        self.auth = None

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
            return self.auth.get_activation_bytes()
        return ""

    def fetch_library(self):
        if not self.auth:
            raise Exception("Not authenticated")
        client = audible.Client(auth=self.auth)
        resp = client.get("1.0/library", response_groups="product_desc,product_attrs,series,contributors,media", num_results=1000)
        return resp.get("items", [])

    def fetch_product_metadata(self, asin, detailed=False):
        if not self.auth:
            raise Exception("Not authenticated")
        client = audible.Client(auth=self.auth)
        rg = "product_desc,product_attrs,contributors,media,series" if detailed else "media,product_attrs"
        resp = client.get(f"1.0/catalog/products/{asin}", response_groups=rg)
        return resp.get("product", {})

    def search_catalog(self, query, num_results=5):
        if not self.auth:
            raise Exception("Not authenticated")
        client = audible.Client(auth=self.auth)
        resp = client.get("1.0/catalog/products", title=query, num_results=num_results, response_groups="product_desc,product_attrs,contributors")
        return resp.get("products", [])

    def get_download_license(self, asin):
        clean_asin = str(asin).zfill(10)
        if not self.auth:
            raise Exception("Not authenticated")
        client = audible.Client(auth=self.auth)
        body = {"drm_type": "Adrm", "consumption_type": "Download"}
        resp = client.post(f"1.0/content/{clean_asin}/licenserequest", body=body)
        
        def find_url(d):
            if isinstance(d, dict):
                if "offline_url" in d: return d["offline_url"]
                for k, v in d.items():
                    res = find_url(v)
                    if res: return res
            elif isinstance(d, list):
                for item in d:
                    res = find_url(item)
                    if res: return res
            return None
            
        download_url = find_url(resp)
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
            def find_key_iv(d):
                k, i = None, None
                if isinstance(d, dict):
                    if "key" in d and "iv" in d: return d["key"], d["iv"]
                    for val in d.values():
                        k, i = find_key_iv(val)
                        if k and i: return k, i
                elif isinstance(d, list):
                    for val in d:
                        k, i = find_key_iv(val)
                        if k and i: return k, i
                return k, i
            a_key, a_iv = find_key_iv(decrypted_voucher)

        return download_url, a_key, a_iv