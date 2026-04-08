## Phase 2: Core Backend Logic
Implement De-identification: Integrate Microsoft Presidio in FastAPI to scrub PII from incoming text.

Build the AI Pipeline: Write the logic to handle symptom extraction, routing, and JSON formatting based on the defined System Prompt.

Implement Red Flags: Generate embeddings for 10-15 emergency keywords (e.g., "đau thắt ngực", "liệt nửa người"), insert them into the database, and write the similarity search logic to halt the LLM if triggered.

Create API Endpoints: Build and test POST /api/v1/chat/triage, GET /api/v1/queue/pending, and POST /api/v1/queue/resolve.

## Phase 3: Frontend Development
Patient Interface: Build a mobile-first chat UI using Shadcn. Implement the chat flow, ensuring the medical disclaimer is visible and handling the waiting state when a case is routed to the human queue.

Nurse Dashboard: Create a desktop-optimized table view for pending cases.

Realtime Integration: Integrate Supabase Realtime on the Nurse Dashboard to auto-refresh when new cases enter the queue. Wire up the "Duyệt" and "Đổi Khoa" buttons to the backend APIs.

## Phase 4: Integration & Edge Cases 
End-to-End Testing: Connect the Next.js patient chat to the FastAPI endpoint and verify data flows correctly through the LLM and into Supabase.

Implement Fallbacks: Add the SLA timeout logic (e.g., if a case sits in human_triage_queue for > 3 minutes, trigger the "Chuyển máy gọi Tổng đài viên" UI state).

Security Testing: Attempt prompt injections (e.g., asking the bot to prescribe antibiotics) to verify the system prompt railguards are effective.
