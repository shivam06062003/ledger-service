// Load test for POST /v1/transfers.
//
//   SCENARIO=spread  transfers between random accounts (realistic traffic)
//   SCENARIO=hot     every transfer debits ONE account: the worst case, since
//                    row locks serialize all transfers touching that account
//   RATE=150         fixed arrival rate (transfers/s) instead of max throughput
//
// Run via `make loadtest` (see README). Rate limiting must be disabled for the
// run, since every virtual user shares one API key.
import http from "k6/http";
import { check } from "k6";

const BASE = __ENV.BASE_URL || "http://api:8000";
const SCENARIO = __ENV.SCENARIO || "spread";
const ACCOUNTS = parseInt(__ENV.ACCOUNTS || "200", 10);
const HEADERS = {
  Authorization: `Bearer ${__ENV.API_KEY}`,
  "Content-Type": "application/json",
};

// RATE set: open model, a fixed number of new transfers per second regardless
// of how fast the server answers. This measures latency at a given load.
// RATE unset: closed model, N users in a loop, which finds maximum throughput.
const scenario = __ENV.RATE
  ? {
      executor: "constant-arrival-rate",
      rate: parseInt(__ENV.RATE, 10),
      timeUnit: "1s",
      duration: __ENV.DURATION || "30s",
      preAllocatedVUs: 100,
      maxVUs: 400,
    }
  : {
      executor: "constant-vus",
      vus: parseInt(__ENV.VUS || "50", 10),
      duration: __ENV.DURATION || "30s",
    };

export const options = {
  scenarios: { transfers: scenario },
  thresholds: {
    "http_req_failed{name:transfer}": ["rate<0.01"],
    "http_req_duration{name:transfer}": ["p(95)<500"],
  },
  summaryTrendStats: ["avg", "p(50)", "p(95)", "p(99)", "max"],
};

function post(path, body, idempotencyKey) {
  const headers = idempotencyKey ? { ...HEADERS, "Idempotency-Key": idempotencyKey } : HEADERS;
  const res = http.post(`${BASE}${path}`, JSON.stringify(body), { headers, tags: { name: "setup" } });
  if (res.status !== 201) throw new Error(`setup ${path} failed: ${res.status} ${res.body}`);
  return res.json();
}

export function setup() {
  const funding = post("/v1/accounts", {
    name: "Load test funding",
    currency: "INR",
    allow_negative_balance: true,
  }).id;
  const accounts = [];
  for (let i = 0; i < ACCOUNTS; i++) {
    const id = post("/v1/accounts", { name: `load-${i}`, currency: "INR" }).id;
    post(
      "/v1/transfers",
      { source_account_id: funding, destination_account_id: id, amount: 1e12, currency: "INR" },
      `fund-${id}`,
    );
    accounts.push(id);
  }
  return { accounts };
}

function pick(accounts, exclude) {
  let id;
  do {
    id = accounts[Math.floor(Math.random() * accounts.length)];
  } while (id === exclude);
  return id;
}

export default function (data) {
  const source = SCENARIO === "hot" ? data.accounts[0] : pick(data.accounts);
  const destination = pick(data.accounts, source);
  const res = http.post(
    `${BASE}/v1/transfers`,
    JSON.stringify({
      source_account_id: source,
      destination_account_id: destination,
      amount: 1,
      currency: "INR",
    }),
    {
      headers: { ...HEADERS, "Idempotency-Key": `${__VU}-${__ITER}-${Date.now()}` },
      tags: { name: "transfer" },
    },
  );
  check(res, { "201 Created": (r) => r.status === 201 });
}
