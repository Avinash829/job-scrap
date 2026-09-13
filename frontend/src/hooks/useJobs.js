import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client.js";

/** Fetches a page of jobs, cancelling in-flight requests when filters change. */
export function useJobs(filters) {
  const [data, setData] = useState({ items: [], total: 0 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const controller = useRef(null);

  const load = useCallback(async () => {
    controller.current?.abort();
    const ctrl = new AbortController();
    controller.current = ctrl;

    setLoading(true);
    setError(null);
    try {
      setData(await api.listJobs(filters, ctrl.signal));
    } catch (err) {
      if (err.name !== "AbortError") setError(err.message);
    } finally {
      if (!ctrl.signal.aborted) setLoading(false);
    }
  }, [JSON.stringify(filters)]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    load();
    return () => controller.current?.abort();
  }, [load]);

  return { ...data, loading, error, reload: load };
}

export function useStats() {
  const [stats, setStats] = useState(null);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    const ctrl = new AbortController();
    api
      .stats(ctrl.signal)
      .then(setStats)
      .catch(() => {});
    return () => ctrl.abort();
  }, [nonce]);

  return { stats, reload: () => setNonce((n) => n + 1) };
}
