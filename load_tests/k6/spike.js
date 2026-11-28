/**
 * Spike test — tăng đột ngột 0 → 20 VU trong 5 giây, giữ 1 phút, giảm nhanh.
 *
 * Mục tiêu: xem hệ thống có trả 429/503 an toàn và tự hồi phục sau spike không.
 * Chạy:
 *   k6 run load_tests/k6/spike.js
 */

import { check, sleep } from "k6";
import { pickEmployee, saturationHit, serverErrors } from "./common.js";

export const options = {
  scenarios: {
    spike: {
      executor: "ramping-vus",
      startVUs: 0,
      stages: [
        { duration: "5s", target: 20 },
        { duration: "1m", target: 20 },
        { duration: "10s", target: 0 },
      ],
      gracefulRampDown: "10s",
    },
  },
  thresholds: {
    server_errors: ["count<10"],
  },
  summaryTrendStats: ["avg", "min", "med", "p(90)", "p(95)", "p(99)", "max"],
};

export default function () {
  const result = saturationHit(pickEmployee());
  check(result, {
    "spike not 5xx": (res) => res.status < 500,
    "spike status expected": (res) => [200, 429].includes(res.status),
  }, { endpoint: "spike" });
  sleep(0.5);
}