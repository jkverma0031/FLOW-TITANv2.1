# titan/runtime/plugins/registry.py
from __future__ import annotations
import threading
import logging
from typing import Dict, Optional, List, Any

logger = logging.getLogger(__name__)

class PluginRegistry:
    """
    Thread-safe plugin registry.
    Plugins are stored by name and should be instances of BasePlugin.
    """
    _instance = None
    _lock = threading.Lock()

    def __init__(self):
        self._plugins: Dict[str, Any] = {}
        self._rw = threading.RLock()

    @classmethod
    def instance(cls) -> "PluginRegistry":
        with cls._lock:
            if cls._instance is None:
                cls._instance = PluginRegistry()
            return cls._instance

    def register(self, name: str, plugin: Any, *, overwrite: bool = False):
        with self._rw:
            if name in self._plugins and not overwrite:
                raise ValueError(f"Plugin already registered: {name}")
            self._plugins[name] = plugin
            logger.info("PluginRegistry: registered plugin %s", name)

    def unregister(self, name: str):
        with self._rw:
            if name in self._plugins:
                del self._plugins[name]
                logger.info("PluginRegistry: unregistered plugin %s", name)

    def get(self, name: str) -> Optional[Any]:
        with self._rw:
            return self._plugins.get(name)

    def list(self) -> List[str]:
        with self._rw:
            return list(self._plugins.keys())

    def all(self) -> Dict[str, Any]:
        with self._rw:
            return dict(self._plugins)

# Convenience module-level functions
def register_plugin(name: str, plugin: Any, *, overwrite: bool = False):
    PluginRegistry.instance().register(name, plugin, overwrite=overwrite)

def unregister_plugin(name: str):
    PluginRegistry.instance().unregister(name)

def get_plugin(name: str) -> Optional[Any]:
    return PluginRegistry.instance().get(name)

def list_plugins() -> List[str]:
    return PluginRegistry.instance().list()
