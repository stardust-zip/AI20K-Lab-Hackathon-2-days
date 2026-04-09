"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import {
  getPendingQueue,
  resolveQueueItem,
  checkTimeouts,
  DEPARTMENTS,
  getDeptName,
  type QueueItem,
} from "@/lib/api";
import { subscribeToQueue, isSupabaseConfigured } from "@/lib/supabase";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface ModalState {
  open: boolean;
  queueId: string;
  currentDept: string | null;
  selectedDept: string;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatElapsed(createdAt: string): string {
  const created = new Date(createdAt);
  const now = new Date();
  const diff = Math.floor((now.getTime() - created.getTime()) / 1000);
  const mins = Math.floor(diff / 60);
  const secs = diff % 60;
  if (mins === 0) return `${secs}s`;
  return `${mins}p ${secs}s`;
}

function getElapsedSeconds(createdAt: string): number {
  const created = new Date(createdAt);
  return Math.floor((Date.now() - created.getTime()) / 1000);
}

function StatusBadge({ item }: { item: QueueItem }) {
  const elapsed = getElapsedSeconds(item.created_at);
  const urgent = elapsed >= 120; // 2 min = warning
  const critical = elapsed >= 170; // ~2m50s = near SLA breach

  if (critical) {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-semibold bg-red-100 text-red-700 border border-red-300 animate-pulse">
        🔴 Khẩn cấp
      </span>
    );
  }
  if (urgent) {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-semibold bg-amber-100 text-amber-700 border border-amber-300">
        🟡 Cần duyệt
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-semibold bg-green-100 text-green-700 border border-green-200">
      🟢 Đang chờ
    </span>
  );
}

function SlaBar({ createdAt }: { createdAt: string }) {
  const [pct, setPct] = useState(0);

  useEffect(() => {
    const update = () => {
      const elapsed = getElapsedSeconds(createdAt);
      setPct(Math.min((elapsed / 180) * 100, 100));
    };
    update();
    const t = setInterval(update, 1000);
    return () => clearInterval(t);
  }, [createdAt]);

  const color =
    pct >= 95
      ? "bg-red-500"
      : pct >= 67
        ? "bg-amber-400"
        : "bg-green-400";

  return (
    <div className="w-full h-1.5 rounded-full bg-gray-100 overflow-hidden">
      <div
        className={`h-full rounded-full transition-all duration-1000 ${color}`}
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

function ElapsedTimer({ createdAt }: { createdAt: string }) {
  const [elapsed, setElapsed] = useState(() => formatElapsed(createdAt));

  useEffect(() => {
    const t = setInterval(() => setElapsed(formatElapsed(createdAt)), 1000);
    return () => clearInterval(t);
  }, [createdAt]);

  const secs = getElapsedSeconds(createdAt);
  const color =
    secs >= 170 ? "text-red-600 font-bold" : secs >= 120 ? "text-amber-600 font-semibold" : "text-gray-500";

  return <span className={`text-sm tabular-nums ${color}`}>{elapsed}</span>;
}

// ---------------------------------------------------------------------------
// Change Department Modal
// ---------------------------------------------------------------------------

function ChangeDeptModal({
  modal,
  onClose,
  onConfirm,
  loading,
}: {
  modal: ModalState;
  onClose: () => void;
  onConfirm: (dept: string) => void;
  loading: boolean;
}) {
  if (!modal.open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-sm mx-4 overflow-hidden">
        {/* Modal header */}
        <div className="bg-blue-700 px-5 py-4">
          <h2 className="text-white font-bold text-base">🔄 Đổi Chuyên Khoa</h2>
          <p className="text-blue-200 text-xs mt-0.5">
            Khoa AI đề xuất:{" "}
            <span className="font-semibold text-white">
              {getDeptName(modal.currentDept)}
            </span>
          </p>
        </div>

        {/* Modal body */}
        <div className="p-5">
          <label className="block text-sm font-semibold text-gray-700 mb-2">
            Chọn chuyên khoa phù hợp:
          </label>
          <select
            className="w-full border border-gray-200 rounded-xl px-3 py-2.5 text-sm text-gray-800 bg-gray-50 focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent"
            value={modal.selectedDept}
            onChange={(e) => {
              // update parent state via close+reopen is clunky; use local state trick
              onConfirm(e.target.value);
            }}
            disabled={loading}
            onClick={(e) => e.stopPropagation()}
          >
            {DEPARTMENTS.map((d) => (
              <option key={d.code} value={d.code}>
                {d.name}
              </option>
            ))}
          </select>
          <p className="text-xs text-gray-400 mt-2">
            Hành động này sẽ được ghi nhận vào hệ thống học máy (Correction Signal).
          </p>
        </div>

        {/* Modal footer */}
        <div className="flex gap-2 px-5 pb-5">
          <button
            onClick={onClose}
            disabled={loading}
            className="flex-1 py-2.5 rounded-xl border border-gray-200 text-sm font-semibold text-gray-600 hover:bg-gray-50 transition-colors disabled:opacity-50"
          >
            Huỷ
          </button>
          <button
            onClick={() => onConfirm(modal.selectedDept)}
            disabled={loading}
            className="flex-1 py-2.5 rounded-xl bg-blue-600 hover:bg-blue-700 text-white text-sm font-semibold transition-colors disabled:opacity-50 flex items-center justify-center gap-2"
          >
            {loading ? (
              <svg className="w-4 h-4 animate-spin" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z" />
              </svg>
            ) : (
              "✓ Xác nhận"
            )}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Queue Row
// ---------------------------------------------------------------------------

function QueueRow({
  item,
  index,
  onApprove,
  onChangeDept,
  approving,
}: {
  item: QueueItem;
  index: number;
  onApprove: (id: string, dept: string) => void;
  onChangeDept: (item: QueueItem) => void;
  approving: string | null;
}) {
  const isApproving = approving === item.id;
  const suggestedDeptName = getDeptName(item.suggested_dept);

  return (
    <tr className="border-b border-gray-100 hover:bg-blue-50/30 transition-colors">
      {/* STT */}
      <td className="px-4 py-3 text-center">
        <span className="text-sm font-semibold text-gray-500">{index + 1}</span>
      </td>

      {/* Patient ID */}
      <td className="px-4 py-3">
        <span className="font-mono text-xs bg-gray-100 text-gray-700 px-2 py-1 rounded-lg">
          {item.patient_id.slice(0, 12)}…
        </span>
      </td>

      {/* Clinical Summary */}
      <td className="px-4 py-3 max-w-xs">
        <p className="text-sm text-gray-700 leading-relaxed line-clamp-3">
          {item.clinical_summary}
        </p>
      </td>

      {/* Suggested Department */}
      <td className="px-4 py-3">
        <div className="flex flex-col gap-1">
          <span className="text-sm font-semibold text-blue-700">
            {suggestedDeptName}
          </span>
          <StatusBadge item={item} />
        </div>
      </td>

      {/* Time */}
      <td className="px-4 py-3">
        <div className="flex flex-col gap-1.5 min-w-[90px]">
          <ElapsedTimer createdAt={item.created_at} />
          <SlaBar createdAt={item.created_at} />
        </div>
      </td>

      {/* Actions */}
      <td className="px-4 py-3">
        <div className="flex gap-2">
          <button
            onClick={() => onApprove(item.id, item.suggested_dept ?? "TIM_MACH")}
            disabled={isApproving}
            className="flex items-center gap-1.5 px-3 py-2 rounded-xl bg-green-500 hover:bg-green-600 active:bg-green-700 text-white text-xs font-semibold transition-colors disabled:opacity-50 shadow-sm whitespace-nowrap"
          >
            {isApproving ? (
              <svg className="w-3.5 h-3.5 animate-spin" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z" />
              </svg>
            ) : (
              "✅"
            )}{" "}
            Duyệt
          </button>

          <button
            onClick={() => onChangeDept(item)}
            disabled={isApproving}
            className="flex items-center gap-1.5 px-3 py-2 rounded-xl bg-blue-500 hover:bg-blue-600 active:bg-blue-700 text-white text-xs font-semibold transition-colors disabled:opacity-50 shadow-sm whitespace-nowrap"
          >
            🔄 Đổi Khoa
          </button>
        </div>
      </td>
    </tr>
  );
}

// ---------------------------------------------------------------------------
// Main Dashboard Component
// ---------------------------------------------------------------------------

export default function NurseDashboard() {
  const [items, setItems] = useState<QueueItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [approving, setApproving] = useState<string | null>(null);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const [realtimeActive, setRealtimeActive] = useState(false);
  const [toast, setToast] = useState<{ msg: string; type: "success" | "error" } | null>(null);
  const [modal, setModal] = useState<ModalState>({
    open: false,
    queueId: "",
    currentDept: null,
    selectedDept: DEPARTMENTS[0].code,
  });
  const [modalLoading, setModalLoading] = useState(false);
  const nurseId = useRef("NURSE-" + Math.random().toString(36).slice(2, 8).toUpperCase());

  // ---------------------------------------------------------------------------
  // Toast helper
  // ---------------------------------------------------------------------------

  const showToast = useCallback(
    (msg: string, type: "success" | "error" = "success") => {
      setToast({ msg, type });
      setTimeout(() => setToast(null), 3500);
    },
    []
  );

  // ---------------------------------------------------------------------------
  // Fetch queue
  // ---------------------------------------------------------------------------

  const fetchQueue = useCallback(async () => {
    try {
      const data = await getPendingQueue();
      setItems(data.items);
      setLastRefresh(new Date());
      setError(null);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Lỗi kết nối backend.";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, []);

  // ---------------------------------------------------------------------------
  // Initial load + polling fallback (every 10s)
  // ---------------------------------------------------------------------------

  useEffect(() => {
    setLastRefresh(new Date());
  }, []);

  useEffect(() => {
    fetchQueue();
    const pollInterval = setInterval(fetchQueue, 10_000);
    return () => clearInterval(pollInterval);
  }, [fetchQueue]);

  // ---------------------------------------------------------------------------
  // SLA timeout sweep every 30s
  // ---------------------------------------------------------------------------

  useEffect(() => {
    const sweep = async () => {
      try {
        await checkTimeouts();
        // Re-fetch so timed-out items disappear from the list
        await fetchQueue();
      } catch {
        // non-critical
      }
    };
    const t = setInterval(sweep, 30_000);
    return () => clearInterval(t);
  }, [fetchQueue]);

  // ---------------------------------------------------------------------------
  // Supabase Realtime
  // ---------------------------------------------------------------------------

  useEffect(() => {
    if (!isSupabaseConfigured()) return;

    const cleanup = subscribeToQueue((payload) => {
      // Any change to the queue → refresh the list
      if (
        payload.eventType === "INSERT" ||
        payload.eventType === "UPDATE"
      ) {
        fetchQueue();
      }
    });

    setRealtimeActive(true);
    return () => {
      cleanup();
      setRealtimeActive(false);
    };
  }, [fetchQueue]);

  // ---------------------------------------------------------------------------
  // Approve handler
  // ---------------------------------------------------------------------------

  const handleApprove = useCallback(
    async (queueId: string, dept: string) => {
      setApproving(queueId);
      try {
        await resolveQueueItem({
          queue_id: queueId,
          approved_dept: dept,
          nurse_id: nurseId.current,
          resolution_type: "NURSE_APPROVED",
        });
        showToast(`✅ Đã duyệt: ${getDeptName(dept)}`);
        await fetchQueue();
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : "Lỗi khi duyệt ca.";
        showToast(msg, "error");
      } finally {
        setApproving(null);
      }
    },
    [fetchQueue, showToast]
  );

  // ---------------------------------------------------------------------------
  // Change dept modal handlers
  // ---------------------------------------------------------------------------

  const openChangeDeptModal = useCallback((item: QueueItem) => {
    setModal({
      open: true,
      queueId: item.id,
      currentDept: item.suggested_dept,
      selectedDept: item.suggested_dept ?? DEPARTMENTS[0].code,
    });
  }, []);

  const closeModal = useCallback(() => {
    if (modalLoading) return;
    setModal((m) => ({ ...m, open: false }));
  }, [modalLoading]);

  // When user picks a new dept in the modal, immediately confirm
  const handleModalConfirm = useCallback(
    async (newDept: string) => {
      if (newDept === modal.currentDept) {
        // No change – treat as approve
        closeModal();
        await handleApprove(modal.queueId, newDept);
        return;
      }
      setModalLoading(true);
      try {
        await resolveQueueItem({
          queue_id: modal.queueId,
          approved_dept: newDept,
          nurse_id: nurseId.current,
          resolution_type: "NURSE_CORRECTED",
        });
        showToast(`🔄 Đã đổi khoa: ${getDeptName(newDept)}`);
        setModal((m) => ({ ...m, open: false }));
        await fetchQueue();
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : "Lỗi khi đổi khoa.";
        showToast(msg, "error");
      } finally {
        setModalLoading(false);
      }
    },
    [modal, closeModal, handleApprove, fetchQueue, showToast]
  );

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Toast notification */}
      {toast && (
        <div
          className={`fixed top-5 right-5 z-50 px-5 py-3 rounded-xl shadow-xl text-sm font-semibold transition-all ${
            toast.type === "success"
              ? "bg-green-600 text-white"
              : "bg-red-600 text-white"
          }`}
        >
          {toast.msg}
        </div>
      )}

      {/* Change dept modal */}
      <ChangeDeptModal
        modal={modal}
        onClose={closeModal}
        onConfirm={handleModalConfirm}
        loading={modalLoading}
      />

      {/* Top bar */}
      <header className="bg-blue-700 shadow-lg px-6 py-4 sticky top-0 z-40">
        <div className="max-w-7xl mx-auto flex items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-full bg-white flex items-center justify-center shrink-0">
              <span className="text-blue-700 font-bold text-sm">V+</span>
            </div>
            <div>
              <h1 className="text-white font-bold text-lg leading-tight">
                🏥 Vinmec Triage Dashboard
              </h1>
              <p className="text-blue-200 text-xs">Hàng đợi phân khoa · Điều dưỡng</p>
            </div>
          </div>

          <div className="flex items-center gap-4">
            {/* Realtime indicator */}
            <div className="flex items-center gap-2">
              <span
                className={`w-2.5 h-2.5 rounded-full ${
                  realtimeActive ? "bg-green-400 animate-pulse" : "bg-gray-400"
                }`}
              />
              <span className="text-blue-200 text-xs hidden sm:inline">
                {realtimeActive ? "Realtime ON" : "Polling"}
              </span>
            </div>

            {/* Pending count badge */}
            <div className="bg-white/20 text-white px-3 py-1.5 rounded-xl text-sm font-semibold">
              {items.length} ca đang chờ
            </div>

            {/* Manual refresh */}
            <button
              onClick={fetchQueue}
              className="text-blue-200 hover:text-white border border-blue-500 hover:border-blue-300 px-3 py-1.5 rounded-xl text-xs font-semibold transition-colors"
            >
              ↻ Làm mới
            </button>

            {/* Back to patient chat */}
            <a
              href="/"
              className="text-blue-200 hover:text-white text-xs underline hidden md:inline"
            >
              ← Giao diện bệnh nhân
            </a>
          </div>
        </div>
      </header>

      {/* Stats bar */}
      <div className="bg-white border-b border-gray-200 px-6 py-3">
        <div className="max-w-7xl mx-auto flex flex-wrap items-center gap-6 text-sm">
          <div className="flex items-center gap-2">
            <span className="text-gray-500">Cập nhật lần cuối:</span>
            <span className="font-semibold text-gray-700">
              {lastRefresh ? lastRefresh.toLocaleTimeString("vi-VN") : "--:--:--"}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <span className="w-3 h-3 rounded-full bg-green-400 inline-block" />
            <span className="text-gray-600">
              {items.filter((i) => getElapsedSeconds(i.created_at) < 120).length} bình thường
            </span>
          </div>
          <div className="flex items-center gap-2">
            <span className="w-3 h-3 rounded-full bg-amber-400 inline-block" />
            <span className="text-gray-600">
              {
                items.filter(
                  (i) =>
                    getElapsedSeconds(i.created_at) >= 120 &&
                    getElapsedSeconds(i.created_at) < 170
                ).length
              }{" "}
              cần duyệt ngay
            </span>
          </div>
          <div className="flex items-center gap-2">
            <span className="w-3 h-3 rounded-full bg-red-500 inline-block animate-pulse" />
            <span className="text-gray-600">
              {items.filter((i) => getElapsedSeconds(i.created_at) >= 170).length} khẩn cấp
            </span>
          </div>
          <div className="ml-auto text-xs text-gray-400">
            SLA: 3 phút/ca · Điều dưỡng: {nurseId.current}
          </div>
        </div>
      </div>

      {/* Main content */}
      <main className="max-w-7xl mx-auto px-4 py-6">
        {/* Error state */}
        {error && (
          <div className="mb-4 bg-red-50 border border-red-200 rounded-xl px-5 py-4 flex items-center gap-3">
            <span className="text-red-500 text-xl">⚠️</span>
            <div>
              <p className="text-red-700 font-semibold text-sm">Lỗi kết nối backend</p>
              <p className="text-red-600 text-xs mt-0.5">{error}</p>
            </div>
            <button
              onClick={fetchQueue}
              className="ml-auto px-3 py-1.5 bg-red-600 hover:bg-red-700 text-white text-xs font-semibold rounded-lg transition-colors"
            >
              Thử lại
            </button>
          </div>
        )}

        {/* Loading skeleton */}
        {loading && (
          <div className="bg-white rounded-2xl shadow-sm border border-gray-200 overflow-hidden">
            {[1, 2, 3].map((i) => (
              <div key={i} className="flex gap-4 px-4 py-4 border-b border-gray-100 animate-pulse">
                <div className="w-8 h-8 rounded-lg bg-gray-100" />
                <div className="flex-1 space-y-2">
                  <div className="h-4 bg-gray-100 rounded w-1/3" />
                  <div className="h-3 bg-gray-100 rounded w-2/3" />
                </div>
                <div className="w-24 h-8 bg-gray-100 rounded-xl" />
                <div className="w-24 h-8 bg-gray-100 rounded-xl" />
              </div>
            ))}
          </div>
        )}

        {/* Empty state */}
        {!loading && items.length === 0 && !error && (
          <div className="bg-white rounded-2xl shadow-sm border border-gray-200 py-20 flex flex-col items-center justify-center gap-4">
            <div className="w-16 h-16 rounded-full bg-green-100 flex items-center justify-center text-3xl">
              ✅
            </div>
            <div className="text-center">
              <p className="text-gray-700 font-semibold text-lg">
                Không có ca nào đang chờ
              </p>
              <p className="text-gray-400 text-sm mt-1">
                Tất cả trường hợp đã được xử lý. Hàng đợi sạch!
              </p>
            </div>
          </div>
        )}

        {/* Table */}
        {!loading && items.length > 0 && (
          <div className="bg-white rounded-2xl shadow-sm border border-gray-200 overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="bg-gray-50 border-b border-gray-200">
                    <th className="px-4 py-3 text-center text-xs font-semibold text-gray-500 uppercase tracking-wider w-12">
                      STT
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">
                      Mã BN
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">
                      Tóm tắt lâm sàng
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">
                      Khoa đề xuất
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider w-28">
                      Thời gian
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider w-44">
                      Thao tác
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((item, idx) => (
                    <QueueRow
                      key={item.id}
                      item={item}
                      index={idx}
                      onApprove={handleApprove}
                      onChangeDept={openChangeDeptModal}
                      approving={approving}
                    />
                  ))}
                </tbody>
              </table>
            </div>

            {/* Table footer */}
            <div className="px-4 py-3 bg-gray-50 border-t border-gray-100 flex items-center justify-between text-xs text-gray-500">
              <span>Tổng cộng {items.length} ca đang chờ duyệt</span>
              <span>
                SLA tối đa: 3 phút/ca · Sau 3 phút tự động chuyển tổng đài
              </span>
            </div>
          </div>
        )}

        {/* Info panel */}
        <div className="mt-6 grid grid-cols-1 sm:grid-cols-3 gap-4">
          <div className="bg-white rounded-xl border border-gray-200 p-4 shadow-sm">
            <p className="text-xs font-semibold text-gray-500 uppercase mb-1">
              Quy trình duyệt
            </p>
            <p className="text-sm text-gray-700 leading-relaxed">
              Đọc tóm tắt lâm sàng → Xác nhận hoặc đổi khoa → Bệnh nhân nhận kết quả tự động.
            </p>
          </div>
          <div className="bg-white rounded-xl border border-gray-200 p-4 shadow-sm">
            <p className="text-xs font-semibold text-gray-500 uppercase mb-1">
              SLA Mục tiêu
            </p>
            <p className="text-sm text-gray-700 leading-relaxed">
              Mỗi ca: <span className="font-bold text-blue-600">15–30 giây</span> duyệt.
              Tối đa <span className="font-bold text-red-600">3 phút</span> trước khi chuyển tổng đài.
            </p>
          </div>
          <div className="bg-white rounded-xl border border-gray-200 p-4 shadow-sm">
            <p className="text-xs font-semibold text-gray-500 uppercase mb-1">
              Ghi chú an toàn
            </p>
            <p className="text-sm text-gray-700 leading-relaxed">
              Mọi ca có triệu chứng nguy hiểm được AI chặn ngay và hướng dẫn gọi 115 — không vào hàng đợi này.
            </p>
          </div>
        </div>
      </main>
    </div>
  );
}
