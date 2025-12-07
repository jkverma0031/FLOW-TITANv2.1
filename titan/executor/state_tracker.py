# Path: titan/executor/state_tracker.py
from __future__ import annotations
from typing import Dict, Any, Optional
import time
import logging

logger = logging.getLogger(__name__)

class StateTracker:
    """
    Manages the persistent state of all nodes during a single Plan execution.
    This acts as the single source of truth for node results, status, and metadata, 
    essential for condition evaluation and data chaining (Part 3, §5.8).
    """
    
    def __init__(self):
        # Stores state by node ID: {node_id: {status: str, result: dict, ...}}
        self._state: Dict[str, Dict[str, Any]] = {}

    def initialize_node_state(self, node_id: str, name: Optional[str] = None):
        """
        FIX: Implements the required method to initialize a node's state to PENDING.
        Called by the Scheduler at the start of execution.
        """
        if node_id not in self._state:
            self._state[node_id] = {
                'id': node_id,
                'name': name,
                'status': 'pending',
                'started_at': None,
                'finished_at': None,
                'result': None,
                'error': None,
                'type': 'node', # Placeholder, should be set by the scheduler/node type
            }
        
    def update_node_state(self, node_id: str, **kwargs):
        """Updates the state of a specific node."""
        if node_id not in self._state:
            logger.warning(f"Attempted to update state for uninitialized node: {node_id}")
            self._state[node_id] = {'id': node_id, 'status': 'unknown'}
            
        self._state[node_id].update(kwargs)
        
        if kwargs.get('status') in ['completed', 'failed', 'cancelled'] and self._state[node_id].get('finished_at') is None:
             self._state[node_id]['finished_at'] = time.time()

    def get_state(self, node_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves the current state dictionary for a node."""
        return self._state.get(node_id)

    def get_all_states(self) -> Dict[str, Dict[str, Any]]:
        """Returns a snapshot of all node states."""
        return self._state
        
    # Helper method for testing and dependencies (e.g., T4 looking up T3's result)
    def get_state_by_task_name(self, task_name: str) -> Optional[Dict[str, Any]]:
        """Retrieves the state of a node based on its friendly task name."""
        return next((s for s in self._state.values() if s.get('name') == task_name), None)