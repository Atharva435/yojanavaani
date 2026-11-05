# YojanaVaani

Voice-first assistant that tells people which government schemes they qualify for and guides them through applying, in their own language.

> **Status:** Work in progress. The backend core is working. The frontend, voice features and Azure deployment are still being built.

## Problem

India has hundreds of central and state welfare schemes, but many eligible people never claim them. They do not know the schemes exist, cannot tell which ones they qualify for, and are unsure which documents are needed. Many depend on middlemen who charge fees or give wrong information, and official portals are text-heavy and hard for low-literacy users.

## Target Users

- Low-income and rural families, elderly people, and women with limited literacy
- Students looking for scholarships
- Panchayat staff, health workers and Common Service Centre operators who help others apply

## Features

| Feature | Status |
|---|---|
| Rule-based eligibility engine (eligible / needs more info / not eligible) | Done |
| REST API with input validation and caching | Done |
| SQL database schema (6 tables and a view) | Done |
| Real, verified scheme data for one state | In progress |
| Azure SQL integration | In progress |
| Voice input and output (Hindi plus one regional language) | Planned |
| Document checklist and step-by-step application guide | Planned |
| Fraud warning (applications are free, do not pay agents) | Planned |
| Helper mode for bulk family checks | Planned |
| Login with roles (citizen / helper) | Planned |

## How It Works

1. The user gives a few details (age, income, state, category, occupation).
2. The rules engine checks each scheme's rules against the profile. **No AI model makes the eligibility decision**, so results are predictable and verifiable.
3. The API returns three groups: `eligible`, `need_more_info` (with the missing fields) and `not_eligible`.
4. Each result includes the benefit, the required documents and a "last verified" date.

## Architecture

```
Frontend (React + voice)  -->  FastAPI backend  -->  Azure SQL Database
                                   |
                         Rules engine (Python)
                                   |
                  Azure AI Speech (speech-to-text / text-to-speech)
```

## Tech Stack

- **Frontend:** React, Web Speech API (planned)
- **Backend:** Python, FastAPI, Pydantic
- **Database:** Azure SQL Database (T-SQL schema in `database/schema.sql`)
- **Cloud:** Azure App Service, Blob Storage, Key Vault, AI Speech (planned)

## Project Structure

```
yojanavaani/
├── backend/
│   ├── main.py             # FastAPI app and endpoints
│   ├── eligibility.py      # Rules engine
│   ├── sample_schemes.py   # DEMO data (to be replaced)
│   └── requirements.txt
├── database/
│   └── schema.sql          # Tables and view
└── README.md
```

## Getting Started

Requires Python 3.10 or newer.

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload
```

Open http://127.0.0.1:8000/docs for the interactive API docs.

## API

| Method | Endpoint | Description |
|---|---|---|
| GET | `/health` | Health check |
| GET | `/schemes` | List available schemes |
| POST | `/eligibility/check` | Check eligibility for a profile |

**Example request**

```bash
curl -X POST http://127.0.0.1:8000/eligibility/check \
  -H "Content-Type: application/json" \
  -d '{"age": 62, "income": 80000, "gender": "female", "marital_status": "widow", "state": "Haryana"}'
```

**Example result:** the demo Old Age Pension and Widow Pension appear under `eligible`, and the demo Scholarship appears under `need_more_info` because occupation and category were not provided.

## Data Disclaimer

The schemes currently in the repository are **demo placeholders**, not real government rules. Real schemes will be added only from official sources (such as myscheme.gov.in and state portals), each with a source link and a "last verified" date. Do not rely on this project for actual eligibility decisions until that is done.

## Security Approach

- All inputs are validated before processing
- Secrets will be stored in Azure Key Vault, never in code
- Role-based access for citizens and helpers
- Sensitive details such as income and category will be protected and not shared with third parties

## Roadmap

1. Replace demo data with verified schemes for one state (15-20 schemes)
2. Connect the backend to Azure SQL
3. Build the React frontend with Hindi voice input and output
4. Add the document checklist, application guide and fraud warning
5. Add login, helper mode and a usage report (Power BI)

## Credits

- Idea brainstorming, project planning and initial code scaffold: Claude (Anthropic)
- Libraries: FastAPI, Pydantic, Uvicorn
- Scheme data: demo placeholders for now. Official sources will be listed here once added.
