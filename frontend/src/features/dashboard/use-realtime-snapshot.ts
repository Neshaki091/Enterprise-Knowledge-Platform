import { useEffect, useState } from "react";
import { ApiError, openSseStream } from "@/lib/api-client";

export type LiveStatus = "connecting" | "live" | "paused" | "error";

interface RealtimeSnapshotState<T> {
  enabled: boolean;
  setEnabled: (enabled: boolean) => void;
  status: LiveStatus;
  snapshot: T | null;
}

/**
 * Mở luồng SSE analytics có Bearer token, tự nối lại khi đứt và dừng hẳn khi mất quyền.
 * `isSnapshot` phải là hàm ổn định (khai báo ngoài component) để tránh khởi tạo lại luồng.
 */
export function useRealtimeSnapshot<T>(
  path: string,
  connectionKey: string,
  isSnapshot: (payload: unknown) => payload is T,
): RealtimeSnapshotState<T> {
  const [enabled, setEnabled] = useState(true);
  const [status, setStatus] = useState<LiveStatus>("connecting");
  const [snapshot, setSnapshot] = useState<T | null>(null);

  useEffect(() => {
    setSnapshot(null);
  }, [connectionKey]);

  useEffect(() => {
    if (!enabled) {
      setStatus("paused");
      return;
    }
    let disposed = false;
    const controller = new AbortController();
    setStatus("connecting");

    const run = async (): Promise<void> => {
      while (!disposed && !controller.signal.aborted) {
        try {
          await openSseStream(
            path,
            (payload) => {
              if (disposed || !isSnapshot(payload)) return;
              setSnapshot(payload);
              setStatus("live");
            },
            controller.signal,
          );
        } catch (error) {
          if (disposed || controller.signal.aborted) return;
          if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
            setStatus("error");
            return;
          }
          setStatus("error");
        }
        if (disposed || controller.signal.aborted) return;
        await new Promise((resolve) => setTimeout(resolve, 4000));
        if (disposed || controller.signal.aborted) return;
        setStatus("connecting");
      }
    };

    void run();
    return () => {
      disposed = true;
      controller.abort();
    };
  }, [path, connectionKey, enabled, isSnapshot]);

  return { enabled, setEnabled, status, snapshot };
}
