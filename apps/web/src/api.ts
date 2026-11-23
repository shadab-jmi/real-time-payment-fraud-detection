export type User = {
  username: string;
  role: "viewer" | "analyst";
  csrf: string;
};
export type Run = { id: string; seed: number; generator_version: string };
export type Tx = {
  id: string;
  transaction_id: string;
  received_at: string;
  account_id: string;
  merchant_id: string;
  device_id: string | null;
  amount_minor: number;
  score: number | null;
  action: string | null;
  state: string;
  case_id: string | null;
  review_threshold: number;
  block_threshold: number;
  model_version: string;
  bias: number;
  raw_margin: number;
  contributions: {
    feature_name: string;
    feature_value: number;
    signed_value: number;
  }[];
  manifest: { name: string; unit: string }[];
};
export type Case = {
  id: string;
  status: string;
  priority: string;
  owner: string | null;
  version: number;
  transaction_id: string;
  score: number;
  resolution: string | null;
  notes: { id: string; username: string; body: string; created_at: string }[];
  timeline: {
    id: string;
    kind: string;
    username: string | null;
    occurred_at: string;
  }[];
  links: { transaction_id: string; relation: string }[];
};
export type Page<T> = { items: T[]; next_cursor: string | null };
export type Summary = {
  accepted: number;
  pending: number;
  reviewed: number;
  blocked: number;
  failed: number;
  series: { minute: string; total: number }[];
};
export class Failure extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}
export async function api<T>(
  path: string,
  method = "GET",
  body?: unknown,
  csrf?: string,
): Promise<T> {
  const r = await fetch("/v1/" + path, {
    method,
    headers: {
      "Content-Type": "application/json",
      ...(csrf ? { "X-CSRF-Token": csrf } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) {
    const error = await r.json().catch(() => ({}));
    throw new Failure(r.status, error.message || "Request failed");
  }
  return r.json();
}
