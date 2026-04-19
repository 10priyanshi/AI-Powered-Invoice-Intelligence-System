# InvoiceAI — AI-Powered Invoice Processing System
AI-powered invoice processing system that extracts, validates, and manages invoices using multi-agent architecture. Features smart OCR, 8-layer validation, ERP integration, analytics dashboard, and FinBot chat for finance workflows.

##  Overview
InvoiceAI is a full-stack, AI-powered back-office automation system designed to streamline invoice processing for finance teams. It eliminates manual data entry, reduces errors, and provides real-time insights using a multi-agent architecture.

From invoice upload → AI extraction → validation → ERP submission → analytics, everything is automated and traceable.

## Key Features
### AI-Powered Processing
- Extracts structured data from PDFs & images
- Uses vision-based AI for contextual understanding
- Auto-classifies invoices into categories
### Multi-Agent Architecture
- **Extraction Agent** → OCR + structured JSON
- **Validation Agent** → 8 business rule checks
- **Insights Agent** → analytics + reports
- **Chat Agent (FinBot)** → finance Q&A assistant
### Smart Validation Engine (8 Layers)
- Required fields check
- Date validation
- Amount threshold (audit trigger)
- Vendor-client mismatch detection
- Duplicate invoice prevention
- GST validation (India-specific)
- Arithmetic consistency
- Numeric sanity checks
### Finance Dashboard
- Spend analytics & trends
- Category-wise breakdown
- Top vendors insights
- Audit alerts (₹ > 1L invoices)
- Export to PDF & Excel
### FinBot Assistant
- Ask questions about invoices & finance data
- Context-aware responses
- Strict scope guard (finance-only queries)
### ERP Workflow Simulation
- Upload → Review → Edit → Submit
- Inline error handling
- Duplicate prevention
Submission locking system

##  End-to-End Workflow

```mermaid
flowchart LR
A[Upload Invoices] --> B[AI Extraction]
B --> C[Validation Engine]
C --> D[ERP Review Console]
D --> E[Submit to ERP]
E --> F[Dashboard & Insights]
F --> G[FinBot Chat]
```

---

## System Architecture

```
invoice-ai/
│
├── backend/
│   ├── app.py
│   ├── agents/
│   │   ├── extraction_agent.py
│   │   ├── validation_agent.py
│   │   ├── insights_agent.py
│   │   └── chat_agent.py
│   └── storage/
│
├── frontend/
│   ├── index.html
│   ├── erp.html
│   ├── dashboard.html
│   └── chat.html
│
├── uploads/
├── processed/
├── exports/
└── requirements.txt
```

---

## Tech Stack

| Layer      | Technology                     |
|-----------|------------------------------|
| Backend   | Python, Flask                |
| AI Model  | Claude Vision (Anthropic)    |
| Frontend  | HTML, CSS, JavaScript        |
| Charts    | Chart.js                     |
| Export    | OpenPyXL, ReportLab          |
| Storage   | JSON-based                   |

---

## Getting Started

### Prerequisites
- Python 3.11+
- API Key

---

### Installation

```bash
# Clone the repo
git clone https://github.com/your-username/invoice-ai.git
cd invoice-ai

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Mac/Linux
venv\Scripts\activate     # Windows

# Install dependencies
pip install -r requirements.txt

# Set API key
cp .env.example .env
# Add your API_KEY

# Run the app
python backend/app.py
```

---

## Run Locally

Open in browser:

```
http://localhost:5000
```

---

## API Endpoints

| Method | Endpoint | Description |
|--------|---------|------------|
| POST   | /api/upload | Upload invoices |
| GET    | /api/invoices | Get all invoices |
| PUT    | /api/invoices/:id | Edit & re-validate |
| POST   | /api/invoices/:id/submit | Submit to ERP |
| GET    | /api/insights | Dashboard data |
| GET    | /api/export/pdf | Download PDF report |
| GET    | /api/export/excel | Download Excel report |
| POST   | /api/chat | FinBot query |

---

## Problem It Solves

| Challenge              | Impact                          |
|----------------------|---------------------------------|
| Manual invoice entry | Time-consuming (15–20 mins/invoice) |
| OCR errors           | Incorrect data mapping          |
| Duplicate invoices   | Financial losses                |
| No audit visibility  | Compliance risk                 |

---

## Why This Project Stands Out

- Real-world finance automation use case  
- Combines AI + backend + frontend + analytics  
- Production-style multi-agent architecture  
- Strong validation + compliance logic  
- End-to-end system (not just a model demo)  

---

## Future Improvements

- Database integration (PostgreSQL / MongoDB)  
- Role-based authentication  
- Real ERP integrations (SAP, Oracle)  
- Multi-currency support  
- Advanced anomaly detection  

---

## Author

Built with focus on **AI + real-world impact + production design**

---

