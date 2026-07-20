from agents.publisher import PublisherAgent


class ContentPublisherAgent(PublisherAgent):
    NAME = "Publisher"
    DESCRIPTION = "Prepare final content for publishing with title, slug, metadata, schema, excerpt, category, tags, and publishing checklist."
    CATEGORY = "Content"
