# 🚀 AutoOps AI

**Autonomous DevOps & Incident Response Platform**

An AI-powered platform that monitors containerized applications, detects infrastructure incidents, investigates root causes, recommends remediation, and safely executes approved recovery actions — all powered by local LLMs via Ollama.

---

## 🏗️ Architecture

```
                    AutoOps AI
                         │
         ┌───────────────┼───────────────┐
         │               │               │
     Monitoring       AI Engine       Action Engine
         │               │               │
   Prometheus          LangGraph      Kubernetes API
   Loki                Ollama (LLM)
   K8s Events          ChromaDB (RAG)
```

**Two systems working together:**

- **System A** — Demo production environment (microservices on Kubernetes that we monitor)
- **System B** — AutoOps platform (the AI that watches, investigates, and fixes System A)

---

## 🤖 How It Works

```
Alert Triggered
      ↓
Evidence Agent      ← gathers metrics, logs, K8s events, recent deployments
      ↓
Investigation Agent ← analyzes evidence, builds hypotheses with confidence scores
      ↓
RCA Agent           ← produces structured root cause analysis
      ↓
Policy Agent        ← checks if proposed action is safe to execute
      ↓
Human Approval      ← dashboard prompt for medium/high risk actions
      ↓
Remediation Agent   ← executes Kubernetes action (restart/rollback/scale)
      ↓
Recovery Verification ← confirms the fix worked, closes incident
```

> **Key safety principle**: The LLM never runs raw shell commands. Every action goes through a typed action object → policy engine → human approval → Kubernetes API.

---

## 📊 Demo Scenarios

| Scenario | What Breaks | AutoOps Response |
|---|---|---|
| CrashLoopBackOff | Pod runs out of memory (OOMKill) | Detect → investigate → restart |
| Bad Deployment | New version spikes error rate | Detect → correlate with deploy → rollback |
| Resource Exhaustion | CPU hits 90%+ | Detect → traffic spike analysis → scale out |

---

## 🛠️ Tech Stack

| Layer | Technology |
|---|---|
| Demo services | Python 3.12 + FastAPI |
| AutoOps backend | Python 3.12 + FastAPI |
| AutoOps dashboard | React 18 + TypeScript + Vite |
| AI Agents | LangGraph + Ollama (local LLM) |
| RAG Memory | ChromaDB |
| Metrics | Prometheus + Grafana |
| Logs | Loki + Promtail |
| Alerts | AlertManager |
| Containers | Docker (multi-stage builds) |
| Kubernetes | k3d (local cluster) |
| K8s packaging | Helm |
| Infrastructure as Code | Terraform |
| CI/CD | GitHub Actions |
| Container Registry | GitHub Container Registry (GHCR) |
| Database | PostgreSQL + Redis |

---

## 📈 Metrics Tracked

| Metric | Description |
|---|---|
| **MTTD** | Mean Time To Detection |
| **MTTR** | Mean Time To Recovery |
| **AI Accuracy** | Was the root cause diagnosis correct? |
| **Remediation Success Rate** | % of AI fixes that worked |
| **Human Intervention Rate** | Incidents requiring manual override |

---

## 🔒 Security Design

The LLM never executes arbitrary shell commands. All actions are structured and validated:

```
AI proposes action
       ↓
Structured Action Object
       ↓
Policy Engine (allowlist + risk scoring)
       ↓
Human Approval (for MEDIUM/HIGH risk)
       ↓
Kubernetes API call
```

---

## 🗺️ Development Phases

- [ ] **Phase 1** — DevOps Foundation: Docker + Microservices + Kubernetes + Helm + CI/CD
- [ ] **Phase 2** — Observability: Prometheus + Grafana + Loki + AlertManager
- [ ] **Phase 3** — AutoOps Backend: FastAPI + PostgreSQL + Incident system
- [ ] **Phase 4** — AI Investigation: LangGraph + Evidence collection + RCA
- [ ] **Phase 5** — Remediation: Policy engine + Human approval + K8s actions
- [ ] **Phase 6** — Recovery Verification: MTTR tracking + incident closure
- [ ] **Phase 7** — Advanced: RAG memory + ChatOps + Chaos Engineering

---

## 📄 License

MIT
