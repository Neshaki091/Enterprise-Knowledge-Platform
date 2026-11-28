/**
 * Helper chung cho k6 load test EKP.
 *
 * Yêu cầu: đã chạy `python load_tests/seed_test_data.py` để tạo
 * load_tests/.runtime/config.json (synthetic, bị gitignore).
 */

import http from "k6/http";
import { check, fail, sleep } from "k6";
import { Counter, Trend } from "k6/metrics";

export const config = JSON.parse(open("../.runtime/config.json"));
export const BASE_URL = (__ENV.BASE_URL || config.base_url || "http://localhost:8000").replace(/\/$/, "");
export const EMPLOYEES = (config.tenants && config.tenants.a && config.tenants.a.users) || [];
export const OWNER = (config.tenants && config.tenants.a && config.tenants.a.owner) || null;
export const TENANT_B = config.tenants && config.tenants.b ? config.tenants.b : null;
export const CHAT_MODE = __ENV.CHAT_MODE || "mock";
export const CHAT_STREAM = (__ENV.CHAT_STREAM || "false") === "true";

export const serverErrors = new Counter("server_errors");
export const rateLimited = new Counter("rate_limited");
export const chatTtfb = new Trend("chat_ttfb_ms", true);

const QUERIES = [
  "Chính sách nghỉ phép của nhân viên là gì?",
  "Quy trình nộp đơn nghỉ phép như thế nào?",
  "Tài liệu hướng dẫn nội bộ nằm ở đâu?",
  "Nhân viên được nghỉ bao nhiêu ngày mỗi năm?",
  "Quy định làm việc từ xa của công ty?",
  "Không tìm thấy thông tin về quy trình mua sắm?",
];

let session = null;

function postJson(path, payload, extraHeaders) {
  return http.post(`${BASE_URL}${path}`, JSON.stringify(payload), {
    headers: {
      "Content-Type": "application/json",
      ...(extraHeaders || {}),
    },
    tags: { endpoint: path },
  });
}

function authHeaders(current) {
  return { Authorization: `Bearer ${current.accessToken}` };
}

export function pick(items) {
  return items[Math.floor(Math.random() * items.length)];
}

export function randomBetween(min, max) {
  return min + Math.random() * (max - min);
}

export function pickEmployee() {
  if (!EMPLOYEES.length) {
    fail("Config không có employee. Chạy seed_test_data.py trước.");
  }
  return pick(EMPLOYEES);
}

export function pickOwner() {
  if (!OWNER) {
    fail("Config không có owner. Chạy seed_test_data.py trước.");
  }
  return OWNER;
}

function loginUser(user) {
  const response = http.post(
    `${BASE_URL}/api/v1/auth/login`,
    JSON.stringify({ email: user.email, password: user.password }),
    {
      headers: { "Content-Type": "application/json" },
      tags: { endpoint: "auth_login" },
    },
  );
  if (response.status !== 200) {
    fail(`Login ${user.email} thất bại: HTTP ${response.status} ${response.body}`);
  }
  const body = response.json();
  return {
    email: user.email,
    accessToken: body.access_token,
    refreshToken: body.refresh_token,
    expiresAt: Date.now() + Number(body.expires_in_seconds || 900) * 1000,
  };
}

export function ensureSession(user) {
  if (
    session &&
    session.email === user.email &&
    session.expiresAt > Date.now() + 60_000
  ) {
    return session;
  }
  if (session && session.email === user.email && session.refreshToken) {
    const refresh = http.post(
      `${BASE_URL}/api/v1/auth/refresh`,
      JSON.stringify({ refresh_token: session.refreshToken }),
      {
        headers: { "Content-Type": "application/json" },
        tags: { endpoint: "auth_refresh" },
      },
    );
    if (refresh.status === 200) {
      const body = refresh.json();
      session.accessToken = body.access_token;
      session.refreshToken = body.refresh_token;
      session.expiresAt = Date.now() + Number(body.expires_in_seconds || 900) * 1000;
      return session;
    }
  }
  session = loginUser(user);
  return session;
}

function recordResponse(response, endpoint) {
  if (response.status === 429) {
    rateLimited.add(1, { endpoint });
  } else if (response.status >= 500) {
    serverErrors.add(1, { endpoint });
  }
}

export function searchOnce(current) {
  const response = http.post(
    `${BASE_URL}/api/v1/search/hybrid`,
    JSON.stringify({ query: pick(QUERIES), top_k: 5 }),
    {
      headers: { ...authHeaders(current), "Content-Type": "application/json" },
      tags: { endpoint: "search" },
    },
  );
  recordResponse(response, "search");
  check(response, {
    "search not 5xx": (res) => res.status < 500,
    "search status expected": (res) => [200, 429].includes(res.status),
  }, { endpoint: "search" });
  return response;
}

export function listOnce(current) {
  const response = http.get(`${BASE_URL}/api/v1/documents?skip=0&limit=10`, {
    headers: authHeaders(current),
    tags: { endpoint: "documents_list" },
  });
  recordResponse(response, "documents_list");
  check(response, {
    "documents list not 5xx": (res) => res.status < 500,
  }, { endpoint: "documents_list" });
  return response;
}

export function workspaceOnce(current) {
  const response = http.get(`${BASE_URL}/api/v1/dashboards/workspace`, {
    headers: authHeaders(current),
    tags: { endpoint: "workspace" },
  });
  recordResponse(response, "workspace");
  check(response, {
    "workspace not 5xx": (res) => res.status < 500,
  }, { endpoint: "workspace" });
  return response;
}

export function chatOnce(current) {
  const query = pick(QUERIES);
  const path = CHAT_STREAM ? "/api/v1/chat/stream" : "/api/v1/chat/query";
  const response = http.post(
    `${BASE_URL}${path}`,
    JSON.stringify({ query, top_k: 3, mode: CHAT_MODE }),
    {
      headers: { ...authHeaders(current), "Content-Type": "application/json" },
      tags: { endpoint: CHAT_STREAM ? "chat_stream" : "chat_query" },
      timeout: "120s",
    },
  );
  if (CHAT_STREAM) {
    chatTtfb.add(response.timings.waiting);
  }
  recordResponse(response, CHAT_STREAM ? "chat_stream" : "chat_query");
  check(response, {
    "chat not 5xx": (res) => res.status < 500,
    "chat status expected": (res) => [200, 429].includes(res.status),
  }, { endpoint: CHAT_STREAM ? "chat_stream" : "chat_query" });

  let answer = "";
  try {
    const body = response.json();
    answer = body.answer || "";
  } catch (_error) {
    answer = "";
  }
  return { query, answer, status: response.status };
}

export function feedbackOnce(current, chat) {
  if (!chat || !chat.answer || chat.status !== 200) return;
  const response = http.post(
    `${BASE_URL}/api/v1/chat/feedback`,
    JSON.stringify({
      query: chat.query,
      answer: chat.answer,
      rating: Math.random() < 0.8 ? "THUMBS_UP" : "THUMBS_DOWN",
    }),
    {
      headers: { ...authHeaders(current), "Content-Type": "application/json" },
      tags: { endpoint: "feedback" },
    },
  );
  recordResponse(response, "feedback");
}

/**
 * Một phiên làm việc của nhân viên: ~90 request, think-time 15–18s,
 * sau đó nghỉ 0–10 phút trước phiên kế tiếp.
 */
export function employeeSession(user, actions = 90) {
  const current = ensureSession(user);
  let lastChat = null;
  for (let index = 0; index < actions; index += 1) {
    const roll = Math.random();
    if (roll < 0.04) {
      lastChat = chatOnce(current);
      if (Math.random() < 0.5) feedbackOnce(current, lastChat);
    } else if (roll < 0.30) {
      listOnce(current);
    } else if (roll < 0.45) {
      workspaceOnce(current);
    } else {
      searchOnce(current);
    }
    sleep(randomBetween(15, 18));
  }
  sleep(randomBetween(0, 600));
}

/** Một request cho bài saturation/spike, không sleep bên trong; trả response để log first-429. */
export function saturationHit(user) {
  const current = ensureSession(user);
  if (Math.random() < 0.05) {
    return chatOnce(current);
  }
  return searchOnce(current);
}