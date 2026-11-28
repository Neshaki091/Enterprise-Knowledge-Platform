/**
 * Journey 1 — Standard Employee.
 *
 * Mỗi VU là một nhân viên: login/refresh, search, list document, workspace,
 * chat RAG (mặc định mock để không đốt quota) và feedback.
 *
 * Chạy soak 4h:
 *   k6 run -e VUS=3 -e DURATION=4h load_tests/k6/journey1_employee.js
 *
 * Chạy chat thật (có kiểm soát quota):
 *   k6 run -e VUS=3 -e DURATION=30m -e CHAT_MODE=hybrid load_tests/k6/journey1_employee.js
 */

import { employeeSession, pickEmployee } from "./common.js";

export const options = {
  vus: Number(__ENV.VUS || 3),
  duration: __ENV.DURATION || "30m",
  thresholds: {
    http_req_failed: ["rate<0.01"],
    server_errors: ["count<5"],
  },
  summaryTrendStats: ["avg", "min", "med", "p(90)", "p(95)", "p(99)", "max"],
};

export default function () {
  employeeSession(pickEmployee());
}