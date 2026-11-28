/**
 * Journey 2 — Knowledge Manager upload tài liệu.
 *
 * Mỗi iteration upload một file synthetic unique (nối thêm marker vào cuối file),
 * nên không bị 409 vì trùng hash. 409 vẫn được chấp nhận nếu file đã tồn tại.
 *
 * Chạy:
 *   k6 run -e VUS=1 -e DURATION=10m load_tests/k6/journey2_ingest.js
 *
 * Cần seed trước để có fixtures + owner account.
 */

import http from "k6/http";
import { check, sleep } from "k6";
import {
  BASE_URL,
  OWNER,
  ensureSession,
  randomBetween,
  serverErrors,
} from "./common.js";

if (!OWNER) {
  throw new Error("Không có owner trong config. Chạy seed_test_data.py trước.");
}

const BASE_TEXT = open("../fixtures/generated/ingest_001.txt");

export const options = {
  vus: Number(__ENV.VUS || 1),
  duration: __ENV.DURATION || "10m",
  thresholds: {
    server_errors: ["count<5"],
  },
  summaryTrendStats: ["avg", "min", "med", "p(90)", "p(95)", "p(99)", "max"],
};

function uniqueUploadBody() {
  return `${BASE_TEXT}\nEKP-UNIQUE-${__VU}-${__ITER}-${Date.now()}-${Math.random()}\n`;
}

export default function () {
  const current = ensureSession(OWNER);
  const fileName = `loadtest_${__VU}_${__ITER}_${Date.now()}.txt`;
  const response = http.post(
    `${BASE_URL}/api/v1/documents/upload`,
    {
      file: http.file(uniqueUploadBody(), fileName, "text/plain"),
      title: `Load test ${fileName}`,
    },
    {
      headers: { Authorization: `Bearer ${current.accessToken}` },
      tags: { endpoint: "documents_upload" },
      timeout: "120s",
    },
  );

  if (response.status >= 500) {
    serverErrors.add(1, { endpoint: "documents_upload" });
  }
  check(response, {
    "upload 201/409/413/429": (res) => [201, 409, 413, 429].includes(res.status),
    "upload not 5xx": (res) => res.status < 500,
  }, { endpoint: "documents_upload" });

  sleep(randomBetween(20, 30));
}