from agents.base_agent import BaseAgent


class RankTrackingAgent(BaseAgent):
    NAME = "Rank Tracking Agent"
    DESCRIPTION = "Track current ranking potential and movement signals."
    ICON = "fa-chart-line"
    CATEGORY = "Performance"
    INPUT_SCHEMA = [
        {"id": "website_url", "label": "Website URL", "type": "url", "placeholder": "https://example.com", "required": True},
        {"id": "keyword_list", "label": "Keyword List", "type": "keyword_list", "required": True, "help_text": "Provide at least one keyword to track."},
        {"id": "country", "label": "Country", "type": "text", "required": False, "default": "United States"},
        {"id": "language", "label": "Language", "type": "text", "required": False, "default": "en"},
        {"id": "device", "label": "Device", "type": "select", "required": False, "options": [{"value": "desktop", "label": "Desktop"}, {"value": "mobile", "label": "Mobile"}]},
        {"id": "search_engine", "label": "Search Engine", "type": "select", "required": False, "default": "google", "options": [{"value": "google", "label": "Google"}]},
        {"id": "location", "label": "Location", "type": "text", "required": False},
        {"id": "tracking_frequency", "label": "Tracking Frequency", "type": "select", "required": False, "default": "weekly", "options": [{"value": "daily", "label": "Daily"}, {"value": "weekly", "label": "Weekly"}, {"value": "monthly", "label": "Monthly"}]},
        {"id": "result_depth", "label": "Result Depth", "type": "number", "required": False, "min": 10, "max": 100, "default": 100},
        {"id": "project_name", "label": "Project Name", "type": "text", "required": False},
    ]

    def run(self, input_data: dict) -> dict:
        keywords = self.parse_keywords(input_data.get("keyword_list") or input_data.get("target_keywords") or input_data.get("keyword") or [])
        website_url = (input_data.get("website_url") or "").strip()
        if not keywords:
            return self.missing_input_response("keyword_list", input_data, "Provide at least one keyword to track.")
        if not website_url:
            return self.missing_input_response("website_url", input_data)
        raw_result_depth = input_data.get("result_depth")
        result_depth = 100 if raw_result_depth in (None, "") else max(10, min(int(raw_result_depth), 100))
        credentials = self.get_dataforseo_credentials(input_data)
        if not credentials["login"]:
            return self.missing_key_response("DATAFORSEO_LOGIN", input_data)
        if not credentials["password"]:
            return self.missing_key_response("DATAFORSEO_PASSWORD", input_data)

        tracked = []
        domain = website_url.replace("https://", "").replace("http://", "").split("/")[0].lower()
        for keyword in keywords[:10]:
            payload = [{
                "keyword": keyword,
                "location_name": (input_data.get("country") or input_data.get("location") or "United States"),
                "language_code": (input_data.get("language") or "en"),
                "depth": result_depth,
            }]
            result, error = self.dataforseo_post("serp/google/organic/live/advanced", payload, credentials)
            if error:
                return {"success": False, "agent": self.NAME, "error": "DataForSEO API error", "message": error}
            items = result.get("items", [])
            found_position = None
            for item in items:
                item_url = (item.get("url") or "").lower()
                if domain in item_url:
                    found_position = item.get("rank_absolute")
                    break
            tracked.append({"keyword": keyword, "position": found_position})
        return self.build_structured_response(
            input_data,
            f"Rank tracking checked {len(tracked)} real SERP result sets through DataForSEO.",
            ["Review keywords without a detected ranking URL.", "Track ranking movement over time in the database if you want historical reporting."],
            {
                "tracked_keywords": tracked,
                "status": "monitor",
                "movement": "live_snapshot_only",
                "data_source": "DataForSEO SERP API",
                "api_used": ["DataForSEO SERP API"],
                "missing_api_keys": [],
            },
        )
