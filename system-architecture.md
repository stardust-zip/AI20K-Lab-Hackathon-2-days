# System Architecture & Tech Stack Specification: Vinmec AI Triage

## 1. High-Level Tech Stack (Hackathon "Half-a-Day" Velocity)

This stack is optimized for rapid prototyping while strictly adhering to the architectural constraints (De-identification, Red Flags, Human-in-the-loop, Vector Semantic Memory).

| Component | Technology Choice | Justification (Why?) |
| :--- | :--- | :--- |
| **Frontend (Patient + Nurse App)** | Next.js (React) + Tailwind CSS + Shadcn UI | Fastest time-to-market. Shadcn provides instant, accessible chat and dashboard components. |
| **Backend & Orchestration** | Python + FastAPI | Native ecosystem for AI/LLMs. High performance, async handling for concurrent chat requests. |
| **LLM Provider** | Azure OpenAI (GPT-4o-mini) / OpenAI API | Azure is preferred for healthcare (HIPAA compliance vibe), but standard OpenAI API is acceptable for the hackathon prototype. |
| **AI Orchestration** | LangChain / LangGraph | Simplifies the multi-step reasoning flow (Extract -> Check Red Flags -> Map -> Confidence Score). |
| **Database & Vector Store** | Supabase (PostgreSQL + `pgvector`) | All-in-one hackathon god-tier tool. Handles relational data (Logs, Users) and Vector Search (Semantic Memory) in one place. |
| **De-identification Layer** | Microsoft Presidio (Python) | Open-source, local execution for PII/PHI masking *before* data hits the LLM cloud. |
| **Real-time Queue (Triage)** | Supabase Realtime (WebSockets) | Instantly pushes `< 85% confidence` cases to the Nurse dashboard without polling. |
| **Hosting** | Vercel (FE) + Render/Fly.io (BE) | Zero-config deployments. |

---

## 2. Core Modules & Implementation Details

### Module 1: Privacy & Entity Extraction Layer
* **Input:** Raw user text (e.g., "Tôi Nguyễn Văn A, sđt 090xxx, bị đau bụng dưới ngực").
* **Process:**
    1.  Pass through **Microsoft Presidio** (AnalyzerEngine & AnonymizerEngine) to strip names, phone numbers, and IDs.
    2.  Pass sanitized text to LLM (Prompt: "Extract Core Symptoms, Age, Gender. Ignore emotional noise.").
* **Output:** JSON `{ "symptoms": "đau bụng dưới ngực", "age": null, "gender": null }`

### Module 2: Semantic Red Flags (Safety First)
* **Input:** Extracted `symptoms`.
* **Process:**
    1.  Convert `symptoms` to embeddings using `text-embedding-3-small`.
    2.  Cosine similarity search via `pgvector` against a predefined "Red Flag" table (e.g., đột quỵ, nhồi máu cơ tim, xuất huyết).
* **Logic:** If `similarity > 0.85` on a Red Flag -> **HALT LLM ROUTING**. Immediately return `Trigger_115_Emergency_Flow`.

### Module 3: LLM Routing & Confidence Scoring
* **Input:** Extracted `symptoms` + System Prompt with Vinmec Department Catalog.
* **Prompt Instruction:** "Map the symptoms to the most appropriate medical department. Provide a confidence score (0-100). Output strictly in JSON."
* **Output Format:**
    ```json
    {
      "department_id": "ngoai_tieu_hoa",
      "department_name": "Ngoại Tiêu hoá",
      "confidence_score": 92,
      "follow_up_question": "Bạn có bị buồn nôn hay sốt không?" // only if confidence < 85
    }
    ```

### Module 4: Thin Human-Triage (Fallback & Queue)
* **Logic:** If `confidence_score >= 85`, return result to user with Disclaimer. If `60 <= confidence_score < 85`, trigger Human Triage.
* **Process:**
    1.  Insert record into `Triage_Queue` table in Supabase.
    2.  Nurse frontend listens via Supabase Realtime.
    3.  Nurse clicks "Duyệt" (Approve) or "Đổi Khoa" (Change Dept).
    4.  Update record; trigger webhook to resolve Patient's pending chat state.

---

## 3. Database Schema (Supabase)

### Table: `departments`
| Column | Type | Description |
| :--- | :--- | :--- |
| `id` | UUID | PK |
| `code` | String | e.g., "TIM_MACH", "NGOAI_TH" |
| `name` | String | e.g., "Nội Tim Mạch" |

### Table: `triage_logs` (The Flywheel / Semantic Memory)
| Column | Type | Description |
| :--- | :--- | :--- |
| `id` | UUID | PK |
| `raw_symptoms` | Text | Original text (anonymized) |
| `symptom_embedding` | Vector(1536) | OpenAI embedding for semantic search |
| `ai_suggested_dept` | String | AI's initial guess |
| `confidence` | Float | AI confidence score |
| `final_dept` | String | The actual correct department |
| `resolution_type` | Enum | `AI_AUTO`, `NURSE_APPROVED`, `NURSE_CORRECTED`, `DOCTOR_CORRECTED` |
| `created_at` | Timestamp | |

### Table: `human_triage_queue`
| Column | Type | Description |
| :--- | :--- | :--- |
| `id` | UUID | PK |
| `patient_id` | String | Foreign key to patient auth |
| `clinical_summary`| Text | LLM generated summary for the nurse to read fast |
| `suggested_dept` | String | |
| `status` | Enum | `PENDING`, `RESOLVED`, `TIMEOUT` |
| `created_at` | Timestamp | SLA timer starts here (3 mins limit) |

---

## 4. API Endpoints Specification (FastAPI)

* `POST /api/v1/chat/triage`
    * **Payload:** `{ "patient_id": "123", "message": "Đau quặn bụng dưới bên phải" }`
    * **Response:**
        ```json
        {
          "status": "success",
          "flow": "AUTO_RESOLVED", // or "PENDING_HUMAN", "EMERGENCY"
          "result": {
             "department": "Ngoại Tiêu hoá",
             "message": "95% phù hợp Khám Ngoại Tiêu hoá. *Disclaimer: Đây là trợ lý gợi ý...*"
          }
        }
        ```
* `GET /api/v1/queue/pending` (Nurse dashboard polling/initial load)
* `POST /api/v1/queue/resolve`
    * **Payload:** `{ "queue_id": "uuid", "approved_dept": "TIM_MACH", "nurse_id": "456" }`
    * **Action:** Resolves queue, triggers async function to update `triage_logs` and regenerate `symptom_embedding` to reinforce the Semantic Memory.

---

## 5. System Prompts Definition

**System Prompt: Persona & Triage Rule**
```text
Bạn là Trợ lý Điều dưỡng Sơ yếu tại bệnh viện Vinmec. Nhiệm vụ của bạn là phân tích triệu chứng của bệnh nhân và điều phối vào đúng 1 trong các chuyên khoa sau: [DANH_SACH_KHOA].

QUY TẮC TUYỆT ĐỐI:
1. KHÔNG BAO GIỜ tự chẩn đoán bệnh.
2. KHÔNG BAO GIỜ kê đơn thuốc. Nếu bệnh nhân yêu cầu đơn thuốc, từ chối lịch sự và nhắc nhở nhiệm vụ của bạn là đặt lịch khám.
3. Trả về kết quả dưới định dạng JSON được yêu cầu, bao gồm ID khoa, Tên khoa và Điểm tự tin (0-100).
4. Nếu triệu chứng không rõ ràng, điểm tự tin phải đặt dưới 85 và tạo ra một `follow_up_question` ngắn gọn.
```
