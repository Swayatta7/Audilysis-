import re
import uuid
from pathlib import Path

from agents.base_agent import BaseAgent
from services.negative_keyword_report import generate_negative_keyword_workbook
from services.negative_keyword_service import (
    NegativeKeywordError,
    analyze_search_terms,
    get_negative_keyword_workspace_state,
    parse_search_terms_upload,
)
from db.storage import create_negative_keyword_report


REPORT_DIR = Path(__file__).resolve().parent.parent / "data" / "negative_keyword_reports"


class NegativeKeywordAgent(BaseAgent):
    NAME = "Negative Keyword Agent"
    DESCRIPTION = "Analyze uploaded Google Ads search-term reports and recommend safe negative keywords."
    ICON = "fa-ban"
    CATEGORY = "PPC / Google Ads"
    INPUT_SCHEMA = [
        {
            "id": "search_terms_file",
            "label": "Google Ads Search-Term Report",
            "type": "file",
            "required": False,
            "accept": ".xlsx,.csv,.tsv",
            "help_text": "Upload a Google Ads search terms export in XLSX, CSV, or TSV format.",
        },
        {"id": "company_name", "label": "Company Name", "type": "text", "required": True},
        {"id": "account_name", "label": "Account Name", "type": "text", "required": False},
        {
            "id": "target_locations",
            "label": "Target Locations",
            "type": "keyword_list",
            "required": False,
            "help_text": "Optional. One location per line or comma-separated.",
        },
        {
            "id": "excluded_locations",
            "label": "Excluded or Wrong Locations",
            "type": "keyword_list",
            "required": False,
            "help_text": "Optional. Terms containing these locations are flagged.",
        },
        {
            "id": "competitor_terms",
            "label": "Competitor or Irrelevant Terms",
            "type": "keyword_list",
            "required": False,
        },
        {
            "id": "custom_negative_terms",
            "label": "Always Negative Terms",
            "type": "keyword_list",
            "required": False,
        },
        {
            "id": "high_cost_threshold",
            "label": "High Cost Threshold",
            "type": "number",
            "required": False,
            "default": 100,
            "min": 0,
        },
        {
            "id": "min_clicks_for_spend_rule",
            "label": "Minimum Clicks for Spend Review",
            "type": "number",
            "required": False,
            "default": 1,
            "min": 1,
        },
        {
            "id": "conversion_threshold",
            "label": "Conversion Threshold to Keep",
            "type": "number",
            "required": False,
            "default": 1,
            "min": 0,
        },
    ]

    def run(self, input_data: dict) -> dict:
        file_storage = input_data.get("search_terms_file") or (input_data.get("_files") or {}).get("search_terms_file")
        if not file_storage:
            return self.missing_input_response("search_terms_file", self._safe_input(input_data))

        missing = self.validate_input(input_data, ["company_name"])
        if missing:
            return self.missing_input_response("company_name", self._safe_input(input_data), missing)

        try:
            rows, source_metadata = parse_search_terms_upload(file_storage)
            return self.build_response_from_rows(
                rows,
                input_data,
                source_metadata=source_metadata,
                data_source_code="uploaded_google_ads_search_terms",
                data_sources=[
                    {
                        "name": "Google Ads Search-Term Report Upload",
                        "status": "Connected",
                        "detail": f"{source_metadata['parsed_rows']} parsed rows from {source_metadata['filename']}",
                    }
                ],
                api_used=["Local Google Ads report parser", "Audilysis negative keyword rules"],
            )
        except NegativeKeywordError as exc:
            return self.build_structured_response(
                self._safe_input(input_data),
                "The uploaded search-term report could not be analyzed.",
                [str(exc)],
                {
                    "data_source": "uploaded_google_ads_search_terms",
                    "api_used": [],
                    "missing_api_keys": [],
                    "error": str(exc),
                },
                success=False,
                message=str(exc),
            )
        except RuntimeError as exc:
            return self.build_structured_response(
                self._safe_input(input_data),
                "The report was parsed, but the Excel export could not be created.",
                [str(exc)],
                {
                    "data_source": "uploaded_google_ads_search_terms",
                    "api_used": [],
                    "missing_api_keys": [],
                    "error": str(exc),
                },
                success=False,
                message=str(exc),
            )

    def build_response_from_rows(
        self,
        rows,
        input_data: dict,
        source_metadata: dict,
        data_source_code: str,
        data_sources: list[dict],
        api_used: list[str],
    ) -> dict:
        if not rows:
            return self.build_empty_response(input_data, source_metadata, data_source_code, data_sources, api_used)
        owner_key = input_data.get("_owner_key")
        user_id = input_data.get("_user_id")
        workspace_state = get_negative_keyword_workspace_state(owner_key, user_id)
        analysis = analyze_search_terms(rows, input_data, workspace_state=workspace_state)
        report_bytes = generate_negative_keyword_workbook(analysis, input_data)
        report_filename = self._save_report(report_bytes, user_id, owner_key, data_source_code, analysis["summary"])
        summary = analysis["summary"]
        recommendations = self._build_recommendations(analysis)
        data = {
            **analysis,
            "copy_paste_text": "\n".join(analysis["copy_paste_lines"]),
            "report_filename": report_filename,
            "report_url": f"/download-negative-keyword-report/{report_filename}",
            "source_metadata": source_metadata,
            "data_source": data_source_code,
            "data_sources": data_sources,
            "api_used": api_used,
            "missing_api_keys": [],
            "workspace_state": workspace_state,
        }
        return self.build_structured_response(
            self._safe_input(input_data),
            (
                f"Analyzed {summary['total_search_terms']} search terms and found "
                f"{summary['negative_count']} negative keyword recommendations."
            ),
            recommendations,
            data,
            success=True,
            message="Negative keyword analysis completed.",
        )

    def build_empty_response(
        self,
        input_data: dict,
        source_metadata: dict,
        data_source_code: str,
        data_sources: list[dict],
        api_used: list[str],
    ) -> dict:
        return self.build_structured_response(
            self._safe_input(input_data),
            "No search terms were found for the selected input.",
            ["Adjust the date range or campaign selection and try again."],
            {
                "summary": {
                    "total_search_terms": 0,
                    "negative_count": 0,
                    "review_count": 0,
                    "keep_count": 0,
                    "total_clicks": 0,
                    "total_impressions": 0,
                    "total_cost": 0.0,
                    "total_conversions": 0.0,
                    "estimated_wasted_spend": 0.0,
                    "high_confidence": 0,
                    "medium_confidence": 0,
                    "low_confidence": 0,
                },
                "rows": [],
                "negative_keywords": [],
                "review_terms": [],
                "keep_terms": [],
                "copy_paste_lines": [],
                "copy_paste_text": "",
                "source_metadata": source_metadata,
                "data_source": data_source_code,
                "data_sources": data_sources,
                "api_used": api_used,
                "missing_api_keys": [],
                "workspace_state": get_negative_keyword_workspace_state(input_data.get("_owner_key"), input_data.get("_user_id")),
            },
            success=True,
            message="No search-term data was returned.",
        )

    def _save_report(self, report_bytes: bytes, user_id: int | None, owner_key: str | None, source_type: str, summary: dict) -> str:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        filename = f"negative-keyword-report-{uuid.uuid4().hex}.xlsx"
        report_path = REPORT_DIR / filename
        report_path.write_bytes(report_bytes)
        create_negative_keyword_report(user_id, owner_key or "__system__", filename, str(report_path), source_type, summary)
        return filename

    def _build_recommendations(self, analysis: dict) -> list[str]:
        summary = analysis["summary"]
        recommendations = []
        if summary["negative_count"]:
            recommendations.append("Review high-confidence negatives first, then upload the Copy-Paste Ready sheet to Google Ads.")
        if summary["review_count"]:
            recommendations.append("Manually inspect Review terms before excluding them because they have weaker negative signals.")
        if summary["estimated_wasted_spend"]:
            recommendations.append(f"Prioritize terms tied to the estimated wasted spend of ${summary['estimated_wasted_spend']:.2f}.")
        if not recommendations:
            recommendations.append("No strong negative keyword candidates were detected in this upload.")
        return recommendations

    def _safe_input(self, input_data: dict) -> dict:
        return {
            key: value
            for key, value in input_data.items()
            if key not in {"search_terms_file", "_files"} and not hasattr(value, "read")
        }


def is_safe_report_filename(filename: str) -> bool:
    return bool(re.fullmatch(r"negative-keyword-report-[a-f0-9]{32}\.xlsx", filename or ""))
