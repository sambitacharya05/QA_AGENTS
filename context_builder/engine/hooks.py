import logging
from typing import Callable, Dict, List, Any

log = logging.getLogger(__name__)


class HookManager:
    """Manages event hooks and triggers for the Context Ingestion & Graph Creation Lifecycle."""

    def __init__(self):
        self._listeners: Dict[str, List[Callable[..., None]]] = {
            "on_parse_start": [],         # Called when workspace scanning begins. Args: (workspace_path: str)
            "on_document_parsed": [],     # Called when a file finishes parsing. Args: (file_path: str, result: dict)
            "on_entity_discovered": [],   # Called when a node entity is extracted. Args: (entity: dict)
            "on_extraction_complete": [], # Called when the graph is fully compiled. Args: (db_path: str)
        }

    def subscribe(self, event_name: str, callback: Callable[..., None]) -> None:
        """Subscribe a callback function to a lifecycle event."""
        if event_name not in self._listeners:
            raise ValueError(
                f"Unknown lifecycle event: {event_name!r}. "
                f"Supported: {list(self._listeners.keys())}"
            )
        self._listeners[event_name].append(callback)

    def trigger(self, event_name: str, *args: Any, **kwargs: Any) -> None:
        """Trigger all callbacks registered for *event_name*."""
        if event_name not in self._listeners:
            return
        for callback in self._listeners[event_name]:
            try:
                callback(*args, **kwargs)
            except Exception as exc:
                log.exception(
                    "Hook callback failed for event %r: %s", event_name, exc
                )


# Global instance for easy access across module boundaries
lifecycle_hooks = HookManager()
