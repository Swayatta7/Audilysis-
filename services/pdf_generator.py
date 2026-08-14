import io
import json
import os
import re
import html
from datetime import datetime
from urllib.parse import urlparse
from io import BytesIO

# Matplotlib headless config
os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# ReportLab components
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak, KeepTogether
)
from reportlab.pdfgen import canvas

# Import DB queries for report content
from db.storage import get_run, get_mention_results, get_competitor_metrics, get_trend_data
from services.report_health import (
    PLATFORM_LABELS,
    PLATFORM_ORDER,
    evaluate_report_data_health,
    is_valid_platform_result,
)
from services.run_context import (
    SEO_AGENT_STATUS_META,
    build_heatmap_data,
    build_visibility_summary_text,
    display_total_checks_value,
    load_run_analysis_context,
)

# ================= Matplotlib Chart Plotting Functions =================

def generate_sov_chart(metrics, brand_domain):
    """Generates a horizontal bar chart for competitor Share of Voice."""
    if not metrics:
        return None
    
    # Sort metrics by total mentions descending
    sorted_metrics = sorted(metrics, key=lambda x: x["total_mentions"], reverse=True)
    domains = [m["domain"] for m in sorted_metrics]
    mentions = [m["total_mentions"] for m in sorted_metrics]
    
    # Custom color palette: Brand is deep blue, competitors are light grey-blue
    bar_colors = ['#4361ee' if d.lower() == brand_domain.lower() else '#cbd5e1' for d in domains]
    
    fig, ax = plt.subplots(figsize=(5.5, 2.8))
    bars = ax.barh(domains, mentions, color=bar_colors, height=0.55)
    ax.invert_yaxis()  # Top-down ordering
    
    ax.set_title("Competitor Share of Voice (SOV) — Total Mentions", fontsize=10, fontweight='bold', color='#1e1a4f', pad=10)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.spines['left'].set_color('#e2e8f0')
    ax.spines['bottom'].set_color('#e2e8f0')
    ax.tick_params(colors='#475569', labelsize=8)
    
    # Add count values onto each bar
    for bar in bars:
        width = bar.get_width()
        ax.text(width + 0.15, bar.get_y() + bar.get_height()/2, f' {int(width)}', 
                va='center', ha='left', fontsize=8, color='#475569', fontweight='bold')
                
    plt.tight_layout()
    img_data = BytesIO()
    plt.savefig(img_data, format='png', dpi=300, bbox_inches='tight')
    plt.close(fig)
    img_data.seek(0)
    return img_data

def generate_platform_chart(platform_breakdown):
    """Generates a doughnut pie chart for platform success rates."""
    platforms = ['Google AI Mode', 'ChatGPT', 'Perplexity', 'Gemini', 'Claude']
    keys = ['google', 'chat_gpt', 'perplexity', 'gemini', 'claude']
    values = [platform_breakdown.get(k, 0) for k in keys]
    
    total = sum(values)
    if total == 0:
        return None
    labels = [f"{p} ({val})" for p, val in zip(platforms, values)]
        
    colors_list = ['#4285F4', '#10a37f', '#19c37d', '#1a73e8', '#d97706']
    
    fig, ax = plt.subplots(figsize=(5.5, 2.8))
    ax.pie(
        values, 
        labels=labels,
        colors=colors_list,
        startangle=90,
        textprops={'fontsize': 8, 'color': '#475569'},
        wedgeprops=dict(width=0.45, edgecolor='w', linewidth=2)
    )
    
    ax.set_title("Brand Mentions by Platform", fontsize=10, fontweight='bold', color='#1e1a4f', pad=10)
    
    plt.tight_layout()
    img_data = BytesIO()
    plt.savefig(img_data, format='png', dpi=300, bbox_inches='tight')
    plt.close(fig)
    img_data.seek(0)
    return img_data

def generate_trend_chart(trend_data, brand_domain, competitor_domains):
    """Generates a line plot showing trends over time."""
    if len(trend_data) < 2:
        return None
        
    dates = [entry["run_date"] for entry in trend_data]
    
    fig, ax = plt.subplots(figsize=(5.5, 2.8))
    
    # Plot Brand
    brand_points = [entry.get("brand") for entry in trend_data]
    if not any(point is not None for point in brand_points):
        return None
    ax.plot(dates, brand_points, color='#4361ee', label=brand_domain, linewidth=2.5, marker='o', markersize=5)
    
    # Plot Competitors
    comp_colors = ['#fb7185', '#38bdf8', '#a78bfa', '#fbbf24']
    for idx, comp in enumerate(competitor_domains):
        comp_points = [entry.get(comp.lower()) for entry in trend_data]
        if not any(point is not None for point in comp_points):
            continue
        color = comp_colors[idx % len(comp_colors)]
        ax.plot(dates, comp_points, color=color, label=comp, linewidth=1.5, marker='x', markersize=4)
        
    ax.set_title("Mention Trend Over Time", fontsize=10, fontweight='bold', color='#1e1a4f', pad=10)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.spines['left'].set_color('#e2e8f0')
    ax.spines['bottom'].set_color('#e2e8f0')
    ax.tick_params(colors='#475569', labelsize=8)
    ax.grid(axis='y', linestyle='--', alpha=0.35)
    ax.legend(loc='upper left', fontsize=7, frameon=False)
    
    plt.tight_layout()
    img_data = BytesIO()
    plt.savefig(img_data, format='png', dpi=300, bbox_inches='tight')
    plt.close(fig)
    img_data.seek(0)
    return img_data

def generate_domains_chart(top_domains, brand_domain):
    """Generates a vertical bar chart for top cited domains."""
    if not top_domains:
        return None
        
    sliced = top_domains[:6]
    domains = [d["domain"] for d in sliced]
    counts = [d["count"] for d in sliced]
    
    colors_list = ['#4361ee' if d.lower() == brand_domain.lower() else '#cbd5e1' for d in domains]
    
    fig, ax = plt.subplots(figsize=(5.5, 2.8))
    bars = ax.bar(domains, counts, color=colors_list, width=0.45)
    
    ax.set_title("Top Cited Reference Domains", fontsize=10, fontweight='bold', color='#1e1a4f', pad=10)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.spines['left'].set_color('#e2e8f0')
    ax.spines['bottom'].set_color('#e2e8f0')
    ax.tick_params(colors='#475569', labelsize=8)
    plt.xticks(rotation=15, ha='right')
    
    # Label height values above bars
    for bar in bars:
        height = bar.get_height()
        ax.text(bar.get_x() + bar.get_width()/2., height + 0.08, f'{int(height)}',
                ha='center', va='bottom', fontsize=8, color='#475569', fontweight='bold')
                
    plt.tight_layout()
    img_data = BytesIO()
    plt.savefig(img_data, format='png', dpi=300, bbox_inches='tight')
    plt.close(fig)
    img_data.seek(0)
    return img_data

# ================= ReportLab Custom Canvas with Header & Footer =================

class NumberedCanvas(canvas.Canvas):
    """Two-pass Canvas to dynamically insert 'Page X of Y' page numbers in footer."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, page_count):
        # Skip headers/footers on page 1 (Cover Page)
        if self._pageNumber == 1:
            return
            
        self.saveState()
        self.setFont("Helvetica-Bold", 8)
        self.setFillColor(HexColor("#1e1a4f"))
        
        # Header text
        self.drawString(54, 755, "AUDILYSIS 2.0 — AI MENTION TRACKING REPORT")
        
        # Header thin division line
        self.setStrokeColor(HexColor("#e2e8f0"))
        self.setLineWidth(0.75)
        self.line(54, 747, 558, 747)
        
        # Footer thin division line
        self.line(54, 55, 558, 55)
        
        self.setFont("Helvetica", 8)
        self.setFillColor(HexColor("#64748b"))
        self.drawString(54, 40, "Confidential — Client Review Report")
        
        # Page X of Y format
        page_str = f"Page {self._pageNumber} of {page_count}"
        self.drawRightString(558, 40, page_str)
        
        self.restoreState()

# ================= Main PDF Generator Engine =================

def extract_domain(url):
    """Helper to parse domain from URLs."""
    try:
        netloc = urlparse(url).netloc
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc.lower()
    except Exception:
        return ""

def escape_and_highlight(text, brand_name, brand_domain):
    """Escapes HTML entities to prevent ReportLab parsing crash and highlights brand names."""
    if not text:
        return "<em color='#94a3b8'>No response data or API call failed.</em>"
    
    # Escape special characters
    escaped = html.escape(text)
    
    # Regex highlight wrap (primary brand color & bold)
    re_name = re.escape(brand_name)
    re_domain = re.escape(brand_domain)
    
    escaped = re.sub(f"({re_name})", r"<font color='#4361ee'><b>\1</b></font>", escaped, flags=re.IGNORECASE)
    escaped = re.sub(f"({re_domain})", r"<font color='#4361ee'><b>\1</b></font>", escaped, flags=re.IGNORECASE)
    
    # Convert newlines to breaks to preserve spacing
    escaped = escaped.replace("\n", "<br/>")
    return escaped


def build_response_explanation(result: dict | None) -> str:
    if not result:
        return "No platform response was recorded for this keyword."
    if result.get("ai_response_text"):
        return result["ai_response_text"]
    return result.get("error_message") or "No usable platform response was collected for this keyword."


def select_visibility_result(results: list[dict], keyword: str) -> dict | None:
    preferred_order = ["google", "chat_gpt", "perplexity", "gemini", "claude"]
    valid_by_platform = {
        row["platform"]: row
        for row in results
        if row.get("keyword") == keyword and row.get("has_valid_data")
    }
    for platform in preferred_order:
        if platform in valid_by_platform:
            return valid_by_platform[platform]
    return None


def create_simple_pdf(story):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=54,
        rightMargin=54,
        topMargin=72,
        bottomMargin=72,
    )
    doc.build(story, canvasmaker=NumberedCanvas)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes


def build_technical_failure_pdf(run: dict, report_health: dict, results: list[dict]):
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('FailureTitle', parent=styles['Title'], fontName='Helvetica-Bold', fontSize=24, leading=30, textColor=HexColor('#1e1a4f'))
    h1_style = ParagraphStyle('FailureHeader', parent=styles['Heading1'], fontName='Helvetica-Bold', fontSize=15, leading=20, textColor=HexColor('#1e1a4f'), spaceBefore=12, spaceAfter=10)
    body_style = ParagraphStyle('FailureBody', parent=styles['Normal'], fontName='Helvetica', fontSize=9, leading=13, textColor=HexColor('#334155'))

    story = [
        Spacer(1, 24),
        Paragraph("Audilysis 2.0", ParagraphStyle('FailureBrand', parent=body_style, fontName='Helvetica-Bold', fontSize=14, textColor=HexColor('#4361ee'))),
        Paragraph("AI Mention Tracking — Technical Failure Report", title_style),
        Spacer(1, 10),
        Paragraph("No valid AI platform responses were collected. Client-facing visibility analytics were not generated for this run.", body_style),
        Spacer(1, 18),
        Paragraph("Run Summary", h1_style),
    ]
    summary_rows = [
        ["Audit ID", str(run["id"])],
        ["Brand", f'{run["brand_name"]} ({run["brand_domain"]})'],
        ["Target Country", str(run["country"])],
        ["Language", str(run["language"])],
        ["Run Timestamp", str(run["run_date"])],
        ["API Response Health", "Data Unavailable"],
        ["Share of Voice", "Data Unavailable"],
        ["Visibility Metrics", "Data Unavailable"],
    ]
    summary_table = Table(summary_rows, colWidths=[150, 354])
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), HexColor('#f8fafc')),
        ('GRID', (0, 0), (-1, -1), 0.5, HexColor('#e2e8f0')),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 16))
    story.append(Paragraph("Platform Status", h1_style))

    status_rows = [["Platform", "Status", "Error Category", "Safe Error Message", "Retry Recommendation"]]
    for item in report_health["platform_summaries"]:
        status_rows.append([
            PLATFORM_LABELS.get(item["platform"], item["platform"]),
            item["status"],
            item["error_category"],
            item["safe_error_message"],
            item["retry_recommendation"],
        ])
    status_table = Table(status_rows, colWidths=[78, 72, 96, 150, 108])
    status_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), HexColor('#1e1a4f')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('GRID', (0, 0), (-1, -1), 0.5, HexColor('#e2e8f0')),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    story.append(status_table)
    story.append(Spacer(1, 16))
    story.append(Paragraph("Recommended Actions", h1_style))
    if not bool(run.get("use_dataforseo", 1)):
        actions = [
            "DataForSEO was not enabled for this run.",
            "Enable DataForSEO in a future run if you need AI visibility metrics from that provider.",
            "Review the run data source settings before rerunning the audit.",
        ]
    else:
        actions = [
            "Verify API credentials.",
            "Verify quota and rate limits.",
            "Check network connectivity from the server.",
            "Check provider availability.",
            "Rerun the audit after resolving the failures.",
        ]
    for action in actions:
        story.append(Paragraph(f"• {action}", body_style))
    story.append(Spacer(1, 12))
    story.append(Paragraph("Safe Technical Diagnostics", h1_style))
    diagnostics = ", ".join(sorted({row.get("error_category") or row.get("response_status") or "unknown_error" for row in results})) or "No diagnostics recorded."
    story.append(Paragraph(f"Observed error categories: {diagnostics}", body_style))
    return create_simple_pdf(story)

def generate_pdf_report(run_id):
    """
    Retrieves database results for run_id and generates a polished, multi-page PDF.
    Returns: PDF file bytes.
    """
    run_context = load_run_analysis_context(run_id)
    if not run_context:
        return None

    run = run_context["run"]
    results = run_context["results"]
    report_health = {
        **run_context["report_health"],
        "platform_summaries": run_context["platform_summaries"],
    }
    if run_context["report_mode"] == "technical_failure":
        return build_technical_failure_pdf(run, report_health, results)

    valid_results = run_context["valid_results"]
    metrics = run_context["metrics"]
    dataforseo_status = run_context["provider_provenance"]["dataforseo"]["status"]

    total_checks = display_total_checks_value(run_context["total_checks"], dataforseo_status=dataforseo_status)
    brand_mentions = run_context["brand_mentions_metric"]["value"]
    brand_sov = run_context["share_of_voice_metric"]["value"]
    api_health = run_context["api_health_metric"]["value"]

    competitors = []
    if run.get("competitors"):
        try:
            competitors = json.loads(run["competitors"]) or []
        except Exception:
            competitors = []
    if not competitors:
        competitors = []

    def format_visibility_label(mentioned, position=None):
        if mentioned is None:
            return "Data Unavailable"
        if mentioned and position is not None:
            if position <= 3:
                return "Top Recommendation"
            if position <= 6:
                return "High Visibility"
            if position <= 10:
                return "Moderate Visibility"
            return "Low Visibility"
        if mentioned:
            return "Moderate Visibility"
        return "No Visibility"

    def build_visibility_summary(match):
        if not match:
            return {
                "mention_status": "Data Unavailable",
                "visibility_level": "Data Unavailable",
                "position": "Data Unavailable",
                "visibility_score": "Data Unavailable",
                "sentiment": "Data Unavailable",
                "confidence": "Data Unavailable"
            }

        mentioned = bool(match.get("mentioned"))
        position = match.get("mention_position")
        if mentioned and position is not None:
            position_text = f"#{int(position)}"
            visibility_score = min(100, max(20, 100 - ((position - 1) * 8)))
            confidence = "High" if position <= 6 else "Medium"
            sentiment = "Positive"
        elif mentioned:
            position_text = "Data Unavailable"
            visibility_score = 62
            confidence = "Medium"
            sentiment = "Positive"
        else:
            position_text = "Data Unavailable"
            visibility_score = 0
            confidence = "Data Unavailable"
            sentiment = "Data Unavailable"

        return {
            "mention_status": "Mentioned" if mentioned else "Not Mentioned",
            "visibility_level": format_visibility_label(mentioned, position),
            "position": position_text,
            "visibility_score": f"{visibility_score}%" if isinstance(visibility_score, (int, float)) else visibility_score,
            "sentiment": sentiment,
            "confidence": confidence
        }
    
    keywords = sorted(list(set(r["keyword"] for r in results))) or run.get("keywords", [])
    heatmap_data = build_heatmap_data(results, keywords, dataforseo_status=dataforseo_status)
        
    platform_breakdown = {plat: 0 for plat in PLATFORM_ORDER}
    for r in valid_results:
        if r.get("mentioned") and r.get("platform") in platform_breakdown:
            platform_breakdown[r["platform"]] += 1
            
    competitor_domains = [m["domain"] for m in metrics if m["domain"].lower() != run["brand_domain"].lower()]
    trend_data = get_trend_data(run["brand_domain"], competitor_domains)
    
    # Top Citations Domain Counter
    domain_counts = {}
    for r in valid_results:
        for url in r.get("sources_cited", []):
            dom = extract_domain(url)
            if dom:
                domain_counts[dom] = domain_counts.get(dom, 0) + 1
    top_domains = [{"domain": dom, "count": count} for dom, count in sorted(domain_counts.items(), key=lambda x: x[1], reverse=True)]

    # PDF Building Process
    buffer = io.BytesIO()
    
    # Set document margins (0.75 in / 54pt)
    # Give topMargin and bottomMargin extra height (72pt) so flowable content does not cover page headers/footers
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=54,
        rightMargin=54,
        topMargin=72,
        bottomMargin=72
    )
    
    styles = getSampleStyleSheet()
    
    # Custom Palette Style sheets
    title_style = ParagraphStyle(
        'CoverTitle',
        parent=styles['Title'],
        fontName='Helvetica-Bold',
        fontSize=26,
        leading=32,
        textColor=HexColor('#1e1a4f'),
        alignment=0, # Left-aligned
        spaceAfter=15
    )
    
    subtitle_style = ParagraphStyle(
        'CoverSubtitle',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=12,
        leading=16,
        textColor=HexColor('#64748b'),
        spaceAfter=30
    )
    
    h1_style = ParagraphStyle(
        'Header1',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=16,
        leading=20,
        textColor=HexColor('#1e1a4f'),
        spaceBefore=18,
        spaceAfter=10,
        keepWithNext=True
    )

    h2_style = ParagraphStyle(
        'Header2',
        parent=styles['Heading2'],
        fontName='Helvetica-Bold',
        fontSize=11,
        leading=14,
        textColor=HexColor('#334155'),
        spaceBefore=12,
        spaceAfter=6,
        keepWithNext=True
    )
    
    body_style = ParagraphStyle(
        'BodyDark',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9,
        leading=13,
        textColor=HexColor('#334155')
    )
    
    body_bold_style = ParagraphStyle(
        'BodyDarkBold',
        parent=body_style,
        fontName='Helvetica-Bold'
    )
    
    cell_lbl_style = ParagraphStyle(
        'CellLabel',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8,
        leading=10,
        textColor=HexColor('#64748b'),
        spaceAfter=2
    )
    
    cell_val_style = ParagraphStyle(
        'CellValue',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=18,
        leading=22,
        textColor=HexColor('#1e1a4f')
    )
    
    center_bold_style = ParagraphStyle(
        'CenterBold',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=8,
        leading=10,
        textColor=colors.white,
        alignment=1
    )

    url_style = ParagraphStyle(
        'UrlWrapStyle',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=7.5,
        leading=9,
        textColor=HexColor('#4361ee'),
        wordWrap='CJK'  # Native string character wrapping
    )

    response_content_style = ParagraphStyle(
        'AIResponseContent',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8,
        leading=11.5,
        textColor=HexColor('#334155')
    )

    story = []
    
    # ------------------ PAGE 1: COVER PAGE ------------------
    story.append(Spacer(1, 40))
    # Top decorative brand stripe (Deep Blue / Purple gradient feel)
    strip_data = [['']]
    strip_table = Table(strip_data, colWidths=[504], rowHeights=[12])
    strip_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), HexColor('#1e1a4f')),
        ('TOPPADDING', (0,0), (-1,-1), 0),
        ('BOTTOMPADDING', (0,0), (-1,-1), 0),
    ]))
    story.append(strip_table)
    story.append(Spacer(1, 30))
    
    story.append(Paragraph("Audilysis 2.0", ParagraphStyle('CoverPre', parent=subtitle_style, fontName='Helvetica-Bold', fontSize=14, textColor=HexColor('#4361ee'), spaceAfter=4)))
    story.append(Paragraph("AI Mention Tracking Report", title_style))
    story.append(Paragraph(f"Brand Reputation Audit &amp; Share of Voice Analysis", subtitle_style))
    
    story.append(Spacer(1, 100))
    
    # Cover Metadata Info Box
    meta_data = [
        [Paragraph("<b>Brand Audited:</b>", body_style), Paragraph(f"{run['brand_name']} ({run['brand_domain']})", body_bold_style)],
        [Paragraph("<b>Date of Analysis:</b>", body_style), Paragraph(f"{datetime.strptime(run['run_date'], '%Y-%m-%d %H:%M:%S').strftime('%B %d, %Y at %I:%M %p')}", body_style)],
        [Paragraph("<b>Target Country:</b>", body_style), Paragraph(f"{run['country']}", body_style)],
        [Paragraph("<b>Language Config:</b>", body_style), Paragraph(f"{run['language']}", body_style)],
    ]
    meta_table = Table(meta_data, colWidths=[120, 384])
    meta_table.setStyle(TableStyle([
        ('BOTTOMPADDING', (0,0), (-1,-1), 8),
        ('TOPPADDING', (0,0), (-1,-1), 8),
        ('LINEBELOW', (0,0), (-1,-2), 0.5, HexColor('#f1f5f9')),
    ]))
    story.append(meta_table)

    story.append(Spacer(1, 16))
    story.append(Paragraph("Competitors Analyzed", ParagraphStyle('CompHeading', parent=h2_style, fontSize=11, textColor=HexColor('#1e1a4f'), spaceBefore=0, spaceAfter=6)))
    if competitors:
        comp_items = []
        for comp in competitors:
            comp_items.append(Paragraph(f"• {comp}", body_style))
        comp_table = Table([[Paragraph("<b>Competitive Landscape</b>", body_bold_style), comp_items]], colWidths=[140, 364])
        comp_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), HexColor('#f8fafc')),
            ('BOX', (0,0), (-1,-1), 1, HexColor('#e2e8f0')),
            ('TOPPADDING', (0,0), (-1,-1), 8),
            ('BOTTOMPADDING', (0,0), (-1,-1), 8),
            ('LEFTPADDING', (0,0), (-1,-1), 10),
            ('RIGHTPADDING', (0,0), (-1,-1), 10),
        ]))
        story.append(comp_table)
    else:
        story.append(Paragraph("No competitors provided.", body_style))

    story.append(Spacer(1, 16))
    story.append(Paragraph("Keyword Coverage", ParagraphStyle('KeywordHeading', parent=h2_style, fontSize=11, textColor=HexColor('#1e1a4f'), spaceBefore=0, spaceAfter=6)))
    story.append(Paragraph("Final keyword list used for this audit:", body_style))
    story.append(Spacer(1, 6))
    for keyword in keywords:
        story.append(Paragraph(f"• {keyword}", body_style))
    
    story.append(Spacer(1, 80))
    story.append(Paragraph("<font color='#94a3b8'>Generated automatically by Audilysis 2.0 Mention Tracker Engine.</font>", body_style))
    story.append(PageBreak())
    
    # ------------------ PAGE 2: EXECUTIVE SUMMARY & HEATMAP ------------------
    story.append(Paragraph("Executive Summary", h1_style))
    story.append(
        Paragraph(
            build_visibility_summary_text(
                run,
                report_mode=report_health["report_mode"],
                dataforseo_status=dataforseo_status,
            ),
            body_style,
        )
    )
    story.append(Spacer(1, 12))
    
    # Summary Grid (2x2 Box Cards)
    summary_data = [
        [
            Paragraph("<b>TOTAL CHECKS</b><br/>Keywords × Platforms run", cell_lbl_style),
            Paragraph("<b>BRAND MENTIONS</b><br/>Mentions across all queries", cell_lbl_style)
        ],
        [
            Paragraph(
                f"<font size=18 color='#1e1a4f'><b>{'Requires DataForSEO' if total_checks is None and dataforseo_status == 'skipped_by_user' else ('Data Unavailable' if total_checks is None else total_checks)}</b></font>",
                cell_val_style,
            ),
            Paragraph(f"<font size=18 color='#10b981'><b>{brand_mentions if brand_mentions is not None else 'Data Unavailable'}</b></font>", cell_val_style)
        ],
        [
            Paragraph("<b>SHARE OF VOICE (SOV)</b><br/>Relative brand presence rate", cell_lbl_style),
            Paragraph("<b>API RESPONSE HEALTH</b><br/>Successful connection responses", cell_lbl_style)
        ],
        [
            Paragraph(f"<font size=18 color='#4361ee'><b>{brand_sov if brand_sov is not None else 'Data Unavailable'}{'%' if brand_sov is not None else ''}</b></font>", cell_val_style),
            Paragraph(f"<font size=18 color='#f43f5e'><b>{api_health if api_health is not None else 'Data Unavailable'}{'%' if api_health is not None else ''}</b></font>", cell_val_style)
        ]
    ]
    summary_table = Table(summary_data, colWidths=[246, 246])
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), HexColor('#f8fafc')),
        ('BOX', (0,0), (-1,-1), 1, HexColor('#e2e8f0')),
        ('INNERGRID', (0,0), (-1,-1), 0.5, HexColor('#e2e8f0')),
        ('TOPPADDING', (0,0), (-1,-1), 10),
        ('BOTTOMPADDING', (0,0), (-1,-1), 10),
        ('LEFTPADDING', (0,0), (-1,-1), 16),
        ('RIGHTPADDING', (0,0), (-1,-1), 16),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 24))

    completed_agent_results = run_context.get("completed_agent_results") or []
    if completed_agent_results:
        agent_label_map = dict(SEO_AGENT_STATUS_META)
        story.append(Paragraph("Completed SEO Agent Outputs", h1_style))
        story.append(Paragraph("Only agents explicitly completed for this run are listed here. Unrun workflows are not represented as completed analysis.", body_style))
        story.append(Spacer(1, 8))
        agent_rows = [["Agent", "Status", "Source", "Summary"]]
        for row in completed_agent_results:
            result = row.get("result") or {}
            provenance = row.get("provenance") or {}
            agent_id = row.get("agent_name") or provenance.get("agent_id") or result.get("agent_id") or ""
            agent_rows.append([
                html.escape(str(provenance.get("agent_label") or result.get("agent") or agent_label_map.get(agent_id) or agent_id)),
                html.escape(str(row.get("status") or "completed")),
                html.escape(str(provenance.get("data_source") or (result.get("data") or {}).get("data_source") or "run_scoped_agent_result")),
                html.escape(str(result.get("summary") or result.get("message") or "Completed agent result stored for this run."))[:260],
            ])
        agent_table = Table(agent_rows, colWidths=[110, 70, 110, 214])
        agent_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), HexColor('#f1f5f9')),
            ('GRID', (0, 0), (-1, -1), 0.5, HexColor('#e2e8f0')),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ]))
        story.append(agent_table)
        story.append(Spacer(1, 24))

    story.append(Paragraph("Platform Response Health", h1_style))
    platform_rows = [["Platform", "Status", "Error Category", "Safe Message"]]
    for item in report_health["platform_summaries"]:
        platform_rows.append([
            PLATFORM_LABELS.get(item["platform"], item["platform"]),
            item["status"],
            item["error_category"],
            item["safe_error_message"],
        ])
    platform_table = Table(platform_rows, colWidths=[90, 72, 96, 246])
    platform_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), HexColor('#f1f5f9')),
        ('GRID', (0, 0), (-1, -1), 0.5, HexColor('#e2e8f0')),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    story.append(platform_table)
    story.append(Spacer(1, 18))
    
    # Heatmap Table
    story.append(Paragraph("Mention Heatmap (Keyword × Platform)", h1_style))
    story.append(Paragraph(
        "A granular breakdown showing whether your brand was cited (with AI source position rank if available) for each keyword phrase across platforms.",
        body_style
    ))
    story.append(Spacer(1, 8))
    heatmap_has_valid_cells = any(heatmap_data[kw][platform] is not None for kw in keywords for platform in PLATFORM_ORDER)
    if heatmap_has_valid_cells:
        heatmap_rows = [["Keyword", "Google AI", "ChatGPT", "Perplexity", "Gemini", "Claude"]]
        for kw in keywords:
            row = [Paragraph(f"<b>{kw}</b>", body_style)]
            for platform in PLATFORM_ORDER:
                res = heatmap_data[kw][platform]
                if isinstance(res, dict) and res.get("status") == "skipped_by_user":
                    row.append(Paragraph("Not Run", center_bold_style))
                elif res is None:
                    row.append(Paragraph("Data Unavailable", center_bold_style))
                elif res["mentioned"]:
                    pos_lbl = f" (#{int(round(res['position']))})" if res.get('position') else ""
                    row.append(Paragraph(f"Mentioned{pos_lbl}", center_bold_style))
                else:
                    row.append(Paragraph("Not Mentioned", center_bold_style))
            heatmap_rows.append(row)

        heatmap_table = Table(heatmap_rows, colWidths=[154, 70, 70, 70, 70, 70])
        heatmap_style = [
            ('BACKGROUND', (0, 0), (-1, 0), HexColor('#1e1a4f')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('ALIGN', (0, 1), (0, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 0.75, HexColor('#cbd5e1')),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('LEFTPADDING', (0, 0), (-1, -1), 4),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ]
        for r_idx, kw in enumerate(keywords, start=1):
            for c_idx, platform in enumerate(PLATFORM_ORDER, start=1):
                res = heatmap_data[kw][platform]
                if isinstance(res, dict) and res.get("status") == "skipped_by_user":
                    bg = '#cbd5e1'
                elif res is None:
                    bg = '#94a3b8'
                elif res["mentioned"]:
                    bg = '#10b981'
                else:
                    bg = '#f43f5e'
                heatmap_style.append(('BACKGROUND', (c_idx, r_idx), (c_idx, r_idx), HexColor(bg)))
        heatmap_table.setStyle(TableStyle(heatmap_style))
        story.append(heatmap_table)
    else:
        story.append(Paragraph("Data Unavailable", body_style))
    story.append(PageBreak())
    
    # ------------------ PAGE 3: CHARTS SECTION ------------------
    story.append(Paragraph("Visual Analytics", h1_style))
    story.append(Paragraph(
        "Visual comparison of your brand share of voice compared to competitors and breakdown success rate across query channels.",
        body_style
    ))
    story.append(Spacer(1, 10))
    
    chart_elements = []
    
    # 1. SOV Chart
    sov_bytes = generate_sov_chart(metrics, run["brand_domain"])
    if sov_bytes:
        chart_elements.append(Image(sov_bytes, width=240, height=130))
        
    # 2. Platform Breakdown Chart
    plat_bytes = generate_platform_chart(platform_breakdown)
    if plat_bytes:
        chart_elements.append(Image(plat_bytes, width=240, height=130))
        
    # Wrap in Table to display side-by-side
    if len(chart_elements) == 2:
        charts_table = Table([[chart_elements[0], chart_elements[1]]], colWidths=[252, 252])
        charts_table.setStyle(TableStyle([
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ]))
        story.append(charts_table)
        story.append(Spacer(1, 20))
    elif len(chart_elements) == 1:
        story.append(chart_elements[0])
        story.append(Spacer(1, 20))
    else:
        story.append(Paragraph("Data Unavailable", body_style))
        story.append(Spacer(1, 20))
        
    chart_elements_bottom = []
    
    # 3. Trend Chart
    trend_bytes = generate_trend_chart(trend_data, run["brand_domain"], competitor_domains)
    if trend_bytes:
        chart_elements_bottom.append(Image(trend_bytes, width=240, height=130))
    else:
        # Placeholder or text explanation if trend is not available yet
        no_trend_txt = Paragraph(
            "<br/><br/><br/><i>Trend Chart requires 2 or more tracking runs. Historical statistics will build here on subsequent executions.</i>",
            ParagraphStyle('NoTrend', parent=body_style, alignment=1)
        )
        chart_elements_bottom.append(no_trend_txt)
        
    # 4. Citations domains Chart
    dom_bytes = generate_domains_chart(top_domains, run["brand_domain"])
    if dom_bytes:
        chart_elements_bottom.append(Image(dom_bytes, width=240, height=130))
    else:
        no_dom_txt = Paragraph(
            "<br/><br/><br/><i>No domain citations were registered in responses.</i>",
            ParagraphStyle('NoDom', parent=body_style, alignment=1)
        )
        chart_elements_bottom.append(no_dom_txt)
        
    if len(chart_elements_bottom) == 2:
        bottom_charts_table = Table([[chart_elements_bottom[0], chart_elements_bottom[1]]], colWidths=[252, 252])
        bottom_charts_table.setStyle(TableStyle([
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ]))
        story.append(bottom_charts_table)
    elif len(chart_elements_bottom) == 1:
        story.append(chart_elements_bottom[0])
    else:
        story.append(Paragraph("Data Unavailable", body_style))
        
    story.append(PageBreak())
    
    # ------------------ PAGE 4: TOP DOMAINS & SOURCES CITED ------------------
    story.append(Paragraph("Reference & Citation Analytics", h1_style))
    story.append(Spacer(1, 4))
    
    # Top Mentioned Domains Table
    story.append(Paragraph("Top Cited Reference Domains", h2_style))
    story.append(Paragraph("Frequency count of domains referenced by AI engines when building response contexts.", body_style))
    story.append(Spacer(1, 6))
    
    dom_rows = [["Reference Domain", "Citations Count", "Presence Significance"]]
    for item in top_domains[:10]:  # Top 10
        sig = "High" if item["count"] > 2 else "Normal"
        if item["domain"].lower() == run["brand_domain"].lower():
            row = [
                Paragraph(f"<b>{item['domain']} (Your Brand)</b>", body_style),
                Paragraph(f"<b>{item['count']}</b>", body_style),
                Paragraph("Target Brand", body_bold_style)
            ]
        else:
            row = [
                Paragraph(item["domain"], body_style),
                Paragraph(str(item["count"]), body_style),
                Paragraph(sig, body_style)
            ]
        dom_rows.append(row)
        
    if len(dom_rows) > 1:
        dom_table = Table(dom_rows, colWidths=[220, 120, 164])
        dom_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), HexColor('#f1f5f9')),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 0.5, HexColor('#e2e8f0')),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ]))
        story.append(dom_table)
    else:
        story.append(Paragraph("No domains were cited in responses.", body_style))
        
    story.append(Spacer(1, 15))
    
    # Sources List Table (Span multiple pages naturally)
    story.append(Paragraph("Sources Being Cited (Citations List)", h2_style))
    story.append(Paragraph("Granular list of exact URL links cited by platform engine for specific keyword targets.", body_style))
    story.append(Spacer(1, 6))
    
    cited_rows = [["Keyword Target", "Platform", "Cited Resource URLs"]]
    for res in valid_results:
        if res.get("sources_cited"):
            kw_para = Paragraph(f"<b>{res['keyword']}</b>", body_style)
            plat_para = Paragraph(res["platform"].upper().replace('_', ' '), body_bold_style)
            
            # Put URLs on separate wrapped lines
            url_elements = []
            for url in res["sources_cited"]:
                if run["brand_domain"].lower() in url.lower():
                    # Bold & highlight brand matching citations
                    url_elements.append(Paragraph(f"<b>[★ Brand] {url}</b>", url_style))
                else:
                    url_elements.append(Paragraph(url, url_style))
                    
            cited_rows.append([kw_para, plat_para, url_elements])
            
    if len(cited_rows) > 1:
        # Col widths summing exactly to 504pt (144 + 80 + 280)
        cited_table = Table(cited_rows, colWidths=[144, 80, 280])
        cited_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), HexColor('#f1f5f9')),
            ('VALIGN', (0,0), (-1,-1), 'TOP'),
            ('GRID', (0,0), (-1,-1), 0.5, HexColor('#e2e8f0')),
            ('TOPPADDING', (0,0), (-1,-1), 6),
            ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ]))
        story.append(cited_table)
    else:
        story.append(Paragraph("Data Unavailable", body_style))
        
    story.append(PageBreak())
    
    # ------------------ PAGE 5+: FULL AI RESPONSES ------------------
    story.append(Paragraph("AI Visibility Summary", h1_style))
    story.append(Paragraph(
        "A client-friendly overview of how the brand appeared in AI-generated results, including visibility level, estimated ranking position, score, sentiment, and confidence.",
        body_style
    ))
    story.append(Spacer(1, 10))

    for kw in keywords:
        match = select_visibility_result(valid_results, kw)
        summary = build_visibility_summary(match)
        summary_row = Table([
            [Paragraph("<b>Keyword</b>", body_bold_style), Paragraph(kw, body_style)],
            [Paragraph("<b>Mention Status</b>", body_bold_style), Paragraph(summary['mention_status'], body_style)],
            [Paragraph("<b>AI Visibility Level</b>", body_bold_style), Paragraph(summary['visibility_level'], body_style)],
            [Paragraph("<b>Approximate Position</b>", body_bold_style), Paragraph(summary['position'], body_style)],
            [Paragraph("<b>Visibility Score</b>", body_bold_style), Paragraph(summary['visibility_score'], body_style)],
            [Paragraph("<b>Sentiment</b>", body_bold_style), Paragraph(summary['sentiment'], body_style)],
            [Paragraph("<b>Confidence Level</b>", body_bold_style), Paragraph(summary['confidence'], body_style)],
        ], colWidths=[180, 324])
        summary_row.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), HexColor('#f8fafc')),
            ('GRID', (0,0), (-1,-1), 0.5, HexColor('#e2e8f0')),
            ('TOPPADDING', (0,0), (-1,-1), 7),
            ('BOTTOMPADDING', (0,0), (-1,-1), 7),
            ('LEFTPADDING', (0,0), (-1,-1), 8),
            ('RIGHTPADDING', (0,0), (-1,-1), 8),
        ]))
        story.append(summary_row)
        story.append(Spacer(1, 10))

    story.append(Paragraph("Full AI Responses (Expanded Review)", h1_style))
    story.append(Paragraph(
        "Complete output responses retrieved from Google AI Overview and LLM agents for keyword queries. Brand matches are highlighted.",
        body_style
    ))
    story.append(Spacer(1, 10))
    
    for kw in keywords:
        story.append(Paragraph(f"Keyword: \"{kw}\"", h2_style))
        
        # Pull all responses for this keyword
        for platform in ['google', 'chat_gpt', 'perplexity', 'gemini', 'claude']:
            match = None
            for res in results:
                if res["keyword"] == kw and res["platform"] == platform:
                    match = res
                    break
                    
            platform_name = platform.upper().replace('_', ' ')
            
            # Subcard structure
            card_content = []
            
            # Header line of the subcard
            status_text = ""
            if match is not None:
                summary_info = build_visibility_summary(match)
                if match.get("mentioned"):
                    status_text = f"<font color='#10b981'><b>{summary_info['mention_status']} · {summary_info['visibility_level']}</b></font>"
                elif match.get("mentioned") is None:
                    status_text = f"<font color='#94a3b8'><b>{html.escape(match.get('response_status') or 'No data')}</b></font>"
                else:
                    status_text = "<font color='#f43f5e'><b>Not mentioned</b></font>"
            else:
                status_text = "<font color='#94a3b8'><b>No data</b></font>"
                
            header_para = Paragraph(f"<b>{platform_name}</b> &nbsp;&nbsp;|&nbsp;&nbsp; Status: {status_text}", body_bold_style)
            card_content.append(header_para)
            card_content.append(Spacer(1, 4))
            
            # Response text
            resp_raw = build_response_explanation(match)
            highlighted_html = escape_and_highlight(resp_raw, run["brand_name"], run["brand_domain"])
            body_para = Paragraph(highlighted_html, response_content_style)
            card_content.append(body_para)
            
            # Build subtable container to simulate card styling
            card_table = Table([[card_content]], colWidths=[500])
            card_table.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,-1), HexColor('#ffffff')),
                ('BOX', (0,0), (-1,-1), 1, HexColor('#e2e8f0')),
                ('TOPPADDING', (0,0), (-1,-1), 8),
                ('BOTTOMPADDING', (0,0), (-1,-1), 8),
                ('LEFTPADDING', (0,0), (-1,-1), 10),
                ('RIGHTPADDING', (0,0), (-1,-1), 10),
            ]))
            
            # Wrap card in a KeepTogether to avoid page-breaking middle-card
            story.append(KeepTogether([card_table, Spacer(1, 10)]))
            
    # Build Document using our custom two-pass NumberedCanvas
    doc.build(story, canvasmaker=NumberedCanvas)
    
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes
