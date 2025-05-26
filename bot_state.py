from typing import Dict, List, Any

# In-memory storage for conversation and model per channel
conversations: Dict[int, List[Dict[str, Any]]] = {}
models: Dict[int, str] = {}
channel_system_prompts: Dict[int, str] = {}
