# Phần 5: Kiểm thử, debug và hiệu suất

## Test results

- Toàn bộ pytest sau khi thêm cache: **53 passed**.
- Chậm nhất: `tests/test_01_provided.py::test_untouched_workspace_does_not_score_full` — **6.90 s**; test này chạy kiểm tra task workspaces và là ngoại lệ rõ rệt so với các test còn lại.
- Worker-tool integration: **3/3 passed** (SQLite query qua DataAgent, tạo/chạy script qua CodeAgent, scoring qua EvaluatorAgent).
- Coordinator debug smoke test: route và aggregate qua hai worker giả thành công.
- Integration test bổ sung kiểm tra pipeline Coordinator → DataAgent → SQLite tool và hai request đồng thời không tráo kết quả.

## Một lần E2E với model thật

- Model adapter: `ChatOpenAI` (giá trị model/provider cụ thể không được ghi vào báo cáo).
- Fixture: SQLite tạm với một bản ghi Q3 revenue = 5,000,000.
- Kết quả: worker trả lời **5,000,000**, gọi `query_database` đúng một lần; trạng thái worker `success`; thời gian đo **2.627 s**.
- Token usage: script không nhận/ghi usage metadata của provider nên không có số token đáng tin cậy để báo cáo.
- Lần chạy này hoàn thành request nhưng lần cleanup ban đầu báo Windows file-lock khi xóa thư mục tạm. Fixture tồn lại đã được xóa thủ công sau khi xác nhận đúng file thuộc run này. Script hiện giải phóng worker/model trước cleanup. Không chạy lại model thật để tránh phát sinh thêm request/chi phí.

## Benchmark local offline

Đo 20 request coordinator với hai fake worker song song; **không gọi LLM/provider**.

| Metric | Đo được |
|---|---:|
| Min latency | 0.0030 s |
| Max latency | 0.0100 s |
| Mean latency | 0.0067 s |
| Median latency | 0.0069 s |
| P50 (nearest-rank approximation) | 0.0069 s |
| P99 (nearest-rank approximation) | 0.0100 s |
| Throughput trong bài đo | 8,893.8 request/phút |
| Error rate | 0% |

Kết quả lưu ở `report/benchmark_offline.json`. Đây là thời gian orchestration và fake worker local, không đại diện hiệu suất model/API, DB từ xa, hay throughput production. Số vòng lặp nhỏ và in-memory queue khiến throughput cao không nên được diễn giải như năng lực hệ thống thực.

## Profiling và bottleneck

`cProfile` trên 100 luồng request offline (mỗi request dispatch tới hai fake worker) ghi `report/profile_offline.prof`. Tổng thời gian profile là **0.219 s**. Các hàm có cumulative time cao nhất gồm vòng lặp asyncio, `Coordinator.process_request` (**400 lần gọi, 0.110 s cumulative**) và `MessageQueue.send_message` (**400 lần gọi, 0.093 s cumulative**). `copy.deepcopy` tiêu thụ **0.066 s cumulative** trong chuỗi message/log. Trong workload giả lập nhẹ này, chi phí queue/deep-copy nổi bật hơn phần xử lý worker; chưa có đủ dữ liệu để tối ưu khi dùng provider thật.

### Cache cold/warm (một request minh họa)

- Cold miss: **0.006872 s**; warm hit: **0.000107 s**; `cache_hit=true`.
- Worker calls tổng **41**: 20 request benchmark chính × 2 worker, cộng cold request × 1 worker; warm request không gọi worker.
- Đây là một mẫu duy nhất với fake workers, không phải ước lượng tăng tốc có ý nghĩa thống kê.

## Kết luận và giới hạn

- Test suite offline ổn định trong lượt chạy này; integration đường đi từ coordinator tới local SQLite tool hoạt động.
- Một E2E thật cho thấy model gọi tool thành công trong 2.627 s, nhưng một mẫu không đủ để suy ra P50/P99, error rate, throughput hoặc token cost.
- Benchmark không gọi model và chỉ đo fake workers; không so sánh trực tiếp được với latency 2.627 s của E2E.
- Lần E2E đầu gặp vấn đề cleanup file tạm trên Windows. File được xác định và dọn thủ công; script đã bổ sung giải phóng tham chiếu, nhưng không chạy lại request thật để kiểm chứng việc cleanup sau sửa.
- Python script worker chạy bằng subprocess có timeout nhưng không phải sandbox bảo mật chống mã không tin cậy.
