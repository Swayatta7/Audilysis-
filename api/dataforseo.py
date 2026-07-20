import time
import requests
from requests.auth import HTTPBasicAuth

def query_platform(platform, keyword, credentials, brand_domain, brand_name, competitor_domains, country="United States", language="en"):
    """
    Queries DataForSEO API for a single keyword on a specific platform.
    Handles auth, retries (up to 3 times on 429), timeouts (120s), and parses responses.
    
    Credentials: {'login': '...', 'password': '...'}
    Returns: (text, sources_cited, error_message)
    """
    login = credentials.get("login")
    password = credentials.get("password")
    
    if not login or not password:
        return None, None, "Missing DataForSEO credentials."
        
    base_url = "https://api.dataforseo.com/v3"
    
    # Set up endpoint, body, and parsing function
    if platform == "google":
        url = f"{base_url}/serp/google/ai_mode/live/advanced"
        payload = [{
            "keyword": keyword,
            "location_name": country,
            "language_code": language
        }]
    else:
        # LLM Platforms
        endpoints = {
            "chat_gpt": "chat_gpt",
            "perplexity": "perplexity",
            "gemini": "gemini",
            "claude": "claude"
        }
        models = {
            "chat_gpt": "gpt-4.1-mini",
            "perplexity": "sonar",
            "gemini": "gemini-2.0-flash",
            "claude": "claude-haiku-4-5"
        }
        
        endpoint_part = endpoints.get(platform)
        model_name = models.get(platform)
        
        url = f"{base_url}/ai_optimization/{endpoint_part}/llm_responses/live"
        payload = [{
            "user_prompt": keyword,
            "model_name": model_name,
            "web_search": True,
            "max_output_tokens": 1000
        }]

    # Request execution with retry logic
    max_retries = 3
    retry_delay = 2 # Start with 2s delay
    timeout = 120
    
    for attempt in range(max_retries + 1):
        try:
            response = requests.post(
                url, 
                json=payload, 
                auth=HTTPBasicAuth(login, password),
                timeout=timeout
            )
            
            if response.status_code == 429:
                if attempt < max_retries:
                    time.sleep(retry_delay)
                    retry_delay *= 2
                    continue
                else:
                    return None, None, "Rate limited by DataForSEO (HTTP 429)."
                    
            if response.status_code != 200:
                return None, None, f"DataForSEO API returned status {response.status_code}: {response.text}"
                
            # Request succeeded, let's parse
            data = response.json()
            tasks = data.get("tasks", [])
            if not tasks:
                return None, None, "API returned empty task list."
                
            task = tasks[0]
            if task.get("status_code") not in [20000, 20100]:
                return None, None, f"Task failed: {task.get('status_message')}"
                
            result = task.get("result", [])
            if not result or not result[0]:
                return None, None, "Task returned empty result."
                
            items = result[0].get("items", [])
            if not items or not items[0]:
                return None, None, "Task returned empty items list."
                
            item = items[0]
            
            # Parse responses based on platform
            text = ""
            sources = []
            
            if platform == "google":
                # Google AI Mode
                text = item.get("markdown") or ""
                refs = item.get("references") or []
                for ref in refs:
                    if isinstance(ref, dict):
                        url_val = ref.get("url")
                        if url_val:
                            sources.append(url_val)
                    elif isinstance(ref, str):
                        sources.append(ref)
            else:
                # LLM response (ChatGPT, Perplexity, Gemini, Claude)
                sections = item.get("sections") or []
                text_parts = []
                for sec in sections:
                    sec_text = sec.get("text") or ""
                    text_parts.append(sec_text)
                    
                    # Extract annotations URLs
                    annotations = sec.get("annotations") or []
                    for ann in annotations:
                        if isinstance(ann, dict):
                            url_val = ann.get("url")
                            if url_val:
                                sources.append(url_val)
                                
                text = "\n".join(text_parts)
                
            return text, list(set(sources)), None
            
        except requests.exceptions.Timeout:
            if attempt < 1: # Retry once on timeout
                time.sleep(1)
                continue
            return None, None, "DataForSEO API timed out after 120 seconds."
        except Exception as e:
            return None, None, f"An unexpected error occurred: {str(e)}"
            
    return None, None, "API request failed after retries."
