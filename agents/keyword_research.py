import re
import json
import math
from collections import Counter
from html.parser import HTMLParser
from urllib.parse import urlparse

from agents.base_agent import BaseAgent
from agents.crawl_utils import crawl_site, ensure_url, fetch_url


UNAVAILABLE = "Unavailable in Free Mode"
STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "is", "it", "of", "on",
    "or", "our", "that", "the", "this", "to", "we", "with", "you", "your", "learn", "more", "home",
    "all", "also", "can", "get", "has", "have", "into", "its", "new", "not", "see", "shop", "use",
}
CONTENT_TAGS = {"title", "h1", "h2", "h3", "nav", "footer", "a", "button", "li", "p", "span"}
SKIP_TAGS = {"script", "style", "noscript", "svg"}


class KeywordHTMLExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title = ""
        self.meta_description = ""
        self.h1 = []
        self.h2 = []
        self.h3 = []
        self.navigation_labels = []
        self.footer_links = []
        self.breadcrumbs = []
        self.image_alt = []
        self.structured_data_terms = []
        self.anchor_texts = []
        self.visible_text = []
        self._tag_stack = []
        self._attrs_stack = []
        self._current_tag = ""
        self._current_attrs = {}
        self._buffer = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag in SKIP_TAGS and not (tag == "script" and attrs_dict.get("type") == "application/ld+json"):
            self._skip_depth += 1
        self._tag_stack.append(tag)
        self._attrs_stack.append(attrs_dict)
        self._current_tag = tag
        self._current_attrs = attrs_dict
        self._buffer = []
        if tag == "meta":
            name = attrs_dict.get("name", "").lower()
            prop = attrs_dict.get("property", "").lower()
            if name == "description" or prop == "og:description":
                self.meta_description = attrs_dict.get("content", "") or self.meta_description
        elif tag == "img":
            alt = attrs_dict.get("alt", "").strip()
            if alt:
                self.image_alt.append(alt)

    def handle_endtag(self, tag):
        text = re.sub(r"\s+", " ", " ".join(self._buffer)).strip()
        if text and self._skip_depth == 0:
            if tag == "title":
                self.title = text
            elif tag == "h1":
                self.h1.append(text)
            elif tag == "h2":
                self.h2.append(text)
            elif tag == "h3":
                self.h3.append(text)
            elif tag in {"nav", "a", "button"} or "nav" in self._tag_stack:
                self.navigation_labels.append(text)
            if tag == "a":
                self.anchor_texts.append(text)
            if tag == "a" and "footer" in self._tag_stack:
                self.footer_links.append(text)
            if self._looks_like_breadcrumb():
                self.breadcrumbs.append(text)
            if tag == "script" and self._current_attrs.get("type") == "application/ld+json":
                self.structured_data_terms.extend(self._structured_terms(text))
            elif tag in CONTENT_TAGS:
                self.visible_text.append(text)
        if tag in SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        if self._tag_stack:
            self._tag_stack.pop()
        if self._attrs_stack:
            self._attrs_stack.pop()
        self._current_tag = self._tag_stack[-1] if self._tag_stack else ""
        self._current_attrs = self._attrs_stack[-1] if self._attrs_stack else {}
        self._buffer = []

    def handle_data(self, data):
        if self._skip_depth:
            return
        if self._current_tag in CONTENT_TAGS or self._current_attrs.get("type") == "application/ld+json" or "nav" in self._tag_stack:
            self._buffer.append(data)

    def _looks_like_breadcrumb(self) -> bool:
        attrs = self._current_attrs or {}
        combined = " ".join(str(attrs.get(key, "")) for key in ["class", "id", "aria-label", "itemprop"]).lower()
        return "breadcrumb" in combined

    def _structured_terms(self, text: str) -> list:
        terms = []
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return terms

        def walk(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in {"name", "headline", "description", "category", "brand", "itemListElement", "@type"}:
                        walk(item)
                    elif isinstance(item, (dict, list)):
                        walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)
            elif isinstance(value, str):
                terms.append(value)

        walk(data)
        return terms


class KeywordResearchAgent(BaseAgent):
    NAME = "Keyword Research Agent"
    DESCRIPTION = "Discover keyword opportunities from a seed keyword, user context, and crawl-visible website topics."
    ICON = "fa-key"
    CATEGORY = "Keyword Research"
    INPUT_SCHEMA = [
        {"id": "website_url", "label": "Website URL", "type": "text", "required": True},
        {"id": "seed_keyword", "label": "Seed Keyword", "type": "text", "required": True},
        {"id": "country", "label": "Country", "type": "text", "required": True},
        {"id": "language", "label": "Language", "type": "text", "required": True},
        {"id": "brand_name", "label": "Brand Name", "type": "text", "required": False},
        {"id": "industry", "label": "Industry", "type": "text", "required": False},
        {"id": "target_audience", "label": "Target Audience", "type": "text", "required": False},
        {"id": "business_goal", "label": "Business Goal", "type": "text", "required": False},
        {"id": "product_or_service", "label": "Product or Service", "type": "text", "required": False},
        {"id": "location", "label": "Location", "type": "text", "required": False},
        {"id": "competitor_urls", "label": "Competitor URLs", "type": "textarea", "required": False},
        {"id": "keyword_limit", "label": "Keyword Limit", "type": "number", "required": False},
    ]

    def run(self, input_data: dict) -> dict:
        website_url = ensure_url(input_data.get("website_url") or "")
        seed = self._clean_phrase(input_data.get("seed_keyword") or input_data.get("keyword") or "")
        country = (input_data.get("country") or input_data.get("location") or "").strip()
        language = (input_data.get("language") or "").strip()
        if not website_url:
            return self.missing_input_response("website_url", input_data)
        if not seed:
            return self.missing_input_response("seed_keyword", input_data)
        if not country:
            return self.missing_input_response("country", input_data)
        if not language:
            return self.missing_input_response("language", input_data)

        crawl_status = {"requested_url": website_url, "status": "not_started", "message": "", "pages_crawled": 0}
        source_urls = []
        crawl_terms = []
        website_documents = []
        pages = []
        try:
            crawl = crawl_site(website_url, depth=1, limit=8, max_link_checks_per_page=0, audit_timeout=12, audit_retries=0)
            pages = [page for page in crawl.get("pages", []) if not page.get("error")]
            source_urls = crawl.get("crawled_urls", [])
            website_documents = self._fetch_keyword_documents(source_urls[:8])
            crawl_terms = self._extract_crawl_terms(pages, website_documents)
            crawl_status = {
                "requested_url": website_url,
                "status": "available" if pages else "unavailable",
                "message": f"Used {len(pages)} crawl-visible page(s)." if pages else "Website context was unavailable from the crawl.",
                "pages_crawled": len(pages),
            }
        except Exception as exc:
            crawl_status = {
                "requested_url": website_url,
                "status": "unavailable",
                "message": f"Website context was unavailable: {str(exc)}",
                "pages_crawled": 0,
            }

        competitor_urls = self.parse_competitors(input_data.get("competitor_urls") or input_data.get("competitors") or [])
        competitor_documents, competitor_source_urls, competitor_warnings = self._crawl_competitors(competitor_urls)
        competitor_terms = self._extract_document_terms(competitor_documents)
        source_urls = self._dedupe(source_urls + competitor_source_urls)

        context = self._build_context(input_data, seed, country, crawl_terms, competitor_terms)
        grouped = self._generate_keyword_groups(seed, context, input_data)
        opportunities = self._build_opportunities(grouped, seed, context, input_data)
        keyword_limit = self._positive_int(input_data.get("keyword_limit"), 80)
        opportunities = opportunities[:keyword_limit]
        api_used, premium_status, api_error = self._enrich_with_dataforseo(opportunities, input_data, country, language)
        grouped = self._sync_group_lists(grouped, opportunities)
        mode = "premium" if api_used else "free"

        unavailable_metrics = [
            {"metric": "search_volume", "status": UNAVAILABLE},
            {"metric": "cpc", "status": UNAVAILABLE},
            {"metric": "competition", "status": UNAVAILABLE},
            {"metric": "keyword_difficulty", "status": UNAVAILABLE},
            {"metric": "trend", "status": UNAVAILABLE},
            {"metric": "serp_features", "status": UNAVAILABLE},
        ]
        data_sources = [
            {"name": "User input", "status": "Connected", "detail": "Seed keyword, country, language, and optional business context."},
            {"name": "Live website crawl", "status": "Connected" if pages else "Not Connected", "detail": crawl_status["message"]},
            {"name": "Website content", "status": "Connected" if website_documents else "Not Connected", "detail": "Titles, meta descriptions, headings, navigation labels, URL slugs, and visible text."},
            {"name": "Competitor crawl", "status": "Connected" if competitor_documents else ("Not Connected" if competitor_urls else "Not Provided"), "detail": f"Used {len(competitor_documents)} competitor page(s)." if competitor_documents else "; ".join(competitor_warnings[:2])},
            {"name": "Local NLP extraction", "status": "Connected", "detail": "N-grams, phrase frequency, heading analysis, URL slug analysis, and token similarity."},
            {"name": "DataForSEO", "status": premium_status, "detail": api_error or UNAVAILABLE},
        ]
        recommendations = self._recommendations(opportunities, pages)
        summary = (
            f"Generated {len(opportunities)} keyword opportunities for '{seed}' from user context"
            f", {len(pages)} website page(s), and {len(competitor_documents)} competitor page(s)."
            f" Mode: {'Premium API Mode' if mode == 'premium' else 'Free Mode'}."
        )
        return {
            "success": True,
            "agent": self.NAME,
            "agent_id": "keyword_research",
            "agent_name": self.NAME,
            "mode": mode,
            "data_sources": data_sources,
            "api_used": api_used,
            "crawl_status": crawl_status,
            "source_urls": source_urls,
            "input_summary": {
                "website_url": website_url,
                "seed_keyword": seed,
                "country": country,
                "language": language,
                "brand_name": input_data.get("brand_name") or "",
                "industry": input_data.get("industry") or "",
                "target_audience": input_data.get("target_audience") or "",
                "business_goal": input_data.get("business_goal") or "",
            },
            "website_topics_detected": context["website_topics"],
            "competitor_topics_detected": context["competitor_topics"],
            "existing_terms_detected": context["existing_terms"],
            "primary_keywords": grouped["Primary Keywords"],
            "secondary_keywords": grouped["Secondary Keywords"],
            "long_tail_keywords": grouped["Long-Tail Keywords"],
            "question_keywords": grouped["Question Keywords"],
            "commercial_keywords": grouped["Commercial Keywords"],
            "transactional_keywords": grouped["Transactional Keywords"],
            "local_keywords": grouped["Local Keywords"],
            "comparison_keywords": grouped["Comparison Keywords"],
            "problem_solution_keywords": grouped["Problem/Solution Keywords"],
            "problem_keywords": grouped["Problem/Solution Keywords"],
            "brand_keywords": grouped["Brand Keywords"],
            "keyword_opportunities": opportunities,
            "unavailable_metrics": unavailable_metrics,
            "local_calculation_methods": [
                "Optional YAKE, KeyBERT, RAKE, and spaCy noun chunk extraction when installed",
                "Built-in TF-IDF and n-gram extraction",
                "Seed keyword token similarity",
                "Crawl-visible website topic matching",
                "Industry, audience, and business-goal token matching",
                "Intent and funnel classification from keyword modifiers",
            ],
            "premium_metrics_status": premium_status if api_used else UNAVAILABLE,
            "recommendations": recommendations,
            "summary": summary,
            "message": "Completed successfully.",
        }

    def _enrich_with_dataforseo(self, opportunities: list, input_data: dict, country: str, language: str) -> tuple[list, str, str]:
        credentials = self.get_dataforseo_credentials(input_data)
        if not credentials["login"] or not credentials["password"] or not opportunities:
            return [], "Not Connected", UNAVAILABLE
        keywords = [item["keyword"] for item in opportunities[:100]]
        payload = [{
            "keywords": keywords,
            "location_name": country,
            "language_name": language,
        }]
        result, error = self.dataforseo_post("keywords_data/google_ads/search_volume/live", payload, credentials)
        if error:
            return [], "Not Connected", error
        items = result.get("items", []) if isinstance(result, dict) else []
        metrics_by_keyword = {str(item.get("keyword", "")).lower(): item for item in items if item.get("keyword")}
        if not metrics_by_keyword:
            return [], "Not Connected", "DataForSEO returned no keyword metric rows."
        for opportunity in opportunities:
            metrics = metrics_by_keyword.get(opportunity["keyword"].lower())
            if not metrics:
                continue
            opportunity["search_volume"] = metrics.get("search_volume")
            opportunity["cpc"] = metrics.get("cpc")
            opportunity["competition"] = metrics.get("competition")
            opportunity["keyword_difficulty"] = metrics.get("competition_index")
            opportunity["trend"] = metrics.get("monthly_searches")
            opportunity["serp_features"] = None
            opportunity["metric_source"] = "DataForSEO Google Ads Search Volume API"
        return ["DataForSEO Google Ads Search Volume API"], "Connected", ""

    def _fetch_keyword_documents(self, urls: list) -> list:
        documents = []
        for url in urls:
            try:
                fetched = fetch_url(url, timeout=8, retries=0)
                parser = KeywordHTMLExtractor()
                parser.feed(fetched.get("text") or "")
                parsed = urlparse(fetched.get("final_url") or url)
                documents.append({
                    "url": fetched.get("final_url") or url,
                    "title": parser.title,
                    "meta_description": parser.meta_description,
                    "h1": parser.h1,
                    "h2": parser.h2,
                    "h3": parser.h3,
                    "navigation_labels": parser.navigation_labels,
                    "footer_links": parser.footer_links,
                    "breadcrumbs": parser.breadcrumbs,
                    "image_alt": parser.image_alt,
                    "structured_data_terms": parser.structured_data_terms,
                    "anchor_texts": parser.anchor_texts,
                    "visible_text": parser.visible_text[:250],
                    "url_slugs": re.split(r"[-_/]+", parsed.path),
                })
            except Exception:
                continue
        return documents

    def _crawl_competitors(self, urls: list) -> tuple[list, list, list]:
        documents = []
        source_urls = []
        warnings = []
        for raw_url in urls[:6]:
            url = ensure_url(raw_url)
            if not url:
                continue
            try:
                crawl = crawl_site(url, depth=0, limit=1, max_link_checks_per_page=0, audit_timeout=10, audit_retries=0)
                page_urls = crawl.get("crawled_urls") or [url]
                source_urls.extend(page_urls)
                docs = self._fetch_keyword_documents(page_urls[:1])
                if not docs:
                    docs = [self._document_from_crawl_page(page) for page in crawl.get("pages", []) if not page.get("error")]
                documents.extend(docs)
                if not docs:
                    warnings.append(f"Competitor crawl unavailable for {url}.")
            except Exception as exc:
                warnings.append(f"Competitor crawl unavailable for {url}: {str(exc)}")
        return documents, source_urls, warnings

    def _document_from_crawl_page(self, page: dict) -> dict:
        parsed = urlparse(page.get("url") or "")
        return {
            "url": page.get("url") or "",
            "title": page.get("title") or "",
            "meta_description": page.get("meta_description") or "",
            "h1": page.get("h1") or [],
            "h2": page.get("h2") or [],
            "h3": page.get("h3") or [],
            "navigation_labels": [],
            "footer_links": [],
            "breadcrumbs": [],
            "image_alt": [],
            "structured_data_terms": [],
            "anchor_texts": [],
            "visible_text": [],
            "url_slugs": re.split(r"[-_/]+", parsed.path),
        }

    def _extract_document_terms(self, documents: list) -> list:
        values = []
        weighted = []
        for document in documents:
            priority_values = []
            priority_values.extend([document.get("title", ""), document.get("meta_description", "")])
            priority_values.extend(document.get("h1") or [])
            priority_values.extend(document.get("h2") or [])
            priority_values.extend(document.get("h3") or [])
            priority_values.extend(document.get("navigation_labels") or [])
            priority_values.extend(document.get("footer_links") or [])
            priority_values.extend(document.get("breadcrumbs") or [])
            priority_values.extend(document.get("image_alt") or [])
            priority_values.extend(document.get("structured_data_terms") or [])
            priority_values.extend(document.get("anchor_texts") or [])
            priority_values.extend(document.get("url_slugs") or [])
            body_values = document.get("visible_text") or []
            values.extend(priority_values + body_values)
            weighted.extend(priority_values * 2)
            weighted.extend(body_values)
        return [item["keyword"] for item in self._keyword_candidates(weighted or values)[:60]]

    def _keyword_candidates(self, texts: list) -> list:
        corpus = [self._clean_phrase(text) for text in texts if self._clean_phrase(text)]
        if not corpus:
            return []
        full_text = " ".join(corpus)
        scored = {}

        def add(keyword: str, score: float, method: str):
            cleaned = self._clean_phrase(keyword)
            if not self._valid_keyword(cleaned):
                return
            current = scored.setdefault(cleaned, {"keyword": cleaned, "confidence": 0.0, "methods": set()})
            current["confidence"] = max(current["confidence"], score)
            current["methods"].add(method)

        # Priority 1: YAKE, if available.
        try:
            import yake
            extractor = yake.KeywordExtractor(lan="en", n=3, top=40)
            for keyword, yake_score in extractor.extract_keywords(full_text):
                add(keyword, max(0.1, 1 / (1 + float(yake_score))), "YAKE")
        except Exception:
            pass

        # Priority 2: KeyBERT, if available. This is intentionally optional because it is heavy.
        try:
            from keybert import KeyBERT
            model = KeyBERT()
            for keyword, score in model.extract_keywords(full_text, keyphrase_ngram_range=(1, 3), stop_words="english", top_n=30):
                add(keyword, float(score), "KeyBERT")
        except Exception:
            pass

        # Priority 3: RAKE, if available.
        try:
            from rake_nltk import Rake
            rake = Rake()
            rake.extract_keywords_from_text(full_text)
            for score, keyword in rake.get_ranked_phrases_with_scores()[:40]:
                add(keyword, min(1.0, float(score) / 20.0), "RAKE")
        except Exception:
            pass

        # Priority 4: built-in TF-IDF over collected page snippets.
        tfidf_scores = self._tfidf_candidate_scores(corpus)
        for keyword, score in tfidf_scores.items():
            add(keyword, score, "TF-IDF")

        # Priority 5: spaCy noun chunks, if available and a model is installed.
        try:
            import spacy
            try:
                nlp = spacy.load("en_core_web_sm")
            except Exception:
                nlp = spacy.blank("en")
            doc = nlp(full_text[:100000])
            chunks = getattr(doc, "noun_chunks", [])
            for chunk in chunks:
                add(chunk.text, 0.62, "spaCy noun chunks")
        except Exception:
            pass

        # Priority 6: deterministic n-grams and phrase frequency fallback.
        phrase_counts = Counter()
        for text in corpus:
            for phrase in self._candidate_phrases(text):
                if self._valid_keyword(phrase):
                    phrase_counts[phrase] += 1
        max_count = max(phrase_counts.values()) if phrase_counts else 1
        for phrase, count in phrase_counts.items():
            add(phrase, 0.25 + (0.5 * count / max_count), "n-grams")

        ranked = []
        for item in scored.values():
            method_bonus = min(0.25, 0.05 * len(item["methods"]))
            ranked.append({
                "keyword": item["keyword"],
                "confidence": round(min(1.0, item["confidence"] + method_bonus), 4),
                "methods": sorted(item["methods"]),
            })
        return sorted(ranked, key=lambda item: (-item["confidence"], len(item["keyword"].split()), item["keyword"]))

    def _tfidf_candidate_scores(self, corpus: list) -> dict:
        docs = [self._candidate_phrases(text) for text in corpus]
        doc_count = len(docs)
        document_frequency = Counter(phrase for doc in docs for phrase in set(doc))
        scores = Counter()
        for doc in docs:
            counts = Counter(doc)
            for phrase, count in counts.items():
                if not self._valid_keyword(phrase):
                    continue
                idf = math.log((doc_count + 1) / (document_frequency[phrase] + 1)) + 1
                scores[phrase] += count * idf
        max_score = max(scores.values()) if scores else 1
        return {phrase: min(1.0, score / max_score) for phrase, score in scores.items()}

    def _extract_crawl_terms(self, pages: list, documents: list) -> list:
        values = []
        for page in pages:
            values.extend([page.get("title", ""), page.get("meta_description", "")])
            values.extend(page.get("h1") or [])
            values.extend(page.get("h2") or [])
            parsed = urlparse(page.get("url") or "")
            values.extend(re.split(r"[-_/]+", parsed.path))
        document_terms = self._extract_document_terms(documents)
        fallback_terms = self._extract_document_terms([{"visible_text": values}])
        return self._dedupe(document_terms + fallback_terms)[:60]

    def _build_context(self, input_data: dict, seed: str, country: str, crawl_terms: list, competitor_terms: list) -> dict:
        user_terms = []
        for key in ["industry", "target_audience", "business_goal", "product_or_service", "brand_name"]:
            user_terms.extend(self._candidate_phrases(self._clean_phrase(input_data.get(key) or "")))
        website_topics = self._dedupe([seed] + crawl_terms[:35] + user_terms[:25])
        discovery_topics = self._dedupe(website_topics + competitor_terms[:15])
        return {
            "seed_tokens": set(self._tokens(seed)),
            "website_topics": website_topics[:25],
            "discovery_topics": discovery_topics[:35],
            "competitor_topics": self._dedupe(competitor_terms)[:25],
            "existing_terms": self._dedupe(crawl_terms)[:30],
            "industry_tokens": set(self._tokens(input_data.get("industry") or "")),
            "audience_tokens": set(self._tokens(input_data.get("target_audience") or "")),
            "goal_tokens": set(self._tokens(input_data.get("business_goal") or "")),
            "country_tokens": set(self._tokens(country)),
            "country": country,
            "location": (input_data.get("location") or country).strip(),
        }

    def _generate_keyword_groups(self, seed: str, context: dict, input_data: dict) -> dict:
        topics = [item for item in context["discovery_topics"] if item != seed][:12]
        competitor_topics = [item for item in context["competitor_topics"] if item != seed][:6]
        product = self._clean_phrase(input_data.get("product_or_service") or "")
        base_topics = self._dedupe([seed, product] + topics + competitor_topics)[:16]
        location = context["location"]
        country = context["country"]
        groups = {
            "Primary Keywords": [seed] + [item for item in base_topics if seed in item or item in seed][:4],
            "Secondary Keywords": [item for item in base_topics if item != seed][:10],
            "Long-Tail Keywords": [],
            "Question Keywords": [],
            "Commercial Keywords": [],
            "Transactional Keywords": [],
            "Local Keywords": [],
            "Comparison Keywords": [],
            "Problem/Solution Keywords": [],
            "Brand Keywords": [],
        }
        brand = self._clean_phrase(input_data.get("brand_name") or "")
        for topic in base_topics[:8]:
            groups["Long-Tail Keywords"].extend([f"{topic} guide", f"{topic} for {self._audience_label(input_data)}"])
            groups["Question Keywords"].extend([f"what is {topic}", f"how to choose {topic}", f"why use {topic}"])
            groups["Commercial Keywords"].extend([f"best {topic}", f"{topic} pricing", f"{topic} reviews"])
            groups["Transactional Keywords"].extend([f"buy {topic}", f"{topic} service", f"hire {topic} provider"])
            groups["Local Keywords"].extend([f"{topic} in {location}", f"{topic} near me", f"local {topic}"])
            groups["Comparison Keywords"].extend([f"{topic} alternatives", f"{topic} comparison", f"{topic} vs competitors"])
            groups["Problem/Solution Keywords"].extend([f"{topic} problems", f"{topic} solutions", f"how to fix {topic} issues"])
            if brand:
                groups["Brand Keywords"].extend([f"{brand} {topic}", f"{brand} {topic} pricing", f"{brand} {topic} reviews"])
        if country and country.lower() != location.lower():
            groups["Local Keywords"].append(f"{seed} in {country}")
        if brand:
            groups["Brand Keywords"].extend([brand, f"{brand} {seed}"])
        return {key: self._dedupe([item for item in values if self._valid_keyword(item)])[:15] for key, values in groups.items()}

    def _build_opportunities(self, grouped: dict, seed: str, context: dict, input_data: dict) -> list:
        opportunities = []
        seen = set()
        for keyword_type, keywords in grouped.items():
            for keyword in keywords:
                key = keyword.lower()
                if key in seen:
                    continue
                seen.add(key)
                score, reasons = self._relevance(keyword, seed, context)
                opportunities.append({
                    "keyword": keyword,
                    "keyword_type": keyword_type,
                    "search_intent": self._intent(keyword),
                    "funnel_stage": self._funnel(keyword),
                    "relevance_score": score,
                    "relevance_priority": "High Priority" if score >= 80 else ("Medium Priority" if score >= 50 else "Low Priority"),
                    "relevance_reason": reasons,
                    "source": self._keyword_source(keyword, context),
                    "search_volume": None,
                    "cpc": None,
                    "competition": None,
                    "keyword_difficulty": None,
                    "trend": None,
                    "serp_features": None,
                    "metric_source": UNAVAILABLE,
                })
        return sorted(opportunities, key=lambda item: (-item["relevance_score"], item["keyword"]))

    def _relevance(self, keyword: str, seed: str, context: dict) -> tuple[int, list]:
        tokens = set(self._tokens(keyword))
        reasons = []
        seed_score = round(25 * self._jaccard(tokens, context["seed_tokens"]))
        topic_pool = context.get("discovery_topics") or context["website_topics"]
        topic_score = round(25 * max([self._jaccard(tokens, set(self._tokens(topic))) for topic in topic_pool] or [0]))
        industry_score = round(15 * self._jaccard(tokens, context["industry_tokens"])) if context["industry_tokens"] else 0
        audience_score = round(10 * self._jaccard(tokens, context["audience_tokens"])) if context["audience_tokens"] else 0
        goal_score = round(15 * self._jaccard(tokens, context["goal_tokens"])) if context["goal_tokens"] else 0
        commercial_score = 10 if self._intent(keyword) in {"Commercial Investigation", "Transactional", "Local"} else 4
        country_match = bool(tokens & context.get("country_tokens", set())) or self._intent(keyword) == "Local"
        score = min(100, seed_score + topic_score + industry_score + audience_score + goal_score + commercial_score)
        reasons.extend([
            f"Seed similarity: {seed_score}/25",
            f"Website/competitor topic match: {topic_score}/25",
            f"Industry match: {industry_score}/15",
            f"Audience match: {audience_score}/10",
            f"Business-goal match: {goal_score}/15",
            f"Commercial intent value: {commercial_score}/10",
            f"Country relevance: {'matched local/country wording' if country_match else 'not directly country-specific'}",
        ])
        return score, reasons

    def _keyword_source(self, keyword: str, context: dict) -> str:
        keyword_tokens = set(self._tokens(keyword))
        if any(keyword_tokens & set(self._tokens(topic)) for topic in context.get("website_topics", [])):
            return "Website crawl + local NLP"
        if any(keyword_tokens & set(self._tokens(topic)) for topic in context.get("competitor_topics", [])):
            return "Competitor crawl + local NLP"
        if keyword_tokens & context.get("industry_tokens", set()):
            return "User industry input + local NLP"
        if keyword_tokens & context.get("audience_tokens", set()):
            return "User audience input + local NLP"
        if keyword_tokens & context.get("goal_tokens", set()):
            return "User business-goal input + local NLP"
        return "Seed keyword + local NLP"

    def _sync_group_lists(self, grouped: dict, opportunities: list) -> dict:
        by_type = {}
        for item in opportunities:
            by_type.setdefault(item["keyword_type"], []).append(item)
        return {key: by_type.get(key, []) for key in grouped}

    def _intent(self, keyword: str) -> str:
        lowered = keyword.lower()
        if any(token in lowered for token in ["near me", " local ", " in "]):
            return "Local"
        if any(token in lowered for token in ["buy", "hire", "book", "order", "service", "provider", "company"]):
            return "Transactional"
        if any(token in lowered for token in ["best", "top", "review", "alternatives", "comparison", "pricing", "cost", " vs "]):
            return "Commercial Investigation"
        if any(lowered.startswith(prefix) for prefix in ["what", "how", "why", "guide", "tips"]):
            return "Informational"
        return "Informational"

    def _funnel(self, keyword: str) -> str:
        intent = self._intent(keyword)
        if intent in {"Transactional", "Local"}:
            return "BOFU"
        if intent == "Commercial Investigation":
            return "MOFU"
        return "TOFU"

    def _candidate_phrases(self, text: str) -> list:
        tokens = self._tokens(text)
        phrases = []
        for size in [3, 2]:
            for index in range(0, max(0, len(tokens) - size + 1)):
                phrases.append(" ".join(tokens[index:index + size]))
        phrases.extend(tokens)
        return phrases

    def _tokens(self, text: str) -> list:
        return [token for token in re.findall(r"[a-z0-9]+", (text or "").lower()) if token not in STOPWORDS and len(token) > 1]

    def _clean_phrase(self, value: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9\s&+-]", " ", str(value or ""))).strip().lower()

    def _valid_keyword(self, value: str) -> bool:
        tokens = self._tokens(value)
        return 1 <= len(tokens) <= 8 and not self._is_noise(value)

    def _is_noise(self, value: str) -> bool:
        return not self._tokens(value) or len(value) < 2

    def _dedupe(self, values: list) -> list:
        seen = set()
        output = []
        for value in values:
            cleaned = self._clean_phrase(value)
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                output.append(cleaned)
        return output

    def _jaccard(self, a: set, b: set) -> float:
        if not a or not b:
            return 0.0
        return len(a & b) / len(a | b)

    def _audience_label(self, input_data: dict) -> str:
        audience = self._clean_phrase(input_data.get("target_audience") or "")
        return audience if audience and len(audience.split()) <= 4 else "customers"

    def _positive_int(self, value, fallback: int) -> int:
        try:
            parsed = int(value)
            return parsed if parsed > 0 else fallback
        except (TypeError, ValueError):
            return fallback

    def _recommendations(self, opportunities: list, pages: list) -> list:
        high = [item["keyword"] for item in opportunities if item["relevance_priority"] == "High Priority"]
        recs = ["Use the relevance score as a local priority signal, not keyword difficulty."]
        if high:
            recs.append(f"Start validation with high-priority terms such as {', '.join(high[:3])}.")
        if not pages:
            recs.append("Crawl-visible website context was unavailable, so add industry, audience, and product/service inputs for better ideas.")
        recs.append("Connect DataForSEO, Semrush, Ahrefs, Moz, or Google Ads Keyword Planner for genuine volume, CPC, difficulty, and SERP metrics.")
        return recs
