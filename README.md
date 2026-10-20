# BLF Converter

Tool chạy cục bộ trên **Windows 10/11 64 bit**, chuyển **BLF + DBC → MF4 và Excel XLSX**, hỗ trợ **CAN/CAN FD**. Giao diện tiếng Việt, xử lý theo luồng để ưu tiên tốc độ và tránh nạp toàn bộ log vào RAM.

## Chạy ứng dụng

1. Tải mã nguồn và giải nén, hoặc clone repository.
2. Cài **Python 3.12 64 bit**, giữ thành phần **Tcl/Tk** và **Python Launcher**.
3. Mở `Run_Windows.bat`. Lần đầu cần mạng để cài thư viện; chuyển đổi log sau đó chạy hoàn toàn cục bộ.
4. Chọn BLF, thêm DBC và gán đúng kênh CAN, chọn thư mục kết quả rồi bắt đầu chuyển đổi.

Để chuyển nhanh nhất, chọn riêng **MF4**, giữ mặc định **không nén** và dùng SSD. Excel tự chia sheet/workbook khi cần; xuất Excel có thể chiếm phần lớn thời gian xử lý. Có thể chạy `Build_Windows.bat` để đóng gói EXE trên Windows.

## Xử lý và kiểm thử

- Đọc BLF trực tiếp theo từng khối; giải mã DBC bằng NumPy.
- Ghi **MDF 4.10**, giữ timestamp gốc và đơn vị tín hiệu; không resample.
- Ghi XLSX theo luồng; có báo cáo ID chưa giải mã và thời gian từng giai đoạn.
- **55 kiểm thử đạt**; có dữ liệu mẫu và kết quả trong `examples/`.

Benchmark tổng hợp trên Linux: **1 triệu frame → MF4 khoảng 2,7 giây**. Đây không phải kết quả trên Windows hoặc log xe thực tế 3 GB, không dùng để cam kết tốc độ trên máy đích. **ETAS MDA 8.6 là ứng dụng đích; chưa xác nhận trực tiếp khả năng mở file bằng MDA 8.6 trong môi trường phát triển.** Có thể dùng `examples/demo.mf4` để kiểm tra trước.

Xem [hướng dẫn đầy đủ](README_VI.md), [kết quả đo hiệu năng](BENCHMARK.md) và [kết quả kiểm thử](verification/test-results.txt). Giữ BLF gốc để lưu các frame không khớp DBC và dữ liệu ngoài phạm vi tín hiệu được xuất.
