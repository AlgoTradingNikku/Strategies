import json
import os
import time
import threading
import logging
from typing import Any, Dict

CONFIG_FILE = "config.json"

class Config:
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(Config, cls).__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
            
        self._data: Dict[str, Any] = {}
        self._last_modified_time = 0
        self._file_lock = threading.Lock()
        self.logger = logging.getLogger("Config")
        
        # Initial Load
        self.load_config()
        
        # Start Auto-Reload Monitor
        self._stop_monitor = False
        self._monitor_thread = threading.Thread(target=self._monitor_changes, daemon=True)
        self._monitor_thread.start()
        
        self._initialized = True

    def load_config(self) -> None:
        """Loads configuration from JSON file."""
        if not os.path.exists(CONFIG_FILE):
            self.logger.error(f"Config file {CONFIG_FILE} not found!")
            return

        try:
            with self._file_lock:
                with open(CONFIG_FILE, 'r') as f:
                    new_config = json.load(f)
                    
                # Basic Validation (ensure keys exist)
                self._validate_structure(new_config)
                
                self._data = new_config
                self._last_modified_time = os.path.getmtime(CONFIG_FILE)
                self.logger.info("Configuration loaded successfully.")
                
        except json.JSONDecodeError as e:
            self.logger.error(f"Error decoding JSON config: {e}")
        except Exception as e:
            self.logger.error(f"Error loading config: {e}")

    def _validate_structure(self, config: Dict) -> None:
        """Ensures essential keys are present. Does not check logic, just structure."""
        required_sections = ["api", "strategy_settings", "risk_management"]
        for section in required_sections:
            if section not in config:
                self.logger.warning(f"Config missing section: {section}")

    def get(self, key: str, default: Any = None) -> Any:
        """
        Thread-safe retrieval of config values.
        Supports dotted access: config.get("risk_management.stop_loss_pct")
        """
        with self._file_lock:
            if "." in key:
                keys = key.split(".")
                value = self._data
                try:
                    for k in keys:
                        value = value[k]
                    return value
                except (KeyError, TypeError):
                    return default
            return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """
        Runtime update of config (Does NOT write to file, only memory).
        For persistent changes, edit the JSON file.
        """
        with self._file_lock:
            # Complex logic to set nested keys could go here, 
            # but for now we mainly read from file.
            pass

    def _monitor_changes(self):
        """Watcher thread to reload config when file changes."""
        while not self._stop_monitor:
            time.sleep(5) # Check every 5 seconds
            try:
                if os.path.exists(CONFIG_FILE):
                    current_mtime = os.path.getmtime(CONFIG_FILE)
                    if current_mtime > self._last_modified_time:
                        self.logger.info("Config file changed. Reloading...")
                        self.load_config()
            except Exception as e:
                self.logger.error(f"Error monitoring config file: {e}")

# Global instance
config = Config()
