import json
import socket
import time
import uuid
import threading
import webbrowser
from pathlib import Path
from urllib.parse import urlparse
from datetime import datetime

from flask import Flask, request, jsonify, render_template, redirect, url_for, session, Response

# Imports from local packages
from db.storage import (
    init_db, create_run, insert_mention_result, insert_competitor_metrics,
    get_run, get_latest_run, get_mention_results, get_competitor_metrics, get_trend_data
)
from api.dataforseo import query_platform
from services.mailer import send_report_email
from services.pdf_generator import generate_pdf_report
from agents.agent_manager import (
    CONTENT_GROUP,
    SEO_GROUP,
    SOCIAL_GROUP,
    get_all_agents,
    get_agents_by_group,
    run_agent,
)

app = Flask(__name__)
app.secret_key = "audilysis_secure_session_key_2.0"

# Thread-safe tracker cancellation map
cancelled_runs = set()

def extract_domain(url):
    """Utility to clean and parse domain name from a URL."""
    try:
        netloc = urlparse(url).netloc
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc.lower()
    except Exception:
        return ""

def generate_report_content(run_id):
    """
    Utility to fetch all dashboard statistics and build templates arguments.
    Reused by Dashboard view, Report export download, and SMTP email attachment.
    """
    run = get_run(run_id)
    if not run:
        return None
        
    results = get_mention_results(run_id)
    metrics = get_competitor_metrics(run_id)
    
    # Calculate stats
    total_checks = len(results)
    brand_mentions = sum(1 for r in results if r.get("mentioned"))
    successful_checks = sum(1 for r in results if r.get("mentioned") is not None)
    
    brand_sov = round((brand_mentions / total_checks * 100), 1) if total_checks > 0 else 0.0
    api_health = round((successful_checks / total_checks * 100), 1) if total_checks > 0 else 0.0
    
    # Heatmap keywords
    keywords = sorted(list(set(r["keyword"] for r in results)))
    
    # Heatmap dictionary mapping keyword -> platform -> result
    heatmap_data = {kw: {plat: None for plat in ['google', 'chat_gpt', 'perplexity', 'gemini', 'claude']} for kw in keywords}
    for r in results:
        kw = r["keyword"]
        plat = r["platform"]
        heatmap_data[kw][plat] = {
            "mentioned": r["mentioned"],
            "position": r["mention_position"]
        } if r["mentioned"] is not None else None
        
    # Platform breakdown count for brand
    platform_breakdown = {plat: 0 for plat in ['google', 'chat_gpt', 'perplexity', 'gemini', 'claude']}
    for r in results:
        if r.get("mentioned") and r.get("platform") in platform_breakdown:
            platform_breakdown[r["platform"]] += 1
            
    # Trend Over Time
    competitor_domains = [m["domain"] for m in metrics if m["domain"].lower() != run["brand_domain"].lower()]
    trend_data = get_trend_data(run["brand_domain"], competitor_domains)
    
    # Top Mentioned Domains (Citation counts)
    domain_counts = {}
    for r in results:
        for url in r.get("sources_cited", []):
            dom = extract_domain(url)
            if dom:
                domain_counts[dom] = domain_counts.get(dom, 0) + 1
    
    top_domains = [{"domain": dom, "count": count} for dom, count in sorted(domain_counts.items(), key=lambda x: x[1], reverse=True)]
    
    # Top competitor details
    top_competitor_name = "None"
    top_competitor_mentions = 0
    competitor_metrics = [m for m in metrics if m["domain"].lower() != run["brand_domain"].lower()]
    if competitor_metrics:
        top_comp = max(competitor_metrics, key=lambda x: x["total_mentions"])
        if top_comp["total_mentions"] > 0:
            top_competitor_name = top_comp["domain"]
            top_competitor_mentions = top_comp["total_mentions"]

    return {
        "run": run,
        "results": results,
        "metrics": metrics,
        "keywords": keywords,
        "heatmap_data": heatmap_data,
        "platform_breakdown": platform_breakdown,
        "trend_data": trend_data,
        "top_domains": top_domains,
        "stat_total_checks": total_checks,
        "stat_brand_mentions": brand_mentions,
        "stat_brand_sov": brand_sov,
        "stat_api_health": api_health,
        "top_competitor_name": top_competitor_name,
        "top_competitor_mentions": top_competitor_mentions
    }

# ================= Routes =================

@app.route("/")
def index():
    """Default landing routing logic."""
    latest = get_latest_run()
    if latest:
        return redirect(url_for("dashboard"))
    return redirect(url_for("setup"))

@app.route("/setup")
def setup():
    """Setup form screen."""
    return render_template("setup.html")


@app.route("/favicon.ico")
def favicon():
    """Prevent browser favicon requests from generating noisy 404 logs."""
    return Response(status=204)

@app.route("/api/run", methods=["POST"])
def api_run():
    """Handles configuration submission and saves settings inside Flask session."""
    data = request.json
    if not data:
        return jsonify({"status": "error", "message": "Missing payload."}), 400
        
    credentials = data.get("credentials")
    config = data.get("config")
    email_settings = data.get("email_settings")
    
    # Validations
    if not credentials or not credentials.get("login") or not credentials.get("password"):
        return jsonify({"status": "error", "message": "DataForSEO credentials are required."}), 400
    if not config or not config.get("brand_domain") or not config.get("brand_name"):
        return jsonify({"status": "error", "message": "Brand domain and brand name are required."}), 400
    if not config.get("keywords") or len(config.get("keywords")) == 0:
        return jsonify({"status": "error", "message": "At least one keyword is required."}), 400
        
    # Store settings in flask session
    session["credentials"] = credentials
    session["tracker_config"] = config
    session["email_settings"] = email_settings
    
    # Assign unique session ID to manage thread cancel hooks
    session["session_run_id"] = str(uuid.uuid4())
    
    return jsonify({"status": "success"})

@app.route("/running")
def running():
    """Progress screen rendering."""
    # Safety checks
    if "tracker_config" not in session:
        return redirect(url_for("setup"))
    return render_template("running.html")

@app.route("/api/cancel", methods=["POST"])
def api_cancel():
    """Sets current session identifier as cancelled."""
    run_sid = session.get("session_run_id")
    if run_sid:
        cancelled_runs.add(run_sid)
    return jsonify({"status": "cancelled"})

@app.route("/stream")
def stream():
    """SSE streaming endpoint."""
    config = session.get("tracker_config")
    creds = session.get("credentials")
    email_cfg = session.get("email_settings")
    session_run_id = session.get("session_run_id")
    
    if not config or not creds or not session_run_id:
        # Yield single error and exit
        def err_gen():
            yield f"data: {json.dumps({'status': 'error', 'error_message': 'Session expired or configuration missing.'})}\n\n"
        return Response(err_gen(), mimetype='text/event-stream')
        
    # Create the run in DB prior to streaming
    brand_domain = config.get("brand_domain")
    brand_name = config.get("brand_name")
    country = config.get("country", "India")
    language = config.get("language", "en")
    competitor_domains = config.get("competitors", [])
    keywords = config.get("keywords", [])
    
    run_id = create_run(brand_domain, brand_name, country, language, competitor_domains)
    session["last_run_id"] = run_id
    
    # Outer generator execution scope passing isolated variables
    def generate(config, creds, email_cfg, run_id, session_run_id):
        platforms = ["google", "chat_gpt", "perplexity", "gemini", "claude"]
        total_steps = len(keywords) * len(platforms)
        current_step = 0
        
        # Clear cancellations list for this new thread execution if present
        if session_run_id in cancelled_runs:
            cancelled_runs.remove(session_run_id)
            
        for keyword in keywords:
            for platform in platforms:
                # Polling Cancel Check
                if session_run_id in cancelled_runs:
                    yield f"data: {json.dumps({'progress': 100, 'current_step': current_step, 'total_steps': total_steps, 'message': '[SYSTEM] Tracker run aborted by user.', 'status': 'error', 'error_message': 'User cancelled run.'})}\n\n"
                    return
                    
                current_step += 1
                progress = (current_step / total_steps) * 100
                
                # Fetch platform name mapping
                platform_names = {
                    "google": "Google AI Mode",
                    "chat_gpt": "ChatGPT",
                    "perplexity": "Perplexity",
                    "gemini": "Gemini",
                    "claude": "Claude"
                }
                p_name = platform_names.get(platform, platform)
                log_message = f'[{current_step}/{total_steps}] Checking "{keyword}" on {p_name}...'
                yield f"data: {json.dumps({'progress': progress, 'current_step': current_step, 'total_steps': total_steps, 'message': log_message, 'status': 'running'})}\n\n"
                
                # Run the API query
                response_text, sources, error = query_platform(
                    platform, keyword, creds, brand_domain, brand_name, competitor_domains, country, language
                )
                
                # Parse brand mention
                mentioned = None
                mention_position = None
                competitor_mentions = {}
                
                if error:
                    # Log error details and write NULL to DB
                    log_line = f'[{current_step}/{total_steps}] "{keyword}" → {p_name}... ❌ API error: {error}'
                else:
                    mentioned = (brand_domain.lower() in response_text.lower()) or (brand_name.lower() in response_text.lower())
                    
                    if mentioned:
                        # Compute average mention position
                        lines = response_text.split('\n')
                        positions = [idx + 1 for idx, l in enumerate(lines) if brand_domain.lower() in l.lower() or brand_name.lower() in l.lower()]
                        if positions:
                            mention_position = int(round(sum(positions) / len(positions)))
                            
                    # Competitors
                    for comp in competitor_domains:
                        competitor_mentions[comp] = (comp.lower() in response_text.lower())
                        
                    status_lbl = "✓ Mentioned" if mentioned else "✗ Not Mentioned"
                    pos_lbl = f" (position {mention_position})" if (mentioned and mention_position) else ""
                    log_line = f'[{current_step}/{total_steps}] "{keyword}" → {p_name}... {status_lbl}{pos_lbl}'
                    
                    # Save result to DB 
                insert_mention_result(
                        run_id, keyword, platform, mentioned, mention_position, sources, competitor_mentions, response_text
                )
                
                yield f"data: {json.dumps({'progress': progress, 'current_step': current_step, 'total_steps': total_steps, 'message': log_line, 'status': 'running'})}\n\n"
                
        # --- Run Complete Post-Processing ---
        # Calculate competitor SOV metrics locally
        results = get_mention_results(run_id)
        total_checks = len(results)
        
        domains_to_track = [brand_domain.lower()] + [c.lower() for c in competitor_domains]
        domain_mentions = {d: 0 for d in domains_to_track}
        domain_positions = {d: [] for d in domains_to_track}
        
        for res in results:
            text = res.get("ai_response_text")
            if not text:
                continue
                
            # Brand
            if brand_domain.lower() in text.lower() or brand_name.lower() in text.lower():
                domain_mentions[brand_domain.lower()] += 1
                pos = res.get("mention_position")
                if pos is not None:
                    domain_positions[brand_domain.lower()].append(pos)
                    
            # Competitors
            for comp in competitor_domains:
                if comp.lower() in text.lower():
                    domain_mentions[comp.lower()] += 1
                    # Find mention position for competitor in this text
                    lines = text.split('\n')
                    comp_positions = [idx + 1 for idx, l in enumerate(lines) if comp.lower() in l.lower()]
                    if comp_positions:
                        avg_p = sum(comp_positions) / len(comp_positions)
                        domain_positions[comp.lower()].append(avg_p)
                        
        # Store competitor metrics
        for dom in domains_to_track:
            mentions_count = domain_mentions[dom]
            avg_pos = sum(domain_positions[dom]) / len(domain_positions[dom]) if domain_positions[dom] else 0.0
            sov = round((mentions_count / total_checks * 100), 1) if total_checks > 0 else 0.0
            insert_competitor_metrics(run_id, dom, mentions_count, avg_pos, sov)

        # Handle automatic emailing if configured
        if email_cfg and email_cfg.get("email_automatically"):
            recipient_list = email_cfg.get('recipient_emails', '')
            yield f"data: {json.dumps({'progress': 100, 'current_step': total_steps, 'total_steps': total_steps, 'message': f'[SMTP] Preparing report email to {recipient_list}...', 'status': 'running'})}\n\n"
            
            # Fetch data & build report
            report_data = generate_report_content(run_id)
            if report_data:
                try:
                    # Generate dynamic PDF
                    report_pdf = generate_pdf_report(run_id)
                    if not report_pdf:
                        raise ValueError("PDF generation returned empty bytes.")
                    
                    # Create email subject and bodies
                    date_str = datetime.now().strftime("%Y-%m-%d")
                    subject = f"Audilysis 2.0 — {brand_name} AI Mention Tracking Report — {date_str}"
                    
                    body_html = f"""
                    <html>
                    <body style="font-family: sans-serif; color: #334155; line-height: 1.5; padding: 20px;">
                        <h2 style="color: #1e1a4f; margin-bottom: 4px;">Audilysis 2.0</h2>
                        <p style="font-size: 14px; color: #64748b; margin-top: 0;">AI Mention Tracking Summary</p>
                        <hr style="border: 0; border-top: 1px solid #e2e8f0; margin: 20px 0;">
                        <table style="width: 100%; max-width: 500px; border-collapse: collapse;">
                            <tr>
                                <td style="padding: 8px 0; font-weight: bold; color: #475569;">Brand Name:</td>
                                <td style="padding: 8px 0; text-align: right;">{brand_name} ({brand_domain})</td>
                            </tr>
                            <tr>
                                <td style="padding: 8px 0; font-weight: bold; color: #475569;">Mention Rate (SOV):</td>
                                <td style="padding: 8px 0; text-align: right; color: #10b981; font-weight: bold;">{report_data['stat_brand_sov']}%</td>
                            </tr>
                            <tr>
                                <td style="padding: 8px 0; font-weight: bold; color: #475569;">Total Checks:</td>
                                <td style="padding: 8px 0; text-align: right;">{report_data['stat_total_checks']}</td>
                            </tr>
                            <tr>
                                <td style="padding: 8px 0; font-weight: bold; color: #475569;">Top Competitor:</td>
                                <td style="padding: 8px 0; text-align: right;">{report_data['top_competitor_name']} ({report_data['top_competitor_mentions']} mentions)</td>
                            </tr>
                        </table>
                        <p style="margin-top: 24px; font-size: 14px;">The complete interactive PDF report is attached to this email. You can open and view it in any PDF reader.</p>
                    </body>
                    </html>
                    """
                    filename = f"Audilysis-2.0-AI-Mention-Report-{brand_domain}-{date_str}.pdf"
                    
                    # Send
                    to_str, mail_err = send_report_email(
                        email_cfg["smtp_host"],
                        email_cfg["smtp_port"],
                        email_cfg["sender_email"],
                        email_cfg["sender_password"],
                        email_cfg["recipient_emails"],
                        subject,
                        body_html,
                        report_pdf,
                        filename
                    )
                    
                    if mail_err:
                        yield f"data: {json.dumps({'progress': 100, 'current_step': total_steps, 'total_steps': total_steps, 'message': f'[SMTP] ❌ Auto-email failed: {mail_err}', 'status': 'running'})}\n\n"
                    else:
                        yield f"data: {json.dumps({'progress': 100, 'current_step': total_steps, 'total_steps': total_steps, 'message': f'[SMTP] ✓ Auto-email sent successfully to {to_str}', 'status': 'running'})}\n\n"
                except Exception as e:
                    yield f"data: {json.dumps({'progress': 100, 'current_step': total_steps, 'total_steps': total_steps, 'message': f'[SMTP] ❌ Auto-email render error: {str(e)}', 'status': 'running'})}\n\n"
                    
        # Redirect URL
        yield f"data: {json.dumps({'progress': 100, 'current_step': total_steps, 'total_steps': total_steps, 'message': '[SYSTEM] Redirecting to Dashboard...', 'status': 'completed', 'redirect_url': '/dashboard'})}\n\n"

    return Response(generate(config, creds, email_cfg, run_id, session_run_id), mimetype='text/event-stream')

@app.route("/agents")
def agents_page():
    """SEO agent studio UI."""
    return render_template("agents.html", agents=get_agents_by_group(SEO_GROUP))


@app.route("/content-agents")
def content_agents_page():
    """Content marketing agent studio UI."""
    return render_template("content_agents.html", agents=get_agents_by_group(CONTENT_GROUP))


@app.route("/social-agents")
def social_agents_page():
    """Social media agent studio UI."""
    return render_template("social_agents.html", agents=get_agents_by_group(SOCIAL_GROUP))


@app.route("/seo-reports")
def seo_reports_page():
    """SEO reports page showing reporting agents."""
    return render_template("seo_reports.html")


@app.route("/seo-strategy")
def seo_strategy_page():
    """SEO strategy page showing the strategy agent."""
    return render_template("seo_strategy.html")


@app.route("/run-agent", methods=["POST"])
def run_agent_route():
    """Execute a selected SEO, Content, or Social agent and return structured JSON."""
    payload = request.get_json(silent=True) or {}
    agent_id = (payload.get("agent") or payload.get("agent_id") or "").strip()
    if not agent_id:
        return jsonify({"success": False, "message": "Agent is required.", "agent": None, "summary": "", "recommendations": [], "data": {}}), 400

    result = run_agent(agent_id, payload)
    if result.get("status") == "error" and not result.get("success"):
        return jsonify(result), 400
    return jsonify(result)


@app.route("/dashboard")
def dashboard():
    """Analytics dashboard main interface view."""
    # Read last run ID
    run_id = session.get("last_run_id")
    if not run_id:
        # Fall back to latest in SQLite
        latest = get_latest_run()
        if latest:
            run_id = latest["id"]
        else:
            return redirect(url_for("setup"))
            
    report_data = generate_report_content(run_id)
    if not report_data:
        return redirect(url_for("setup"))
        
    # Check if SMTP email credentials exist in current session
    email_enabled = False
    email_cfg = session.get("email_settings")
    if email_cfg and email_cfg.get("smtp_host") and email_cfg.get("sender_email") and email_cfg.get("sender_password"):
        email_enabled = True
        
    report_data["email_enabled"] = email_enabled
    grouped_agents = get_all_agents()
    report_data["agent_counts"] = {
        "SEO": len(grouped_agents.get(SEO_GROUP, [])),
        "Content": len(grouped_agents.get(CONTENT_GROUP, [])),
        "Social": len(grouped_agents.get(SOCIAL_GROUP, [])),
    }
    return render_template("dashboard.html", **report_data)

@app.route("/download-report")
def download_report():
    """Generates a downloadable offline PDF file."""
    run_id = request.args.get("run_id", type=int)
    if not run_id:
        latest = get_latest_run()
        if latest:
            run_id = latest["id"]
        else:
            return "No runs available.", 404
            
    run_data = get_run(run_id)
    if not run_data:
        return "Run not found.", 404
        
    # Generate dynamic PDF report bytes
    report_pdf = generate_pdf_report(run_id)
    if not report_pdf:
        return "Failed to generate PDF report.", 500
        
    # Filename
    brand_domain = run_data["brand_domain"]
    date_str = datetime.now().strftime("%Y-%m-%d")
    filename = f"Audilysis-2.0-AI-Mention-Report-{brand_domain}-{date_str}.pdf"
    
    return Response(
        report_pdf,
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment;filename={filename}"}
    )

@app.route("/api/email-report", methods=["POST"])
def api_email_report():
    """AJAX endpoint to email latest report on demand as PDF."""
    data = request.json or {}
    run_id = data.get("run_id")
    
    if not run_id:
        latest = get_latest_run()
        if latest:
            run_id = latest["id"]
        else:
            return jsonify({"status": "error", "message": "No run data available to email."}), 404
            
    # Read SMTP configurations from session
    email_cfg = session.get("email_settings")
    if not email_cfg or not email_cfg.get("smtp_host") or not email_cfg.get("sender_email") or not email_cfg.get("sender_password"):
        return jsonify({"status": "error", "message": "Email settings are missing in current session. Configure SMTP settings in Setup page first."}), 400
        
    report_data = generate_report_content(run_id)
    if not report_data:
        return jsonify({"status": "error", "message": "Run report content could not be found."}), 404
        
    brand_name = report_data["run"]["brand_name"]
    brand_domain = report_data["run"]["brand_domain"]
    
    try:
        # Generate dynamic PDF report
        report_pdf = generate_pdf_report(run_id)
        if not report_pdf:
            return jsonify({"status": "error", "message": "Failed to compile PDF report."}), 500
            
        # Build Subject and Body HTML
        date_str = datetime.now().strftime("%Y-%m-%d")
        subject = f"Audilysis 2.0 — {brand_name} AI Mention Tracking Report — {date_str}"
        
        body_html = f"""
        <html>
        <body style="font-family: sans-serif; color: #334155; line-height: 1.5; padding: 20px;">
            <h2 style="color: #1e1a4f; margin-bottom: 4px;">Audilysis 2.0</h2>
            <p style="font-size: 14px; color: #64748b; margin-top: 0;">AI Mention Tracking Summary</p>
            <hr style="border: 0; border-top: 1px solid #e2e8f0; margin: 20px 0;">
            <table style="width: 100%; max-width: 500px; border-collapse: collapse;">
                <tr>
                    <td style="padding: 8px 0; font-weight: bold; color: #475569;">Brand Name:</td>
                    <td style="padding: 8px 0; text-align: right;">{brand_name} ({brand_domain})</td>
                </tr>
                <tr>
                    <td style="padding: 8px 0; font-weight: bold; color: #475569;">Mention Rate (SOV):</td>
                    <td style="padding: 8px 0; text-align: right; color: #10b981; font-weight: bold;">{report_data['stat_brand_sov']}%</td>
                </tr>
                <tr>
                    <td style="padding: 8px 0; font-weight: bold; color: #475569;">Total Checks:</td>
                    <td style="padding: 8px 0; text-align: right;">{report_data['stat_total_checks']}</td>
                </tr>
                <tr>
                    <td style="padding: 8px 0; font-weight: bold; color: #475569;">Top Competitor:</td>
                    <td style="padding: 8px 0; text-align: right;">{report_data['top_competitor_name']} ({report_data['top_competitor_mentions']} mentions)</td>
                </tr>
            </table>
            <p style="margin-top: 24px; font-size: 14px;">Your complete Audilysis 2.0 report is attached as a PDF file. You can view it in any PDF reader.</p>
        </body>
        </html>
        """
        filename = f"Audilysis-2.0-AI-Mention-Report-{brand_domain}-{date_str}.pdf"
        
        # Send mail
        to_str, mail_err = send_report_email(
            email_cfg["smtp_host"],
            email_cfg["smtp_port"],
            email_cfg["sender_email"],
            email_cfg["sender_password"],
            email_cfg["recipient_emails"],
            subject,
            body_html,
            report_pdf,
            filename
        )
        
        if mail_err:
            return jsonify({"status": "error", "message": mail_err}), 500
            
        return jsonify({"status": "sent", "to": to_str})
        
    except Exception as e:
        return jsonify({"status": "error", "message": f"Server encountered mail error: {str(e)}"}), 500

# ================= Startup =================

def find_available_port(start_port=5000, host="127.0.0.1"):
    """Returns the first available local TCP port at or above start_port."""
    port = start_port
    while port < start_port + 100:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
                return port
            except OSError:
                port += 1
    raise RuntimeError("Unable to find an open local port for the Flask app.")


def open_browser(port):
    """Opens local Flask app in default browser after server initializes."""
    time.sleep(1.0)
    webbrowser.open(f"http://127.0.0.1:{port}")

if __name__ == "__main__":
    # Initialize SQLite schema
    init_db()
    host = "127.0.0.1"
    port = find_available_port(5000, host)
    
    # Print clean terminal startup banner
    print("\n" + "="*50)
    print(" ✦ Audilysis 2.0 is running!")
    print(f" ✦ Open in your browser: http://{host}:{port}")
    print(" ✦ Press Ctrl+C to stop.")
    print("="*50 + "\n")
    
    # Launch browser window asynchronously
    threading.Thread(target=open_browser, args=(port,), daemon=True).start()
    
    # Start Flask (Local-only binding)
    app.run(host=host, port=port, debug=True, use_reloader=False)
