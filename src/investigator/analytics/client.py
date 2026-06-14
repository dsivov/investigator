import asyncio
from socket import timeout
import requests
import httpx
import json
from typing import AsyncGenerator, Optional

import json
from time import sleep
import pydot # type: ignore

class RAGClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip('/')
    
    
    async def advanced_query_async(
        self,
        query: str,
        mode: str = "mix",
        only_need_context: bool = True,
        only_need_prompt: bool = True,
        response_type: str = "string",
        top_k: int = 1,
        chunk_top_k: int = 1,
        max_entity_tokens: int = 1,
        max_relation_tokens: int = 1,
        max_total_tokens: int = 1,
        conversation_history: list = None,
        user_prompt: str = "",
        enable_rerank: bool = True,
        include_references: bool = True,
        stream: bool = True,
        timeout: int = 120
    ) -> AsyncGenerator[dict, None]:
        """
        Asynchronous version of the advanced query API.

        Sends a query to the backend and streams the response line by line.

        Args:
            query: The main query to send.
            stream: Whether to stream the response.
            user_prompt: Optional user prompt to include in the request.
            **kwargs: Additional parameters to include in the request.

        Yields:
            Each line of the response as a JSON dictionary.
        """
        async with httpx.AsyncClient() as client:
            url = f"{self.base_url}/query"
            payload = {
                "query": query,
                "mode": mode,
                "only_need_context": only_need_context,
                "only_need_prompt": only_need_prompt,
                "response_type": response_type,
                "top_k": top_k,
                "chunk_top_k": chunk_top_k,
                "max_entity_tokens": max_entity_tokens,
                "max_relation_tokens": max_relation_tokens,
                "max_total_tokens": max_total_tokens,
                "conversation_history": conversation_history or [],
                "user_prompt": user_prompt,
                "enable_rerank": enable_rerank,
                "include_references": include_references,
                "stream": stream
                    }
            async with client.post(
                self.base_url + "/query/stream",
                json=payload
            ) as response:
                if response.status_code != 200:
                    raise Exception(f"Request failed with status code {response.status_code}")

                async for line in response.aiter_lines():
                    try:
                        data = json.loads(line)
                        yield data
                    except json.JSONDecodeError:
                        continue

    def advanced_query(
        self,
        query: str,
        mode: str = "mix",
        only_need_context: bool = True,
        only_need_prompt: bool = True,
        response_type: str = "string",
        top_k: int = 1,
        chunk_top_k: int = 1,
        max_entity_tokens: int = 1,
        max_relation_tokens: int = 1,
        max_total_tokens: int = 1,
        conversation_history: list = None,
        user_prompt: str = "",
        enable_rerank: bool = True,
        include_references: bool = True,
        stream: bool = True,
        timeout: int = 120
    ):
        """
        Interface for advanced REST query service.
        """
        url = f"{self.base_url}/query"
        payload = {
            "query": query,
            "mode": mode,
            "only_need_context": only_need_context,
            "only_need_prompt": only_need_prompt,
            "response_type": response_type,
            "top_k": top_k,
            "chunk_top_k": chunk_top_k,
            "max_entity_tokens": max_entity_tokens,
            "max_relation_tokens": max_relation_tokens,
            "max_total_tokens": max_total_tokens,
            "conversation_history": conversation_history or [],
            "user_prompt": user_prompt,
            "enable_rerank": enable_rerank,
            "include_references": include_references,
            "stream": stream
        }
        try:
            resp = requests.post(url, json=payload, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as e:
            if resp.status_code == 400:
                raise ValueError(f"Invalid input: {resp.text}")
            elif resp.status_code == 500:
                raise RuntimeError(f"Internal server error: {resp.text}")
            else:
                raise
        except Exception as e:
            raise RuntimeError(f"Request failed: {e}")
        
    def base_query(
        self,
        query: str,
        mode: str = "mix",
        include_references: bool = True,
        response_type: str = None, # type: ignore
        top_k: int = 200,
        chunk_top_k: int = 100,
        max_entity_tokens: int = 6000,
        max_relation_tokens: int = 8000,
        conversation_history: list = None, # type: ignore
        max_total_tokens: int = 30000,
        timeout: int = 120
    ):
        url = f"{self.base_url}/query"
        payload = {
            "query": query,
            "mode": mode,
        }
        
        if include_references is not None:
            payload["include_references"] = str(include_references)
        if response_type:
            payload["response_type"] = response_type
        if top_k:
            payload["top_k"] = str(top_k)
        if chunk_top_k:
            payload["chunk_top_k"] = str(chunk_top_k)
        if max_entity_tokens:
            payload["max_entity_tokens"] = str(max_entity_tokens)
        if max_relation_tokens:
            payload["max_relation_tokens"] = str(max_relation_tokens)
        if conversation_history:
            payload["conversation_history"] = str(conversation_history)
        if max_total_tokens:
            payload["max_total_tokens"] = str(max_total_tokens)

        try:
            resp = requests.post(url, json=payload, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as e:
            if resp.status_code == 400: # type: ignore
                raise ValueError(f"Invalid input: {resp.text}") # type: ignore
            elif resp.status_code == 500: # type: ignore
                raise RuntimeError(f"Internal server error: {resp.text}") # type: ignore
            else:
                raise
        except Exception as e:
            raise RuntimeError(f"Request failed: {e}")
    
    def add_text_document(
        self,
        text: str,
        file_source: str = None, # type: ignore
        timeout: int = 30
    ):
        """
        Adds a text document to the RAG system.

        Args:
            text (str): The text content to insert.
            file_source (str, optional): Source of the text.
            timeout (int): Request timeout in seconds.

        Returns:
            dict: API response.
        """
        url = f"{self.base_url}/documents/text"
        payload = {"text": text}
        if file_source:
            payload["file_source"] = file_source

        try:
            resp = requests.post(url, json=payload, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as e:
            if resp.status_code == 400: # type: ignore
                raise ValueError(f"Invalid input: {resp.text}") # type: ignore
            elif resp.status_code == 500: # type: ignore
                raise RuntimeError(f"Internal server error: {resp.text}") # type: ignore
            else:
                raise
        except Exception as e:
            raise RuntimeError(f"Request failed: {e}")
    
    def get_track_status(
        self,
        track_id: str,
        timeout: int = 30
    ):
        """
        Get the processing status of documents by tracking ID.

        Args:
            track_id (str): The tracking ID returned from upload, text, or texts endpoints.
            timeout (int): Request timeout in seconds.

        Returns:
            dict: Response containing track_id, documents, total_count, and status_summary.

        Raises:
            ValueError: If track_id is invalid (400).
            RuntimeError: For internal server errors (500) or request failures.
        """
        url = f"{self.base_url}/documents/track_status/{track_id}"
        try:
            resp = requests.get(url, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as e:
            if resp.status_code == 400: # type: ignore
                raise ValueError(f"Invalid track_id: {resp.text}") # type: ignore
            elif resp.status_code == 500: # type: ignore
                raise RuntimeError(f"Internal server error: {resp.text}") # type: ignore
            else:
                raise
        except Exception as e:
            raise RuntimeError(f"Request failed: {e}")
       
    def get_subgraph_by_label(
        self,
        label: str, 
        max_depth: int = 3,
        max_nodes: int = 50,
        timeout: int = 60
    ) -> dict:
        """
        Retrieve a connected subgraph of nodes where the label includes the specified label.
        Prioritization: 1. Hops (path) to the starting node, 2. Degree of the nodes.

        Args:
            label (str): Label of the starting node.
            max_depth (int, optional): Maximum depth of the subgraph. Defaults to 3.
            max_nodes (int, optional): Maximum nodes to return. Defaults to 50.
            timeout (int): Request timeout in seconds.

        Returns:
            Dict[str, List[str]]: Knowledge graph for label.
        """
        url = f"{self.base_url}/graphs"
        params = {
            "label": label,
            "max_depth": max_depth,
            "max_nodes": max_nodes
        }
        try:
            resp = requests.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as e:
            if resp.status_code == 400:
                raise ValueError(f"Invalid input: {resp.text}")
            elif resp.status_code == 500:
                raise RuntimeError(f"Internal server error: {resp.text}")
            else:
                raise
        except Exception as e:
            raise RuntimeError(f"Request failed: {e}")
        
    def get_documents_paginated(
        self,
        page: int = 1,
        page_size: int = 50,
        sort_direction: str = "desc",
        sort_field: str = "updated_at",
        status_filter: str = None, # type: ignore
        timeout: int = 30
    ):
        """
        Retrieve documents with pagination, filtering, and sorting.

        Args:
            page (int): Page number (default 1).
            page_size (int): Number of documents per page (default 50).
            sort_direction (str): "asc" or "desc" (default "desc").
            sort_field (str): Field to sort by (default "updated_at").
            status_filter (str, optional): Filter by document status.
            timeout (int): Request timeout in seconds.

        Returns:
            dict: Response containing documents, pagination, and status_counts.

        Raises:
            ValueError: For invalid input (400).
            RuntimeError: For internal server errors (500) or request failures.
        """
        url = f"{self.base_url}/documents/paginated"
        payload = {
            "page": page,
            "page_size": page_size,
            "sort_direction": sort_direction,
            "sort_field": sort_field
        }
        if status_filter:
            payload["status_filter"] = status_filter

        try:
            resp = requests.post(url, json=payload, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as e:
            if resp.status_code == 400: # type: ignore
                raise ValueError(f"Invalid input: {resp.text}") # type: ignore
            elif resp.status_code == 500: # type: ignore
                raise RuntimeError(f"Internal server error: {resp.text}") # type: ignore
            else:
                raise
        except Exception as e:
            raise RuntimeError(f"Request failed: {e}")
    
    def get_all_ter_related_topics_prompt(self, timeout: int = 30):
        return """
        list all persons and organizations suspected of or involved in terrorist activities or the financial support of terror. Return in JSON by following format {name: , description: , reference: ''}
    """

    def get_relevant_documents(self, domain_prompt: str, query: str):
        """
        Get relevant documents based on a domain prompt and query.

        Args:
            domain_prompt (str): The domain-specific prompt.
            query (str): The user query.    
        Returns:
            dict: Response containing relevant documents.
        """
        if query:
            domain_query = f"{domain_prompt}\nUser query: {query}"
        else:
            domain_query = domain_prompt
        
        response = self.base_query(
            query=domain_query
        )

        response = response['response']
        result = self.get_json_substring(response)
        try:
            return json.loads(result)
        except json.JSONDecodeError:
            return []

    def get_json_substring(self, text: str) -> str:  # type: ignore
        """
        Extracts the substring between '```json' and '```' from a given text.

        Args:
            text (str): The input string containing the markers.

        Returns:
            str or None: The extracted JSON substring, or None if markers are not found.
        """
        start_marker = "```json"
        end_marker = "```"

        start_index = text.find(start_marker)
        if start_index == -1:
            return None  # type: ignore # Start marker not found

        # Adjust start_index to point after the start marker
        start_index += len(start_marker)

        end_index = text.find(end_marker, start_index)
        if end_index == -1:
            return None  # type: ignore # End marker not found after the start marker

        return text[start_index:end_index].strip()

    def blocking_wait_for_processing(self):
        while True:
            pending = False
            docs = self.get_documents_paginated()
            print("Documents:", len(docs.get("documents", [])))
            for d in docs.get("documents", []):
                if d.get("status") != "processed":
                    print(f"Document {d.get('id')} status: {d.get('status')}")
                    pending = True
                    sleep(3)
            if not pending:
                break
    
    def get_query_siblings(self, query: str, timeout: int = 30):
        """
        Get sibling nodes for a given query.

        Args:
            query (str): The user query.
            timeout (int): Request timeout in seconds.
        """
        siblings = []
        prompt = """list all persons, connected to ###NAME### and suspected of or involved in terrorist activities or the financial support of terror.
            IMPORTANT!
            Return at least five relevant documents.
            Return always in JSON by following format {name: <name> , description: <description>, reference: <reference>}
            """. \
            replace("###NAME###", query)
        response = self.get_relevant_documents(
            domain_prompt=prompt,
            query=""
        )
        for r in response:
            siblings.append(r.get('name')) #type: ignore
            print(f"Name: {r.get('name')}, Description: {r.get('description')}, Reference: {r.get('reference')}")    # type: ignore

        prompt = """list all organisations, connected to ###NAME### and suspected of or involved in terrorist activities or the financial support of terror.
                IMPORTANT!
                Return at least five relevant documents.
                Return always in JSON by following format {name: <name> , description: <description>, reference: <reference>}
                """. \
                replace("###NAME###", query)
        response = self.get_relevant_documents(
            domain_prompt=prompt,
            query=""
        )  
        for r in response:
            siblings.append(r.get('name')) # type: ignore
            print(f"Name: {r.get('name')}, Description: {r.get('description')}, Reference: {r.get('reference')}")    # type: ignore

        return siblings

    def test_hypothesis_task(self, query: str):
        """
        Test the hypothesis task for a given query.

        Args:
            query (str): The user query.
        """
        hypothesis = (
            # f"Based on the available evidence, assess whether '{investigated_entity_name}' has credible links to "
            # f"terror-affiliated organizations or individuals, and evaluate the strength of any "
            # f"financial or operational connections that could indicate terror financing activities."
            # f"In case there are specific mentions of sanctions codes, specify them explicitly with their links if exists"
            f"Assess whether '{query}' is a person or organization, which has credible associations with "
            f"terror-affiliated organizations or individuals, and evaluate the strength of any financial or "
            f"operational connections that could indicate involvement in terror financing. "
            f"If any specific sanctions codes are mentioned in the evidence, explicitly reference them and "
            f"provide links if available. "
            f"Provide all the links that are mentioned in the text. Do not omit any links."
            f"Score the relevance of the '{query}' from 1 to 10, where 10 is the highest relevance."
            f"Determine the type of '{query}' is it a person or organization, event, date or location."
            #f"For each link that is mentioned in the text, provide score of relevance from 1 to 10, where 10 is the highest relevance. "
            f"After generating your assessment, validate that all required output fields are complete and that any "
            f"claims about evidence or codes are clearly backed by the summary. "
            """
            Return always in JSON by following format: {assessment: <assessment> , evidence_summary: <evidence_summary>, type: <type>, sanctions_codes: <sanctions_codes>, relevance: <score>, references: <references>}.
            If no evidence or sanctions codes are identified, explicitly state this in the respective fields."""
        )
        #print("Hypothesis prompt:", hypothesis)
        response = self.get_relevant_documents(
            domain_prompt=hypothesis,
            query=""
        )
        return response

    async def test_hypothesis_task_adv(self, query: str):
        """
        Test the hypothesis task for a given query.

        Args:
            query (str): The user query.
        """
        hypothesis = (
            # f"Based on the available evidence, assess whether '{investigated_entity_name}' has credible links to "
            # f"terror-affiliated organizations or individuals, and evaluate the strength of any "
            # f"financial or operational connections that could indicate terror financing activities."
            # f"In case there are specific mentions of sanctions codes, specify them explicitly with their links if exists"
            f"Assess whether subject has credible associations with "
            f"terror-affiliated organizations or individuals, and evaluate the strength of any financial or "
            f"operational connections that could indicate involvement in terror financing. "
            f"If any specific sanctions codes are mentioned in the evidence, explicitly reference them and "
            f"provide links if available. "
            f"Provide all the links that are mentioned in the text. Do not omit any"
            f"For each link that is mentioned in the text, provide score of relevance from 1 to 10, where 10 is the highest relevance. "
            f"After generating your assessment, validate that all required output fields are complete and that any "
            f"claims about evidence or codes are clearly backed by the summary. "
            """
            Return always in JSON by following format: {assessment: <assessment> , evidence_summary: <evidence_summary>, sanctions_codes: <sanctions_codes>, references: <references with score>}.
            If no evidence or sanctions codes are identified, explicitly state this in the respective fields."""
        )
        print("Hypothesis prompt:", hypothesis)
        response = self.advanced_query(
            query=query,
            stream=False,
            user_prompt=hypothesis
        )
        async for line in response.iter_lines():
            data = json.loads(line)
            if "references" in data:
                # Handle references (first message)
                references = data["references"]
            if "response" in data:
                # Handle content chunk
                content_chunk = data["response"]
            if "error" in data:
                # Handle error
                error_message = data["error"]
        return response
def main():
    import argparse
    import os
    from investigator.analytics.client import RAGClient
    graph = pydot.Dot('graphname', graph_type='graph') # type: ignore
   
    # Initialize RAG client
    rag_client = RAGClient(base_url="http://10.0.0.80:9621")
    # siblings = rag_client.get_query_siblings("Sami Al-Arian")
    # # Determine if yaml_path is absolute or relative
    # print("Siblings found:", siblings)
    #print(docs)
    # resp = rag_client.add_text_document(text="""
    #    Dima Sivov is currently working for Investigator, a company specializing in AI-driven solutions.
    # """, file_source="ver1-https://il.linkedin.com/in/dsivov")
    
    # print("Add text document response:", resp)
    #prompt = rag_client.get_ter_prompts()
    # print("Starting test_hypothesis_task")
    response = rag_client.get_subgraph_by_label("Hamas", max_depth=3, max_nodes=20)
    # \\print("Hypothesis task response:")
    print(json.dumps(response, indent=4))
    # for node in response.get('nodes', []):
    #     print(f"Node: {node['id']}")
        # if node['properties']['entity_type'] == 'person' or node['properties']['entity_type'] == 'organization':
        #     h = rag_client.test_hypothesis_task(node['id'])  
        #     print(h)  
   
  
       
if __name__ == "__main__":
   main()