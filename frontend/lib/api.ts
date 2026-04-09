// lib/api.ts – Typed API client for the Vinmec AI Triage FastAPI backend

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

// ---------------------------------------------------------------------------
// Shared types (mirror backend schema.py)
// ---------------------------------------------------------------------------

export type TriageFlow = "AUTO_RESOLVED" | "PENDING_HUMAN" | "EMERGENCY" | "FOLLOW_UP";
export type QueueStatus = "PENDING" | "RESOLVED" | "TIMEOUT";
export type ResolutionType =
  | "AI_AUTO"
  | "NURSE_APPROVED"
  | "NURSE_CORRECTED"
  | "DOCTOR_CORRECTED";

export interface DoctorInfo {
  id: string;
  name: string;
  specialty: string;
  department_code: string;
}

export interface ClinicInfo {
  name: string;
  address: string;
}

export interface TriageResult {
  department_code: string | null;
  department_name: string | null;
  confidence_score: number | null;
  message: string;
  follow_up_question: string | null;
  queue_id: string | null;
  clinical_summary: string | null;
  doctors?: DoctorInfo[] | null;
  clinics?: ClinicInfo[] | null;
}

export interface EmergencyResult {
  matched_keyword: string;
  similarity_score: number;
  message: string;
  instructions: string[];
}

export interface ChatResponse {
  status: string;
  flow: TriageFlow;
  result?: TriageResult;
  emergency?: EmergencyResult;
}

export interface ConversationTurn {
  role: "user" | "assistant";
  content: string;
}

export interface ChatRequest {
  patient_id: string;
  message: string;
  session_id?: string;
  conversation_history?: ConversationTurn[];
  follow_up_rounds?: number;
}

export interface AppointmentRequest {
  patient_id: string;
  doctor_id: string;
  department_code: string;
  appointment_time: string;
}

export interface AppointmentResponse {
  success: boolean;
  appointment_id: string;
  message: string;
}

export interface QueueItem {
  id: string;
  patient_id: string;
  clinical_summary: string;
  suggested_dept: string | null;
  status: QueueStatus;
  created_at: string;
  minutes_waiting: number | null;
  sla_breached: boolean | null;
}

export interface PendingQueueResponse {
  total: number;
  items: QueueItem[];
}

export interface ResolveRequest {
  queue_id: string;
  approved_dept: string;
  nurse_id: string;
  resolution_type?: ResolutionType;
  notes?: string;
}

export interface ResolveResponse {
  success: boolean;
  queue_id: string;
  final_dept: string;
  resolution_type: ResolutionType;
  message: string;
}

export interface TimeoutCheckResponse {
  success: boolean;
  timed_out_count: number;
  message: string;
}

// ---------------------------------------------------------------------------
// Internal fetch helper
// ---------------------------------------------------------------------------

async function apiFetch<T>(
  path: string,
  options?: RequestInit
): Promise<T> {
  const url = `${API_BASE}${path}`;

  const res = await fetch(url, {
    headers: {
      "Content-Type": "application/json",
      ...(options?.headers ?? {}),
    },
    ...options,
  });

  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      detail = body?.message ?? body?.detail ?? detail;
    } catch {
      // ignore parse error
    }
    throw new Error(detail);
  }

  return res.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Triage
// ---------------------------------------------------------------------------

/**
 * Send a patient message through the triage pipeline.
 *
 * Returns AUTO_RESOLVED, PENDING_HUMAN, or EMERGENCY flow result.
 */
export async function sendTriageMessage(req: ChatRequest): Promise<ChatResponse> {
  return apiFetch<ChatResponse>("/api/v1/chat/triage", {
    method: "POST",
    body: JSON.stringify(req),
  });
}

// ---------------------------------------------------------------------------
// Nurse queue
// ---------------------------------------------------------------------------

/**
 * Fetch all PENDING queue items for the nurse dashboard.
 */
export async function getPendingQueue(): Promise<PendingQueueResponse> {
  return apiFetch<PendingQueueResponse>("/api/v1/queue/pending");
}

/**
 * Nurse resolves (approves or corrects) a pending queue item.
 */
export async function resolveQueueItem(req: ResolveRequest): Promise<ResolveResponse> {
  return apiFetch<ResolveResponse>("/api/v1/queue/resolve", {
    method: "POST",
    body: JSON.stringify(req),
  });
}

/**
 * Trigger the SLA timeout sweep – marks stale PENDING items as TIMEOUT.
 * Useful to call periodically from the nurse dashboard.
 */
export async function checkTimeouts(): Promise<TimeoutCheckResponse> {
  return apiFetch<TimeoutCheckResponse>("/api/v1/queue/check-timeouts", {
    method: "POST",
  });
}

// ---------------------------------------------------------------------------
// Departments (static list – mirrors config.py / DB seed)
// ---------------------------------------------------------------------------

export interface Department {
  code: string;
  name: string;
}

export const DEPARTMENTS: Department[] = [
  { code: "TIM_MACH", name: "Nội Tim Mạch" },
  { code: "NGOAI_TH", name: "Ngoại Tiêu hoá" },
  { code: "THAN_KINH", name: "Nội Thần Kinh" },
  { code: "SAN_PHU", name: "Sản Phụ Khoa" },
  { code: "NHI", name: "Nhi Khoa" },
  { code: "DA_LIEU", name: "Da liễu" },
  { code: "MAT", name: "Nhãn Khoa" },
  { code: "TAI_MUI_HONG", name: "Tai Mũi Họng" },
  { code: "CO_XUONG_KHOP", name: "Cơ Xương Khớp" },
  { code: "NGOAI_CHINH_HINH", name: "Ngoại Chỉnh hình" },
];

export function getDeptName(code: string | null | undefined): string {
  if (!code) return "Chưa xác định";
  return DEPARTMENTS.find((d) => d.code === code)?.name ?? code;
}

// ---------------------------------------------------------------------------
// Appointments
// ---------------------------------------------------------------------------

/**
 * Book an appointment with a chosen doctor after AUTO_RESOLVED triage.
 */
export async function createAppointment(
  req: AppointmentRequest
): Promise<AppointmentResponse> {
  return apiFetch<AppointmentResponse>("/api/v1/appointments", {
    method: "POST",
    body: JSON.stringify(req),
  });
}
