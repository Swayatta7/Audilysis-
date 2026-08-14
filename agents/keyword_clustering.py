import math
import re
from collections import Counter, defaultdict

from agents.base_agent import BaseAgent


STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "is", "it", "of", "on",
    "or", "the", "this", "to", "vs", "with", "near", "me", "online", "best", "buy", "hire", "service",
    "services", "pricing", "price", "cost", "features", "guide", "how", "what", "why",
}
MODIFIERS = {
    "best", "buy", "pricing", "price", "cost", "review", "reviews", "alternatives", "comparison",
    "compare", "near", "local", "online", "features", "benefits", "guide", "how", "what", "why",
}


class KeywordClusteringAgent(BaseAgent):
    NAME = "Keyword Clustering Agent"
    DESCRIPTION = "Organize an existing keyword list into SEO topic clusters and content architecture."
    ICON = "fa-layer-group"
    CATEGORY = "Keyword Research"
    INPUT_SCHEMA = [
        {"id": "keyword_list", "label": "Keyword List", "type": "keyword_list", "required": True, "placeholder": "iPhone\nbuy iPhone online\niPhone camera features", "help_text": "One keyword per line. Provide at least two valid keywords."},
        {"id": "website_url", "label": "Website URL", "type": "url", "required": False, "placeholder": "https://example.com"},
        {"id": "country", "label": "Country", "type": "text", "required": False, "placeholder": "United States"},
        {"id": "language", "label": "Language", "type": "text", "required": False, "placeholder": "en"},
        {"id": "similarity_threshold", "label": "Similarity Threshold", "type": "number", "required": False, "min": 0.05, "max": 0.95, "default": 0.24},
        {"id": "minimum_cluster_size", "label": "Minimum Cluster Size", "type": "number", "required": False, "min": 2, "default": 2},
        {"id": "use_embeddings", "label": "Use Embeddings", "type": "checkbox", "required": False, "default": False},
        {"id": "use_serp_similarity", "label": "Use SERP Similarity", "type": "checkbox", "required": False, "default": False},
    ]

    def run(self, input_data: dict) -> dict:
        raw = input_data.get("keyword_list")
        run_context = input_data.get("_tracker_run_context") or {}
        run_data = run_context.get("run") or {}
        if not raw:
            raw = (run_data.get("high_volume_keywords") or []) + (run_data.get("brand_keywords") or [])
        parsed, total_received, duplicates_removed, invalid_rows_removed = self._parse_keyword_list(raw)
        if len(parsed) < 2:
            return self.missing_input_response("keyword_list", input_data, "Provide at least 2 valid keywords, one per line.")

        threshold = self._float(input_data.get("similarity_threshold"), 0.24)
        threshold = min(max(threshold, 0.05), 0.95)
        minimum_cluster_size = max(2, self._int(input_data.get("minimum_cluster_size"), 2))

        profiles = [self._profile(keyword) for keyword in parsed]
        tfidf = self._tfidf_vectors([profile["tokens"] for profile in profiles])
        embedding_similarities, embedding_method = self._embedding_similarity_matrix(parsed)
        similarities = self._similarity_matrix(profiles, tfidf, embedding_similarities)
        groups = self._build_groups(parsed, similarities, threshold)

        clusters = []
        unclustered = []
        for group in groups:
            if len(group) < minimum_cluster_size:
                unclustered.extend(parsed[index] for index in group)
                continue
            cluster = self._make_cluster(len(clusters) + 1, group, parsed, profiles, similarities)
            if cluster["average_similarity_score"] <= 0:
                unclustered.extend(parsed[index] for index in group)
            else:
                clusters.append(cluster)

        self._attach_related_clusters(clusters)
        content_architecture = [self._content_architecture(cluster) for cluster in clusters]
        internal_linking_plan = [link for cluster in clusters for link in cluster["internal_linking_suggestions"]]
        data_sources = [
            {"name": "User input keyword list", "status": "Connected", "detail": f"{len(parsed)} valid unique keywords."},
            {"name": "Local NLP calculations", "status": "Connected", "detail": "TF-IDF cosine, Jaccard, entity overlap, modifier overlap, and intent compatibility."},
            {"name": "Embeddings API", "status": "Not Connected", "detail": "Skipped unless a genuine embeddings API is configured."},
            {"name": "SERP similarity API", "status": "Not Connected", "detail": "Skipped because no genuine SERP API result was available."},
        ]
        recommendations = self._recommendations(clusters, unclustered)
        summary = (
            f"Clustered {len(parsed)} valid supplied keyword(s) into {len(clusters)} topic cluster(s)"
            f" using local similarity calculations. {len(unclustered)} keyword(s) remained unclustered."
        )
        return {
            "success": True,
            "agent": self.NAME,
            "agent_id": "keyword_clustering",
            "agent_name": self.NAME,
            "data_sources": data_sources,
            "api_used": [],
            "input_summary": {
                "website_url": input_data.get("website_url") or "",
                "country": input_data.get("country") or input_data.get("location") or "",
                "language": input_data.get("language") or "",
                "target_audience": input_data.get("target_audience") or "",
                "business_goal": input_data.get("business_goal") or "",
                "similarity_threshold": threshold,
                "minimum_cluster_size": minimum_cluster_size,
                "source_run_id": run_context.get("run_id"),
            },
            "tracker_keyword_groups": {
                "high_volume_keywords": run_data.get("high_volume_keywords") or [],
                "brand_keywords": run_data.get("brand_keywords") or [],
                "used_when_keyword_list_missing": bool(run_context and not input_data.get("keyword_list")),
            },
            "total_keywords_received": total_received,
            "valid_keywords": len(parsed),
            "duplicates_removed": duplicates_removed,
            "invalid_rows_removed": invalid_rows_removed,
            "number_of_clusters": len(clusters),
            "clusters": clusters,
            "unclustered_keywords": unclustered,
            "content_architecture": content_architecture,
            "internal_linking_plan": internal_linking_plan,
            "methodology": {
                "normalization": "Trimmed whitespace, collapsed repeated spacing, removed blank rows and exact duplicates.",
                "local_similarity_weights": {
                    "tfidf_cosine_similarity": "32%" if embedding_similarities else "40%",
                    "token_jaccard_similarity": "16%" if embedding_similarities else "20%",
                    "shared_entity_score": "16%" if embedding_similarities else "20%",
                    "intent_compatibility": "8%" if embedding_similarities else "10%",
                    "shared_modifier_score": "8%" if embedding_similarities else "10%",
                    "sentence_embedding_similarity": "20%" if embedding_similarities else "Unavailable",
                },
                "embeddings": embedding_method,
                "serp_overlap": "Unavailable in Free Mode",
            },
            "recommendations": recommendations,
            "summary": summary,
            "message": "Completed successfully.",
        }

    def _parse_keyword_list(self, raw) -> tuple[list, int, int, int]:
        rows = raw if isinstance(raw, list) else str(raw or "").splitlines()
        total = len([row for row in rows if str(row).strip()])
        seen = set()
        parsed = []
        duplicates = 0
        invalid = 0
        for row in rows:
            original = re.sub(r"\s+", " ", str(row or "")).strip()
            if not original:
                continue
            if len(self._tokens(original, keep_modifiers=True)) == 0:
                invalid += 1
                continue
            key = original.lower()
            if key in seen:
                duplicates += 1
                continue
            seen.add(key)
            parsed.append(original)
        return parsed, total, duplicates, invalid

    def _profile(self, keyword: str) -> dict:
        all_tokens = self._tokens(keyword, keep_modifiers=True)
        entity_tokens = [token for token in all_tokens if token not in STOPWORDS and token not in MODIFIERS]
        if not entity_tokens and all_tokens:
            entity_tokens = [all_tokens[0]]
        modifiers = sorted(set(token for token in all_tokens if token in MODIFIERS))
        return {
            "keyword": keyword,
            "tokens": all_tokens,
            "token_set": set(all_tokens),
            "entities": set(entity_tokens),
            "entity_sequence": entity_tokens,
            "modifiers": set(modifiers),
            "primary_entity": entity_tokens[0] if entity_tokens else "",
            "intent": self._intent(keyword),
            "funnel": self._funnel(keyword),
        }

    def _embedding_similarity_matrix(self, keywords: list) -> tuple[list | None, str]:
        try:
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer("all-MiniLM-L6-v2")
            vectors = model.encode(keywords, normalize_embeddings=True)
            matrix = [[0.0 for _ in keywords] for _ in keywords]
            for i in range(len(keywords)):
                for j in range(i + 1, len(keywords)):
                    score = float(sum(vectors[i][k] * vectors[j][k] for k in range(len(vectors[i]))))
                    matrix[i][j] = matrix[j][i] = round(score, 4)
            return matrix, "SentenceTransformer all-MiniLM-L6-v2"
        except Exception:
            return None, "Unavailable in Free Mode"

    def _similarity_matrix(self, profiles: list, tfidf: list, embedding_similarities: list | None = None) -> list:
        matrix = [[0.0 for _ in profiles] for _ in profiles]
        for i in range(len(profiles)):
            for j in range(i + 1, len(profiles)):
                cosine = self._cosine(tfidf[i], tfidf[j])
                jaccard = self._jaccard(profiles[i]["token_set"], profiles[j]["token_set"])
                entity = self._jaccard(profiles[i]["entities"], profiles[j]["entities"])
                intent = 1.0 if profiles[i]["intent"] == profiles[j]["intent"] else (0.35 if profiles[i]["funnel"] == profiles[j]["funnel"] else 0.0)
                modifier = self._jaccard(profiles[i]["modifiers"], profiles[j]["modifiers"])
                if embedding_similarities:
                    embedding = max(0.0, embedding_similarities[i][j])
                    score = (0.32 * cosine) + (0.16 * jaccard) + (0.16 * entity) + (0.08 * intent) + (0.08 * modifier) + (0.20 * embedding)
                else:
                    score = (0.4 * cosine) + (0.2 * jaccard) + (0.2 * entity) + (0.1 * intent) + (0.1 * modifier)
                if profiles[i]["primary_entity"] and profiles[j]["primary_entity"] and profiles[i]["primary_entity"] != profiles[j]["primary_entity"]:
                    score *= 0.2
                elif profiles[i]["entities"].isdisjoint(profiles[j]["entities"]):
                    score *= 0.35
                matrix[i][j] = matrix[j][i] = round(score, 4)
        return matrix

    def _build_groups(self, keywords: list, similarities: list, threshold: float) -> list:
        parent = list(range(len(keywords)))

        def find(index):
            while parent[index] != index:
                parent[index] = parent[parent[index]]
                index = parent[index]
            return index

        def union(a, b):
            root_a, root_b = find(a), find(b)
            if root_a != root_b:
                parent[root_b] = root_a

        for i in range(len(keywords)):
            for j in range(i + 1, len(keywords)):
                if similarities[i][j] >= threshold:
                    union(i, j)

        groups = defaultdict(list)
        for index in range(len(keywords)):
            groups[find(index)].append(index)
        return list(groups.values())

    def _make_cluster(self, number: int, group: list, keywords: list, profiles: list, similarities: list) -> dict:
        group_profiles = [profiles[index] for index in group]
        pillar = self._pillar(group, keywords, profiles, similarities)
        supporting = [keywords[index] for index in group if keywords[index] != pillar]
        intents = Counter(profile["intent"] for profile in group_profiles)
        dominant_intent = intents.most_common(1)[0][0]
        funnel = Counter(profile["funnel"] for profile in group_profiles).most_common(1)[0][0]
        topic = self._cluster_name(group_profiles, pillar)
        pair_scores = [similarities[i][j] for idx, i in enumerate(group) for j in group[idx + 1:]]
        average = round(sum(pair_scores) / len(pair_scores), 2) if pair_scores else 0
        confidence, confidence_reason = self._confidence(group, profiles, similarities, average, dominant_intent)
        page_type = self._page_type(dominant_intent, pillar)
        slug = self._slug(pillar)
        suggestions = self._internal_links(pillar, supporting, topic)
        return {
            "cluster_id": f"cluster-{number}",
            "cluster_name": topic,
            "pillar_keyword": pillar,
            "supporting_keywords": supporting,
            "dominant_search_intent": dominant_intent,
            "funnel_stage": funnel,
            "recommended_page_type": page_type,
            "suggested_page_title": self._title_for(page_type, pillar),
            "suggested_url_slug": slug,
            "suggested_content_angle": self._content_angle(dominant_intent, pillar),
            "average_similarity_score": average,
            "confidence_score": self._confidence_score(group, profiles, similarities, average, dominant_intent),
            "confidence": confidence,
            "confidence_reason": confidence_reason,
            "related_clusters": [],
            "internal_linking_suggestions": suggestions,
        }

    def _pillar(self, group: list, keywords: list, profiles: list, similarities: list) -> str:
        best_index = group[0]
        best_score = -1
        for index in group:
            centrality = sum(similarities[index][other] for other in group if other != index) / max(1, len(group) - 1)
            modifier_penalty = len(profiles[index]["modifiers"]) * 0.08
            length_penalty = max(0, len(profiles[index]["tokens"]) - 3) * 0.03
            entity_coverage = len(profiles[index]["entities"]) / max(1, len(set().union(*(profiles[other]["entities"] for other in group))))
            broad_bonus = 0.45 if len(profiles[index]["tokens"]) <= 2 and not profiles[index]["modifiers"] else 0
            exact_entity_bonus = 0.45 if keywords[index].lower() == profiles[index]["primary_entity"] else 0
            score = centrality + entity_coverage + broad_bonus + exact_entity_bonus - modifier_penalty - length_penalty
            if score > best_score:
                best_score = score
                best_index = index
        return keywords[best_index]

    def _attach_related_clusters(self, clusters: list) -> None:
        for cluster in clusters:
            current = set(self._tokens(cluster["cluster_name"], keep_modifiers=False))
            related = []
            for other in clusters:
                if other["cluster_id"] == cluster["cluster_id"]:
                    continue
                overlap = self._jaccard(current, set(self._tokens(other["cluster_name"], keep_modifiers=False)))
                if overlap > 0:
                    related.append(other["cluster_name"])
            cluster["related_clusters"] = related[:3]

    def _content_architecture(self, cluster: dict) -> dict:
        return {
            "cluster_name": cluster["cluster_name"],
            "pillar_keyword": cluster["pillar_keyword"],
            "supporting_keywords": cluster["supporting_keywords"],
            "dominant_intent": cluster["dominant_search_intent"],
            "funnel_stage": cluster["funnel_stage"],
            "recommended_page_type": cluster["recommended_page_type"],
            "suggested_page_title": cluster["suggested_page_title"],
            "suggested_url_slug": cluster["suggested_url_slug"],
            "suggested_content_angle": cluster["suggested_content_angle"],
            "related_clusters": cluster["related_clusters"],
        }

    def _internal_links(self, pillar: str, supporting: list, topic: str) -> list:
        links = []
        for keyword in supporting[:8]:
            links.append({
                "pillar_page": pillar,
                "supporting_page_idea": self._title_for(self._page_type(self._intent(keyword), keyword), keyword),
                "source_keyword": keyword,
                "destination_keyword": pillar,
                "suggested_anchor_text": pillar,
                "link_direction": "Supporting page to pillar page",
                "relationship_reason": f"Both keywords share the {topic} topic cluster.",
            })
        return links

    def _cluster_name(self, profiles: list, pillar: str) -> str:
        sequences = [profile["entity_sequence"] for profile in profiles if profile["entity_sequence"]]
        if sequences:
            prefix = list(sequences[0])
            for sequence in sequences[1:]:
                next_prefix = []
                for left, right in zip(prefix, sequence):
                    if left == right:
                        next_prefix.append(left)
                    else:
                        break
                prefix = next_prefix
                if not prefix:
                    break
            if prefix:
                return self._cased_prefix(pillar, len(prefix))
        primary_entities = [profile["primary_entity"] for profile in profiles if profile["primary_entity"]]
        if primary_entities:
            return self._cased_prefix(pillar, 1) or Counter(primary_entities).most_common(1)[0][0]
        return pillar

    def _cased_prefix(self, keyword: str, token_count: int) -> str:
        words = re.findall(r"[A-Za-z0-9]+", keyword or "")
        if not words:
            return ""
        return " ".join(words[:token_count])

    def _confidence(self, group: list, profiles: list, similarities: list, average: float, dominant_intent: str) -> tuple[str, list]:
        quality = self._confidence_score(group, profiles, similarities, average, dominant_intent)
        label = "High" if quality >= 0.62 else ("Medium" if quality >= 0.38 else "Low")
        intents = [profiles[index]["intent"] for index in group]
        consistency = intents.count(dominant_intent) / len(intents)
        nearest_external = 0
        for index in group:
            for other in range(len(profiles)):
                if other not in group:
                    nearest_external = max(nearest_external, similarities[index][other])
        separation = max(0, average - nearest_external)
        return label, [
            f"Average within-cluster similarity: {average}",
            f"Separation from nearest other cluster: {round(separation, 2)}",
            f"Intent consistency: {round(consistency, 2)}",
            f"Cluster size: {len(group)}",
        ]

    def _confidence_score(self, group: list, profiles: list, similarities: list, average: float, dominant_intent: str) -> float:
        intents = [profiles[index]["intent"] for index in group]
        consistency = intents.count(dominant_intent) / len(intents)
        nearest_external = 0
        for index in group:
            for other in range(len(profiles)):
                if other not in group:
                    nearest_external = max(nearest_external, similarities[index][other])
        separation = max(0, average - nearest_external)
        quality = (average * 0.45) + (separation * 0.25) + (consistency * 0.2) + (min(len(group), 5) / 5 * 0.1)
        return round(min(1.0, quality), 2)

    def _tfidf_vectors(self, docs: list) -> list:
        doc_count = len(docs)
        document_frequency = Counter(token for doc in docs for token in set(doc))
        vectors = []
        for doc in docs:
            counts = Counter(doc)
            vector = {}
            for token, count in counts.items():
                idf = math.log((doc_count + 1) / (document_frequency[token] + 1)) + 1
                vector[token] = count * idf
            vectors.append(vector)
        return vectors

    def _tokens(self, text: str, keep_modifiers: bool = False) -> list:
        tokens = [token for token in re.findall(r"[a-z0-9]+", str(text or "").lower()) if len(token) > 1]
        if keep_modifiers:
            return tokens
        return [token for token in tokens if token not in STOPWORDS]

    def _intent(self, keyword: str) -> str:
        lowered = keyword.lower()
        if "near me" in lowered or "local " in lowered or re.search(r"\bin\s+[a-z]+", lowered):
            return "Local"
        if any(token in lowered for token in ["buy", "hire", "book", "order", "pricing", "price"]):
            return "Transactional"
        if any(token in lowered for token in ["best", "review", "reviews", "comparison", "alternatives", " vs "]):
            return "Commercial Investigation"
        if lowered.startswith(("what", "how", "why")) or any(token in lowered for token in ["features", "benefits", "guide"]):
            return "Informational"
        return "Informational"

    def _funnel(self, keyword: str) -> str:
        intent = self._intent(keyword)
        if intent in {"Transactional", "Local"}:
            return "BOFU"
        if intent == "Commercial Investigation":
            return "MOFU"
        return "TOFU"

    def _page_type(self, intent: str, keyword: str) -> str:
        lowered = keyword.lower()
        if intent == "Local":
            return "Local Landing Page"
        if " vs " in lowered or "comparison" in lowered or "alternatives" in lowered:
            return "Comparison Page"
        if "buy" in lowered:
            return "Product Page"
        if "service" in lowered or "hire" in lowered:
            return "Service Page"
        if keyword.lower().startswith(("what", "how", "why")):
            return "FAQ Page"
        if intent == "Commercial Investigation":
            return "Category Page"
        if "guide" in lowered:
            return "Guide"
        return "Pillar Page"

    def _title_for(self, page_type: str, keyword: str) -> str:
        if page_type in {"Blog Article", "Guide", "FAQ Page"}:
            return keyword[:1].upper() + keyword[1:]
        return f"{keyword[:1].upper() + keyword[1:]} | {page_type}"

    def _content_angle(self, intent: str, pillar: str) -> str:
        if intent == "Transactional":
            return f"Help visitors take action on {pillar} with clear product, service, or purchase paths."
        if intent == "Commercial Investigation":
            return f"Compare options and decision criteria for {pillar}."
        if intent == "Local":
            return f"Match local demand around {pillar} with location-specific proof and next steps."
        return f"Explain the topic, answer common questions, and route readers toward deeper {pillar} resources."

    def _slug(self, keyword: str) -> str:
        return "/" + "-".join(self._tokens(keyword, keep_modifiers=True))[:80]

    def _cosine(self, a: dict, b: dict) -> float:
        keys = set(a) | set(b)
        dot = sum(a.get(key, 0) * b.get(key, 0) for key in keys)
        norm_a = math.sqrt(sum(value * value for value in a.values()))
        norm_b = math.sqrt(sum(value * value for value in b.values()))
        if not norm_a or not norm_b:
            return 0.0
        return dot / (norm_a * norm_b)

    def _jaccard(self, a: set, b: set) -> float:
        if not a or not b:
            return 0.0
        return len(a & b) / len(a | b)

    def _float(self, value, fallback: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return fallback

    def _int(self, value, fallback: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return fallback

    def _recommendations(self, clusters: list, unclustered: list) -> list:
        recs = ["Use these clusters for content architecture only; this agent does not discover new keyword ideas."]
        if clusters:
            recs.append("Create one primary page for each pillar keyword, then support it with focused pages for the supporting keywords.")
        if unclustered:
            recs.append("Review unclustered keywords separately instead of forcing unrelated topics into one page.")
        recs.append("Connect a genuine embeddings or SERP API only if you want external semantic or ranking-overlap signals.")
        return recs
