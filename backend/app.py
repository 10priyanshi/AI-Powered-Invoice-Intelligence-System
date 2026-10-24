"""
Invoice AI Processing System - Main Flask Application
Production-grade backend with multi-agent architecture
"""

import os
import sys
import json
import uuid
import base64
import logging
from datetime import datetime
from pathlib import Path

# Load .env file if present (must happen before agents init so API key is available)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass  # pip install python-dotenv  OR  export ANTHROPIC_API_KEY=...

from flask import Flask, request, jsonify, send_from_directory, render_template
from flask_cors import CORS
from werkzeug.utils import secure_filename

# Add parent to path for agent imports
sys.path.insert(0, str(Path(__file__).parent))

from agents.extraction_agent import ExtractionAgent
from agents.validation_agent import ValidationAgent
from agents.insights_agent import InsightsAgent
from agents.chat_agent import ChatAgent
from storage.invoice_store import InvoiceStore

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

# ── App Setup ─────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.parent
UPLOAD_DIR = BASE_DIR / "uploads"
EXPORT_DIR = BASE_DIR / "exports"
UPLOAD_DIR.mkdir(exist_ok=True)
EXPORT_DIR.mkdir(exist_ok=True)

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "pdf", "tiff", "webp"}
MAX_BATCH_SIZE = 50

app = Flask(
    __name__,
    static_folder=str(BASE_DIR / "frontend" / "static"),
    template_folder=str(BASE_DIR / "frontend" / "templates")
)
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # 200MB
CORS(app)

# ── Agent Initialisation ──────────────────────────────────────────────────────
store = InvoiceStore(BASE_DIR / "processed")
extraction_agent = ExtractionAgent()
validation_agent = ValidationAgent(store)
insights_agent = InsightsAgent(store)
chat_agent = ChatAgent(store)


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def encode_file(filepath: str) -> tuple[str, str]:
    """Return (base64_data, media_type) for a file."""
    ext = Path(filepath).suffix.lower().lstrip(".")
    mime_map = {
        "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "png": "image/png", "tiff": "image/tiff",
        "webp": "image/webp", "pdf": "application/pdf"
    }
    media_type = mime_map.get(ext, "image/jpeg")
    with open(filepath, "rb") as f:
        return base64.standard_b64encode(f.read()).decode("utf-8"), media_type


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/erp")
def erp():
    return render_template("erp.html")

@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")

@app.route("/chat")
def chat_page():
    return render_template("chat.html")


@app.route("/api/upload", methods=["POST"])
def upload_invoices():
    """Batch upload endpoint — up to 50 files."""
    if "files" not in request.files:
        return jsonify({"error": "No files provided"}), 400

    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "Empty file list"}), 400
    if len(files) > MAX_BATCH_SIZE:
        return jsonify({"error": f"Max {MAX_BATCH_SIZE} files per batch"}), 400

    batch_id = str(uuid.uuid4())[:8].upper()
    results = []

    for file in files:
        if not file or not file.filename:
            continue
        if not allowed_file(file.filename):
            results.append({
                "filename": file.filename,
                "status": "error",
                "error": "Unsupported file type"
            })
            continue

        # Save file
        safe_name = secure_filename(file.filename)
        unique_name = f"{batch_id}_{uuid.uuid4().hex[:6]}_{safe_name}"
        filepath = UPLOAD_DIR / unique_name
        file.save(str(filepath))

        try:
            # Encode file for AI
            b64_data, media_type = encode_file(str(filepath))

            # Agent 1: Extract structured data
            logger.info(f"Extracting: {safe_name}")
            extracted = extraction_agent.extract(b64_data, media_type, safe_name, batch_id)

            # Agent 2: Validate extracted data
            logger.info(f"Validating: {safe_name}")
            validated = validation_agent.validate(extracted, batch_id)

            # Persist
            invoice_id = store.save(validated, str(filepath), batch_id)

            results.append({
                "filename": safe_name,
                "invoice_id": invoice_id,
                "batch_id": batch_id,
                "status": "success",
                "data": validated
            })

        except Exception as e:
            logger.error(f"Processing failed for {safe_name}: {e}", exc_info=True)
            results.append({
                "filename": safe_name,
                "status": "error",
                "error": str(e)
            })

    summary = {
        "batch_id": batch_id,
        "total": len(results),
        "success": sum(1 for r in results if r["status"] == "success"),
        "errors": sum(1 for r in results if r["status"] == "error"),
        "results": results
    }
    return jsonify(summary), 200


@app.route("/api/invoices", methods=["GET"])
def list_invoices():
    """Return all stored invoices with optional filters."""
    status_filter = request.args.get("status")
    category_filter = request.args.get("category")
    invoices = store.list_all(status_filter=status_filter, category_filter=category_filter)
    return jsonify({"invoices": invoices, "total": len(invoices)})


@app.route("/api/invoices/<invoice_id>", methods=["GET"])
def get_invoice(invoice_id):
    invoice = store.get(invoice_id)
    if not invoice:
        return jsonify({"error": "Invoice not found"}), 404
    return jsonify(invoice)


@app.route("/api/invoices/<invoice_id>", methods=["PUT"])
def update_invoice(invoice_id):
    """ERP edit — update fields and re-validate."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    existing = store.get(invoice_id)
    if not existing:
        return jsonify({"error": "Invoice not found"}), 404

    # Merge edits — only overwrite fields that are actually provided and non-empty
    incoming_fields = data.get("extracted_data", {})
    for k, v in incoming_fields.items():
        # Keep existing value when user sends empty string (means they didn't touch it)
        if v is not None and str(v).strip() != "":
            existing["extracted_data"][k] = v

    existing["last_modified"] = datetime.utcnow().isoformat()
    existing["modified_by"] = data.get("modified_by", "finance_user")

    # Re-validate — is_edit=True relaxes date hard-fail to warning on edits
    re_validated = validation_agent.validate(
        existing,
        existing.get("batch_id", "manual"),
        is_edit=True
    )
    store.update(invoice_id, re_validated)
    return jsonify({"status": "updated", "invoice": re_validated})


@app.route("/api/invoices/<invoice_id>", methods=["DELETE"])
def delete_invoice(invoice_id):
    """Delete invoice record. Only allowed before ERP submission."""
    invoice = store.get(invoice_id)
    if not invoice:
        return jsonify({"error": "Invoice not found"}), 404

    if invoice.get("erp_status") == "SUBMITTED":
        return jsonify({
            "error": "Cannot delete an invoice that has been submitted to ERP. "
                     "Contact Finance Controller for reversals."
        }), 400

    record_id = invoice.get("record_id", invoice_id)
    deleted = store.delete(record_id)

    # Best-effort: remove uploaded file from disk too
    file_path = invoice.get("file_path")
    if file_path:
        try:
            from pathlib import Path as _P
            fp = _P(file_path)
            if fp.exists():
                fp.unlink()
        except Exception as fe:
            logger.warning(f"Could not remove upload file {file_path}: {fe}")

    if deleted:
        logger.info(f"Deleted record {record_id} (was {invoice.get('erp_status')})")
        return jsonify({"status": "deleted", "record_id": record_id}), 200

    return jsonify({"error": "Delete failed"}), 404


@app.route("/api/invoices/<invoice_id>/submit", methods=["POST"])
def submit_invoice(invoice_id):
    """Submit approved invoice to ERP."""
    invoice = store.get(invoice_id)
    if not invoice:
        return jsonify({"error": "Invoice not found"}), 404

    if invoice.get("validation", {}).get("overall_status") == "FAILED":
        return jsonify({"error": "Cannot submit invoice with validation failures"}), 400

    invoice["erp_status"] = "SUBMITTED"
    invoice["submitted_at"] = datetime.utcnow().isoformat()
    store.update(invoice_id, invoice)
    # Lock invoice_id into uniqueness index ONLY after successful ERP submission
    store.mark_submitted(invoice_id)
    return jsonify({"status": "submitted", "erp_ref": f"ERP-{invoice_id[:8].upper()}"})


@app.route("/api/insights", methods=["GET"])
def get_insights():
    """Finance officer dashboard insights."""
    try:
        insights = insights_agent.generate_insights()
        resp = jsonify(insights)
        # Prevent browser/proxy from caching — dashboard must always show latest data
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
        return resp
    except Exception as e:
        logger.error(f"Insights error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/export/<fmt>", methods=["GET"])
def export_data(fmt):
    """Export insights/data as PDF or Excel."""
    if fmt not in ("pdf", "excel"):
        return jsonify({"error": "Supported formats: pdf, excel"}), 400
    try:
        filepath = insights_agent.export(fmt, EXPORT_DIR)
        return send_from_directory(
            str(EXPORT_DIR),
            Path(filepath).name,
            as_attachment=True
        )
    except Exception as e:
        logger.error(f"Export error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/chat", methods=["POST"])
def chat():
    """Chat agent for back-office Q&A."""
    data = request.get_json()
    if not data or "message" not in data:
        return jsonify({"error": "message required"}), 400

    user_message = data["message"].strip()
    session_id = data.get("session_id", "default")
    history = data.get("history", [])

    if not user_message:
        return jsonify({"error": "Empty message"}), 400

    try:
        response = chat_agent.respond(user_message, session_id, history)
        # chat_agent handles its own exceptions and returns error in response dict
        # so we always return 200 — the frontend reads response.error if present
        return jsonify(response), 200
    except Exception as e:
        logger.error(f"Chat route unexpected error: {e}", exc_info=True)
        return jsonify({
            "response": f"Server error: {str(e)}. Check that ANTHROPIC_API_KEY is set.",
            "session_id": session_id,
            "error": str(e),
            "sources": []
        }), 200


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "invoice_count": store.count()
    })


if __name__ == "__main__":
    logger.info("🚀 Starting Invoice AI Processing System...")
    app.run(host="0.0.0.0", port=5000, debug=True)
