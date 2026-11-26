import { request } from "./http.js";

export const api = {
  config: () => request("/workbench"),
  login: data => request("/auth/login", {method: "POST", data}),
  cases: () => request("/cases"),
  createCase: data => request("/cases", {method: "POST", data}),
  runs: () => request("/runs"),
  run: (id, signal) => request(`/runs/${id}`, {signal}),
  enqueue: (id, data) => request(`/cases/${id}/runs`, {method: "POST", data}),
  review: id => request(`/runs/${id}/review`),
  submitReview: (id, data) => request(`/runs/${id}/review`, {method: "POST", data}),
  export: id => request(`/runs/${id}/export`, {raw: true}),
  audit: () => request("/audit", {signal: AbortSignal.timeout(5000)}),

  // 炉体退役批次 · 放行判定
  releaseBoard: () => request("/board"),
  releaseBatches: params => request(`/batches${params ? `?${new URLSearchParams(params)}` : ""}`),
  createLine: data => request("/lines", {method: "POST", data}),
  createOven: data => request("/ovens", {method: "POST", data}),
  createReplacement: data => request("/replacements", {method: "POST", data}),
  createReleaseBatch: data => request("/batches", {method: "POST", data}),
  releaseBatch: (id, data = {}) => request(`/batches/${id}/release`, {method: "POST", data}),
  rejudgeBatch: (id, data = {}) => request(`/batches/${id}/rejudge`, {method: "POST", data}),
  submitReleaseBatch: (id, data = {}) => request(`/batches/${id}/submit`, {method: "POST", data}),
  failReleaseBatch: (id, data = {}) => request(`/batches/${id}/fail`, {method: "POST", data}),
  restoreBatch: (id, data = {}) => request(`/batches/${id}/restore`, {method: "POST", data}),
  acceptOven: (ref, data = {}) => request(`/ovens/${encodeURIComponent(ref)}/accept`, {method: "POST", data}),
  restoreOven: (ref, data = {}) => request(`/ovens/${encodeURIComponent(ref)}/confirm-restore`, {method: "POST", data}),
};
