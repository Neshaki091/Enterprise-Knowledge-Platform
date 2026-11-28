/**
 * Saturation / stress test — tăng tải đến khi gặp 429 hoặc 5xx.
 *
 * Không dùng bài này để đo soak: 429 là kết quả mong đợi khi chạm rate limit.
 * Chạy ngoài giờ/staging:
 *   k6 run load_tests/k6/saturation.js
 *
 * Ghi chú:
 * - 429 của token bucket ứng dụng có Retry-After; 503 của Nginx là limit_req.
 * - Theo dõi first-429 trong log và metric rate_limited.
 */

import { check, sleep } from "k6";
import { pickEmployee, saturationHit, serverErrors } from "./common.js";

let loggedFirst429 = false;

export const options = {
  scenarios: {
    saturation: {
      executor: "ramping-vus",
      startVUs: 0,
      stages: [
        { duration: "1m", target: 10 },
        { duration: "2m", target: 20 },
        { duration: "2m", target: 40 },
        { duration: "2m", target: 60 },
        { duration: "1m", target: 0 },
      ],
      gracefulRampDown: "20s",
    },
  },
  thresholds: {
    server_errors: ["count<10"],
  },
  summaryTrendStats: ["avg", "min", "med", "p(90)", "p(95)", "p(99)", "max"],
};

export default function () {
  const result = saturationHit(pickEmployee());
  if (!loggedFirst429 && result && result.status === 429) {
    loggedFirst429 = true;
    console.log(`First 429 observed at ${new Date().toISOString()} (VU=${__VU}, iter=${__ITER})`);
  }
  // Saturation không pacing dài; chỉ nhường event loop một chút.
  sleep(0.2);
}