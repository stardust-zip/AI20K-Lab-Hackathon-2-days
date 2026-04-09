"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import {
  sendTriageMessage,
  createAppointment,
  type ChatResponse,
  type ConversationTurn,
  type TriageFlow,
  type DoctorInfo,
  type ClinicInfo,
} from "@/lib/api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: Date;
  flow?: TriageFlow;
  departmentName?: string;
  departmentCode?: string;
  confidence?: number;
  queueId?: string;
  isEmergency?: boolean;
  emergencyKeyword?: string;
  instructions?: string[];
  doctors?: DoctorInfo[];
  clinics?: ClinicInfo[];
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DISCLAIMER =
  "⚕️ Đây là trợ lý gợi ý chuyên khoa, không thay thế chẩn đoán y khoa. Trong trường hợp khẩn cấp, hãy gọi 115 ngay.";

const SLA_TIMEOUT_MS = 3 * 60 * 1000; // 3 minutes

const WELCOME_MESSAGE: Message = {
  id: "welcome",
  role: "assistant",
  content:
    "Xin chào! Tôi là Trợ lý Điều dưỡng Sơ yếu của Vinmec. Bạn đang gặp triệu chứng gì? Hãy mô tả chi tiết để tôi có thể hỗ trợ bạn chọn đúng chuyên khoa.",
  timestamp: new Date(),
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function generateId(): string {
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

function getOrCreatePatientId(): string {
  if (typeof window === "undefined") return generateId();
  const stored = localStorage.getItem("vinmec_patient_id");
  if (stored) return stored;
  const id = "PAT-" + generateId().toUpperCase();
  localStorage.setItem("vinmec_patient_id", id);
  return id;
}

function formatTime(date: Date): string {
  return date.toLocaleTimeString("vi-VN", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function DisclaimerBanner() {
  return (
    <div className="bg-blue-50 border-b border-blue-200 px-4 py-2">
      <p className="text-xs text-blue-700 text-center leading-relaxed">
        {DISCLAIMER}
      </p>
    </div>
  );
}

function EmergencyAlert({
  keyword,
  instructions,
}: {
  keyword: string;
  instructions: string[];
}) {
  return (
    <div className="mx-3 my-2 rounded-xl bg-red-50 border-2 border-red-400 p-4 shadow-sm">
      <div className="flex items-center gap-2 mb-3">
        <span className="text-2xl">🚨</span>
        <div>
          <p className="font-bold text-red-700 text-sm">
            Phát hiện triệu chứng nguy hiểm!
          </p>
          <p className="text-red-600 text-xs">
            Từ khóa nhận diện: <span className="font-semibold">{keyword}</span>
          </p>
        </div>
      </div>

      <a
        href="tel:115"
        className="flex items-center justify-center gap-2 w-full bg-red-600 hover:bg-red-700 active:bg-red-800 text-white font-bold py-3 rounded-lg text-base transition-colors mb-3 shadow"
      >
        📞 GỌI CẤP CỨU 115 NGAY
      </a>

      <ul className="space-y-1">
        {instructions.map((inst, i) => (
          <li key={i} className="flex items-start gap-2 text-xs text-red-700">
            <span className="mt-0.5 shrink-0 font-bold">{i + 1}.</span>
            <span>{inst}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function PendingHumanBubble({
  queueId,
  onResolved,
}: {
  queueId: string;
  onResolved: (dept: string) => void;
}) {
  const [elapsed, setElapsed] = useState(0);
  const [timedOut, setTimedOut] = useState(false);
  const startRef = useRef(Date.now());
  const resolvedRef = useRef(false);

  // Poll for resolution every 3 seconds
  useEffect(() => {
    const poll = async () => {
      if (resolvedRef.current) return;
      try {
        const res = await fetch(
          `${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/api/v1/queue/pending`,
        );
        if (!res.ok) return;
        const data = await res.json();
        const item = data.items?.find(
          (i: { id: string; status: string }) => i.id === queueId,
        );
        // If item is gone from pending list, it was resolved
        if (!item) {
          resolvedRef.current = true;
          onResolved("Điều dưỡng đã xác nhận");
        }
      } catch {
        // ignore
      }
    };

    const pollInterval = setInterval(poll, 3000);
    return () => clearInterval(pollInterval);
  }, [queueId, onResolved]);

  // Timer
  useEffect(() => {
    const tick = setInterval(() => {
      const secs = Math.floor((Date.now() - startRef.current) / 1000);
      setElapsed(secs);
      if (secs >= SLA_TIMEOUT_MS / 1000) {
        setTimedOut(true);
        clearInterval(tick);
      }
    }, 1000);
    return () => clearInterval(tick);
  }, []);

  const mins = Math.floor(elapsed / 60);
  const secs = elapsed % 60;
  const timeStr = mins > 0 ? `${mins}p ${secs}s` : `${secs}s`;

  if (timedOut) {
    return (
      <div className="mx-3 my-2 rounded-xl bg-amber-50 border border-amber-300 p-4 shadow-sm">
        <p className="font-semibold text-amber-800 text-sm mb-1">
          ⏱️ Hết thời gian chờ (3 phút)
        </p>
        <p className="text-amber-700 text-xs mb-3">
          Điều dưỡng hiện đang bận. Vui lòng liên hệ tổng đài để được hỗ trợ.
        </p>
        <a
          href="tel:18006858"
          className="flex items-center justify-center gap-2 w-full bg-amber-500 hover:bg-amber-600 text-white font-bold py-2.5 rounded-lg text-sm transition-colors"
        >
          📞 Gọi Tổng đài: 1800 6858
        </a>
      </div>
    );
  }

  return (
    <div className="mx-3 my-2 rounded-xl bg-blue-50 border border-blue-200 p-4 shadow-sm">
      <div className="flex items-center gap-3 mb-2">
        <div className="flex gap-1">
          {[0, 1, 2].map((i) => (
            <span
              key={i}
              className="w-2 h-2 rounded-full bg-blue-500 animate-bounce"
              style={{ animationDelay: `${i * 0.2}s` }}
            />
          ))}
        </div>
        <p className="text-sm font-semibold text-blue-800">
          Đang chờ điều dưỡng xác nhận...
        </p>
      </div>
      <p className="text-xs text-blue-600">
        Thời gian chờ: {timeStr} / tối đa 3 phút
      </p>
      <div className="mt-2 h-1.5 rounded-full bg-blue-100 overflow-hidden">
        <div
          className="h-full rounded-full bg-blue-400 transition-all duration-1000"
          style={{
            width: `${Math.min((elapsed / (SLA_TIMEOUT_MS / 1000)) * 100, 100)}%`,
          }}
        />
      </div>
    </div>
  );
}

function AutoResolvedBubble({
  departmentName,
  confidence,
}: {
  departmentName: string;
  confidence: number;
}) {
  return (
    <div className="mx-3 my-1 rounded-xl bg-green-50 border border-green-200 p-3 shadow-sm">
      <div className="flex items-center gap-2 mb-1">
        <span className="text-lg">✅</span>
        <p className="font-bold text-green-800 text-sm">{departmentName}</p>
      </div>
      <div className="flex items-center gap-1 mb-2">
        <div className="flex-1 h-2 rounded-full bg-green-100 overflow-hidden">
          <div
            className="h-full rounded-full bg-green-500"
            style={{ width: `${confidence}%` }}
          />
        </div>
        <span className="text-xs font-semibold text-green-700 w-10 text-right">
          {confidence}%
        </span>
      </div>
      <p className="text-xs text-green-700">
        Vui lòng đến quầy{" "}
        <span className="font-semibold">{departmentName}</span> để được khám.
      </p>
    </div>
  );
}

// Predefined appointment time slots (relative to today)
function getTimeSlots(): { label: string; iso: string }[] {
  const now = new Date();
  const slots: { label: string; iso: string }[] = [];
  for (let dayOffset = 1; dayOffset <= 2; dayOffset++) {
    const d = new Date(now);
    d.setDate(d.getDate() + dayOffset);
    const dayLabel =
      dayOffset === 1 ? "Ngày mai" : `${d.getDate()}/${d.getMonth() + 1}`;
    for (const [hour, label] of [
      [8, "08:00"],
      [14, "14:00"],
    ] as [number, string][]) {
      d.setHours(hour, 0, 0, 0);
      slots.push({ label: `${dayLabel} – ${label}`, iso: d.toISOString() });
    }
  }
  return slots;
}

function DoctorSelectionBubble({
  doctors,
  clinics,
  patientId,
  departmentCode,
}: {
  doctors: DoctorInfo[];
  clinics: ClinicInfo[];
  patientId: string;
  departmentCode: string;
}) {
  const [selectedClinic, setSelectedClinic] = useState<ClinicInfo | null>(null);

  const [selectedDoctor, setSelectedDoctor] = useState<DoctorInfo | null>(null);
  const [selectedSlot, setSelectedSlot] = useState<string>("");
  const [booking, setBooking] = useState(false);
  const [booked, setBooked] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timeSlots = getTimeSlots();

  const handleBook = async () => {
    if (!selectedDoctor || !selectedSlot) return;
    setBooking(true);
    setError(null);
    try {
      const res = await createAppointment({
        patient_id: patientId,
        doctor_id: selectedDoctor.id,
        department_code: departmentCode,
        appointment_time: selectedSlot,
      });
      setBooked(res.message);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Không thể đặt lịch.");
    } finally {
      setBooking(false);
    }
  };

  if (booked) {
    return (
      <div className="mx-3 my-2 rounded-xl bg-green-50 border border-green-300 p-4 shadow-sm">
        <div className="flex items-center gap-2 mb-1">
          <span className="text-xl">🎉</span>
          <p className="font-bold text-green-800 text-sm">
            Đặt lịch thành công!
          </p>
        </div>
        <p className="text-xs text-green-700">{booked}</p>
      </div>
    );
  }

  return (
    <div className="mx-3 my-2 rounded-xl bg-white border border-blue-200 p-4 shadow-sm space-y-3">
      {/* Step 1: Location picker */}
      {!selectedClinic ? (
        <>
          <p className="text-xs font-semibold text-gray-700">
            📍 Bạn muốn khám tại cơ sở nào?
          </p>
          <div className="space-y-2">
            {clinics.map((clinic) => (
              <button
                key={clinic.name}
                onClick={() => setSelectedClinic(clinic)}
                className="w-full text-left px-3 py-2.5 rounded-lg border border-gray-200 bg-gray-50 hover:border-blue-400 hover:bg-blue-50 text-xs transition-colors"
              >
                <p className="font-semibold text-gray-800">{clinic.name}</p>
                <p className="text-gray-500">{clinic.address}</p>
              </button>
            ))}
          </div>
        </>
      ) : (
        <>
          {/* Selected clinic badge + change button */}
          <div className="flex items-start justify-between bg-blue-50 rounded-lg px-3 py-2">
            <div className="text-xs text-blue-800">
              <p className="font-semibold">📍 {selectedClinic.name}</p>
              <p className="text-blue-600">{selectedClinic.address}</p>
            </div>
            <button
              onClick={() => {
                setSelectedClinic(null);
                setSelectedDoctor(null);
                setSelectedSlot("");
              }}
              className="text-[10px] text-blue-500 hover:text-blue-700 underline shrink-0 ml-2 mt-0.5"
            >
              Đổi
            </button>
          </div>

          {/* Step 2: Doctor list */}
          <p className="text-xs font-semibold text-gray-600">Chọn bác sĩ:</p>
          <div className="space-y-2">
            {doctors.map((doc) => (
              <button
                key={doc.id}
                onClick={() => {
                  setSelectedDoctor(doc);
                  setSelectedSlot("");
                }}
                className={`w-full text-left px-3 py-2 rounded-lg border text-xs transition-colors ${
                  selectedDoctor?.id === doc.id
                    ? "border-blue-500 bg-blue-50 text-blue-800"
                    : "border-gray-200 bg-gray-50 hover:border-blue-300 text-gray-700"
                }`}
              >
                <p className="font-semibold">{doc.name}</p>
                <p className="text-gray-500">{doc.specialty}</p>
              </button>
            ))}
          </div>

          {/* Step 3: Time slot picker */}
          {selectedDoctor && (
            <>
              <p className="text-xs font-semibold text-gray-600">
                Chọn giờ khám:
              </p>
              <div className="grid grid-cols-2 gap-2">
                {timeSlots.map((slot) => (
                  <button
                    key={slot.iso}
                    onClick={() => setSelectedSlot(slot.iso)}
                    className={`px-2 py-1.5 rounded-lg border text-xs transition-colors ${
                      selectedSlot === slot.iso
                        ? "border-blue-500 bg-blue-50 text-blue-800 font-semibold"
                        : "border-gray-200 bg-gray-50 hover:border-blue-300 text-gray-600"
                    }`}
                  >
                    {slot.label}
                  </button>
                ))}
              </div>

              <button
                onClick={handleBook}
                disabled={!selectedSlot || booking}
                className="w-full py-2 rounded-xl bg-blue-600 hover:bg-blue-700 disabled:bg-gray-300 text-white text-sm font-semibold transition-colors"
              >
                {booking ? "Đang đặt lịch..." : "Xác nhận đặt lịch"}
              </button>
            </>
          )}
        </>
      )}

      {error && <p className="text-xs text-red-600">⚠️ {error}</p>}
    </div>
  );
}

function ChatBubble({ message }: { message: Message }) {
  const isUser = message.role === "user";

  // Tạo state để kiểm tra xem component đã hiển thị trên trình duyệt chưa
  const [mounted, setMounted] = useState(false);

  // useEffect này chỉ chạy một lần duy nhất sau khi component mount thành công
  useEffect(() => {
    setMounted(true);
  }, []);

  if (message.isEmergency) {
    return (
      <div className="w-full">
        <EmergencyAlert
          keyword={message.emergencyKeyword ?? "triệu chứng nguy hiểm"}
          instructions={
            message.instructions ?? [
              "Gọi ngay số khẩn cấp 115.",
              "Đến phòng Cấp Cứu gần nhất.",
              "Không tự lái xe – nhờ người đưa hoặc gọi xe cấp cứu.",
              "Giữ bình tĩnh và theo dõi các dấu hiệu sinh tồn.",
            ]
          }
        />
      </div>
    );
  }

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"} mb-1`}>
      {!isUser && (
        <div className="w-7 h-7 rounded-full bg-blue-600 flex items-center justify-center text-white text-xs font-bold shrink-0 mt-1 mr-2">
          V
        </div>
      )}
      <div
        className={`max-w-[78%] ${isUser ? "items-end" : "items-start"} flex flex-col`}
      >
        <div
          className={`px-3.5 py-2.5 rounded-2xl text-sm leading-relaxed shadow-sm ${
            isUser
              ? "bg-blue-600 text-white rounded-br-sm"
              : "bg-white text-gray-800 border border-gray-100 rounded-bl-sm"
          }`}
        >
          {message.content}
        </div>

        {/* Chỉ hiển thị thời gian khi đã mounted để tránh lệch giây giữa Server và Client */}
        <span
          className="text-[10px] text-gray-400 mt-0.5 px-1"
          suppressHydrationWarning
        >
          {mounted ? formatTime(message.timestamp) : ""}
        </span>
      </div>
      {isUser && (
        <div className="w-7 h-7 rounded-full bg-gray-300 flex items-center justify-center text-gray-600 text-xs font-bold shrink-0 mt-1 ml-2">
          B
        </div>
      )}
    </div>
  );
}

function TypingIndicator() {
  return (
    <div className="flex items-start mb-1">
      <div className="w-7 h-7 rounded-full bg-blue-600 flex items-center justify-center text-white text-xs font-bold shrink-0 mt-1 mr-2">
        V
      </div>
      <div className="bg-white border border-gray-100 rounded-2xl rounded-bl-sm px-4 py-3 shadow-sm">
        <div className="flex gap-1 items-center">
          {[0, 1, 2].map((i) => (
            <span
              key={i}
              className="w-1.5 h-1.5 rounded-full bg-gray-400 animate-bounce"
              style={{ animationDelay: `${i * 0.15}s` }}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function PatientChatPage() {
  const [messages, setMessages] = useState<Message[]>([WELCOME_MESSAGE]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [sessionId] = useState(() => generateId());
  const [patientId, setPatientId] = useState<string>("");
  const [pendingQueueId, setPendingQueueId] = useState<string | null>(null);
  const [followUpRounds, setFollowUpRounds] = useState(0);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Initialise patient ID on client (avoids SSR mismatch)
  useEffect(() => {
    setPatientId(getOrCreatePatientId());
  }, []);

  // Auto-scroll to latest message
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isLoading]);

  // Build conversation history for multi-turn context
  const buildHistory = useCallback((): ConversationTurn[] => {
    return messages
      .filter((m) => m.id !== "welcome")
      .map((m) => ({
        role: m.role,
        content: m.content,
      }));
  }, [messages]);

  const handleNurseResolved = useCallback((dept: string) => {
    setPendingQueueId(null);
    setMessages((prev) => [
      ...prev,
      {
        id: generateId(),
        role: "assistant",
        content: `✅ Điều dưỡng đã xác nhận chuyên khoa phù hợp cho bạn. ${dept}. Vui lòng đến quầy tiếp đón để được hướng dẫn thêm.`,
        timestamp: new Date(),
        flow: "AUTO_RESOLVED",
      },
    ]);
  }, []);

  const handleSend = async () => {
    const text = input.trim();
    if (!text || isLoading || !patientId) return;

    // Optimistically add user message
    const userMessage: Message = {
      id: generateId(),
      role: "user",
      content: text,
      timestamp: new Date(),
    };
    setMessages((prev) => [...prev, userMessage]);
    setInput("");
    setIsLoading(true);

    try {
      const response: ChatResponse = await sendTriageMessage({
        patient_id: patientId,
        message: text,
        session_id: sessionId,
        conversation_history: buildHistory(),
        follow_up_rounds: followUpRounds,
      });

      if (response.flow === "EMERGENCY" && response.emergency) {
        const em = response.emergency;
        setMessages((prev) => [
          ...prev,
          {
            id: generateId(),
            role: "assistant",
            content: em.message,
            timestamp: new Date(),
            flow: "EMERGENCY",
            isEmergency: true,
            emergencyKeyword: em.matched_keyword,
            instructions: em.instructions,
          },
        ]);
      } else if (response.flow === "FOLLOW_UP" && response.result) {
        // Chatbot needs more info — ask follow-up question, no queue insert
        const r = response.result;
        setFollowUpRounds((n) => n + 1);
        setMessages((prev) => [
          ...prev,
          {
            id: generateId(),
            role: "assistant",
            content: `🩺 ${r.follow_up_question ?? r.message}`,
            timestamp: new Date(),
            flow: "FOLLOW_UP",
          },
        ]);
      } else if (response.flow === "PENDING_HUMAN" && response.result) {
        const r = response.result;
        setFollowUpRounds(0);
        setMessages((prev) => [
          ...prev,
          {
            id: generateId(),
            role: "assistant",
            content: r.message,
            timestamp: new Date(),
            flow: "PENDING_HUMAN",
            queueId: r.queue_id ?? undefined,
          },
        ]);
        if (r.queue_id) {
          setPendingQueueId(r.queue_id);
        }
      } else if (response.flow === "AUTO_RESOLVED" && response.result) {
        const r = response.result;
        setFollowUpRounds(0);
        setMessages((prev) => [
          ...prev,
          {
            id: generateId(),
            role: "assistant",
            content: r.message,
            timestamp: new Date(),
            flow: "AUTO_RESOLVED",
            departmentName: r.department_name ?? undefined,
            departmentCode: r.department_code ?? undefined,
            confidence: r.confidence_score ?? undefined,
            doctors: r.doctors ?? undefined,
            clinics: r.clinics ?? undefined,
          },
        ]);
      }
    } catch (err: unknown) {
      const errMsg =
        err instanceof Error ? err.message : "Đã xảy ra lỗi không xác định.";
      setMessages((prev) => [
        ...prev,
        {
          id: generateId(),
          role: "assistant",
          content: `⚠️ Lỗi kết nối: ${errMsg}. Vui lòng thử lại hoặc gọi 1800 6858.`,
          timestamp: new Date(),
        },
      ]);
    } finally {
      setIsLoading(false);
      inputRef.current?.focus();
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleReset = () => {
    setMessages([WELCOME_MESSAGE]);
    setPendingQueueId(null);
    setFollowUpRounds(0);
    setInput("");
    inputRef.current?.focus();
  };

  return (
    <div className="min-h-screen bg-gradient-to-b from-blue-50 to-gray-50 flex flex-col items-center py-4 px-2">
      {/* Card container */}
      <div
        className="w-full max-w-md bg-white rounded-2xl shadow-xl flex flex-col overflow-hidden"
        style={{ height: "calc(100vh - 2rem)", maxHeight: "780px" }}
      >
        {/* Header */}
        <div className="bg-blue-700 px-4 py-3 flex items-center gap-3 shrink-0">
          <div className="w-9 h-9 rounded-full bg-white flex items-center justify-center shrink-0">
            <span className="text-blue-700 font-bold text-sm">V+</span>
          </div>
          <div className="flex-1 min-w-0">
            <h1 className="text-white font-bold text-sm leading-tight">
              Vinmec AI Triage
            </h1>
            <p className="text-blue-200 text-xs truncate">
              Trợ lý Điều dưỡng Sơ yếu
            </p>
          </div>
          <button
            onClick={handleReset}
            className="text-blue-200 hover:text-white text-xs border border-blue-500 hover:border-blue-300 px-2 py-1 rounded-lg transition-colors"
            title="Bắt đầu cuộc trò chuyện mới"
          >
            Mới
          </button>
        </div>

        {/* Disclaimer */}
        <DisclaimerBanner />

        {/* Messages */}
        <div className="flex-1 overflow-y-auto px-2 py-3 space-y-1">
          {messages.map((msg) => (
            <div key={msg.id}>
              <ChatBubble message={msg} />
              {/* Auto-resolved result card (Removed as per user request) */}
              {/* Doctor selection card (shown when doctors list is available) */}
              {msg.flow === "AUTO_RESOLVED" &&
                msg.doctors &&
                msg.doctors.length > 0 &&
                msg.departmentCode && (
                  <div className="flex justify-start ml-9">
                    <div className="max-w-[78%] w-full">
                      <DoctorSelectionBubble
                        doctors={msg.doctors}
                        clinics={msg.clinics ?? []}
                        patientId={patientId}
                        departmentCode={msg.departmentCode}
                      />
                    </div>
                  </div>
                )}
            </div>
          ))}

          {/* Pending human triage widget */}
          {pendingQueueId && (
            <PendingHumanBubble
              queueId={pendingQueueId}
              onResolved={handleNurseResolved}
            />
          )}

          {/* Loading indicator */}
          {isLoading && <TypingIndicator />}

          <div ref={messagesEndRef} />
        </div>

        {/* Input area */}
        <div className="border-t border-gray-100 px-3 py-3 shrink-0 bg-white">
          <div className="flex gap-2 items-end">
            <textarea
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Mô tả triệu chứng của bạn... (Enter để gửi)"
              rows={2}
              disabled={isLoading}
              className="flex-1 resize-none rounded-xl border border-gray-200 bg-gray-50 px-3 py-2.5 text-sm text-gray-800 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent disabled:opacity-50 transition"
            />
            <button
              onClick={handleSend}
              disabled={isLoading || !input.trim() || !patientId}
              className="shrink-0 w-10 h-10 rounded-xl bg-blue-600 hover:bg-blue-700 active:bg-blue-800 disabled:bg-gray-300 text-white flex items-center justify-center transition-colors shadow-sm"
              aria-label="Gửi"
            >
              {isLoading ? (
                <svg
                  className="w-4 h-4 animate-spin"
                  viewBox="0 0 24 24"
                  fill="none"
                >
                  <circle
                    className="opacity-25"
                    cx="12"
                    cy="12"
                    r="10"
                    stroke="currentColor"
                    strokeWidth="4"
                  />
                  <path
                    className="opacity-75"
                    fill="currentColor"
                    d="M4 12a8 8 0 018-8v8z"
                  />
                </svg>
              ) : (
                <svg
                  className="w-4 h-4"
                  viewBox="0 0 24 24"
                  fill="currentColor"
                >
                  <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z" />
                </svg>
              )}
            </button>
          </div>
          <p className="text-[10px] text-gray-400 text-center mt-1.5">
            Shift+Enter để xuống dòng · Dữ liệu được ẩn danh hoá trước khi xử lý
          </p>
        </div>
      </div>

      {/* Link to nurse dashboard */}
      <a
        href="/dashboard"
        className="mt-3 text-xs text-gray-400 hover:text-gray-600 underline transition-colors"
      >
        Màn hình Điều dưỡng →
      </a>
    </div>
  );
}
