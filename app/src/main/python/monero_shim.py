"""
Monero Liquid Pro - Python Shim & Android Bridge Wrapper
Complies strictly with the HARD RULE: zero modifications to monero_liquid.py.
All adaptations, caching, and persistence happen in this wrapper.
"""
import sys
import os
import json
import time

_initialized = False
_files_dir = ""
_bridge = None
_network_cache_path = ""
_stats_cache_path = ""
ml = None


def initialize(files_dir: str):
    """
    Sets up the Android environment before importing monero_liquid:
    1. Sets sys.frozen and sys.executable to point to writable app storage.
    2. Imports the fake webview module so import webview succeeds.
    3. Imports monero_liquid as a module without executing its __main__ block.
    4. Restores cached network difficulty so it survives restarts.
    5. Monkeypatches HashVaultEngine to persist future network data.
    """
    global _initialized, _files_dir, _bridge, _network_cache_path, _stats_cache_path, ml
    if _initialized:
        return

    _files_dir = files_dir
    _network_cache_path = os.path.join(files_dir, "network_cache.json")
    _stats_cache_path = os.path.join(files_dir, "stats_cache.json")

    # Step 3: Persistence without touching monero_liquid.py
    # monero_liquid uses: if sys.frozen: BASE_DIR = dirname(sys.executable)
    sys.frozen = True
    sys.executable = os.path.join(files_dir, "app")

    # Ensure fake webview module is loaded
    if "webview" not in sys.modules:
        import webview

    # Import the original file byte-for-byte as a module
    import monero_liquid
    ml = monero_liquid

    # Step 4: Restore last known network difficulty on startup
    if os.path.exists(_network_cache_path):
        try:
            with open(_network_cache_path, "r", encoding="utf-8") as f:
                cached_net = json.load(f)
                if isinstance(cached_net, dict) and cached_net.get("diff", 0) > 0:
                    cached_net["live"] = False
                    ml.HashVaultEngine._last_network = cached_net
                    ml.logger.info("[SHIM] Restored previous network difficulty from cache")
        except Exception as e:
            ml.logger.warning(f"[SHIM] Error restoring network cache: {e}")

    # Wrap HashVaultEngine.fetch_network to persist successful fetches
    orig_fetch_network = ml.HashVaultEngine.fetch_network

    @classmethod
    def wrapped_fetch_network(cls, pool_data):
        result = orig_fetch_network(pool_data)
        if result and result.get("live"):
            try:
                with open(_network_cache_path, "w", encoding="utf-8") as f:
                    json.dump(result, f, indent=2)
            except Exception as e:
                ml.logger.warning(f"[SHIM] Error saving network cache: {e}")
        return result

    ml.HashVaultEngine.fetch_network = wrapped_fetch_network

    # Instantiate WebBridge with the saved wallet
    saved_wallet = ml.load_saved_wallet()
    _bridge = ml.WebBridge(saved_wallet)
    _initialized = True
    ml.logger.info("[SHIM] Monero Liquid Python Shim initialized successfully")


def get_html_content() -> str:
    """Generates the HTML string using monero_liquid's own generate_html()."""
    global ml
    saved_wallet = ml.load_saved_wallet()
    monero_b64 = ml.get_image_base64("monero")
    coin_b64 = ml.get_image_base64("coin")
    return ml.generate_html(monero_b64, coin_b64, saved_wallet)


def get_initial_wallet() -> str:
    """Returns the currently saved wallet or fallback wallet."""
    global _bridge, ml
    if _bridge:
        return _bridge.get_initial_wallet()
    if ml:
        return ml.load_saved_wallet()
    return ""


def get_cached_stats_json() -> str:
    """Returns the cached stats JSON string if available, or empty string."""
    global _stats_cache_path
    if os.path.exists(_stats_cache_path):
        try:
            with open(_stats_cache_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            pass
    return ""


def fetch_stats(raw_address: str) -> str:
    """
    Fetches stats via WebBridge.
    Caches successful results. If live request fails, falls back to cache.
    Returns JSON string for Kotlin to dispatch to JavaScript.
    """
    global _bridge, _stats_cache_path, ml
    try:
        res = _bridge.get_stats(raw_address)
        if res and not res.get("error"):
            payload = dict(res)
            payload["_cached_at"] = int(time.time())
            try:
                with open(_stats_cache_path, "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2)
            except Exception as e:
                ml.logger.warning(f"[SHIM] Error caching stats: {e}")
            return json.dumps(res)
        else:
            cached = get_cached_stats_json()
            if cached:
                ml.logger.info("[SHIM] Request returned error/empty, using cached stats")
                return cached
            return json.dumps(res if res else {"error": "اطلاعاتی دریافت نشد."})
    except Exception as e:
        ml.logger.error(f"[SHIM] Live fetch failed: {e}")
        cached = get_cached_stats_json()
        if cached:
            ml.logger.info("[SHIM] Live fetch exception, using cached stats")
            return cached
        return json.dumps({"error": f"خطا در برقراری ارتباط با استخر: {str(e)}"})
