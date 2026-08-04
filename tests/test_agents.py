import json
import os
import re
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import requests
from werkzeug.security import generate_password_hash

from app import app
from agents.agent_manager import CONTENT_GROUP, SEO_GROUP, SOCIAL_GROUP, get_all_agents
from agents.base_agent import BaseAgent
from db.storage import create_run, create_user, get_user_by_email, init_db
from services.pdf_generator import generate_pdf_report


class AgentRoutesTestCase(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        app.config['TESTING'] = True
        user = get_user_by_email("agent-tests@example.com")
        if not user:
            user_id = create_user("agent-tests@example.com", generate_password_hash("Password123"))
            user = {"id": user_id, "email": "agent-tests@example.com"}
        self.csrf_token = "test-csrf-token"
        with self.client.session_transaction() as flask_session:
            flask_session["auth_user_id"] = user["id"]
            flask_session["csrf_token"] = self.csrf_token
            flask_session["browser_session_id"] = "agent-tests-session"
        original_open = self.client.open

        def open_with_auth(*args, **kwargs):
            method = str(kwargs.get("method", "GET")).upper()
            if method in {"POST", "PUT", "PATCH", "DELETE"}:
                headers = kwargs.setdefault("headers", {})
                headers.setdefault("X-CSRF-Token", self.csrf_token)
                headers.setdefault("X-Requested-With", "XMLHttpRequest")
            return original_open(*args, **kwargs)

        self.client.open = open_with_auth

    def test_agents_page_renders(self):
        response = self.client.get('/agents')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'SEO Agent Studio', response.data)

    def test_index_route_redirects(self):
        response = self.client.get('/', follow_redirects=False)
        self.assertIn(response.status_code, (302, 308))

    def test_content_agents_page_renders(self):
        response = self.client.get('/content-agents')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Content Agent Studio', response.data)
        self.assertIn(b'Content Calendar', response.data)

    def test_social_agents_page_renders(self):
        response = self.client.get('/social-agents')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Social Agent Studio', response.data)
        self.assertIn(b'Social Calendar', response.data)

    @patch('agents.keyword_research.crawl_site')
    def test_run_agent_endpoint_returns_json(self, keyword_crawl):
        keyword_crawl.return_value = {
            'pages': [{
                'url': 'https://example.com',
                'title': 'Digital Marketing Agency',
                'meta_description': 'SEO services and content strategy for growth.',
                'h1': ['Digital Marketing Agency'],
                'h2': ['SEO Services', 'Content Strategy'],
            }],
            'crawled_urls': ['https://example.com'],
        }
        payload = {
            'agent': 'keyword_research',
            'website_url': 'https://example.com',
            'seed_keyword': 'digital marketing agency',
            'country': 'United States',
            'language': 'en',
            'competitors': ['https://competitor1.com', 'https://competitor2.com'],
            'business_goal': 'generate SEO leads'
        }
        response = self.client.post('/run-agent', json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data['success'])
        self.assertEqual(data['agent_id'], 'keyword_research')
        self.assertEqual(data['keyword_opportunities'][0]['search_volume'], None)
        self.assertEqual(data['keyword_opportunities'][0]['metric_source'], 'Unavailable in Free Mode')

    def test_run_content_and_social_agents_return_json(self):
        base_payload = {
            'website_url': 'https://example.com',
            'brand_name': 'Example',
            'industry': 'SaaS',
            'target_audience': 'Marketing leaders',
            'keyword': 'content strategy',
            'topic': 'AI content planning',
            'platform': 'LinkedIn',
            'tone': 'helpful',
            'language': 'en',
            'country': 'United States',
            'business_goal': 'generate leads',
            'content_type': 'blog',
            'competitors': ['https://competitor1.com']
        }

        content_response = self.client.post('/run-agent', json={**base_payload, 'agent': 'content_calendar'})
        self.assertEqual(content_response.status_code, 400)
        self.assertEqual(content_response.get_json()['missing_key'], 'OPENAI_API_KEY')

        analytics_response = self.client.post('/run-agent', json={
            **base_payload,
            'agent': 'analytics',
            'impressions': 1000,
            'clicks': 80,
            'engagement': 120,
            'conversions': 10,
        })
        self.assertEqual(analytics_response.status_code, 200)
        analytics_data = analytics_response.get_json()
        self.assertEqual(analytics_data['agent'], 'Analytics')
        self.assertEqual(analytics_data['data']['analysis']['ctr'], 8.0)
        self.assertEqual(analytics_data['data']['analysis']['engagement_rate'], 12.0)
        self.assertEqual(analytics_data['data']['analysis']['conversion_rate'], 12.5)

    def test_reports_page_renders(self):
        response = self.client.get('/seo-reports')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'SEO Reports', response.data)

    def test_strategy_page_renders(self):
        response = self.client.get('/seo-strategy')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'SEO Strategy', response.data)

    def test_required_templates_exist(self):
        for template_name in [
            'layout.html',
            'base.html',
            'dashboard.html',
            'content_agents.html',
            'social_agents.html',
            'agents.html',
            'seo_reports.html',
            'seo_strategy.html',
        ]:
            self.assertTrue(os.path.exists(os.path.join('templates', template_name)), template_name)

    def test_sidebar_links_resolve(self):
        response = self.client.get('/content-agents')
        self.assertEqual(response.status_code, 200)
        hrefs = set(re.findall(rb'href="([^"#]+)"', response.data))
        required = {b'/setup', b'/dashboard', b'/agents', b'/seo-reports', b'/seo-strategy', b'/content-agents', b'/social-agents', b'/'}
        self.assertTrue(required.issubset(hrefs))
        for href in required - {b'/'}:
            link_response = self.client.get(href.decode('utf-8'))
            self.assertNotIn(link_response.status_code, (404, 500), href)

    def test_dashboard_shows_agent_counts(self):
        response = self.client.get('/dashboard')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'SEO Agents', response.data)
        self.assertIn(b'Content Agents', response.data)
        self.assertIn(b'Social Agents', response.data)

        grouped = get_all_agents()
        self.assertIn(str(len(grouped[SEO_GROUP])).encode(), response.data)
        self.assertIn(str(len(grouped[CONTENT_GROUP])).encode(), response.data)
        self.assertIn(str(len(grouped[SOCIAL_GROUP])).encode(), response.data)

    def test_all_registered_agents_have_required_metadata_and_run(self):
        grouped = get_all_agents()
        seen_ids = set()
        base_payload = {
            'website_url': 'https://example.com',
            'brand_name': 'Example',
            'industry': 'SaaS',
            'target_audience': 'Marketing leaders',
            'keyword': 'content strategy',
            'topic': 'AI content planning',
            'platform': 'LinkedIn',
            'tone': 'helpful',
            'language': 'en',
            'country': 'United States',
            'location': 'United States',
            'business_goal': 'generate leads',
            'content_type': 'blog',
            'competitors': ['https://competitor1.com']
        }

        for group_name, agents in grouped.items():
            for agent in agents:
                self.assertNotIn(agent['id'], seen_ids)
                seen_ids.add(agent['id'])
                self.assertTrue(agent['name'])
                self.assertTrue(agent['description'])
                self.assertTrue(agent['icon'])
                self.assertTrue(agent['category'])
                self.assertEqual(agent['group'], group_name)

    @patch('agents.keyword_research.crawl_site')
    def test_keyword_research_free_mode_uses_real_context_without_fake_metrics(self, keyword_crawl):
        keyword_crawl.return_value = {
            'pages': [{
                'url': 'https://www.apple.com/iphone/',
                'title': 'iPhone - Apple',
                'meta_description': 'Discover iPhone models, camera features, battery life, and pricing.',
                'h1': ['iPhone'],
                'h2': ['iPhone 17 Pro', 'Compare iPhone models', 'Shop iPhone'],
            }],
            'crawled_urls': ['https://www.apple.com/iphone/'],
        }
        response = self.client.post('/run-agent', json={
            'agent': 'keyword_research',
            'website_url': 'https://www.apple.com',
            'seed_keyword': 'iPhone',
            'country': 'United States',
            'language': 'English',
            'industry': 'Consumer Electronics',
            'target_audience': 'Smartphone buyers',
            'business_goal': 'Increase product sales',
            'competitors': ['https://www.samsung.com'],
        })
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data['agent_id'], 'keyword_research')
        self.assertEqual(data['mode'], 'free')
        self.assertTrue(data['competitor_topics_detected'])
        self.assertTrue(any('iphone' in item['keyword'] for item in data['keyword_opportunities']))
        self.assertTrue(all(item['search_volume'] is None for item in data['keyword_opportunities']))
        self.assertTrue(all(item['cpc'] is None for item in data['keyword_opportunities']))
        self.assertTrue(all(item['keyword_difficulty'] is None for item in data['keyword_opportunities']))

    def test_keyword_clustering_separates_product_clusters_and_unrelated_terms(self):
        response = self.client.post('/run-agent', json={
            'agent_id': 'keyword_clustering',
            'keyword_list': '\n'.join([
                'iPhone',
                'buy iPhone online',
                'best iPhone for photography',
                'iPhone camera features',
                'iPhone battery life',
                'iPhone pricing',
                'MacBook Pro',
                'best MacBook for developers',
                'MacBook battery life',
                'Apple Watch fitness tracking',
                'Apple Watch health features',
            ])
        })
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data['agent_id'], 'keyword_clustering')
        names = {cluster['cluster_name'].lower() for cluster in data['clusters']}
        self.assertIn('iphone', names)
        self.assertIn('macbook', names)
        self.assertIn('apple watch', names)

        array_response = self.client.post('/run-agent', json={
            'agent_id': 'keyword_clustering',
            'keyword_list': ['iPhone', 'iPhone', 'MacBook Pro', 'Apple Watch fitness tracking']
        })
        self.assertEqual(array_response.status_code, 200)
        array_data = array_response.get_json()
        self.assertTrue(array_data['success'])
        self.assertEqual(array_data['duplicates_removed'], 1)

        unrelated_response = self.client.post('/run-agent', json={
            'agent': 'keyword_clustering',
            'keyword_list': '\n'.join([
                'technical SEO audit',
                'buy running shoes',
                'best CRM software',
                'local dentist near me',
            ])
        })
        self.assertEqual(unrelated_response.status_code, 200)
        unrelated = unrelated_response.get_json()
        self.assertEqual(unrelated['number_of_clusters'], 0)
        self.assertEqual(len(unrelated['unclustered_keywords']), 4)

    @patch('agents.on_page_optimizer.audit_single_page')
    @patch('agents.schema_agent.audit_single_page')
    @patch('agents.internal_linking.crawl_site')
    @patch('agents.content_gap.audit_single_page')
    @patch('agents.competitor_analysis.CompetitorAnalysisAgent._crawl_site_or_warning')
    @patch('agents.backlink_prospecting.audit_single_page')
    @patch('agents.backlink_verification.audit_single_page')
    @patch('agents.technical_audit.crawl_site')
    def test_crawl_based_agents_run_from_real_crawl_interfaces(
        self,
        technical_crawl,
        backlink_verify_page,
        backlink_prospect_page,
        competitor_page,
        content_gap_page,
        internal_crawl,
        schema_page,
        onpage_page,
    ):
        page = {
            'url': 'https://example.com',
            'http_status': 200,
            'https': True,
            'title': 'Example Title',
            'meta_description': 'Example description',
            'h1': ['Example Heading'],
            'h2': ['Section One'],
            'canonical': 'https://example.com',
            'meta_robots': 'index,follow',
            'internal_links': ['https://example.com/about'],
            'external_links': ['https://external.com'],
            'images_missing_alt': [],
            'structured_data': [{'@type': 'Organization'}],
            'faq_markers': [],
            'page_size_bytes': 1024,
            'response_time_ms': 120.0,
            'word_count': 250,
            'redirect_chain': [],
            'robots_txt': {'url': 'https://example.com/robots.txt', 'status_code': 200},
            'sitemap_xml': {'url': 'https://example.com/sitemap.xml', 'status_code': 200},
        }
        crawl_payload = {'pages': [page], 'crawled_urls': ['https://example.com'], 'broken_links': [], 'start_url': 'https://example.com'}
        technical_crawl.return_value = crawl_payload
        internal_crawl.return_value = crawl_payload
        onpage_page.return_value = page
        schema_page.return_value = page
        competitor_page.return_value = (page, None)
        content_gap_page.return_value = page
        backlink_prospect_page.return_value = page
        backlink_verify_page.return_value = page

        for agent_id, payload in [
            ('technical_audit', {'website_url': 'https://example.com'}),
            ('competitor_analysis', {'website_url': 'https://example.com', 'competitors': ['https://competitor.com']}),
            ('content_gap', {'website_url': 'https://example.com', 'competitors': ['https://competitor.com']}),
            ('on_page_optimizer', {'website_url': 'https://example.com'}),
            ('schema_agent', {'website_url': 'https://example.com'}),
            ('internal_linking', {'website_url': 'https://example.com'}),
            ('backlink_prospecting', {'website_url': 'https://example.com', 'competitors': ['https://competitor.com']}),
            ('backlink_verification', {'backlink_url': 'https://example.com/link-page'}),
        ]:
            response = self.client.post('/run-agent', json={'agent': agent_id, **payload})
            self.assertEqual(response.status_code, 200, agent_id)
            self.assertTrue(response.get_json()['success'], agent_id)

    @patch('agents.competitor_analysis.CompetitorAnalysisAgent._crawl_site_or_warning')
    def test_competitor_analysis_timeout_returns_warning_not_500(self, crawl_site_or_warning):
        primary_page = {
            'url': 'https://example.com',
            'http_status': 200,
            'https': True,
            'title': 'Example Title',
            'meta_description': 'Example description',
            'h1': ['Example Heading'],
            'h2': ['Section'],
            'internal_links': ['https://example.com/about'],
            'external_links': ['https://external.com'],
            'images': [],
            'images_missing_alt': [],
            'structured_data': [{'@type': 'Organization'}],
            'page_size_bytes': 1024,
            'response_time_ms': 120.0,
            'word_count': 250,
            'canonical': 'https://example.com',
            'meta_robots': 'index,follow',
        }
        competitor_page = {
            'url': 'https://audi.com',
            'http_status': 200,
            'https': True,
            'title': 'Audi',
            'meta_description': 'Audi description',
            'h1': ['Audi Heading'],
            'h2': ['Models'],
            'internal_links': ['https://audi.com/about'],
            'external_links': ['https://external.com'],
            'images': [],
            'images_missing_alt': [],
            'structured_data': [],
            'page_size_bytes': 2048,
            'response_time_ms': 180.0,
            'word_count': 300,
            'canonical': 'https://audi.com',
            'meta_robots': 'index,follow',
        }
        crawl_site_or_warning.side_effect = [
            (primary_page, None),
            (None, {
                'website': 'https://www.bmw.com',
                'status': 'timeout',
                'message': 'Website could not be crawled within 45 seconds.',
            }),
            (competitor_page, None),
        ]

        response = self.client.post('/run-agent', json={
            'agent': 'competitor_analysis',
            'website_url': 'https://example.com',
            'competitors': ['https://www.bmw.com', 'https://audi.com'],
        })
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data['success'])
        self.assertEqual(len(data['data']['competitor_comparison_table']), 2)
        self.assertEqual(len(data['data']['warnings']), 1)
        self.assertEqual(data['data']['warnings'][0]['status'], 'timeout')
        self.assertIn('premium_apis', data['data'])

    @patch('agents.technical_audit.get_env_value', return_value='')
    @patch('agents.technical_audit.crawl_site')
    def test_technical_audit_returns_grouped_sections_and_score_status(self, crawl_site_mock, _env):
        crawl_site_mock.return_value = {
            'pages': [{
                'url': 'https://example.com',
                'http_status': 200,
                'https': True,
                'title': 'Example',
                'meta_description': '',
                'h1': [],
                'h2': [],
                'canonical': '',
                'meta_robots': '',
                'internal_links': ['https://example.com/about'],
                'external_links': [],
                'images': ['https://example.com/image.jpg'],
                'images_missing_alt': ['https://example.com/image.jpg'],
                'structured_data': [],
                'page_size_bytes': 2048,
                'response_time_ms': 2300,
                'redirect_chain': [],
                'robots_txt': {'status_code': 200},
                'sitemap_xml': {'status_code': 200},
            }],
            'crawled_urls': ['https://example.com'],
            'broken_links': [],
        }

        response = self.client.post('/run-agent', json={'agent': 'technical_audit', 'website_url': 'https://example.com'})
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data['success'])
        self.assertIn('seo_status', data['data'])
        self.assertIsInstance(data['data']['warnings'], list)
        self.assertTrue(data['data']['warnings'])
        self.assertEqual(data['data']['warnings'][0]['issue'], 'Missing Meta Descriptions')
        self.assertIn('technical_metrics', data['data'])
        self.assertIn('priority_fixes', data['data'])

    def test_api_backed_agents_fail_honestly_without_keys(self):
        cases = {
            'serp_analysis': ('DATAFORSEO_LOGIN', {'keyword': 'seo agency'}),
            'rank_tracking': ('DATAFORSEO_LOGIN', {'website_url': 'https://example.com', 'target_keywords': 'seo agency'}),
            'content_calendar': ('OPENAI_API_KEY', {'topic': 'SEO', 'target_audience': 'Marketers', 'business_goal': 'Leads'}),
            'seo_brief': ('OPENAI_API_KEY', {'topic': 'SEO', 'keyword': 'seo brief'}),
            'blog_writer': ('OPENAI_API_KEY', {'topic': 'SEO', 'target_audience': 'Marketers'}),
            'humanizer': ('OPENAI_API_KEY', {'content': 'Draft copy'}),
            'fact_check': ('OPENAI_API_KEY', {'content': 'Draft copy'}),
            'publisher': ('OPENAI_API_KEY', {'topic': 'SEO', 'content_type': 'blog'}),
            'social_calendar': ('OPENAI_API_KEY', {'topic': 'SEO', 'business_goal': 'Leads'}),
            'caption': ('OPENAI_API_KEY', {'platform': 'LinkedIn', 'topic': 'SEO'}),
            'image_agent': ('OPENAI_API_KEY', {'platform': 'LinkedIn', 'topic': 'SEO'}),
            'video_agent': ('OPENAI_API_KEY', {'platform': 'LinkedIn', 'topic': 'SEO'}),
            'social_publisher': ('OPENAI_API_KEY', {'platform': 'LinkedIn', 'topic': 'SEO'}),
            'strategy': ('OPENAI_API_KEY', {'website_url': 'https://example.com', 'business_goal': 'Grow leads'}),
            'outreach': ('OPENAI_API_KEY', {'website_url': 'https://example.com', 'business_goal': 'Earn links'}),
        }
        for agent_id, (missing_key, extra_payload) in cases.items():
            response = self.client.post('/run-agent', json={'agent': agent_id, **extra_payload})
            self.assertEqual(response.status_code, 400, agent_id)
            data = response.get_json()
            self.assertFalse(data['success'], agent_id)
            self.assertEqual(data['missing_key'], missing_key, agent_id)

    def test_pdf_report_includes_competitors_and_visibility_summary(self):
        init_db()
        run_id = create_run('example.com', 'Example', 'United States', 'en', ['https://competitor1.com', 'https://competitor2.com'])
        pdf_bytes = generate_pdf_report(run_id)
        self.assertTrue(pdf_bytes and len(pdf_bytes) > 1000)

        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as temp_pdf:
            temp_pdf.write(pdf_bytes)
            temp_path = temp_pdf.name

        try:
            text_output = subprocess.check_output(['pdftotext', temp_path, '-'], text=True)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

        self.assertIn('Competitors Analyzed', text_output)
        self.assertIn('https://competitor1.com', text_output)
        self.assertIn('AI Visibility Summary', text_output)

    def test_base_agent_input_schema_is_empty(self):
        self.assertEqual(BaseAgent.INPUT_SCHEMA, [])

    def test_all_seo_agents_have_nonempty_unique_input_schema(self):
        seo_agents = get_all_agents()[SEO_GROUP]
        self.assertEqual(len(seo_agents), 17)
        for agent in seo_agents:
            schema = agent['input_schema']
            self.assertTrue(schema, f"{agent['id']} has an empty INPUT_SCHEMA")
            field_ids = [field['id'] for field in schema]
            self.assertEqual(len(field_ids), len(set(field_ids)), f"{agent['id']} has duplicate field ids")
            for field in schema:
                self.assertIn('id', field)
                self.assertIn('label', field)
                self.assertIn('type', field)

    def test_content_and_social_agents_have_nonempty_input_schema(self):
        grouped = get_all_agents()
        for group in (CONTENT_GROUP, SOCIAL_GROUP):
            for agent in grouped[group]:
                self.assertTrue(agent['input_schema'], f"{agent['id']} has an empty INPUT_SCHEMA")

    def test_agents_page_renders_only_schema_driven_fields(self):
        response = self.client.get('/agents')
        html = response.data.decode('utf-8')
        match = re.search(r'<script id="agents-data" type="application/json">(.*?)</script>', html, re.S)
        self.assertIsNotNone(match)
        rendered_agents = {agent['id']: agent for agent in json.loads(match.group(1))}

        expected_fields = {
            'technical_audit': {'website_url', 'crawl_depth', 'maximum_pages', 'device', 'sitemap_url', 'robots_txt_url', 'force_refresh'},
            'negative_keyword': {'search_terms_file', 'company_name', 'account_name', 'target_locations', 'excluded_locations', 'competitor_terms', 'custom_negative_terms', 'high_cost_threshold', 'min_clicks_for_spend_rule', 'conversion_threshold'},
            'backlink_verification': {'backlink_url', 'target_url', 'expected_anchor_text', 'project_name', 'verification_frequency', 'force_javascript_rendering'},
            'strategy': {'website_url', 'business_goal', 'target_country', 'target_audience', 'target_keywords', 'competitor_urls', 'timeframe', 'budget_level', 'team_capacity', 'project_name', 'selected_agent_results'},
        }
        for agent_id, fields in expected_fields.items():
            actual = {field['id'] for field in rendered_agents[agent_id]['input_schema']}
            self.assertEqual(actual, fields, agent_id)

        for marker in ('id="brand_name"', 'id="industry"', 'id="content_type"', 'id="impressions"', 'setAgentFieldVisibility'):
            self.assertNotIn(marker, html)
        self.assertIn('id="agent-fields-container"', html)

    @patch('agents.weekly_report.get_latest_run')
    @patch('agents.weekly_report.get_mention_results', return_value=[])
    @patch('agents.weekly_report.get_competitor_metrics', return_value=[])
    def test_weekly_report_date_range_validation(self, _metrics, _results, latest_run):
        latest_run.return_value = {'id': 1}

        seven_day_response = self.client.post('/run-agent', json={
            'agent': 'weekly_report',
            'start_date': '2026-07-01',
            'end_date': '2026-07-08',
        })
        self.assertEqual(seven_day_response.status_code, 200)
        self.assertIsNone(seven_day_response.get_json()['data']['date_range_warning'])

        skewed_response = self.client.post('/run-agent', json={
            'agent': 'weekly_report',
            'start_date': '2026-07-01',
            'end_date': '2026-07-20',
        })
        self.assertEqual(skewed_response.status_code, 200)
        self.assertIsNotNone(skewed_response.get_json()['data']['date_range_warning'])

    def test_rank_tracking_and_serp_analysis_accept_new_schema_field_names(self):
        rank_response = self.client.post('/run-agent', json={
            'agent': 'rank_tracking',
            'website_url': 'https://example.com',
            'keyword_list': ['seo agency'],
        })
        self.assertEqual(rank_response.status_code, 400)
        self.assertEqual(rank_response.get_json()['missing_key'], 'DATAFORSEO_LOGIN')

        serp_response = self.client.post('/run-agent', json={
            'agent': 'serp_analysis',
            'target_keyword': 'seo agency',
        })
        self.assertEqual(serp_response.status_code, 400)
        self.assertEqual(serp_response.get_json()['missing_key'], 'DATAFORSEO_LOGIN')


if __name__ == '__main__':
    unittest.main()
