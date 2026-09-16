/* Shared waiting for durable analyses. Browser storage contains opaque job IDs
   only; every status request is authenticated and checked against its scope. */
(function () {
  "use strict";
  const pending = new Map();
  const generations = new Map();
  const jobId = /^[0-9a-f]{32}$/;

  function remembered(key) {
    try {
      const value = localStorage.getItem(key);
      return jobId.test(value || "") ? value : null;
    } catch (_) { return null; }
  }

  function save(key, value) {
    try {
      if (value) localStorage.setItem(key, value);
      else localStorage.removeItem(key);
    } catch (_) { /* Analysis still works when browser storage is disabled. */ }
  }

  function abortIfNeeded(signal) {
    if (signal && signal.aborted) throw new DOMException("Stopped waiting", "AbortError");
  }

  async function request(url, options) {
    const response = await fetch(url, Object.assign({ credentials: "same-origin", cache: "no-store" }, options));
    if (response.status === 401) location.assign("/login");
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok !== true) {
      const detail = data.error;
      const error = new Error(typeof detail === "string" ? detail
        : (detail && detail.message) || "Analysis is unavailable. Try again.");
      error.code = detail && detail.code;
      throw error;
    }
    if (!data.job || !jobId.test(data.job.job_id || "")) {
      throw new Error("The server returned an invalid analysis job.");
    }
    return data.job;
  }

  function pause(signal) {
    return new Promise((resolve, reject) => {
      const aborted = () => {
        clearTimeout(timer);
        reject(new DOMException("Stopped waiting", "AbortError"));
      };
      const timer = setTimeout(() => {
        if (signal) signal.removeEventListener("abort", aborted);
        resolve();
      }, 5000);
      if (signal) signal.addEventListener("abort", aborted, { once: true });
      if (signal && signal.aborted) aborted();
    });
  }

  async function wait({ startURL, statusURL, storageKey, signal, onStatus, resumeOnly = false }) {
    const generation = (generations.get(storageKey) || 0) + 1;
    generations.set(storageKey, generation);
    const remember = (value) => {
      if (generations.get(storageKey) === generation) save(storageKey, value);
    };
    abortIfNeeded(signal);
    let job = null;
    const previous = remembered(storageKey);
    if (previous) {
      try { job = await request(statusURL(previous), { signal }); }
      catch (error) {
        if (!["job_scope_mismatch", "job_not_found", "unknown_job"].includes(error.code)) throw error;
        remember(null);
      }
    }
    abortIfNeeded(signal);
    if (!job) {
      if (resumeOnly) return null;
      // Do not abort submission: retain its acknowledgement even when the user
      // stops waiting. A rapid resume shares the same in-flight submission.
      let submission = pending.get(startURL);
      if (!submission) {
        const csrf = document.querySelector('meta[name="csrf-token"]').content;
        submission = request(startURL, {
          method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
          body: "{}",
        });
        pending.set(startURL, submission);
        submission.finally(() => pending.delete(startURL)).catch(() => {});
      }
      job = await submission;
      remember(job.job_id);
    }
    while (true) {
      abortIfNeeded(signal);
      if (onStatus) onStatus(job.status);
      if (job.status === "completed") return job;
      if (job.status === "failed" || job.status === "stale") {
        remember(null);
        const error = new Error(job.status === "stale"
          ? "Records or analysis settings changed. Run the analysis again to use current data."
          : (job.error && job.error.message) || "Analysis could not finish. Try again.");
        error.code = (job.error && job.error.code) || job.status;
        throw error;
      }
      if (!["queued", "running"].includes(job.status)) throw new Error("Unknown analysis status.");
      await pause(signal);
      abortIfNeeded(signal);
      job = await request(statusURL(job.job_id), { signal });
    }
  }

  window.HermesAnalysisJobs = { wait, remembered };
})();
