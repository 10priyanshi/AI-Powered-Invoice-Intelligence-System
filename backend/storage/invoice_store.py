"""
Invoice Store — Fixed
File-system JSON persistence layer.

Key fix: Invoice ID uniqueness index is only updated on ERP SUBMIT,
not on upload/save. This prevents re-validation of the same invoice
from falsely flagging its own ID as a duplicate.
"""

import json
import uuid
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class InvoiceStore:
    """Simple JSON file-based invoice storage."""

    def __init__(self, storage_dir: Path):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        # Only contains IDs of SUBMITTED invoices
        self._submitted_ids: set = set()
        self._load_submitted_index()

    def _load_submitted_index(self):
        """
        Load only SUBMITTED invoice IDs into the uniqueness index.
        Pending/failed/review invoices are NOT indexed — they can be
        re-validated freely without triggering false duplicate errors.
        """
        for f in self.storage_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if data.get("erp_status") == "SUBMITTED":
                    inv_id = data.get("extracted_data", {}).get("invoice_id")
                    if inv_id:
                        self._submitted_ids.add(str(inv_id).strip().upper())
            except Exception:
                pass
        logger.info(f"Loaded {len(self._submitted_ids)} submitted IDs into uniqueness index")

    def save(self, invoice: dict, filepath: str, batch_id: str) -> str:
        """
        Persist invoice. Does NOT touch the uniqueness index.
        ID is only locked after ERP submission via mark_submitted().
        """
        record_id = f"REC-{uuid.uuid4().hex[:8].upper()}"
        invoice["record_id"] = record_id
        invoice["file_path"] = filepath
        invoice["stored_at"] = datetime.utcnow().isoformat()

        out_path = self.storage_dir / f"{record_id}.json"
        out_path.write_text(
            json.dumps(invoice, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        inv_id = invoice.get("extracted_data", {}).get("invoice_id")
        logger.info(f"Saved invoice {record_id} (invoice_id={inv_id})")
        return record_id

    def mark_submitted(self, record_id: str):
        """
        Lock the invoice_id into the uniqueness index.
        Called ONLY when user successfully submits to ERP.
        """
        invoice = self.get(record_id)
        if invoice:
            inv_id = invoice.get("extracted_data", {}).get("invoice_id")
            if inv_id:
                self._submitted_ids.add(str(inv_id).strip().upper())
                logger.info(f"Invoice ID '{inv_id}' locked after ERP submission")

    def get(self, record_id: str) -> Optional[dict]:
        path = self.storage_dir / f"{record_id}.json"
        if not path.exists():
            return self._search_by_invoice_id(record_id)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error(f"Error reading {path}: {e}")
            return None

    def _search_by_invoice_id(self, invoice_id: str) -> Optional[dict]:
        for f in self.storage_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if data.get("extracted_data", {}).get("invoice_id") == invoice_id:
                    return data
            except Exception:
                pass
        return None

    def update(self, record_id: str, invoice: dict) -> bool:
        path = self.storage_dir / f"{record_id}.json"
        if not path.exists():
            for f in self.storage_dir.glob("*.json"):
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    if data.get("record_id") == record_id:
                        path = f
                        break
                except Exception:
                    pass

        if not path.exists():
            logger.warning(f"Cannot update — record {record_id} not found")
            return False

        invoice["updated_at"] = datetime.utcnow().isoformat()
        path.write_text(
            json.dumps(invoice, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return True

    def list_all(self, status_filter: str = None, category_filter: str = None) -> list:
        invoices = []
        for f in sorted(
            self.storage_dir.glob("*.json"),
            key=lambda x: x.stat().st_mtime,
            reverse=True
        ):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if status_filter and data.get("erp_status") != status_filter:
                    continue
                if category_filter and data.get("category") != category_filter:
                    continue
                invoices.append(data)
            except Exception as e:
                logger.error(f"Error reading {f}: {e}")
        return invoices

    def invoice_id_submitted(self, invoice_id: str) -> bool:
        """
        True only if this invoice_id is already SUBMITTED to ERP.
        Re-validation of the same un-submitted invoice will never trigger this.
        """
        if not invoice_id:
            return False
        return str(invoice_id).strip().upper() in self._submitted_ids

    def invoice_id_exists(self, invoice_id: str) -> bool:
        """Alias kept for backward compatibility."""
        return self.invoice_id_submitted(invoice_id)

    def count(self) -> int:
        return len(list(self.storage_dir.glob("*.json")))

    def delete(self, record_id: str) -> bool:
        path = self.storage_dir / f"{record_id}.json"
        if path.exists():
            path.unlink()
            return True
        return False
