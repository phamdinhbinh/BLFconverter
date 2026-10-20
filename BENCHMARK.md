# Kết quả đo trên dữ liệu tổng hợp

Đo bằng `tools/benchmark.py` trong môi trường Linux, Python 3.12, 3 CPU logic. Đây **không phải** phép đo trên Windows, ETAS MDA 8.6 hay BLF thực tế 3 GB. Bộ dữ liệu lặp lại nên nén mạnh; không suy ra thời gian thực tế từ dung lượng BLF nén.

Các số dưới đây được đo với bản 1.0.0 (`asammdf==8.8.27`). Bản 1.0.1 chuyển sang `asammdf==8.7.2` để cài được bằng wheel trên Windows/Python 3.14; xem [kiểm tra tương thích](verification/python314.md). Chạy lại benchmark trên máy đích để đo cấu hình hiện tại.

| Phép đo | Dữ liệu | Thời gian |
| --- | --- | --- |
| Đọc bằng python-can | 1.000.000 frame | 3,581 giây |
| Đọc bằng parser của tool | Cùng 1.000.000 frame | 2,352 giây |
| Chuyển BLF + DBC → MF4 | 1.000.000 frame, 2.714.287 mẫu tín hiệu | 2,725 giây |
| Chuyển BLF + DBC → MF4 + XLSX | 100.000 frame, 271.431 mẫu tín hiệu | 1,199 giây |

Parser nhanh hơn khoảng **1,52 lần** trong phép đo chỉ đọc. Peak RSS của toàn bộ tiến trình khoảng **136,9 MiB**, bao gồm tạo dữ liệu mẫu và tất cả giai đoạn benchmark. Đây không phải cam kết giới hạn RAM cho mọi DBC.

Dữ liệu gồm CAN/CAN FD 64 byte, hai kênh CAN, standard/extended ID, signed/scaled, Intel/Motorola, multiplex và số nguyên 64 bit. Chi tiết đo máy và JSON có trong `verification/benchmark.json`.

Chạy lại trên máy đích sau khi cài dependencies phát triển:

```bat
python tools/benchmark.py benchmark-output --frames 1000000 --excel-frames 100000
```

Để đo log của bạn, chuyển bằng giao diện hoặc CLI và xem `*.report.json`: có thời gian đọc, giải mã, ghi từng đầu ra và tổng thời gian. Nên thử riêng MF4 trước, sau đó bật Excel để đánh giá chi phí ghi số ô thực tế.
