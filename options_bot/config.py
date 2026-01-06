import json
import yaml
import os
import time
import threading
import logging
from typing import Any, Dict
from utils import parse_time_value

# Prioritize YAML for better comment support
YAML_FILE = "config.yaml"
JSON_FILE = "config.json"

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
        self._active_file = YAML_FILE
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
        """Loads configuration from YAML (priority) or JSON file."""
        # Determine which file to use
        if os.path.exists(YAML_FILE):
            self._active_file = YAML_FILE
        elif os.path.exists(JSON_FILE):
            self._active_file = JSON_FILE
        else:
            self.logger.error("No configuration file (config.yaml or config.json) found!")
            return

        try:
            with self._file_lock:
                with open(self._active_file, 'r') as f:
                    if self._active_file.endswith('.yaml'):
                        new_config = yaml.safe_load(f)
                    else:
                        new_config = json.load(f)
                    
                # Basic Validation (ensure keys exist)
                self._validate_structure(new_config)
                
                self._data = new_config
                self._last_modified_time = os.path.getmtime(self._active_file)
                self.logger.info(f"Configuration loaded from {self._active_file}")
                
        except Exception as e:
            self.logger.error(f"Error loading {self._active_file}: {e}")

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
                    
                    # Parse time values (gap, wait, etc) but skip timeframes
                    if ("minutes" in key or "time" in key.lower()) and "frame" not in key.lower():
                        try:
                            return parse_time_value(value)
                        except (ValueError, TypeError):
                            pass  # Return as-is if parsing fails
                    
                    return value
                except (KeyError, TypeError):
                    return default
            
            value = self._data.get(key, default)
            
            if ("minutes" in key or "time" in key.lower()) and "frame" not in key.lower():
                try:
                    return parse_time_value(value)
                except (ValueError, TypeError):
                    pass
            
            return value

    def set(self, key: str, value: Any) -> None:
        """Runtime update of config (memory only)."""
        with self._file_lock:
            pass

    def _monitor_changes(self):
        """Watcher thread to reload config when file changes."""
        while not self._stop_monitor:
            time.sleep(5) 
            try:
                if os.path.exists(self._active_file):
                    current_mtime = os.path.getmtime(self._active_file)
                    if current_mtime > self._last_modified_time:
                        self.logger.info(f"Config file {self._active_file} changed. Reloading...")
                        self.load_config()
            except Exception as e:
                self.logger.error(f"Error monitoring config file: {e}")

# Global instance
config = Config()
