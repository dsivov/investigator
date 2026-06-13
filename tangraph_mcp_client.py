import requests
import json
class DedupClient:
    def __init__(self, base_url: str):
        """
        Initialize the NERClient with the base URL of the NER MCP service.

        Args:
            base_url (str): The base URL of the NER MCP service (e.g., "http://127.0.0.1:5000/api/v1").
        """
        self.base_url = base_url.rstrip("/")

    def get_deduplicated_entities(self, session_id: str, text: str):
        """
        Send a request to the NER MCP service to perform Named Entity Recognition (NER).

        Args:
            session_id (str): A unique session identifier.
            text (str): The text to analyze for named entities.

        Returns:
            dict: The response from the NER MCP service.
        """
        url = f"{self.base_url}/dedup"
        payload = {
            "session_id": session_id,
            "text": text
        }

        try:
            response = requests.post(url, json=payload)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            return {"status": "error", "message": str(e)}


class GetGraphClient:
    def __init__(self, base_url: str):
        """
        Initialize the GetNodesClient with the base URL of the NER MCP service.

        Args:
            base_url (str): The base URL of the NER MCP service (e.g., "http://127.0.0.1:5000/api/v1").
        """
        self.base_url = base_url.rstrip("/")

    def get_graph_data_api(self, session_id: str, text: str, investigation_query: str, hypotests: str, domain: str, use_regular_triangulation: bool = False, relevance_threshold: float = 0.5):
        """
        Send a request to the NER MCP service to perform Named Entity Recognition (NER).

        Args:
            session_id (str): A unique session identifier.
            text (str): The text to analyze for named entities.

        Returns:
            dict: The response from the NER MCP service.
        """
        url = f"{self.base_url}/get_nodes"
        payload = {
            "session_id": session_id,
            "text": text,
            "query": investigation_query,
            "hypotests": hypotests,
            "use_regular_triangulation": use_regular_triangulation,
            "domain": domain,
            "relevance_threshold": relevance_threshold
        }

        try:
            response = requests.post(url, json=payload)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            return {"status": "error", "message": str(e)}
    
    def update_light_rag(self):
        """
        Send a request to update the LightRAG storages.

        Returns:
            dict: The response from the NER MCP service.
        """
        url = f"{self.base_url}/update_light_rag"

        try:
            response = requests.get(url)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            return {"status": "error", "message": str(e)}
