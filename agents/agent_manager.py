"""
agent_manager.py — Central registry and dispatcher for Audilysis AI Agents.

Usage:
    from agents.agent_manager import get_all_agents, run_agent, get_agent_metadata

    agents = get_all_agents()        # grouped dict for UI rendering
    result = run_agent("keyword_research", input_data)
"""
import warnings

from agents.technical_audit      import TechnicalAuditAgent
from agents.competitor_analysis  import CompetitorAnalysisAgent
from agents.keyword_research     import KeywordResearchAgent
from agents.keyword_clustering   import KeywordClusteringAgent
from agents.content_gap          import ContentGapAgent
from agents.serp_analysis        import SerpAnalysisAgent
from agents.rank_tracking        import RankTrackingAgent
from agents.on_page_optimizer    import OnPageOptimizerAgent
from agents.schema_agent         import SchemaAgent
from agents.internal_linking     import InternalLinkingAgent
from agents.backlink_prospecting import BacklinkProspectingAgent
from agents.outreach             import OutreachAgent
from agents.backlink_verification import BacklinkVerificationAgent
from agents.weekly_report        import WeeklyReportAgent
from agents.monthly_report       import MonthlyReportAgent
from agents.strategy             import StrategyAgent
from agents.content_calendar     import ContentCalendarAgent
from agents.seo_brief            import SEOBriefAgent
from agents.blog_writer          import BlogWriterAgent
from agents.humanizer            import HumanizerAgent
from agents.fact_check           import FactCheckAgent
from agents.content_publisher    import ContentPublisherAgent
from agents.social_calendar      import SocialCalendarAgent
from agents.social_caption       import SocialCaptionAgent
from agents.social_image         import SocialImageAgent
from agents.social_video         import SocialVideoAgent
from agents.social_publisher     import SocialPublisherAgent
from agents.social_analytics     import SocialAnalyticsAgent


# ── Registry ────────────────────────────────────────────────────────────────
# Each entry: agent_id -> (agent group, agent class)
# Metadata (name, description, icon, category) is pulled from the class itself.

SEO_GROUP = "SEO AI Agents"
CONTENT_GROUP = "Content"
SOCIAL_GROUP = "Social"

_REGISTRY: dict = {
    "technical_audit":       (SEO_GROUP, TechnicalAuditAgent),
    "competitor_analysis":   (SEO_GROUP, CompetitorAnalysisAgent),
    "keyword_research":      (SEO_GROUP, KeywordResearchAgent),
    "keyword_clustering":    (SEO_GROUP, KeywordClusteringAgent),
    "content_gap":           (SEO_GROUP, ContentGapAgent),
    "serp_analysis":         (SEO_GROUP, SerpAnalysisAgent),
    "rank_tracking":         (SEO_GROUP, RankTrackingAgent),
    "on_page_optimizer":     (SEO_GROUP, OnPageOptimizerAgent),
    "schema_agent":          (SEO_GROUP, SchemaAgent),
    "internal_linking":      (SEO_GROUP, InternalLinkingAgent),
    "backlink_prospecting":  (SEO_GROUP, BacklinkProspectingAgent),
    "outreach":              (SEO_GROUP, OutreachAgent),
    "backlink_verification": (SEO_GROUP, BacklinkVerificationAgent),
    "weekly_report":         (SEO_GROUP, WeeklyReportAgent),
    "monthly_report":        (SEO_GROUP, MonthlyReportAgent),
    "strategy":              (SEO_GROUP, StrategyAgent),
    "content_calendar":      (CONTENT_GROUP, ContentCalendarAgent),
    "seo_brief":             (CONTENT_GROUP, SEOBriefAgent),
    "blog_writer":           (CONTENT_GROUP, BlogWriterAgent),
    "humanizer":             (CONTENT_GROUP, HumanizerAgent),
    "fact_check":            (CONTENT_GROUP, FactCheckAgent),
    "publisher":             (CONTENT_GROUP, ContentPublisherAgent),
    "social_calendar":       (SOCIAL_GROUP, SocialCalendarAgent),
    "caption":               (SOCIAL_GROUP, SocialCaptionAgent),
    "image_agent":           (SOCIAL_GROUP, SocialImageAgent),
    "video_agent":           (SOCIAL_GROUP, SocialVideoAgent),
    "social_publisher":      (SOCIAL_GROUP, SocialPublisherAgent),
    "analytics":             (SOCIAL_GROUP, SocialAnalyticsAgent),
}


def _agent_metadata(agent_id: str, group: str, cls: type) -> dict:
    instance = cls()
    if not instance.INPUT_SCHEMA:
        warnings.warn(
            f"Agent '{agent_id}' ({cls.__name__}) has an empty INPUT_SCHEMA. "
            "It will render with no input fields in the studio UI.",
            RuntimeWarning,
            stacklevel=2,
        )
    return {
        "id":           agent_id,
        "name":         instance.NAME,
        "description":  instance.DESCRIPTION,
        "icon":         instance.ICON,
        "category":     instance.CATEGORY,
        "group":        group,
        "input_schema": instance.INPUT_SCHEMA,
    }


def get_all_agents() -> dict:
    """
    Returns registered agents grouped by SEO, Content, and Social category.
    Used by the studio templates to render group-specific cards.
    """
    groups = {SEO_GROUP: [], CONTENT_GROUP: [], SOCIAL_GROUP: []}
    for agent_id, (group, cls) in _REGISTRY.items():
        groups[group].append(_agent_metadata(agent_id, group, cls))
    return groups


def get_agents_by_group(group: str) -> list:
    """Returns metadata for agents in one top-level group."""
    return get_all_agents().get(group, [])


def get_agent_metadata(agent_id: str) -> dict | None:
    """Returns metadata dict for a single agent, or None if not found."""
    entry = _REGISTRY.get(agent_id)
    if not entry:
        return None
    group, cls = entry
    return _agent_metadata(agent_id, group, cls)


def run_agent(agent_id: str, input_data: dict) -> dict:
    """
    Instantiates the requested agent and calls its run() method.
    Returns structured JSON result dict.
    """
    entry = _REGISTRY.get(agent_id)
    if not entry:
        return {
            "status": "error",
            "message": f"Unknown agent '{agent_id}'. Valid IDs: {list(_REGISTRY.keys())}",
            "agent": agent_id,
            "data": {}
        }
    try:
        _, cls = entry
        instance = cls()
        return instance.run(input_data)
    except Exception as e:
        return {
            "status": "error",
            "message": f"Agent '{agent_id}' raised an unexpected error: {str(e)}",
            "agent": agent_id,
            "data": {}
        }
