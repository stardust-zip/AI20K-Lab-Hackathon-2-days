// lib/supabase.ts – Supabase client for Realtime subscriptions
//
// Used exclusively on the Nurse Dashboard to receive live updates when
// new cases enter the human_triage_queue table.
//
// The REST/auth features are NOT used here – we connect directly to the
// FastAPI backend for all data mutations.  Supabase is only used for its
// Realtime WebSocket capability.

import { createClient, SupabaseClient } from "@supabase/supabase-js";

const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "";
const supabaseAnonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? "";

// ---------------------------------------------------------------------------
// Singleton client
// ---------------------------------------------------------------------------

let _client: SupabaseClient | null = null;

/**
 * Returns the shared Supabase client instance.
 *
 * We use a lazy singleton so the client is only created once, and only when
 * it is actually needed (avoids SSR issues on the server).
 *
 * If the environment variables are not configured the client is still
 * created but Realtime subscriptions will fail gracefully – the dashboard
 * will fall back to polling instead.
 */
export function getSupabaseClient(): SupabaseClient {
  if (_client) return _client;

  if (!supabaseUrl || !supabaseAnonKey) {
    console.warn(
      "[supabase] NEXT_PUBLIC_SUPABASE_URL or NEXT_PUBLIC_SUPABASE_ANON_KEY " +
      "is not set. Realtime subscriptions will be unavailable. " +
      "The dashboard will fall back to polling."
    );
  }

  _client = createClient(supabaseUrl || "https://placeholder.supabase.co", supabaseAnonKey || "placeholder", {
    realtime: {
      params: {
        eventsPerSecond: 10,
      },
    },
    auth: {
      // We are not using Supabase Auth – disable auto-refresh
      autoRefreshToken: false,
      persistSession: false,
    },
  });

  return _client;
}

// ---------------------------------------------------------------------------
// Typed Realtime payload shapes
// ---------------------------------------------------------------------------

/**
 * The shape of a row change event emitted by Supabase Realtime for the
 * `human_triage_queue` table.
 */
export interface QueueRealtimePayload {
  schema: string;
  table: string;
  commit_timestamp: string;
  eventType: "INSERT" | "UPDATE" | "DELETE";
  new: {
    id: string;
    patient_id: string;
    clinical_summary: string;
    suggested_dept: string | null;
    status: "PENDING" | "RESOLVED" | "TIMEOUT";
    created_at: string;
  };
  old: {
    id?: string;
    status?: string;
  };
  errors: string[] | null;
}

// ---------------------------------------------------------------------------
// Helper: subscribe to human_triage_queue changes
// ---------------------------------------------------------------------------

/**
 * Subscribe to all INSERT / UPDATE events on `human_triage_queue`.
 *
 * @param onEvent  Callback invoked with the typed payload on each event.
 * @returns        A cleanup function that unsubscribes and removes the channel.
 *
 * @example
 * ```ts
 * const cleanup = subscribeToQueue((payload) => {
 *   if (payload.eventType === "INSERT") refetchQueue();
 *   if (payload.eventType === "UPDATE") refetchQueue();
 * });
 *
 * // In a useEffect cleanup:
 * return () => cleanup();
 * ```
 */
export function subscribeToQueue(
  onEvent: (payload: QueueRealtimePayload) => void
): () => void {
  const client = getSupabaseClient();

  const channel = client
    .channel("human_triage_queue_changes")
    .on(
      "postgres_changes",
      {
        event: "*",           // INSERT | UPDATE | DELETE
        schema: "public",
        table: "human_triage_queue",
      },
      (payload) => {
        onEvent(payload as unknown as QueueRealtimePayload);
      }
    )
    .subscribe((status) => {
      if (status === "SUBSCRIBED") {
        console.info("[supabase] Realtime: subscribed to human_triage_queue");
      } else if (status === "CHANNEL_ERROR" || status === "TIMED_OUT") {
        console.warn("[supabase] Realtime channel error:", status);
      }
    });

  // Return cleanup function
  return () => {
    client.removeChannel(channel).catch(() => {
      // Ignore errors on cleanup
    });
  };
}

/**
 * Check whether the Supabase environment variables are properly configured.
 * Used by the dashboard to decide whether to enable Realtime or fall back
 * to polling.
 */
export function isSupabaseConfigured(): boolean {
  return Boolean(supabaseUrl && supabaseAnonKey &&
    supabaseUrl !== "https://placeholder.supabase.co");
}
