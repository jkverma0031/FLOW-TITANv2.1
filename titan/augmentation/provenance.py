# Path: titan/augmentation/provenance.py
from __future__ import annotations
from typing import List, Dict, Any, Optional
import time
import json
import hashlib
import os
import logging

logger = logging.getLogger(__name__)

class ProvenanceChain:
    """
    Implements a cryptographic audit trail (Blockchain-lite).
    Each event is hashed with the previous event's hash, ensuring the log
    cannot be tampered with without breaking the chain.
    """

    def __init__(self, file_path: str = "data/provenance.jsonl"):
        self.file_path = file_path
        self._ensure_file()

    def _ensure_file(self):
        if not os.path.exists(os.path.dirname(self.file_path)):
            os.makedirs(os.path.dirname(self.file_path), exist_ok=True)
        if not os.path.exists(self.file_path):
            with open(self.file_path, "w") as f:
                pass

    def log_event(self, event_type: str, payload: Dict[str, Any]) -> None:
        """
        Appends an event to the chain with a cryptographic signature.
        """
        prev_hash = self._get_last_hash()
        timestamp = time.time()
        
        # Canonicalize payload for deterministic hashing
        payload_str = json.dumps(payload, sort_keys=True)
        
        # Create hash input: prev_hash + type + timestamp + payload
        hash_input = f"{prev_hash}|{event_type}|{timestamp}|{payload_str}".encode("utf-8")
        current_hash = hashlib.sha256(hash_input).hexdigest()
        
        entry = {
            "timestamp": timestamp,
            "type": event_type,
            "payload": payload,
            "prev_hash": prev_hash,
            "hash": current_hash
        }
        
        with open(self.file_path, "a") as f:
            f.write(json.dumps(entry) + "\n")
            
        logger.debug(f"Provenance logged: {event_type} (hash={current_hash[:8]}...)")

    def read_chain(self) -> List[Dict[str, Any]]:
        """
        Reads and returns the full chain.
        """
        chain = []
        if not os.path.exists(self.file_path):
            return chain
            
        with open(self.file_path, "r") as f:
            for line in f:
                if line.strip():
                    try:
                        chain.append(json.loads(line))
                    except json.JSONDecodeError:
                        logger.error("Corrupt line in provenance file")
        return chain

    def _get_last_hash(self) -> str:
        """
        Reads the last line of the file to get the previous hash.
        Returns '0000000000000000000000000000000000000000000000000000000000000000' if empty.
        """
        genesis_hash = "0" * 64
        
        if not os.path.exists(self.file_path):
            return genesis_hash
            
        # Efficiently read last line without reading whole file
        try:
            with open(self.file_path, "rb") as f:
                try:
                    f.seek(-2, os.SEEK_END)
                    while f.read(1) != b'\n':
                        f.seek(-2, os.SEEK_CUR)
                except OSError:
                    f.seek(0)
                
                last_line = f.readline().decode()
                
            if not last_line.strip():
                return genesis_hash
                
            last_entry = json.loads(last_line)
            return last_entry.get("hash", genesis_hash)
            
        except (OSError, json.JSONDecodeError):
            return genesis_hash

# Maintain backward compatibility if other modules import the Tracker class
# (Optional adapter if needed, but for now we focus on the Chain)
class ProvenanceTracker(ProvenanceChain):
    pass