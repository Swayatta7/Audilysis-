from agents.base_agent import BaseAgent
import requests


class SerpAnalysisAgent(BaseAgent):
    NAME = "SERP Analysis Agent"
    DESCRIPTION = "Study the current SERP landscape for a target keyword."
    ICON = "fa-searchengin"
    CATEGORY = "SERP Research"
    INPUT_SCHEMA = [
        {"id": "target_keyword", "label": "Target Keyword", "type": "text", "placeholder": "digital marketing agency", "required": True},
        {"id": "country", "label": "Country", "type": "text", "placeholder": "United States", "required": False},
        {"id": "language", "label": "Language", "type": "text", "placeholder": "en", "required": False},
        {"id": "device", "label": "Device", "type": "select", "required": False, "default": "desktop", "options": [{"value": "desktop", "label": "Desktop"}, {"value": "mobile", "label": "Mobile"}]},
        {"id": "search_engine", "label": "Search Engine", "type": "select", "required": False, "default": "google", "options": [{"value": "google", "label": "Google"}]},
        {"id": "location", "label": "Location", "type": "text", "required": False},
        {"id": "result_limit", "label": "Result Limit", "type": "number", "required": False, "min": 1, "max": 10, "default": 10},
        {"id": "website_url", "label": "Website URL", "type": "url", "required": False, "placeholder": "https://example.com"},
        {"id": "competitor_urls", "label": "Competitor URLs", "type": "url_list", "required": False},
    ]

    def run(self, input_data: dict) -> dict:
        keyword = (input_data.get("target_keyword") or input_data.get("keyword") or input_data.get("target_keywords") or "").strip()
        if not keyword:
            return self.missing_input_response("target_keyword", input_data)
        raw_result_limit = input_data.get("result_limit")
        result_limit = 10 if raw_result_limit in (None, "") else max(1, min(int(raw_result_limit), 10))

        credentials = self.get_dataforseo_credentials(input_data)
        serpapi_key = self.get_env_config().get("SERPAPI_KEY")
        results = None
        source = None
        api_used = []
        if credentials["login"] and credentials["password"]:
            payload = [{
                "keyword": keyword,
                "location_name": (input_data.get("country") or input_data.get("location") or "United States"),
                "language_code": (input_data.get("language") or "en"),
                "device": (input_data.get("device") or "desktop"),
                "depth": result_limit,
            }]
            result, error = self.dataforseo_post("serp/google/organic/live/advanced", payload, credentials)
            if error:
                return {"success": False, "agent": self.NAME, "error": "SERP API error", "message": error}
            results = result.get("items", [])
            source = "DataForSEO SERP API"
            api_used = [source]
        elif serpapi_key:
            response = requests.get(
                "https://serpapi.com/search.json",
                params={
                    "engine": "google",
                    "q": keyword,
                    "api_key": serpapi_key,
                    "gl": (input_data.get("country_code") or "us").lower(),
                    "hl": (input_data.get("language") or "en"),
                },
                timeout=60,
            )
            if response.status_code != 200:
                return {"success": False, "agent": self.NAME, "error": "SERP API error", "message": f"SerpAPI returned HTTP {response.status_code}."}
            data = response.json()
            results = data.get("organic_results", [])[:result_limit]
            source = "SerpAPI"
            api_used = [source]
        else:
            return self.missing_key_response("DATAFORSEO_LOGIN", input_data) | {
                "message": "Configure DATAFORSEO_LOGIN/DATAFORSEO_PASSWORD or SERPAPI_KEY to run this agent with genuine data."
            }

        top_results = []
        for item in results[:result_limit]:
            top_results.append({
                "position": item.get("rank_absolute") or item.get("position"),
                "title": item.get("title"),
                "url": item.get("url"),
                "snippet": item.get("description") or item.get("snippet"),
            })

        return self.build_structured_response(
            input_data,
            f"SERP analysis returned {len(top_results)} real search results for '{keyword}'.",
            ["Build your outline around the formats and intents already winning on page one."],
            {
                "search_intent": "commercial" if any(token in keyword.lower() for token in ["best", "top", "vs", "service"]) else "informational",
                "top_results": top_results,
                "serp_features": [],
                "competitor_breakdown": [item["url"] for item in top_results],
                "recommended_outline": [item["title"] for item in top_results[:5] if item.get("title")],
                "faq_suggestions": [],
                "data_source": source,
                "api_used": api_used,
                "missing_api_keys": [],
            }
        )
